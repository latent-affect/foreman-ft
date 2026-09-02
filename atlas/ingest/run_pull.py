"""The orchestration entrypoint that runs every real ATLAS source --
TESSERA, git history, the real verdict ledger, the real audit-plane, and resolve -- into a
persistent warehouse database, rather than the test fixtures every unit test uses. Runnable
standalone or from a scheduler (this machine's existing hourly-cron pattern):

    /path/to/venv/bin/python3 -m atlas.ingest.run_pull
    /path/to/venv/bin/python3 -m atlas.ingest.run_pull --warehouse-db /path/to/atlas.db

A dogfood pass against the first real run (2026-08-22) found that this file's original
version only ran tessera_to_ingest
and gitrepo_to_ingest -- hook_verdict/audit_event/cwd_project stayed at 0 rows, so every
hook_verdict-derived gated view served only the distrust sentinel, correctly but silently
(ARCHITECTURE.md section 11's disclosed "trusted but empty" case, hit for real on a warehouse
that otherwise read clean). Extended to also stream.tail() the real verdict ledger and
audit-plane, and run resolve against the real distinct cwds that produces.

Scope, matching this component's own GOALS.json out_of_scope boundary: NOT in scope here --
actually installing the cron schedule itself (a separate, later concern).
"""

import argparse
import datetime
import functools
import sqlite3
from pathlib import Path

from atlas.ingest import audit, audit_scrub, stream, verdicts
from atlas.resolve import run as resolve_run
from atlas.warehouse import dq_runner, migrate
from . import gitrepo_pull, tessera_pull

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAREHOUSE_DB = REPO_ROOT / "atlas" / "warehouse" / "atlas.db"
DEFAULT_TESSERA_DB = Path("/path/to/ticket-system/data/tessera.db")
DEFAULT_VERDICTS_SOURCE = Path.home() / ".claude" / "telemetry" / "verdicts.jsonl"

# Audit-plane ledgers are a general ingestion source, not specific to Bollard --
# any hook that writes its own JSONL ledger under ~/.claude/audit-plane/<name>/ can be added
# here. Each gets its own source_name -- stream.tail() resolves its resume-state lookup by
# source_name alone (no stream_id in the WHERE clause), so two permanently-coexisting files
# sharing one source_name would make every pull look like a false "rotation" of the other.
# (This originally also pulled from a second, project-specific ledger belonging to an
# unrelated internal research project. Dropped for this release, along with that project's
# own audit_lib module -- see bollard/hook_common.py's docstring.)
DEFAULT_AUDIT_SOURCES = [
    ("audit-global-safety", Path.home() / ".claude" / "audit-plane" / "global-safety" / "safety.jsonl"),
]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _open_tessera_readonly(tessera_db_path):
    # mode=ro at the SQLite level, not just "don't call .execute() with writes" by convention --
    # this is TESSERA's own live production database, shared with every other project on this
    # machine, and a write attempt against it must fail loudly rather than merely be avoided by
    # this script's own discipline.
    conn = sqlite3.connect(f"file:{tessera_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def detect_root_state(source_root):
    """Matches ARCHITECTURE.md's dim_project.root_state comment and FATAL-class fix: .git must
    be accepted as either a directory or a file (git worktrees use a file, not a directory) --
    os.path.isdir(root/'.git') alone misreads every worktree as not-a-git-repo."""
    if not source_root:
        return "no-source-root"
    root = Path(source_root)
    if not root.is_dir():
        return "root-missing"
    git_path = root / ".git"
    if git_path.is_dir() or git_path.is_file():
        return "git-repo"
    return "not-a-git-repo"


def sync_dim_project(warehouse_conn, tessera_conn):
    """Upserts dim_project from TESSERA's real project registry. root_state is determined by
    actually checking the filesystem for each source_root, not copied from TESSERA (which has
    no way to know whether a root still exists or has a real .git) -- the same measured
    30-real-git-repos-of-35 result ARCHITECTURE.md section 1 cites came from exactly this check.
    Returns the number of projects synced."""
    rows = tessera_conn.execute(
        "SELECT prefix, codename, source_root, id FROM projects WHERE source_root IS NOT NULL"
    ).fetchall()
    now = _now()
    for row in rows:
        root_state = detect_root_state(row["source_root"])
        warehouse_conn.execute(
            "INSERT INTO dim_project (project_prefix, project_codename, tessera_project_id, "
            "source_root, root_state, refreshed_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_prefix) DO UPDATE SET "
            "project_codename=excluded.project_codename, "
            "tessera_project_id=excluded.tessera_project_id, "
            "source_root=excluded.source_root, root_state=excluded.root_state, "
            "refreshed_at=excluded.refreshed_at",
            (row["prefix"], row["codename"], row["id"], row["source_root"], root_state, now),
        )
    warehouse_conn.commit()
    return len(rows)


def _pull_stream_source(warehouse_conn, source_name, source_path, row_mapper, insert_row):
    """Wraps stream.tail() with the F3 failure discipline every source in this component
    already follows: a missing real source file is a named, reported skip, not a crash that
    kills the whole orchestration run over one source. Returns (rows_inserted_or_None, detail)."""
    path = Path(source_path)
    if not path.is_file():
        return None, f"source file not found: {path}"
    result = stream.tail(warehouse_conn, source_name, str(path), row_mapper, insert_row)
    warehouse_conn.commit()
    if result.error is not None:
        return None, f"tail() error: {result.error}"
    return result.rows_inserted, (
        f"rows_inserted={result.rows_inserted}, rows_skipped={result.rows_skipped}, "
        f"rows_rejected_by_mapper={result.rows_rejected_by_mapper}, "
        f"rotation_detected={result.rotation_detected}"
    )


def resolve_real_cwds(warehouse_conn):
    """Resolves every real distinct cwd already ingested into hook_verdict against the real
    dim_project registry sync_dim_project just refreshed. Returns {cwd: resolution}."""
    cwds = [
        r[0] for r in warehouse_conn.execute(
            "SELECT DISTINCT cwd FROM hook_verdict WHERE cwd IS NOT NULL"
        )
    ]
    return resolve_run.resolve_all(warehouse_conn, cwds)


def run(warehouse_db_path, tessera_db_path,
        verdicts_source=DEFAULT_VERDICTS_SOURCE, audit_sources=DEFAULT_AUDIT_SOURCES):
    """Returns a dict summary. Raises on a real TESSERA-connection failure (a missing/unreadable
    tessera.db is not a valid empty state the way a missing verdicts.jsonl is -- TESSERA is a
    required dependency this script cannot proceed without at all, while the streaming sources
    degrade to a named, reported skip per source, F3's failure signature)."""
    warehouse_conn = migrate.connect(str(warehouse_db_path))
    run_id = migrate.new_ingest_run(warehouse_conn, status="ok")

    tessera_conn = _open_tessera_readonly(tessera_db_path)
    try:
        projects_synced = sync_dim_project(warehouse_conn, tessera_conn)
        tessera_events_ingested = tessera_pull.pull_tessera_events(
            warehouse_conn, tessera_conn, run_id
        )
    finally:
        tessera_conn.close()

    git_repos_pulled = 0
    git_commits_seen = {}
    registered_prefixes = {
        r[0] for r in warehouse_conn.execute("SELECT project_prefix FROM dim_project")
    }
    git_repo_rows = warehouse_conn.execute(
        "SELECT project_prefix, source_root FROM dim_project WHERE root_state = 'git-repo'"
    ).fetchall()
    for project_prefix, source_root in git_repo_rows:
        n = gitrepo_pull.pull_git_commits(
            warehouse_conn, source_root, project_prefix, registered_prefixes, run_id
        )
        git_commits_seen[project_prefix] = n
        git_repos_pulled += 1

    verdict_rows_inserted, verdict_detail = _pull_stream_source(
        warehouse_conn, "verdicts", verdicts_source, verdicts.map_verdict_row,
        functools.partial(verdicts.insert_hook_verdict_row, ingest_run_id=run_id),
    )
    audit_rows_inserted_by_source = {}
    audit_detail_by_source = {}
    audit_scrub_drops_by_source = {}
    for source_name, source_path in audit_sources:
        # PRD.md R8 / atlas/GOALS.json (DEVH-13): audit-plane rows go through the write-time
        # credential scrub, never directly through _pull_stream_source's plain
        # audit.insert_audit_event_row path other sources (verdicts) still use.
        result = audit_scrub.ingest_audit_source(warehouse_conn, source_name, source_path, run_id)
        audit_rows_inserted_by_source[source_name] = result.rows_inserted
        audit_scrub_drops_by_source[source_name] = result.dropped_count
        if result.error is not None:
            audit_detail_by_source[source_name] = f"tail() error: {result.error}"
        else:
            audit_detail_by_source[source_name] = (
                f"rows_inserted={result.rows_inserted}, rows_skipped={result.rows_skipped}, "
                f"rows_rejected_by_mapper={result.rows_rejected_by_mapper}, "
                f"rotation_detected={result.rotation_detected}, "
                f"fail_closed_drops={result.dropped_count}"
            )
    audit_rows_inserted = sum(n for n in audit_rows_inserted_by_source.values() if n is not None) \
        if any(n is not None for n in audit_rows_inserted_by_source.values()) else None
    # A dropped line is a fail-closed success (no credential persisted), not a clean run -- the
    # caller must not be able to report success while silently having lost a record
    # (GOALS.json's design_decision / C4 / F5).
    audit_scrub_drops_total = sum(audit_scrub_drops_by_source.values())

    resolve_results = resolve_real_cwds(warehouse_conn)
    resolution_counts = {}
    for res in resolve_results.values():
        resolution_counts[res] = resolution_counts.get(res, 0) + 1

    dq_results = dq_runner.run_all(warehouse_conn, run_id)
    # Split by REAL severity, not just pass/fail -- dq_check.severity distinguishes 'contract'
    # (withholds the source from every gated view) from 'advisory' (records and alerts without
    # gating). A failed name list with no severity split would have mislabeled real advisory
    # findings (e.g. handler_denominator_nonzero, a structural-zero-denominator class -- ARCHITECTURE.md section 8
    # documents this as an EXPECTED real finding on the real corpus, not a defect) as if they
    # were withholding trust, which they structurally do not.
    check_severity = dict(warehouse_conn.execute("SELECT check_name, severity FROM dq_check"))
    dq_contract_failures = [
        name for name, r in dq_results.items()
        if not r.passed and check_severity.get(name) == "contract"
    ]
    dq_advisory_failures = [
        name for name, r in dq_results.items()
        if not r.passed and check_severity.get(name) == "advisory"
    ]

    warehouse_conn.close()
    return {
        "run_id": run_id,
        "projects_synced": projects_synced,
        "tessera_events_ingested": tessera_events_ingested,
        "git_repos_pulled": git_repos_pulled,
        "git_commits_seen": git_commits_seen,
        "verdict_rows_inserted": verdict_rows_inserted,
        "verdict_detail": verdict_detail,
        "audit_rows_inserted": audit_rows_inserted,
        "audit_rows_inserted_by_source": audit_rows_inserted_by_source,
        "audit_detail_by_source": audit_detail_by_source,
        "audit_scrub_drops_total": audit_scrub_drops_total,
        "audit_scrub_drops_by_source": audit_scrub_drops_by_source,
        "cwds_resolved": len(resolve_results),
        "resolution_counts": resolution_counts,
        "dq_checks_run": len(dq_results),
        "dq_contract_failures": dq_contract_failures,
        "dq_advisory_failures": dq_advisory_failures,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-db", default=str(DEFAULT_WAREHOUSE_DB))
    parser.add_argument("--tessera-db", default=str(DEFAULT_TESSERA_DB))
    parser.add_argument("--verdicts-source", default=str(DEFAULT_VERDICTS_SOURCE))
    args = parser.parse_args()

    summary = run(Path(args.warehouse_db), Path(args.tessera_db), Path(args.verdicts_source))
    print(f"[run_pull] warehouse={args.warehouse_db} run_id={summary['run_id']}")
    print(f"[run_pull] dim_project synced: {summary['projects_synced']} real TESSERA projects")
    print(f"[run_pull] tessera_event rows ingested this pass: {summary['tessera_events_ingested']}")
    print(f"[run_pull] git repos pulled: {summary['git_repos_pulled']}")
    for prefix, n in sorted(summary["git_commits_seen"].items()):
        print(f"[run_pull]   {prefix}: {n} commits seen")
    print(f"[run_pull] verdict ledger: {summary['verdict_detail']}")
    for source_name, detail in summary["audit_detail_by_source"].items():
        print(f"[run_pull] audit plane ({source_name}): {detail}")
    print(f"[run_pull] resolve: {summary['cwds_resolved']} real distinct cwds -> {summary['resolution_counts']}")
    print(f"[run_pull] dq_runner: {summary['dq_checks_run']} checks run, "
          f"{len(summary['dq_contract_failures'])} contract failures: {summary['dq_contract_failures']}, "
          f"{len(summary['dq_advisory_failures'])} advisory failures: {summary['dq_advisory_failures']}")
    if summary["audit_scrub_drops_total"]:
        print(f"[run_pull] audit scrub: {summary['audit_scrub_drops_total']} line(s) fail-closed "
              f"dropped: {summary['audit_scrub_drops_by_source']}")
    # A fail-closed drop means no credential was persisted -- exactly the guarantee working as
    # designed -- but it must never look like a clean run to whatever invoked this process
    # (PRD.md R8 / GOALS.json's design_decision / C4 / F5: silent record loss is the failure
    # mode per-line atomicity trades for, and this is the one place that trade gets reported).
    # Deliberately scoped to audit_scrub drops only -- dq_contract_failures is pre-existing
    # behavior outside R8's freeze and this change does not touch its semantics.
    if summary["audit_scrub_drops_total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
