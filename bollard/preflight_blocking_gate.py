#!/usr/bin/env python3
"""Pre-flight gate. Before build work proceeds in a Foreman project, refuse (DENY)
if open, blocking tickets exist for that project OR any project it structurally depends on
(cross_project_routing.DEPENDS_ON) -- and ASK, separately, if an open ticket touching
hook-code scope (per routing_table.classify() on its reference_docs) lacks human ratification.

Same posture family as architecture_gate.py/goals_freeze_gate.py: project-local registration
only, .foreman/ marker self-check as defense in depth, DENY/ASK are named with the specific
ticket ids so the message is actionable, not just "blocked, go look."

Fail-direction, deliberate: tessera_resolver returning "unregistered" is silent (never opted
into TESSERA tracking, mirrors architecture_gate.py's own "never opted in, never gated").
"ambiguous" or "unreachable" DENY -- "confirmed clean" and "couldn't check" must never look
the same to whoever reads the outcome, the same asymmetry ticket_status_gate's design commits
to (see its own C3 vs C4).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tessera_resolver"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "cross_project_routing"))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402
import tessera_resolver as tr  # noqa: E402
import routing_table as rt  # noqa: E402

RULE_ID = "FOREMAN-PREFLIGHT-GATE"


def remedy_path(project_root):
    return (Path(project_root) / ".foreman" / tr.OVERRIDE_FILENAME).resolve()


def is_remedy_write(data, project_root):
    """True only when the tool call's sole write target is the named override file.

    The ambiguous/unreachable deny names that file as the escape. Without this
    carve-out the same matcher that just denied also blocks the Write/Edit/Bash that would
    create it. Identity is Path.resolve equality against remedy_path, not a suffix match,
    and not a class-wide .foreman/ or control-file exemption.
    """
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    wanted = remedy_path(project_root)
    if tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path")
        if not file_path:
            return False
        candidate = Path(file_path)
        if not candidate.is_absolute():
            cwd = data.get("cwd")
            if not cwd:
                return False
            candidate = Path(cwd) / candidate
        try:
            return candidate.resolve() == wanted
        except (OSError, RuntimeError):
            return False
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        targets = cc.extract_bash_write_targets(command, data.get("cwd"))
        if len(targets) != 1:
            return False
        try:
            return Path(targets[0]).resolve() == wanted
        except (OSError, RuntimeError):
            return False
    return False


def collect_blocking(prefixes):
    """(blocking_found_or_None, error_prefix_or_None, error_text). Stops at the first
    prefix TESSERA can't be queried for -- a partial answer is not a safe answer here."""
    found = []
    for p in prefixes:
        ok, tickets, err = tr.open_blocking_tickets(p)
        if not ok:
            return None, p, err
        found.extend((p, t) for t in tickets)
    return found, None, None


def ratification_gaps(prefix):
    """Open tickets in `prefix` whose reference_docs touch FORE/AREM hook-code scope and
    lack a human ratified_by. Reuses tessera_resolver.list_open_tickets -- the same call
    collect_blocking already made for this prefix, filtered differently, not a second
    subprocess round-trip on top."""
    ok, data, err = tr.list_open_tickets(prefix)
    if not ok:
        return None, err
    gaps = []
    for t in data.get("tickets", []):
        touches_hook_scope = any(
            rt.classify(doc, registered_project_resolver=tr.resolve) in ("FORE", "AREM")
            for doc in (t.get("reference_docs") or [])
        )
        if touches_hook_scope and not tr.ticket_ratified(prefix, t["ticket_id"]):
            gaps.append(t["ticket_id"])
    return gaps, None


def format_blocking(blocking_found):
    parts = []
    for p, t in blocking_found:
        if t["malformed"]:
            parts.append(f"{p}/{t['ticket_id']} (malformed: no blocking_criterion)")
        else:
            parts.append(f"{p}/{t['ticket_id']} (blocks {t['blocking_criterion']})")
    return "; ".join(parts)


def main(data):
    if data.get("tool_name") not in ("Edit", "Write", "Bash"):
        return

    project_root = cc.find_project_root(data.get("cwd"))
    if project_root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    result = tr.resolve(project_root)
    status = result["status"]

    if status == "unregistered":
        hc.set_rule(f"{RULE_ID}:not-tessera-registered")
        return

    if status in ("ambiguous", "unreachable"):
        if is_remedy_write(data, project_root):
            hc.set_rule(f"{RULE_ID}:{status}-remedy-write")
            return
        hc.set_rule(f"{RULE_ID}:{status}")
        detail = (f"candidates {result['candidates']}" if status == "ambiguous"
                  else result.get("reason", "unknown"))
        hc.deny(
            f"Foreman preflight: could not confirm this project's TESSERA registration is "
            f"safe to build against ({status}: {detail}). Write .foreman/tessera-prefix "
            f"naming the correct prefix to disambiguate, then retry."
        )
        return

    prefix = result["prefix"]
    to_check = [prefix, *sorted(rt.depends_on(prefix))]

    blocking_found, unreachable_prefix, err = collect_blocking(to_check)
    if blocking_found is None:
        hc.set_rule(f"{RULE_ID}:blocking-check-unreachable")
        hc.deny(
            f"Foreman preflight: could not check {unreachable_prefix} for open blocking "
            f"tickets ({err}). Not proceeding on an unconfirmed queue."
        )
        return

    if blocking_found:
        hc.set_rule(f"{RULE_ID}:blocking-tickets-open")
        hc.deny(
            f"Foreman preflight: open blocking ticket(s) for {prefix} or a project it "
            f"depends on -- {format_blocking(blocking_found)}. Resolve or clear "
            f"blocking=false before starting new build work here."
        )
        return

    gaps, err = ratification_gaps(prefix)
    if gaps is None:
        hc.set_rule(f"{RULE_ID}:ratification-check-unreachable")
        return  # a read-only speed bump failing to run degrades to silent, not a hard deny
    if gaps:
        hc.set_rule(f"{RULE_ID}:needs-ratification")
        hc.ask(
            f"Foreman preflight: {', '.join(gaps)} touch(es) hook-code scope and has no "
            f"human ratified_by set (agent proposes, does not self-ratify). Confirm before "
            f"proceeding, or set ratified_by explicitly."
        )
        return

    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
