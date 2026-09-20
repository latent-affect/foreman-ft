#!/usr/bin/env python3
"""Foreman hard gate -- REQ-45: a gate that opens is not a briefing. No implementation-scope
Write/Edit/Bash until the judging persona for this component has recorded a real, ticket-anchored
brief AND that component's GOALS.json cites the same ticket.

THE GAP THIS CLOSES: architecture_gate.py's own docstring already says the quiet part out loud --
"this hook checks that ARCHITECTURE-REVIEW.md exists and is non-empty. It cannot check whether the
review is any good, or even whether it's real." Once that gate opens, the implementing agent is
trusted to have correctly read and internalized however much of ARCHITECTURE.md applies to the
component it's about to touch, with nothing checking that the specific applicable constraints are
actually in its own working context. REQ-21 freezes GOALS.json criteria before implementation;
REQ-44 picks the right persona to judge the eventual gate. Neither requires that persona to have
actively communicated its own criteria before the code exists. This gate is that missing step.

LAYERED QUALIFICATION, NOT A NEW PERSONA (operator's explicit design call, 2026-08-29): the persona
that writes the brief is the SAME persona whose lane will judge the eventual gate (REQ-44's rule
picks which one). One file, one standard, two modes -- brief mode (this gate) is communication
only and may run as an in-process Agent-tool subagent, since it renders no judgment and the
independence concern FORE-206 exists for does not apply to it. Judge mode (the actual review that
checks conformance) still requires a genuine peer session, unchanged, per FORE-206.

WHAT THIS GATE DOES NOT CHECK, disclosed on purpose, same convention as architecture_gate.py: it
does not verify the brief's CONTENT is any good, does not verify the persona field names the
correct persona for this component's eventual gate (that binding is deliberately loose for this
first cut -- REQ-45's full generalization across every stage transition is future work, not this
gate), and does not shell out to TESSERA to confirm the cited ticket actually exists there --
that's a live cross-repo dependency this codebase already has a named lesson against (LESSONS-
LEARNED.md, "a gate that shells out to another repository fails whenever that repository is
mid-edit"). This gate only checks its own local marker file's shape and that GOALS.json cites the
same ticket ID the marker names -- cheap, fully local, no fragile live dependency.

SCOPE: same trigger as architecture_gate.py (implementation-scope Write/Edit/Bash), and expects
architecture_gate.py to have already opened -- this gate answers a different question (was the
agent told what it'll be judged against), not "does an architecture doc exist."

Registered PROJECT-LOCALLY, same reasoning as architecture_gate.py: a user-wide hard DENY would
immediately block every project that hasn't opted in.

FAIL-CLOSED, NOT hc.run() -- same reasoning as agent_dispatch_gate.py/bob_write_gate.py: this is a
policy gate, and failing open on an internal error means silently allowing the exact thing it
exists to stop.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_lib  # noqa: E402
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402
import verdict_ledger  # noqa: E402

RULE_ID = "PRE-IMPLEMENTATION-BRIEF-GATE"
BRIEFS_DIR = ".foreman/briefs"


def _brief_marker_path(project_root, component_name):
    return project_root / BRIEFS_DIR / f"{component_name}.json"


# CHV2-174: _goals_json_path() (removed) used to build "<root>/GOALS.json" by hand, unconditionally
# -- the exact shared-baseline path FORE-CHV2-102 already fixed goals_freeze_gate.py to stop
# using. This gate was never updated in step, so for any bare-file component sharing its root
# with others (the live hooks/ collision, 7 components), it kept reading the shared baseline
# GOALS.json instead of that component's own per-component file -- denying a real, already-
# satisfied brief because it was checking the wrong file, not because the citation was
# missing. Found live 2026-09-19, blocking FORE-672 (foreman_evidence): its brief cited
# FORE-672 and its real GOALS.json (hooks/foreman_evidence.GOALS.json) already did too, via
# its own amendment A4 -- the gate denied anyway, reading hooks/GOALS.json (7 OTHER
# components) baseline instead. Fixed by calling component_coupling.goals_json_path(), the
# exact resolver goals_freeze_gate.py already uses correctly (same call shape, confirmed by
# direct read of goals_freeze_gate.py before this fix, not assumed) -- one resolver, not two
# that can drift apart. See test_pre_implementation_brief_gate_goals_path.py
# (CHV2-174-TEST-TICKET, a self-contained repro, not tied to FORE-672's own live state) for
# the real, executed proof: 4/5 pass unfixed (this exact bug reproduces), 5/5 pass fixed.


def _load_brief(marker_path):
    """Returns (ticket_id, persona) if the marker is well-formed, else (None, reason)."""
    try:
        raw = marker_path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"marker unreadable ({e})"
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"marker is not valid JSON ({e})"
    if not isinstance(blob, dict):
        return None, "marker JSON is not an object"
    ticket_id = blob.get("ticket_id")
    persona = blob.get("persona")
    if not ticket_id or not isinstance(ticket_id, str):
        return None, "marker has no non-empty string ticket_id"
    if not persona or not isinstance(persona, str):
        return None, "marker has no non-empty string persona"
    return (ticket_id, persona), None


def _goals_json_cites(goals_path, ticket_id):
    try:
        text = goals_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return ticket_id in text


def _check(file_path, project_root, component_map):
    rel_path = cc._relative_or_none(file_path, project_root)
    if rel_path is None:
        # Outside the project entirely -- same convention component_coupling.py's own
        # predicates use for this case (never gated), not an error condition.
        return False

    component_name = cc.component_of(rel_path, component_map)
    if component_name is None:
        # Not attributable to a declared component -- architecture_gate.py's own scope check
        # (is_pre_architecture_scope) already governs whether an undeclared-component write is
        # allowed at all; this gate has nothing further to say about a path it can't attribute.
        return False

    marker_path = _brief_marker_path(project_root, component_name)
    if not marker_path.is_file():
        hc.set_rule(f"{RULE_ID}:no-brief-recorded")
        hc.deny(
            f"Foreman: {file_path} is in component '{component_name}', but no pre-implementation "
            f"brief is recorded at {marker_path}. Run the brief mode of the persona who will judge "
            f"this component's eventual gate (skills/foreman/skills/brief) before writing "
            f"implementation files -- it should cite a real ticket and write that marker."
        )
        return True

    loaded, reason = _load_brief(marker_path)
    if loaded is None:
        hc.set_rule(f"{RULE_ID}:malformed-brief")
        hc.deny(
            f"Foreman: the brief marker at {marker_path} exists but is malformed ({reason}). "
            f"Fix or re-run the brief before writing implementation files for '{component_name}'."
        )
        return True

    ticket_id, _persona = loaded
    component_root_dir = cc.component_root(component_name, component_map)
    goals_path = cc.goals_json_path(component_name, component_root_dir, project_root, component_map)
    if not _goals_json_cites(goals_path, ticket_id):
        hc.set_rule(f"{RULE_ID}:goals-does-not-cite-brief")
        hc.deny(
            f"Foreman: component '{component_name}' has a recorded brief ({ticket_id}) but its "
            f"GOALS.json at {goals_path} does not cite that ticket. The frozen criteria and the "
            f"brief must be the same document by reference -- add the citation before writing "
            f"implementation files."
        )
        return True

    hc.set_rule(f"{RULE_ID}:brief-satisfied")
    return False


def component_map_for_root(project_root, cache):
    """Per-root component map, memoized for one payload. The map has to be loaded INSIDE the
    per-target loop now, not once before it: which ARCHITECTURE.md declares the components that
    govern a write is a property of the project holding the file, so a single pre-loop load
    would answer the jurisdiction question with the writer's answer no matter how carefully the
    target was resolved afterwards.

    FORE-339's note about `malformed` still stands and is deliberately not acted on here -- see
    _check()'s own statement that an unattributable path is not this gate's concern.
    """
    if project_root not in cache:
        component_map, unused_malformed = cc.parse_component_map(project_root)
        cache[project_root] = component_map
    return cache[project_root]


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    cwd = data.get("cwd")
    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        targets = [file_path]
    else:
        command = (data.get("tool_input") or {}).get("command", "")
        targets = list(cc.extract_bash_write_targets(command, cwd))
        if not targets:
            return

    # FORE-581. project_root used to be resolved ONCE from the payload cwd -- the writer's own
    # location -- and every target was then attributed to THAT project's components and checked
    # against THAT project's briefs. Fired, two sibling Foreman projects each declaring a
    # component the other does not, neither with a brief recorded:
    #
    #   cwd=A, target in A's component    DENY     the gate works when cwd matches
    #   cwd=B, target in A's component    SILENT   the cross-project fail-open
    #   cwd=A, target in B's component    SILENT   and in the other direction too
    #
    # The project that declared the component, froze its GOALS.json and would have to record the
    # brief never got asked about a write into its own component.
    #
    # THIS GATE IS THE SECOND LINE BEHIND architecture_gate (FORE-576), by that gate's own
    # admission that it cannot judge whether a review is any good. Both lines failed open in the
    # same direction for the same reason, so neither backstopped the other for a cross-project
    # write. FORE-576 fixed the first line; this is the second.
    #
    # cwd is still passed to is_pre_architecture_scope and to the extractor, which need it to
    # resolve a relative target against the writer's directory. That is a use of cwd the target
    # genuinely requires; deciding which project governs was not.
    cache = {}
    saw_a_project = False
    saw_a_component_map = False
    gated_any = False
    for target in targets:
        target_root = cc.project_root_for_target(target, cwd)
        if target_root is None:
            continue  # not inside any Foreman project -- not this gate's concern
        saw_a_project = True
        component_map = component_map_for_root(target_root, cache)
        if not component_map:
            continue  # nothing to attribute a write to yet; architecture_gate.py governs this
        saw_a_component_map = True
        if not cc.is_pre_architecture_scope(target, target_root):
            continue
        if _check(target, target_root, component_map):
            return
        gated_any = True

    if not saw_a_project:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return
    if not saw_a_component_map:
        hc.set_rule(f"{RULE_ID}:no-component-map")
        return
    if not gated_any:
        hc.set_rule(f"{RULE_ID}:not-in-scope")
        return
    hc.set_rule(f"{RULE_ID}:gate-open")


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
    except BaseException as exc:  # noqa: BLE001 -- deliberate, see module docstring
        try:
            audit_lib.audit_append("HOOK_ERROR", session_id=sid, cwd=cwd, severity="high",
                                    hook="pre_implementation_brief_gate.py", error=repr(exc))
        except Exception:
            print("[pre_implementation_brief_gate] could not record HOOK_ERROR to the audit plane",
                  file=sys.stderr)
        hc.set_rule(f"{RULE_ID}:internal-error")
        hc.deny(f"Blocked: pre_implementation_brief_gate.py hit an internal error "
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
                handler_id="pre_implementation_brief_gate.py",
                decision=hc.EMITTED_DECISION,
                rule_id=hc.EMITTED_RULE_ID,
                reason=hc.EMITTED_REASON,
            )
        except Exception:
            print("[pre_implementation_brief_gate] verdict ledger write failed", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    _fail_closed_entrypoint()
