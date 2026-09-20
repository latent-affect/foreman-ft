#!/usr/bin/env python3
"""FORE-5: pre-flight gate. Before build work proceeds in a Foreman project, refuse (DENY)
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
to (FORE-3, C3 vs C4).
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

    if status == "ambiguous":
        hc.set_rule(f"{RULE_ID}:ambiguous")
        hc.deny(
            f"Foreman preflight: this project's TESSERA registration is ambiguous "
            f"(candidates {result['candidates']}). Write .foreman/tessera-prefix naming the "
            f"correct one of these, e.g. `echo \"<PREFIX>\" > .foreman/tessera-prefix` "
            f"with <PREFIX> replaced by whichever of {result['candidates']} actually matches "
            f"this project, then retry."
        )
        return

    if status == "unreachable":
        hc.set_rule(f"{RULE_ID}:unreachable")
        # FORE-659 (Stage 2): "unreachable" used to get ONE remedy ("write
        # .foreman/tessera-prefix") regardless of cause, but that remedy is actively wrong
        # for cause="cli-error" -- tessera_resolver.resolve() returns before it ever reads
        # the override file in that branch (confirmed by reading resolve()'s own source), so
        # writing one and retrying reliably reproduces the identical denial. Branching on the
        # now-machine-readable "cause" field (tessera_resolver.py, this same proposal) instead
        # of giving one message for both.
        cause = result.get("cause")
        if cause == "invalid-override":
            known = result.get("known_prefixes") or []
            hc.deny(
                f"Foreman preflight: .foreman/tessera-prefix names an invalid prefix "
                f"({result.get('reason', 'unknown')}). Known valid prefixes right now: "
                f"{known}. Fix the file, e.g. `echo \"<PREFIX>\" > "
                f".foreman/tessera-prefix` with <PREFIX> replaced by whichever of {known} "
                f"actually matches this project, then retry."
            )
        else:
            # cause == "cli-error", or an older/unrecognized result shape with no "cause" at
            # all (fails toward the safer, more honest message rather than assuming the
            # override-file remedy is safe to suggest).
            hc.deny(
                f"Foreman preflight: TESSERA itself could not be queried "
                f"({result.get('reason', 'unknown')}) -- this is NOT a prefix-naming "
                f"problem, and writing .foreman/tessera-prefix will not fix it (the query "
                f"that failed happens before that file is ever read). Reproduce directly to "
                f"see the real error: `/usr/bin/python3 -m tessera.api.cli --db "
                f"{tr.DB_PATH!r} list-projects` (run from directory {tr.TESSERA_CWD!r}). If "
                f"that also fails, this needs an operator/human to look at TESSERA's own "
                f"health -- retrying the build action that hit this gate will not help."
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
