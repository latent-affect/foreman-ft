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


def _goals_json_path(project_root, component_root_dir):
    # component_root() returns a project-relative string ("store/", or "" for a bare-file
    # component per its own docstring), not a Path -- join it onto project_root explicitly
    # rather than assume Path-like division works on it.
    return Path(project_root) / (component_root_dir or "") / "GOALS.json"


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
    goals_path = _goals_json_path(project_root, component_root_dir)
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


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    project_root = cc.find_project_root(data.get("cwd"))
    if project_root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    component_map = cc.parse_component_map(project_root)
    if not component_map:
        hc.set_rule(f"{RULE_ID}:no-component-map")
        return  # nothing to attribute a write to yet; architecture_gate.py governs this case

    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        if not cc.is_pre_architecture_scope(file_path, project_root):
            hc.set_rule(f"{RULE_ID}:not-in-scope")
            return
        if _check(file_path, project_root, component_map):
            return
        return

    command = (data.get("tool_input") or {}).get("command", "")
    targets = cc.extract_bash_write_targets(command, data.get("cwd"))
    gated_targets = [t for t in targets if cc.is_pre_architecture_scope(t, project_root)]
    if not gated_targets:
        hc.set_rule(f"{RULE_ID}:not-in-scope")
        return
    for target in gated_targets:
        if _check(target, project_root, component_map):
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
                handler_id="pre_implementation_brief_gate.py",
                decision=hc.EMITTED_DECISION,
                rule_id=hc.EMITTED_RULE_ID,
            )
        except Exception:
            print("[pre_implementation_brief_gate] verdict ledger write failed", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    _fail_closed_entrypoint()
