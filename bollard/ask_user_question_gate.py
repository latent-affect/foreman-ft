#!/usr/bin/env python3
"""AskUserQuestion category-declaration gate -- FORE-208. Applies to any Foreman-v2-governed
project, not just tessera-v2: the whole point is a durable mechanism, because in-context
corrections (a broadcast SendMessage, a corrected ticket) do not survive context loss, and a
session that hits compaction or starts fresh reverts to whatever its base instructions say.
A hook is the only part of tonight's fix that outlives the conversation that produced it.

THE GAP THIS EXISTS FOR (found live, 2026-08-27, tessera-v2's dogfood run): 6 AskUserQuestion
calls in ~4 hours, no mechanical constraint on any of them. REQ-35/38's PDP (pdp_turn_gate.py)
gates the Stop event -- named stop conditions before a session may end its turn -- but has zero
provision for AskUserQuestion, which any session can call freely regardless of standing
continuation authority. See ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md section 4.4.3b and FORE-208 for
the full incident: broadcasting "stop asking, batch your questions" into live sessions worked for
one evening; nothing survives a fresh session without a hook.

WHY THIS IS A DECLARATION GATE, NOT A CLASSIFIER OR A RATE LIMIT. Live investigation of the 6
real events (FORE-208, corrected once already) found FOUR genuinely different categories, and
two of them must always reach the operator:
  1. unverifiable peer authorization / gate-bypass-by-proxy claims -- ALWAYS escalate. A peer
     asking this session to do what IT was itself denied, on an authorization claim the operator
     has not confirmed directly, is the exact permission-laundering pattern this project's own
     standing rules require refusing. (The one real miscategorization in this investigation: an
     early draft called this "peer-coordination noise, self-resolve" and was wrong. A session
     correctly refused that instruction and was right to.)
  2. ordinary role/task coordination -- should self-resolve via SendMessage/ListAgents.
  3. mechanical implementation defaults -- should just be decided, not asked.
  4. genuine permission-boundary exceptions (a session's own hard constraint, e.g. "my
     instructions bar the Agent tool unless the operator asks for it") -- only the operator can
     grant these.

A regex or a model call cannot reliably tell (1) from (2): both legitimately mention "a peer
session said X." A blind rate-limit cannot either, and would have blocked the one correct
escalation (category 1) alongside the noise. Building either would repeat this project's own
already-diagnosed mistake (DUCKING conflating a false-positive dodge with a real-control dodge,
ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md 4.4.3, "Run 3 changed the method" #3) at a new site. So this
gate does not judge content. It requires DISCLOSURE -- the same pattern REQ-35 already uses for
stop conditions (`stop-declared.json`) -- and lets a human or a later audit pass judge whether the
declared category was honest, the same falsifiable-not-unforgeable posture this project already
applies elsewhere (REQ-34, section 5 of the Alice/Bob design).

FAIL-CLOSED, NOT hc.run(). Same reasoning as bob_write_gate.py/agent_dispatch_gate.py section 4.5:
failing open here means silently allowing an undeclared question through, which defeats the one
thing this file is for.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_lib  # noqa: E402
import hook_common as hc  # noqa: E402
import verdict_ledger  # noqa: E402

RULE_ID = "ASK-USER-QUESTION-GATE"
CATEGORIES = (
    "peer-authorization-unverified",
    "peer-coordination",
    "mechanical-default",
    "permission-boundary",
)
DECLARATION_RELPATH = Path(".foreman") / "ask-category-declared.json"
# Provisional PoC value, same disclosure as pdp_turn_gate.py's MAX_CONSECUTIVE_BLOCKS: no
# evidence behind the exact number, calibrate from real use. A declaration older than this is
# treated as stale -- forces a fresh, honest declaration per question rather than one
# declaration silently covering an unrelated later ask.
STALE_AFTER_SECONDS = 900


def find_project_root(cwd):
    """Walk up from cwd looking for a .foreman/ directory -- same discovery convention
    architecture_gate.py's find_project_root uses (see component_coupling.py), reimplemented
    minimally here rather than imported, since this gate must not depend on any Alice/Bob-pipeline
    module that could itself be mid-change."""
    if not cwd:
        return None
    p = Path(cwd).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / ".foreman").is_dir():
            return candidate
    return None


def main(data):
    if data.get("tool_name") != "AskUserQuestion":
        return

    sid = data.get("session_id")
    cwd = data.get("cwd")
    # FORE-562: origin cwd, not the live one, for jurisdiction -- a mid-session `cd` must not
    # move which project's opt-in this gate checks. `cwd` stays live for everything else in
    # this file (audit/deny-message context, a legitimate "where did this actually happen" use).
    project_root = find_project_root(hc.origin_cwd(data))
    if project_root is None:
        # Same posture as case 5 elsewhere in this harness: never gated, never opted in.
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    decl_path = project_root / DECLARATION_RELPATH
    if not decl_path.is_file():
        deny_undeclared(sid, cwd)
        return

    try:
        decl = json.loads(decl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        deny_undeclared(sid, cwd)
        return

    if not isinstance(decl, dict):
        deny_undeclared(sid, cwd)
        return

    if decl.get("session_id") != sid:
        # A declaration written by a DIFFERENT session does not authorize this one's question --
        # same principle as every other per-session record in this codebase (the dispatch
        # record, the intent file): a claim is not transferable just by being present on disk.
        deny_undeclared(sid, cwd)
        return

    written_at = decl.get("written_at")
    if not isinstance(written_at, (int, float)) or (time.time() - written_at) > STALE_AFTER_SECONDS:
        deny_undeclared(sid, cwd, reason_suffix="stale")
        return

    category = decl.get("category")
    if category not in CATEGORIES:
        deny_undeclared(sid, cwd, reason_suffix="invalid-category")
        return

    # REQ-43 (FORE-208): a declared peer-coordination question redirects to the project's
    # declared orchestrator, if one exists, rather than reaching the operator -- exactly the
    # role this category was supposed to route to once REQ-43 gave it somewhere to route to.
    # No orchestrator on record -> allow unchanged (nobody else to check with, same as before
    # REQ-43 existed).
    if category == "peer-coordination":
        orch_path = project_root / ".foreman" / "pdp-orchestrator.json"
        held_by = None
        if orch_path.is_file():
            try:
                orch_blob = json.loads(orch_path.read_text(encoding="utf-8"))
                if isinstance(orch_blob, dict):
                    held_by = orch_blob.get("session_id")
            except (OSError, json.JSONDecodeError):
                held_by = None
        if held_by and held_by != sid:
            hc.audit("SAFETY_DENY", {"guard": "ask_user_question_gate", "reason":
                      "peer-coordination-has-orchestrator", "held_by": held_by}, sid, cwd,
                     severity="info")
            hc.set_rule(f"{RULE_ID}:peer-coordination-has-orchestrator")
            try:
                decl_path.unlink()
            except OSError:
                pass
            hc.deny(
                f"Blocked: this project has a declared orchestrator (session {held_by}, "
                f"REQ-43/FORE-208). Peer-coordination questions route to them via SendMessage, "
                f"not to the operator. If you believe you need to reach the operator anyway "
                f"(e.g. the orchestrator is unresponsive), that is itself a "
                f"peer-authorization-unverified case, not peer-coordination -- redeclare under "
                f"that category if it's genuinely that."
            )
            return

    # Declared, fresh, valid: allow (silent), audit the declaration itself so there's a durable
    # record independent of this conversation. This gate never permanently blocks a real need --
    # it costs one extra round trip the first time, never again per declaration.
    hc.audit("ASK_USER_QUESTION_DECLARED", {
        "guard": "ask_user_question_gate", "category": category,
        "why": str(decl.get("why", ""))[:500],
    }, sid, cwd, severity="info")
    hc.set_rule(f"{RULE_ID}:declared-{category}")
    # Consume the declaration so a second question doesn't silently ride on the same one --
    # forces a fresh declaration per ask, matching the fresh_json "written during THIS turn"
    # discipline pdp_turn_gate.py already uses for the wakeup marker.
    try:
        decl_path.unlink()
    except OSError:
        pass
    return


def deny_undeclared(sid, cwd, reason_suffix="undeclared"):
    hc.set_rule(f"{RULE_ID}:{reason_suffix}")
    hc.audit("SAFETY_DENY", {"guard": "ask_user_question_gate", "reason": reason_suffix},
             sid, cwd, severity="info")
    hc.deny(
        "Blocked: AskUserQuestion requires a fresh category declaration first (FORE-208). "
        "Write .foreman/ask-category-declared.json in the project root with "
        '{"session_id": "<your real session_id>", "written_at": <unix epoch seconds>, '
        '"category": one of ["peer-authorization-unverified", "peer-coordination", '
        '"mechanical-default", "permission-boundary"], "why": "one sentence"}, then retry this '
        "call. This never blocks a real need permanently -- it costs one extra step, and the "
        "declaration becomes a durable, auditable record instead of an unrecorded guess. "
        "Categories 2 (peer-coordination) and 3 (mechanical-default) should almost always be "
        "resolved via SendMessage/ListAgents or a reasonable default instead of asking at all -- "
        "declaring them here is not the same as being told to ask anyway."
    )


def _fail_closed_entrypoint():
    started = time.time()
    data = hc.read_input()
    sid = None
    cwd = None
    try:
        if not isinstance(data, dict):
            raise ValueError(f"hook payload was not a JSON object (got {type(data).__name__})")
        sid = data.get("session_id")
        cwd = data.get("cwd")
        main(data)
        # No GateDeny-style control-flow exception here, unlike bob_write_gate.py/
        # agent_dispatch_gate.py: hc.deny() emits its output and returns normally rather than
        # raising, and deny_undeclared() already returns after calling it -- so main() returning
        # is itself the only path, whether it denied or allowed.
    except BaseException as exc:  # noqa: BLE001 -- deliberate, see module docstring
        try:
            audit_lib.audit_append("HOOK_ERROR", session_id=sid, cwd=cwd, severity="high",
                                    hook="ask_user_question_gate.py", error=repr(exc))
        except Exception:
            print("[ask_user_question_gate] could not record HOOK_ERROR to the audit plane",
                  file=sys.stderr)
        hc.set_rule(f"{RULE_ID}:internal-error")
        hc.deny(f"Blocked: ask_user_question_gate.py hit an internal error "
                f"({type(exc).__name__}). Fails closed by design.")
    finally:
        elapsed = int((time.time() - started) * 1000)
        try:
            verdict_ledger.record(
                data,
                "fire" if hc.EMITTED_KIND else "silent",
                kind=hc.EMITTED_KIND,
                duration_ms=elapsed,
            # CHV2-134: reason=hc.EMITTED_REASON threaded through explicitly, the same fix
            # BUILD4-I1 landed in agent_dispatch_gate.py and alice_bob_fable/gate/write_gate.py.
            # BUILD4-I1 enumerated the affected hooks by hand and named two; an AST sweep over
            # every non-test file under hooks/ that both denies and records a verdict found
            # three more with the identical shape, this file among them. Without it, FORE-659's
            # deny-plus-next-command guidance reached the harness on stdout and never reached the
            # durable record, so the ledger could show THAT a deny happened and not WHAT the
            # agent was told to do about it.
                handler_id="ask_user_question_gate.py",
                decision=hc.EMITTED_DECISION,
                rule_id=hc.EMITTED_RULE_ID,
                reason=hc.EMITTED_REASON,
            )
        except Exception:
            print("[ask_user_question_gate] verdict ledger write failed", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    _fail_closed_entrypoint()
