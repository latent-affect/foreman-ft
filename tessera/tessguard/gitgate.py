"""Layer 2: the git pre-commit/pre-push/commit-msg hard gates.

Genuinely blocking -- exit code is the entire enforcement mechanism. Runs as git's own
pre-commit/pre-push/commit-msg subprocess (invoked via .githooks/ + core.hooksPath), independent
of Claude Code entirely: it fires the same way for a human typing `git commit` as for an agent.

Repo root here is ALWAYS git-derived (`git rev-parse --show-toplevel`) -- unlike layer 1's
operator-supplied root, this one necessarily is a real git repository, since a git hook cannot
fire anywhere else. A repo resolving to zero registered TESSERA projects BLOCKS here (the
deliberate inverse of layer 1's fail-open on the same input) -- an empty project set is
vacuously false under the any-of rule, and blocking-by-default is correct for a hard gate: an
unregistered repo is not evidence that logging happened.

Two real checks, at two different git hook points, because they check different things and one
of them structurally CANNOT run at the other's hook point:

check_gate() / pre-commit and pre-push: window is wall-clock "now" minus HARD_GATE_WINDOW_HOURS,
never a commit's author-date -- pre-commit runs before the commit object exists, so there is no
commit timestamp to anchor to. This is Tier E0 (QUALITY-BAR.md's evidence-tier vocabulary):
existence of *some* real TESSERA activity for the project, no binding to what this specific commit
actually does. Kept exactly as tested -- a fast, cheap, early warning, per the same
keep-the-fast-check-add-the-real-enforcement-elsewhere pattern QUALITY-BAR.md's Design
principles section (§5) already established for the ship-readiness/v1.0 gate split.

check_commit_message() / commit-msg: the real T-7 binding (QUALITY-BAR.md §8), and the
reason it lives at commit-msg specifically -- pre-commit fires before the
commit message is collected, so it structurally cannot see message content; commit-msg is the
first point in git's own hook sequence where the actual message text exists to check. Denies
unless the message names a real, existing ticket for one of this repo's registered projects --
raises the bar from "some activity happened somewhere" (E0) to "this specific commit is bound to
a real, checkable ticket" (E1). Fails closed on the same structural-claim logic as check_gate:
a ticket-shaped string in a commit message is not proof the ticket is real, so the ticket is
looked up in the store, not just pattern-matched.

Named, accepted limitation: `git commit --no-verify` bypasses both. No server-side git remote
exists in this local-only setup to add a second enforcement point.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

from tessera.store.store import Store
from tessera.common import utc_now_iso

from . import config, event_activity, gitutil, project_resolve

TICKET_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d+)\b")


class GateResult:
    def __init__(self, passed, reason, projects_checked, events_found):
        self.passed = passed
        self.reason = reason
        self.projects_checked = projects_checked
        self.events_found = events_found


def check_gate(repo_root, store, window_hours=None):
    window_hours = window_hours if window_hours is not None else config.HARD_GATE_WINDOW_HOURS

    projects = project_resolve.resolve_projects_for_repo(repo_root, store)
    if not projects:
        return GateResult(
            passed=False,
            reason=(
                f"no TESSERA project registered for this repo ({repo_root}) -- register it "
                f"(tessera register-project) before committing, or bypass with --no-verify "
                f"if this is intentionally unregistered work."
            ),
            projects_checked=[],
            events_found=0,
        )

    prefixes = [p["prefix"] for p in projects]
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    events = event_activity.real_events_for_projects(
        store, prefixes,
        window_start_iso=window_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        window_end_iso=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    if not events:
        return GateResult(
            passed=False,
            reason=(
                f"no real TESSERA activity ({'/'.join(prefixes)}) in the trailing "
                f"{window_hours}h. Log this work first: `python3 -m tessera.api.cli --db "
                f"{store.db_path} comment <TICKET> --actor <you> --body ...` "
                f"(or create/transition/freeze-criteria/claim), then commit again."
            ),
            projects_checked=prefixes,
            events_found=0,
        )

    return GateResult(passed=True, reason="ok", projects_checked=prefixes, events_found=len(events))


class CommitMessageResult:
    def __init__(self, passed, reason, projects_checked, matched_ticket):
        self.passed = passed
        self.reason = reason
        self.projects_checked = projects_checked
        self.matched_ticket = matched_ticket


def check_commit_message(repo_root, store, message):
    """T-7: a commit only counts as real compliance evidence for a ticket if the message names
    that specific, real, existing ticket -- not a ticket-shaped string that happens to look
    right, and not mere co-occurrence with unrelated recent TESSERA activity (check_gate's job).

    Every candidate token matching TICKET_ID_RE is checked against the store, not just the
    first one -- a message can legitimately reference a ticket from a project this repo isn't
    registered under (a cross-project note); that candidate is skipped, not treated as a match,
    but doesn't block a real match elsewhere in the same message from passing.
    """
    projects = project_resolve.resolve_projects_for_repo(repo_root, store)
    if not projects:
        return CommitMessageResult(
            passed=False,
            reason=(
                f"no TESSERA project registered for this repo ({repo_root}) -- register it "
                f"(tessera register-project) before committing, or bypass with --no-verify "
                f"if this is intentionally unregistered work."
            ),
            projects_checked=[],
            matched_ticket=None,
        )

    prefixes = {p["prefix"] for p in projects}
    for match in TICKET_ID_RE.finditer(message or ""):
        candidate = f"{match.group(1)}-{match.group(2)}"
        if match.group(1) not in prefixes:
            continue
        ticket = store.get_ticket(candidate, with_context=False)
        if ticket is not None:
            return CommitMessageResult(
                passed=True, reason="ok", projects_checked=sorted(prefixes),
                matched_ticket=candidate,
            )

    return CommitMessageResult(
        passed=False,
        reason=(
            f"commit message doesn't name a real, existing ticket for this repo's registered "
            f"project(s) ({'/'.join(sorted(prefixes))}). Name the specific ticket this commit "
            f"closes or advances directly in the message, e.g. '{sorted(prefixes)[0]}-123: "
            f"<what this commit does>' -- general recent TESSERA activity elsewhere in the "
            f"project is not evidence this commit is tied to real, checkable work "
            f"(QUALITY-BAR.md T-7)."
        ),
        projects_checked=sorted(prefixes),
        matched_ticket=None,
    )


def _open_repo_and_store(label):
    """Shared fail-closed resolution for both hook entrypoints: real git repo, real reachable
    TESSERA store, or a printed reason and None -- never a silent default. `label` names the
    calling hook in stderr output so a denial is traceable to which hook fired it."""
    cwd = os.getcwd()
    repo_root = gitutil.git_toplevel(cwd)
    if repo_root is None:
        print(f"tessguard {label}: {cwd} is not inside a git working tree -- refusing to guess, blocking.", file=sys.stderr)
        return None

    try:
        db_path = config.resolve_db_path()
    except config.DbPathError as exc:
        # Fail CLOSED here, the deliberate opposite of layer 1's fail-open on the same
        # input -- a hard gate that can't reach its data source must not silently pass.
        print(f"tessguard {label}: {exc} -- failing closed (this is the blocking layer; "
              f"an unreachable data source must not silently pass).", file=sys.stderr)
        return None

    try:
        store = Store(str(db_path))
    except Exception as exc:  # noqa: BLE001 -- any Store construction failure fails closed
        print(f"tessguard {label}: could not open TESSERA store at {db_path}: {exc} -- "
              f"failing closed.", file=sys.stderr)
        return None

    return repo_root, store


def main():
    opened = _open_repo_and_store("gitgate")
    if opened is None:
        return 1
    repo_root, store = opened

    result = check_gate(repo_root, store)
    if not result.passed:
        print(f"tessguard gitgate: BLOCKED. {result.reason}", file=sys.stderr)
        return 1

    return 0


def main_commit_msg():
    """commit-msg hook entrypoint. Git passes the path to a file holding the commit message as
    its own first argument to whatever script .githooks/commit-msg names -- read from argv[-1]
    (the last argument) rather than a fixed index, so this works whether invoked directly or via
    `python3 -m tessera.tessguard.gitgate --commit-msg <path>` (the `--commit-msg` dispatch flag
    below), without the two invocation shapes fighting over which index is real."""
    if len(sys.argv) < 2:
        print("tessguard commit-msg: no message file path given by git -- refusing to guess, blocking.", file=sys.stderr)
        return 1
    message_path = sys.argv[-1]
    try:
        with open(message_path, encoding="utf-8") as fh:
            message = fh.read()
    except OSError as exc:
        print(f"tessguard commit-msg: could not read message file {message_path}: {exc} -- "
              f"failing closed.", file=sys.stderr)
        return 1

    opened = _open_repo_and_store("commit-msg")
    if opened is None:
        return 1
    repo_root, store = opened

    result = check_commit_message(repo_root, store, message)
    if not result.passed:
        print(f"tessguard commit-msg: BLOCKED. {result.reason}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--commit-msg":
        sys.exit(main_commit_msg())
    sys.exit(main())
