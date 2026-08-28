"""Layer 1: the non-blocking coverage audit.

Binary predicate over a COMPLETED session, evaluated only against an explicitly-named
transcript file -- there is no wall-clock/cron auto-trigger in v1 (see ARCHITECTURE.md: a
running/idle-based completion check was measured to false-positive on 15 of 22 checkpoints of
this project's OWN good session, and a stricter cwd-only transcript-validation rule was
toy-modeled and found to reject the one clean example -- GIF -- this whole design rests on).

Never registered in .claude/settings.json. Never touches a Claude Code permission-decision
path. Never exits non-zero for a flagged result -- non-blocking by construction, not by
convention.
"""

import json
import sys
from pathlib import Path

from tessera.store.store import Store
from tessera.common import utc_now_iso

from . import config, event_activity, project_resolve, transcript

AUDIT_LOG_PATH = Path("/path/to/ticket-system/.foreman/tessguard-audit-log.jsonl")


class AuditInputError(Exception):
    """A layer-1 input failed validation. Distinct from AuditResult with flagged=False --
    this must never be silently treated as a clean, checked result."""


def append_log_internal(entry, log_path=None):
    log_path = Path(log_path) if log_path else AUDIT_LOG_PATH
    entry = dict(entry, logged_at=utc_now_iso())
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def validate_transcript(transcript_path, repo_root, total_in_repo_edits=None):
    """OR-rule: accept if the transcript's first recorded cwd is under repo_root, OR the
    transcript -- INCLUDING its subagent transcripts -- contains real in-repo edits. Reject
    (raise AuditInputError) only when neither holds -- a stricter cwd-only precondition was
    tried and rejected because it silently drops GIF whenever the audited root is GIF's own
    real git repository (its session cwd is the outer, non-git directory; its edits are
    still real and in-repo).

    The edit-count side of this OR-rule must be subagent-inclusive, not just the parent
    transcript's own count: a session whose parent cwd doesn't match the audited root, and
    whose real edits were all performed by a delegated subagent rather than the parent
    directly (real and in daily use -- see transcript.py's docstring), would otherwise be
    wrongly rejected as invalid input even though real in-repo edits happened. `summary`'s
    own `in_repo_edits` key stays parent-only (informational, matches transcript_summary()'s
    documented contract); `total_in_repo_edits` is the subagent-inclusive figure this
    function actually gates on.

    `total_in_repo_edits`, if given, is reused as-is instead of rescanning -- callers that
    already computed it (audit_session) should pass it, so the parent+subagent transcripts
    aren't scanned twice; if omitted (e.g. a caller validating a transcript standalone), it's
    computed here via transcript.in_repo_edit_count_including_subagents().

    Returns the full transcript.transcript_summary() dict plus cwd_matches, so callers get
    session_start/session_end from the same single scan rather than re-reading the file.
    A missing/unreadable transcript file is also invalid input -- raised here as
    AuditInputError rather than leaking transcript.py's raw OSError, matching this
    function's own documented contract of the exceptions audit_session may raise."""
    try:
        summary = transcript.transcript_summary(transcript_path, repo_root)
    except OSError as exc:
        raise AuditInputError(
            f"{transcript_path}: could not be read ({exc}) -- rejecting as invalid input."
        ) from exc
    if total_in_repo_edits is None:
        total_in_repo_edits = transcript.in_repo_edit_count_including_subagents(
            transcript_path, repo_root
        )
    repo_root_path = Path(repo_root).resolve()
    cwd_matches = False
    if summary["first_cwd"]:
        try:
            cwd_matches = Path(summary["first_cwd"]).resolve() == repo_root_path
        except OSError:
            cwd_matches = False
    if not cwd_matches and total_in_repo_edits == 0:
        raise AuditInputError(
            f"{transcript_path}: recorded cwd ({summary['first_cwd']!r}) does not match "
            f"audited root {repo_root} and zero in-repo edits (including subagents) found "
            f"-- rejecting as invalid input, not a clean zero-edit session."
        )
    return summary, cwd_matches


def audit_session(transcript_path, repo_root, db_path=None, log_path=None):
    """Runs the full layer-1 check for one named, presumed-complete transcript against
    one audited repo root. Returns a result dict; never raises for a flagged (uncovered)
    result -- only for genuinely invalid input (AuditInputError, config.DbPathError,
    transcript.TranscriptParseError)."""
    if db_path:
        # An explicitly-passed db_path still must not reach Store() unchecked -- config.py's
        # own invariant is that a wrong/missing db path must read as DbPathError, never as
        # "zero registered projects, nothing to flag", and that has to hold for every caller
        # of audit_session, not only the zero-arg default path through resolve_db_path().
        resolved_db_path = Path(db_path)
        if not resolved_db_path.is_file():
            raise config.DbPathError(
                f"no TESSERA db at {resolved_db_path} -- refusing to let Store() create one."
            )
    else:
        resolved_db_path = config.resolve_db_path()
    store = Store(str(resolved_db_path))

    total_in_repo_edits = transcript.in_repo_edit_count_including_subagents(
        transcript_path, repo_root
    )
    summary, cwd_matches = validate_transcript(
        transcript_path, repo_root, total_in_repo_edits
    )

    projects = project_resolve.resolve_projects_for_repo(repo_root, store)
    prefixes = [p["prefix"] for p in projects]

    # Coverage window is the session's OWN span (start to end), not "ever, all time" --
    # an established, continuously-active project (like this one) would otherwise always
    # show real events regardless of whether THIS session logged anything, defeating the
    # whole point of a per-session audit. A real event logged after the transcript's last
    # line (e.g. a delayed backlog comment) is a real, accepted residual this window does
    # not credit -- matches the project's own "live logging, not backfilled" discipline.
    real_events = []
    if prefixes and summary["session_start"] and summary["session_end"]:
        real_events = event_activity.real_events_for_projects(
            store, prefixes,
            window_start_iso=summary["session_start"],
            window_end_iso=summary["session_end"],
        )

    flagged = total_in_repo_edits > 0 and len(real_events) == 0

    result = {
        "kind": "audit",
        "transcript": str(transcript_path),
        "repo_root": str(Path(repo_root).resolve()),
        "registered_projects": prefixes,
        "in_repo_edits": total_in_repo_edits,
        "real_events": len(real_events),
        "flagged": flagged,
        "transcript_cwd_matched": cwd_matches,
    }
    append_log_internal(result, log_path=log_path)
    return result


def self_check_hard_gate(repo_root="/path/to/ticket-system", expected_shas=None, log_path=None):
    """Asserts core.hooksPath is set to .githooks and both shims' live sha256 match
    config.EXPECTED_SHIM_SHAS (or an injected mapping, for testing against a fixture repo
    rather than the real one). Detection-friction, NOT enforcement -- goals_freeze_gate.py
    opens permanently once tessguard's own criteria are frozen, and its matcher doesn't
    cover MultiEdit/NotebookEdit at all, so a shim and its expected-hash constant can still
    be edited together in one unimpeded change. What this buys: neutering the hard gate now
    requires a second, visible, separately-reviewable edit rather than one quiet change to
    an untracked hook."""
    import hashlib
    import subprocess

    expected_shas = expected_shas if expected_shas is not None else config.EXPECTED_SHIM_SHAS
    proc = subprocess.run(
        ["git", "-C", repo_root, "config", "--get", "core.hooksPath"],
        capture_output=True, text=True,
    )
    hooks_path_ok = proc.returncode == 0 and proc.stdout.strip() == ".githooks"

    shim_status = {}
    for name, expected in expected_shas.items():
        shim_file = Path(repo_root) / ".githooks" / name
        if not shim_file.is_file():
            shim_status[name] = "missing"
            continue
        actual = hashlib.sha256(shim_file.read_bytes()).hexdigest()
        shim_status[name] = "ok" if actual == expected else f"MISMATCH (got {actual})"

    result = {
        "kind": "self_check",
        "core_hooks_path_ok": hooks_path_ok,
        "shims": shim_status,
        "healthy": hooks_path_ok and all(v == "ok" for v in shim_status.values()),
    }
    append_log_internal(result, log_path=log_path)
    return result


def run_periodic_audit(transcript_path, repo_root=None, log_path=None):
    """Entry point for an explicit, on-demand invocation. Always exits 0 -- a flagged
    result is data appended to the log, never a failure exit code; this is what makes
    non-blocking true structurally, not just by convention. log_path threads through to
    both self_check_hard_gate() and audit_session() -- neither has its own way to be
    pointed at a fixture log otherwise, which previously meant any caller (including this
    module's own tests) unconditionally wrote to the real AUDIT_LOG_PATH."""
    repo_root = repo_root or "/path/to/ticket-system"
    self_check_hard_gate(repo_root=repo_root, log_path=log_path)
    result = audit_session(transcript_path, repo_root, log_path=log_path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 -m tessera.tessguard.audit <transcript_path> [repo_root]", file=sys.stderr)
        sys.exit(2)
    sys.exit(run_periodic_audit(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
