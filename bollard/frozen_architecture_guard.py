#!/usr/bin/env python3
"""Foreman hard gate -- no Write/Edit/Bash-write to a project's root ARCHITECTURE.md once the
architecture stage has closed for that project, for ANY session, with NO identity exception.

Real gap, found live 2026-09-20 (PDP.md section 11.6, rewritten same night): a build's frozen
ARCHITECTURE.md has no mechanical protection today. architecture_gate.py gates IMPLEMENTATION
files until architecture closes; concept_gate.py gates writing ARCHITECTURE.md itself until
concept closes. Nothing gates writing ARCHITECTURE.md itself AFTER architecture closes -- the
exact direction this incident needed. Both existing gates were read in full before writing this
one specifically to confirm neither already covered it, not assumed.

PDP.md section 11.6, canonical text (2026-09-20 rewrite, itself correcting an earlier same-day
draft that wrongly authorized the orchestrating session to perform this write): once architecture
closes for a build, ARCHITECTURE.md does not get edited again for that build BY ANYONE -- not
Clint (no session is ever dispatched to), not the orchestrator, not a peer session, no exception
for "the content is already fully decided." A needed change is a TESSERA ticket for the NEXT
build's architecture stage, never a write to this build's frozen document. This hook makes that
mechanical instead of relying on every session reading and honoring the prose correctly under
pressure -- the same reason architecture_gate.py and concept_gate.py exist for their own
directions.

DENY-or-nothing, same discipline as every other guard hook in this codebase (architecture_gate.py,
concept_gate.py, guard_prodconfig.py): silence IS the allow.

Registered PROJECT-LOCALLY (a project's own .claude/settings.json), never ~/.claude/settings.json
-- same reasoning architecture_gate.py's own comment gives: a user-wide hard gate would instantly
deny every project that has never opened an architecture stage.

CLOSED-STATE SIGNAL, reused rather than reinvented: identical bar architecture_gate.py already
uses to decide architecture is done enough to open the implementation gate --
ARCHITECTURE-REVIEW.md exists and is non-empty (existence-of-review bar, not quality -- that
judgment stays Clint Eastwood's, made once, at authorship time). If the project has opted into
REVIEW_BINDING_MARKER, the stronger ledger-bound check is used instead: fe.
review_binds_architecture_via_ledger() returning "bound" means a real, independently-verified
review still matches ARCHITECTURE.md's current content, which is the closed state this hook
denies against. A "mismatch"/"unbound"/"error" verdict under the marker is NOT treated as open
here (unlike architecture_gate.py's own direction) -- an unverifiable binding state is exactly
when accidentally allowing a further edit would be worst, so this hook fails toward DENY on any
non-"bound" outcome once the review file itself is non-empty, rather than architecture_gate.py's
fail-toward-DENY-the-other-direction.

NO IDENTITY EXCEPTION, ON PURPOSE. A real Clint session, if one existed, is denied by this hook
exactly the same as any other session -- there is no carve-out for "the named author," because
per PDP.md section 11.6 the named author does not return to a closed stage within the same build
either. This also sidesteps the whole class of problem this project's own findings already name
(session identity is forgeable, subagents share a parent session_id) -- the gate does not need to
know who is asking, because the answer is the same for everyone once the state is closed.

FIX 2026-09-20 (FORE-711, Iris Chen's real, executed adversarial probe, independently reproduced
by the orchestrating session): _is_architecture_doc() decided identity by comparing RESOLVED
PATHS and, as a fallback, case-folded NAMES -- both purely name/path-based, with no inode-identity
check. A real hardlink alias (a second directory entry for the SAME inode as ARCHITECTURE.md --
not a link `resolve()` can follow, since there is nothing to follow) named a totally unrelated
path, and the hook allowed a write through it silently, then that write really did change
ARCHITECTURE.md's own bytes. This codebase already has the correct pattern for exactly this
bypass class: registry_write_guard.py's (CHV2-115) `same_stat_identity()`, os.path.samestat on
(st_dev, st_ino), checked as the PRIMARY identity test rather than a fallback. Ported here, in
the same position CHV2-115 uses it: checked against the real ARCHITECTURE.md path before the
case-fold fallback, which is now reached only for a target that does not exist yet (no inode to
compare) -- a real alias of an existing ARCHITECTURE.md is caught by the identity test first,
regardless of what name or path it was written through.

FIX 2026-09-20 (CHV2-179, Nadia Osei's real STRIDE/validate finding, Build 8, independently
re-verified by the Orchestrator before routing): FORE-711's own identity check above is only ever
REACHED inside main()'s per-target loop AFTER component_coupling.project_root_for_target() --
purely path-based, no inode awareness -- has already resolved a target into SOME recognized
Foreman project. Two real bypasses follow directly from that ordering, neither requiring anything
beyond an ordinary hardlink:

  (1) A hardlink alias of this project's own ARCHITECTURE.md placed at a path OUTSIDE every
      Foreman project root (no .foreman/ marker anywhere above it, e.g. /tmp/notes.txt) makes
      project_root_for_target() return None, and main()'s loop `continue`s on that None BEFORE
      _is_architecture_doc() -- and therefore _same_stat_identity() -- is ever called. The write
      proceeds untouched by this hook and still mutates the real, frozen ARCHITECTURE.md's bytes,
      because the alias shares its inode.

  (2) A hardlink alias placed INSIDE a DIFFERENT project that also happens to carry its own
      .foreman/ marker resolves project_root_for_target() to that OTHER project, and
      _is_architecture_doc() then compares the alias's inode against THAT project's OWN
      ARCHITECTURE.md -- a different file, a different inode from the one actually being
      mutated. The comparison correctly returns False, the hook stays silent, and the write still
      mutates the real, original project's frozen document through the shared inode.

Both are the identical underlying defect: the inode-identity test is conditioned on a path-based
resolution succeeding first, so a target whose PATH does not (or does not correctly) resolve to
the protected project never reaches an identity comparison at all, no matter what inode it really
shares. Fixed by adding a SECOND, unconditional identity check in main()'s own loop, checked
BEFORE (not instead of) the existing path-based route: every candidate target's inode is compared
directly against the ACTING SESSION's OWN project's ARCHITECTURE.md, independent of where the
target's path resolves. This hook is registered PROJECT-LOCALLY (this docstring's own point
above), so whenever it fires at all, the session's cwd is inside (or under) the one project whose
frozen document it exists to protect -- cwd_project_root IS that project, regardless of where a
given target's own path happens to land. Neither bypass shape above has anywhere left to hide: os.
path.samestat compares the real (st_dev, st_ino) pair, not a path, a name, or a containing
project's boundary.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402
import foreman_evidence as fe  # noqa: E402

RULE_ID = "FOREMAN-FROZEN-ARCHITECTURE-GUARD"

# Shared with architecture_gate.py's own opt-in -- a project that has already decided "prove the
# binding, don't just trust existence" gets the stronger check here too, for the same reason.
REVIEW_BINDING_MARKER = ".foreman/review-binding-enabled"
REVIEW_LEDGER_RELPATH = ".foreman/review-events.jsonl"


def _same_stat_identity(path_a, path_b):
    """True when both paths exist and name the SAME real file, compared by (st_dev, st_ino) via
    os.path.samestat -- the identity test registry_write_guard.py (CHV2-115) already established
    in this codebase for exactly this bypass class, ported here rather than re-derived. A
    hardlink is a second directory entry for one inode, not a link to follow, so Path.resolve()
    cannot see it; this comparison is correct regardless of name, path, or filesystem
    case-sensitivity, and needs no platform assumption.

    A path that does not exist raises OSError from os.stat and returns False here, which routes
    to the case-fold fallback in _is_architecture_doc() rather than to a deny -- there is no
    inode yet for a target that has not been created."""
    try:
        return os.path.samestat(os.stat(path_a), os.stat(path_b))
    except (OSError, ValueError):
        return False


def _is_architecture_doc(file_path, project_root):
    """Identical predicate to concept_gate.py's own -- the one file this hook protects, ONLY
    the project-root ARCHITECTURE.md, kept separate from architecture_gate.py's much broader
    implementation-path scope so the three gates' jurisdictions never overlap.

    FORE-711: the PRIMARY test is now real filesystem identity, not path/name comparison -- see
    _same_stat_identity()'s own docstring for why. Path/name comparison survives only as the
    fallback for a target with no inode yet to compare against."""
    try:
        resolved = (project_root / file_path).resolve() if not Path(file_path).is_absolute() \
            else Path(file_path).resolve()
    except OSError:
        return False
    target = (project_root / "ARCHITECTURE.md").resolve()
    if resolved == target:
        return True
    if _same_stat_identity(resolved, target):
        return True
    # Case-fold fallback, same rationale as concept_gate.py's identical guard: this machine's
    # filesystem is case-insensitive, so a differently-cased path must not slip through. Reached
    # only for a target that does not exist yet (no inode for the identity test above to compare)
    # -- a real alias of an EXISTING ARCHITECTURE.md, hardlink or otherwise, is caught by the
    # identity test first, regardless of what it is named.
    return resolved.parent == target.parent and resolved.name.lower() == "architecture.md"


def _architecture_is_closed(project_root):
    """Returns (closed: bool, detail: str). Reuses architecture_gate.py's own closed-state bar
    rather than inventing a second one that could silently disagree with it."""
    review_path = project_root / "ARCHITECTURE-REVIEW.md"
    try:
        review_nonempty = review_path.is_file() and review_path.stat().st_size > 0
    except OSError:
        # Fails toward DENY here, the OPPOSITE direction from architecture_gate.py's own
        # identical stat-failure branch -- deliberate, not a copy-paste mismatch. That hook's
        # safe side is "assume architecture isn't ready, block the implementation write."
        # This hook's risk runs the other way: an uncertain state should not silently permit an
        # edit to a document that might already be closed, which is exactly the incident this
        # hook exists to prevent. "Closed" here, with an honest reason, not "open".
        return True, "ARCHITECTURE-REVIEW.md could not be stat'd -- closed-state unknown, failing toward DENY"

    if not review_nonempty:
        return False, "no non-empty ARCHITECTURE-REVIEW.md -- architecture stage not closed yet"

    if not (project_root / REVIEW_BINDING_MARKER).is_file():
        return True, "ARCHITECTURE-REVIEW.md is recorded and non-empty"

    ledger_path = project_root / REVIEW_LEDGER_RELPATH
    arch_path = project_root / "ARCHITECTURE.md"
    verdict, reason = fe.review_binds_architecture_via_ledger(review_path, arch_path, ledger_path)
    # Deliberately NOT architecture_gate.py's direction. That hook treats a broken binding as
    # "not yet provably open" and denies the IMPLEMENTATION write until it's fixed. This hook
    # treats a broken binding as "still closed, and now also unverifiable" -- the state where
    # silently allowing a further ARCHITECTURE.md edit would be worst, since it could paper over
    # exactly the drift the ledger check exists to catch. Every verdict this function can return
    # keeps the document locked; the ledger's own state changes WHY, never WHETHER.
    return True, f"ARCHITECTURE-REVIEW.md is recorded and ledger-checked as bound (verdict={verdict}, {reason})"


def _check(file_path, project_root):
    closed, detail = _architecture_is_closed(project_root)
    if not closed:
        hc.set_rule(f"{RULE_ID}:architecture-open")
        return False

    hc.set_rule(f"{RULE_ID}:architecture-closed")
    hc.deny(
        f"Foreman: {file_path} is {project_root}'s ARCHITECTURE.md, and architecture has already "
        f"closed for this build ({detail}). PDP.md section 11.6: once closed, this document is not "
        f"edited again for the rest of this build, by anyone -- not Clint Eastwood (no session is "
        f"dispatched back into a closed stage), not the orchestrating session, not a peer session, "
        f"regardless of how settled the intended content is. Log the needed change as a real "
        f"TESSERA ticket comment (what's needed, the evidence, what it would change) instead -- it "
        f"becomes input to the NEXT build's architecture stage, not a write to this one's frozen "
        f"document."
    )
    return True


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    cwd = data.get("cwd")
    # CHV2-179: resolved ONCE per invocation, used below as a hardlink-proof identity anchor
    # independent of any per-target path resolution -- see this module's own top docstring for
    # the full incident and why this project (the one whose cwd governs this hook firing at all)
    # is the correct, and only, anchor to check every target against.
    cwd_project_root = cc.find_project_root(cwd)
    cwd_arch_path = (cwd_project_root / "ARCHITECTURE.md") if cwd_project_root else None

    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        targets = [file_path]
    else:
        # A denied Edit/Write is trivially routed around with a shell redirect, tee, cp/mv, or
        # sed -i -- same FORE-1 coverage architecture_gate.py and concept_gate.py both carry.
        command = (data.get("tool_input") or {}).get("command", "")
        targets = list(cc.extract_bash_write_targets(command, cwd))

    gated_any = False
    saw_a_project = False
    for raw_target in targets:
        try:
            candidate = Path(raw_target)
            if not candidate.is_absolute():
                candidate = (Path(cwd) if cwd else Path.cwd()) / candidate
            resolved_target = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            continue

        # CHV2-179: checked FIRST, unconditionally, before the path-based route below. See this
        # module's own top docstring for the two bypass shapes this closes -- a hardlink alias
        # placed outside every project root, or inside an unrelated one, neither of which
        # project_root_for_target() can see, since it resolves by PATH, not by inode.
        if cwd_arch_path is not None and _same_stat_identity(resolved_target, cwd_arch_path):
            saw_a_project = True
            gated_any = True
            if _check(str(resolved_target), cwd_project_root):
                return
            # Architecture is still open for cwd's own project -- fall through to the ordinary
            # path-based route below for this same target, which is a harmless no-op re-check
            # in the common case (the target usually resolves to this exact project too) and
            # costs nothing extra when it doesn't.

        target_root = cc.project_root_for_target(str(resolved_target), cwd)
        if target_root is None:
            continue  # not inside any Foreman project -- never gated
        saw_a_project = True
        if not _is_architecture_doc(str(resolved_target), target_root):
            continue
        gated_any = True
        if _check(str(resolved_target), target_root):
            return
        # Last-target-wins on the open-path rule, same disclosed limitation both sibling gates
        # carry -- _check() already set the specific rule_id for this path before returning False.

    if not saw_a_project:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return
    if not gated_any:
        hc.set_rule(f"{RULE_ID}:not-in-scope")


if __name__ == "__main__":
    hc.run(main)
