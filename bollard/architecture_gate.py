#!/usr/bin/env python3
"""Foreman hard gate 1 of 2 -- no Write/Edit inside a declared component's implementation path
until ARCHITECTURE.md exists AND a review event is recorded (foreman-design.html v0.1 SS01 item
1, v0.2 SS01 "The two hard hooks in front of implementation").

DENY-or-nothing, same discipline as R-DEP-DOCS: this hook either denies or says nothing. It
never asks and never allows explicitly -- silence IS the allow, same as every other guard hook
in this codebase (guard_prodconfig.py, rule_dependency_docs.py).

Registered PROJECT-LOCALLY (a project's own .claude/settings.json), not in
~/.claude/settings.json. Registering a hard DENY gate user-wide would immediately start
blocking every other project on this machine the moment it's registered, since none of them
have ARCHITECTURE.md. find_project_root()'s .foreman/ marker check is defense in depth on top
of that, not a substitute for the safer registration scope -- see foreman-design.html v0.3 SS01.

The "review event recorded" bar is deliberately weak and says so out loud
(foreman-design.html v0.1/v0.2 SS07, "the architecture gate enforces existence, not quality"):
this hook checks that ARCHITECTURE-REVIEW.md exists and is non-empty. It cannot check whether
the review is any good, or even whether it's real -- that stays Clint Eastwood's judgment, made
by a human or agent, not by this hook.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402
import foreman_evidence as fe  # noqa: E402

RULE_ID = "FOREMAN-ARCH-GATE"

# A3 (QUALITY-BAR.md section 2.1): does the recorded review still bind to ARCHITECTURE.md's
# CURRENT content? Opt-in via this marker, same pattern as ship_readiness_gate.py's own
# .foreman/ship-readiness-gate-enabled -- deliberately NOT a blanket behavior change. Wiring
# this as an unconditional DENY the day it ships would immediately break every one of the 23
# real bootstrapped projects on this machine, none of which have a review-binding hash line
# yet -- the exact class of mistake an earlier architecture review pass caught in the
# GOALS.json binding attempt this replaced (both toy hashers would have false-denied all 76
# real GOALS.json on this machine). Silent-by-default until a project explicitly opts in.
REVIEW_BINDING_MARKER = ".foreman/review-binding-enabled"


def _check(file_path, project_root):
    """The actual gate logic, shared by the direct Edit/Write path and the Bash-target loop
    below. Returns True if it denied (caller should stop there), False if the gate is open
    for this specific path."""
    arch_path = project_root / "ARCHITECTURE.md"
    review_path = project_root / "ARCHITECTURE-REVIEW.md"

    if not arch_path.is_file():
        hc.set_rule(f"{RULE_ID}:no-architecture-doc")
        hc.deny(
            f"Foreman: {file_path} looks like implementation code (nested below the project "
            f"root), but no ARCHITECTURE.md exists at {project_root} yet. Run "
            f"foreman:architecture first."
        )
        return True

    try:
        review_nonempty = review_path.is_file() and review_path.stat().st_size > 0
    except OSError:
        review_nonempty = False  # fails toward DENY (the safe side), not toward silently allowing

    if not review_nonempty:
        hc.set_rule(f"{RULE_ID}:no-review-recorded")
        hc.deny(
            f"Foreman: ARCHITECTURE.md exists but no ARCHITECTURE-REVIEW.md review event is "
            f"recorded at {project_root}. Run the Clint Eastwood architecture review before "
            f"writing implementation files."
        )
        return True

    if (project_root / REVIEW_BINDING_MARKER).is_file():
        verdict, reason = fe.review_binds_architecture(review_path, arch_path)
        if verdict in ("mismatch", "unbound", "ambiguous"):
            hc.set_rule(f"{RULE_ID}:review-{verdict}")
            hc.deny(
                f"Foreman: ARCHITECTURE-REVIEW.md at {project_root} does not bind to "
                f"ARCHITECTURE.md's current content ({reason}). This project has opted into "
                f"review-hash binding via {REVIEW_BINDING_MARKER} -- re-run the Clint Eastwood "
                f"architecture review and stamp the fresh hash before writing implementation "
                f"files."
            )
            return True
        hc.set_rule(f"{RULE_ID}:review-bound")
    else:
        # Not opted in -- still worth a real signal in the ledger (free, no deny), so adoption
        # can be measured the same way F-10's own gate-fire evidence was measured, rather than
        # only ever knowing a project's binding state by asking.
        verdict, _ = fe.review_binds_architecture(review_path, arch_path)
        hc.set_rule(f"{RULE_ID}:review-{verdict}-unenforced")

    return False


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    project_root = cc.find_project_root(data.get("cwd"))
    if project_root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return  # this project never opted in -- never gated

    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        if not cc.is_pre_architecture_scope(file_path, project_root):
            hc.set_rule(f"{RULE_ID}:not-in-scope")
            return
        if _check(file_path, project_root):
            return
        hc.set_rule(f"{RULE_ID}:gate-open")
        return

    # tool_name == "Bash": a denied Edit/Write is trivially routed around with a shell
    # redirect, tee, cp/mv, or sed -i -- an earlier finding here. Same predicate, same deny,
    # applied to every write-shaped target the command appears to touch.
    command = (data.get("tool_input") or {}).get("command", "")
    targets = cc.extract_bash_write_targets(command, data.get("cwd"))
    gated_targets = [t for t in targets if cc.is_pre_architecture_scope(t, project_root)]
    if not gated_targets:
        hc.set_rule(f"{RULE_ID}:not-in-scope")
        return
    for target in gated_targets:
        if _check(target, project_root):
            return
    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
