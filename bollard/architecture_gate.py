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
import artifact_provenance as ap  # noqa: E402

RULE_ID = "FOREMAN-ARCH-GATE"

# A3 (QUALITY-BAR.md section 2.1): does the recorded review still bind to ARCHITECTURE.md's
# CURRENT content? Opt-in via this marker, same pattern as ship_readiness_gate.py's own
# .foreman/ship-readiness-gate-enabled -- deliberately NOT a blanket behavior change. Wiring
# this as an unconditional DENY the day it ships would immediately break every one of the 23
# real bootstrapped projects on this machine, none of which have a review-binding hash line
# yet -- the exact class of mistake Clint Eastwood's FORE-65 architecture pass caught in the
# GOALS.json binding attempt this replaced (both toy hashers would have false-denied all 76
# real GOALS.json on this machine). Silent-by-default until a project explicitly opts in.
REVIEW_BINDING_MARKER = ".foreman/review-binding-enabled"

# CHV2-10 (PDP-RATIONALE.md section 8 item 0): review_binds_architecture_via_ledger is REQ-12's
# ledger-consult REPLACEMENT for review_binds_architecture()'s self-reported hash (see that
# function's own docstring) -- not a second, independent check. So it shares REVIEW_BINDING_MARKER
# as its opt-in signal rather than introducing one keyed on the ledger file's own existence: a
# project that has already decided "prove it" via the marker gets the stronger of the two
# mechanisms, and a project that hasn't opted in stays exactly as open as it always was, same as
# today. tests/test_architecture_gate.py already encoded this exact contract (opted-in-with-no-
# ledger-entry denies, opted-in-with-a-real-stamped-entry opens, not-opted-in stays open
# regardless) before this gate had any real caller wiring it -- this fixes that, not the tests.
#
# CHV2-22 correction: the first landing of this fix in the main working tree had a real, live
# blast radius on 14 external real projects that register claude-hooks-v2's hooks by absolute
# working-tree path rather than a deployed copy (FORE-338/FORE-344's known issue, hit for real --
# tessera-v2 and atlas-sonnet-qa were actually denied, live, by this edit before it was reverted).
#
# FORE-458 correction: this WAS rebuilt in an isolated worktree to keep it at zero live effect,
# but commit 93b3d93 ("land staged REQ-12 ledger-binding change") landed it again in the main
# tree, and it is live on this branch right now -- actively denying real Edit/Write/Bash calls,
# not dormant. Do not read the paragraph above as describing the current state; it describes the
# incident and the mitigation that was IN EFFECT before 93b3d93, not now. If this needs to go back
# to zero live effect, that's a deliberate revert/worktree move, not a fact already true here.
REVIEW_LEDGER_RELPATH = ".foreman/review-events.jsonl"


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

    ledger_path = project_root / REVIEW_LEDGER_RELPATH
    if (project_root / REVIEW_BINDING_MARKER).is_file():
        verdict, reason = fe.review_binds_architecture_via_ledger(review_path, arch_path, ledger_path)
        if verdict in ("mismatch", "unbound", "error"):
            hc.set_rule(f"{RULE_ID}:ledger-{verdict}")
            # FORE-659 (Stage 2): the old message named the script but none of its four
            # required positional args (confirmed real signature: stamp_review_event.py
            # <project_root> <artifact_relpath> <stage> <reviewer> -- test_review_events_
            # ledger_guard.py's own real invocation example is the precedent this fills in
            # from: "python3 stamp_review_event.py /tmp/proj ARCHITECTURE-REVIEW.md
            # architecture clint-eastwood"). project_root, the artifact path, and the stage
            # are structural facts this gate already knows and are safe to pre-fill outright.
            # <REVIEWER> is deliberately left a literal placeholder, NOT pre-filled with a
            # default name (e.g. "clint-eastwood") even though that name is this stage's own
            # established convention elsewhere in this file's OTHER deny branch -- REQ-12's
            # entire purpose (stamp_review_event.py's own module docstring: "the review-
            # writing process no longer gets to assert its own hash... refuses to run without
            # a real reviewer-output artifact") is that this ledger entry attests a REAL
            # review happened. Pre-filling a copy-pasteable reviewer identity would let an
            # agent stamp a hollow entry under a name that never actually reviewed anything --
            # the exact hollow-attestation failure this mechanism exists to prevent -- so
            # construction-over-instruction stops at the three non-identity arguments here,
            # deliberately, not by oversight.
            stamp_script = Path(__file__).resolve().parent / "stamp_review_event.py"
            hc.deny(
                f"Foreman: ARCHITECTURE-REVIEW.md at {project_root} has no entry proving it "
                f"binds to ARCHITECTURE.md's current content in the review-events ledger "
                f"({reason}). This project has opted into review binding via "
                f"{REVIEW_BINDING_MARKER}. Run this command, with <REVIEWER> replaced by "
                f"whoever actually performed the review (never a placeholder, and never the "
                f"agent that hit this deny), to record a real, independently-computed review "
                f"event, then retry: `python3 {stamp_script} {project_root} "
                f"ARCHITECTURE-REVIEW.md architecture <REVIEWER>`"
            )
            return True
        hc.set_rule(f"{RULE_ID}:ledger-bound")
    else:
        # Not opted in -- still worth a real signal in the ledger (free, no deny), so adoption
        # can be measured the same way F-10's own gate-fire evidence was measured, rather than
        # only ever knowing a project's binding state by asking. Uses the same ledger-consult
        # check the opted-in branch does (REQ-12's replacement for the self-reported hash), not
        # the older review_binds_architecture(), so the unenforced signal measures adoption of
        # the mechanism this gate would actually enforce if the project opted in.
        verdict, _ = fe.review_binds_architecture_via_ledger(review_path, arch_path, ledger_path)
        hc.set_rule(f"{RULE_ID}:ledger-{verdict}-unenforced")

    return False


def _audit_artifact_provenance(project_root, data):
    """FORE-207, audit-only, non-blocking (same posture as FORE-232/FORE-465): does either
    ARCHITECTURE.md or ARCHITECTURE-REVIEW.md's content trace back to a merge that brought in
    unrelated history -- the exact `--allow-unrelated-histories` seeding shape that let TESSERA
    V2 inherit ticket-system's already-frozen documents wholesale. Called only once the gate is
    already about to open (both files exist and pass the checks above); never denies, never
    changes the return value of the caller. See artifact_provenance.py's own module docstring
    for the precise, non-heuristic signature this checks."""
    for name in ("ARCHITECTURE.md", "ARCHITECTURE-REVIEW.md"):
        flagged, detail = ap.check_artifact_provenance(project_root / name, project_root)
        if flagged:
            hc.audit(
                "GATE_ARTIFACT_UNRELATED_HISTORY_PROVENANCE",
                {"artifact": name, "detail": detail}, data.get("session_id"), data.get("cwd"),
                severity="medium",
            )


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
        # tool_name == "Bash": a denied Edit/Write is trivially routed around with a shell
        # redirect, tee, cp/mv, or sed -i -- FORE-1's actual finding.
        command = (data.get("tool_input") or {}).get("command", "")
        targets = list(cc.extract_bash_write_targets(command, cwd))

    # FORE-576. project_root came from find_project_root(cwd) -- the WRITER's location -- and
    # both the scope test and _check then ran against THAT project's architecture state. A write
    # into a nested child project was judged by the PARENT's ARCHITECTURE.md and review, so a
    # parent whose gate is open opened it for a child whose own gate is closed.
    #
    # Fired, and the fixture has to make the two projects DISAGREE or it proves nothing. My first
    # two attempts did not: with neither project having an ARCHITECTURE.md the child's file was
    # denied anyway, as the PARENT's own pre-architecture scope -- the right answer arriving from
    # the wrong project. _check() denies on a missing ARCHITECTURE.md, then on a missing or EMPTY
    # ARCHITECTURE-REVIEW.md, so a genuinely open parent needs both.
    #
    #   PARENT has ARCHITECTURE.md + non-empty ARCHITECTURE-REVIEW.md (gate OPEN)
    #   CHILD has neither (gate CLOSED)
    #     cwd=CHILD,  CHILD/src/mod.py     DENY     the child's gate works when cwd matches
    #     cwd=PARENT, PARENT/src/mod.py    allow    the parent is genuinely open
    #     cwd=PARENT, CHILD/src/mod.py     ALLOW    the cross-project fail-open
    #     cwd=CHILD,  outside any project  silent   the negative control
    #
    # cwd is still used to resolve a relative target. It is no longer used to decide which
    # project's architecture governs.
    gated_any = False
    saw_a_project = False
    audited_roots = set()
    for raw_target in targets:
        try:
            candidate = Path(raw_target)
            if not candidate.is_absolute():
                candidate = (Path(cwd) if cwd else Path.cwd()) / candidate
            resolved_target = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        target_root = cc.project_root_for_target(str(resolved_target), cwd)
        if target_root is None:
            continue  # not inside any Foreman project -- never gated
        saw_a_project = True
        if not cc.is_pre_architecture_scope(str(resolved_target), target_root):
            continue
        gated_any = True
        if _check(str(resolved_target), target_root):
            return
        if str(target_root) not in audited_roots:
            _audit_artifact_provenance(target_root, data)
            audited_roots.add(str(target_root))
        # N9-3 fix (2026-08-27): _check() already set the specific open-path rule
        # (review-bound / review-*-unenforced) before returning False -- do not overwrite it.
        # Known limitation, disclosed rather than solved: with multiple gated targets this is
        # still last-target-wins, not an aggregate rule.

    if not saw_a_project:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return
    if not gated_any:
        hc.set_rule(f"{RULE_ID}:not-in-scope")


if __name__ == "__main__":
    hc.run(main)
