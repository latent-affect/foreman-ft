"""ATLASSN-18/ATLASSN-19: the orchestration entrypoint that runs every real ATLAS source --
TESSERA, git history, the real verdict ledger, the real audit-plane, and resolve -- into a
persistent warehouse database, rather than the test fixtures every unit test uses. Runnable
standalone or from a scheduler (this machine's existing hourly-cron pattern):

    /Users/m5/.venv/bin/python3 -m atlas.ingest.run_pull
    /Users/m5/.venv/bin/python3 -m atlas.ingest.run_pull --warehouse-db /path/to/atlas.db

ATLASSN-19: a dogfood pass against the first real run (peer session ticket-system-a3,
2026-08-22) found that ATLASSN-18's original version of this file only ran tessera_to_ingest
and gitrepo_to_ingest -- hook_verdict/audit_event/cwd_project stayed at 0 rows, so every
hook_verdict-derived gated view served only the distrust sentinel, correctly but silently
(ARCHITECTURE.md section 11's disclosed "trusted but empty" case, hit for real on a warehouse
that otherwise read clean). Extended to also stream.tail() the real verdict ledger and
audit-plane, and run resolve against the real distinct cwds that produces.

ATLASSN-172: run_slow's own docstring already named "registry refresh" as part of the slow
plane's job, but no call site ever existed -- registry_pull.refresh_registry() was real, tested,
and reachable only by a manual `python3 -m atlas.ingest.registry_pull` invocation
(ARCHITECTURE.md section 38.4 disclosed this exact gap as a deferred "deployment step", not a
silent omission). Wired in here so the real com.atlas-sonnet.ingest launchd cadence
(atlas_ingest_cron.sh -> this module's run()) is what refreshes registry_assertion, matching
every other slow-plane source.

Scope, matching this component's own GOALS.json out_of_scope boundary: NOT in scope here --
actually installing the cron schedule itself (a separate, later concern).
"""

import argparse
import datetime
import functools
import sqlite3
import subprocess
from pathlib import Path

from atlas.ingest import audit, stream, verdicts
from atlas.resolve import run as resolve_run
from atlas.warehouse import dq_runner, migrate
from . import gitrepo_pull, registry_pull, tessera_pull

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAREHOUSE_DB = REPO_ROOT / "atlas" / "warehouse" / "atlas.db"
DEFAULT_TESSERA_DB = Path("/Users/m5/dev/ticket-system/data/tessera.db")
DEFAULT_VERDICTS_SOURCE = Path.home() / ".claude" / "telemetry" / "verdicts.jsonl"
# ATLASSN-172: the same real default registry_pull.py itself uses standalone
# (~/.claude/foreman/registry/registry.db, ARCHITECTURE.md section 34.0) -- referenced from
# registry_pull rather than restated, so the two never drift.
DEFAULT_REGISTRY_DB = registry_pull.DEFAULT_REGISTRY_DB

# ATLASSN-20: ARCHITECTURE.md section 1 and section 16's DDL comment both name TWO real
# audit-plane files as the dual-ledger source (the FORE-40 split) -- global-safety/safety.jsonl
# AND misalignment-marker-search/audit.jsonl, distinguished by source_path (the file ATLAS
# actually read) vs ledger_claim (the writer's self-reported, and possibly wrong, ledger name).
# The first version of this file only unioned the first; the second (10 real, distinct rows)
# never landed. Each gets its own source_name -- stream.tail() resolves its resume-state lookup
# by source_name alone (no stream_id in the WHERE clause), so two permanently-coexisting files
# sharing one source_name would make every pull look like a false "rotation" of the other.
DEFAULT_AUDIT_SOURCES = [
    ("audit-global-safety", Path.home() / ".claude" / "audit-plane" / "global-safety" / "safety.jsonl"),
    ("audit-misalignment-marker-search",
     Path.home() / ".claude" / "audit-plane" / "misalignment-marker-search" / "audit.jsonl"),
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


def detect_is_git_repo(root_state):
    """NULL when root_state itself couldn't determine anything real ('root-missing' or
    'no-source-root' -- there was nothing to check), 0/1 otherwise. Kept as its own boolean
    column, redundant with root_state, because v_ticket_diff_binding (ARCHITECTURE.md's own
    schema comment on dim_project) reads `is_git_repo = 0` directly rather than the text enum."""
    if root_state == "git-repo":
        return 1
    if root_state == "not-a-git-repo":
        return 0
    return None


def detect_foreman_bootstrapped(source_root, root_state):
    """.foreman/ presence, matching this project's own convention (CLAUDE.md: "`.foreman/` marks
    it as opted in") and the 2026-08-20 manual cross-project audit's identical check. NULL when
    there is nothing to check (root missing or no source_root), matching detect_is_git_repo's
    same NULL-vs-0 discipline."""
    if root_state in ("root-missing", "no-source-root"):
        return None
    return 1 if (Path(source_root) / ".foreman").is_dir() else 0


def detect_tessguard_wired(source_root, root_state):
    """`git config core.hooksPath` set, non-empty -- per the 2026-08-20 audit, "the only thing
    that makes tessguard's .githooks/pre-commit actually run" (tessguard/README.md's own install
    line). NULL when root_state is not 'git-repo': core.hooksPath is meaningless outside a real
    git repo, so 0 there would misreport "checked, not wired" for a root that was never
    checkable at all. A subprocess failure (git missing, config unreadable) is also NULL rather
    than 0, for the same reason -- "the check could not run" is not the same fact as "it ran and
    found nothing"."""
    if root_state != "git-repo":
        return None
    try:
        result = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=str(source_root), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return 0
    return 1 if result.stdout.strip() else 0


def sync_dim_project(warehouse_conn, tessera_conn):
    """Upserts dim_project from TESSERA's real project registry. root_state is determined by
    actually checking the filesystem for each source_root, not copied from TESSERA (which has
    no way to know whether a root still exists or has a real .git) -- the same measured
    30-real-git-repos-of-35 result ARCHITECTURE.md section 1 cites came from exactly this check.
    is_git_repo/foreman_bootstrapped/tessguard_wired (ATLASSN-21) are populated the same way, by
    actually checking the filesystem and git config per project, not inferred or copied.
    Returns the number of projects synced."""
    rows = tessera_conn.execute(
        "SELECT prefix, codename, source_root, id FROM projects WHERE source_root IS NOT NULL"
    ).fetchall()
    now = _now()
    for row in rows:
        root_state = detect_root_state(row["source_root"])
        is_git_repo = detect_is_git_repo(root_state)
        foreman_bootstrapped = detect_foreman_bootstrapped(row["source_root"], root_state)
        tessguard_wired = detect_tessguard_wired(row["source_root"], root_state)
        warehouse_conn.execute(
            "INSERT INTO dim_project (project_prefix, project_codename, tessera_project_id, "
            "source_root, root_state, is_git_repo, foreman_bootstrapped, tessguard_wired, "
            "refreshed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_prefix) DO UPDATE SET "
            "project_codename=excluded.project_codename, "
            "tessera_project_id=excluded.tessera_project_id, "
            "source_root=excluded.source_root, root_state=excluded.root_state, "
            "is_git_repo=excluded.is_git_repo, "
            "foreman_bootstrapped=excluded.foreman_bootstrapped, "
            "tessguard_wired=excluded.tessguard_wired, "
            "refreshed_at=excluded.refreshed_at",
            (row["prefix"], row["codename"], row["id"], row["source_root"], root_state,
             is_git_repo, foreman_bootstrapped, tessguard_wired, now),
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
    dim_project registry sync_dim_project just refreshed. Returns {cwd: resolution}.

    This is the SLOW plane's full re-resolve: it re-runs every cwd because the registry it
    resolves against may have changed, which is a thing only the slow plane refreshes."""
    cwds = [
        r[0] for r in warehouse_conn.execute(
            "SELECT DISTINCT cwd FROM hook_verdict WHERE cwd IS NOT NULL"
        )
    ]
    return resolve_run.resolve_all(warehouse_conn, cwds)


def resolve_new_cwds(warehouse_conn):
    """The FAST plane's incremental counterpart (FORE-276): resolve only cwds that have no
    cwd_project row yet. The registry cannot have changed since the last tick -- only the slow
    plane refreshes dim_project -- so re-resolving a cwd already resolved against the same
    registry cannot produce a different answer.

    THE QUERY IS NOT IN, AND THAT IS THE MEASURED ANSWER RATHER THAN THE OBVIOUS ONE. I proposed
    rewriting it as a LEFT JOIN, and as its own watermark, on the reasoning that a correlated
    membership test over 1,559 cwds was what made it cost 65 ms to return zero rows. Both
    rewrites were measured against the live warehouse 2026-09-04, best of five each:

        NOT IN                              44.9 ms -> 0 rows
        LEFT JOIN                           55.5 ms -> 0 rows
        scoped to one ingest_run_id        151.0 ms -> 0 rows

    Both proposals are SLOWER. The cost is not the membership test, it is `DISTINCT cwd` over
    467,000 rows, which ix_hv_cwd_epoch already serves about as well as anything can; the
    run-scoped form is worst because hook_verdict has no index on ingest_run_id at all, so it
    scans the table and then filters, and it costs the same on an empty run as on a full one.

    So the original query stands, at roughly 9% of the 500 ms tick budget rather than the 13% I
    first reported. The real improvement is an index on ingest_run_id, which is a schema change
    and is deliberately not bundled here. Recorded in full because "the obvious optimisation is a
    regression" is the kind of thing that gets re-proposed every time someone reads this function
    and reasons about it instead of timing it."""
    cwds = [
        r[0] for r in warehouse_conn.execute(
            "SELECT DISTINCT cwd FROM hook_verdict WHERE cwd IS NOT NULL "
            "AND cwd NOT IN (SELECT cwd FROM cwd_project)"
        )
    ]
    if not cwds:
        return {}
    return resolve_run.resolve_all(warehouse_conn, cwds)


def run_fast(warehouse_conn, run_id,
             verdicts_source=DEFAULT_VERDICTS_SOURCE, audit_sources=DEFAULT_AUDIT_SOURCES):
    """The FAST plane (FORE-276): the append-only tails, plus resolve for cwds that have none.

    run_id is INJECTED rather than created here, deliberately. Which plane a run belongs to, and
    therefore how it is stamped, is the caller's decision -- the run-over-run DQ checks select
    their comparison points from ingest_run, so a fast tick that stamped itself an ordinary run
    would silently turn every one of them into a comparison across 15 seconds instead of across a
    real batch interval. That is not a noisy check, it is a check that structurally cannot fire.
    Keeping the stamp out of here means the decision lives in one place instead of two.

    Everything in this plane is cheap by construction: a byte-offset watermark read of an
    append-only file, and a resolve over cwds nobody has resolved yet. Nothing here walks a git
    repository, opens TESSERA, or re-resolves history."""
    verdict_rows_inserted, verdict_detail = _pull_stream_source(
        warehouse_conn, "verdicts", verdicts_source, verdicts.map_verdict_row,
        functools.partial(verdicts.insert_hook_verdict_row, ingest_run_id=run_id),
    )
    audit_rows_inserted_by_source = {}
    audit_detail_by_source = {}
    for source_name, source_path in audit_sources:
        n, detail = _pull_stream_source(
            warehouse_conn, source_name, source_path, audit.make_row_mapper(str(source_path)),
            functools.partial(audit.insert_audit_event_row, ingest_run_id=run_id),
        )
        audit_rows_inserted_by_source[source_name] = n
        audit_detail_by_source[source_name] = detail
    audit_rows_inserted = sum(n for n in audit_rows_inserted_by_source.values() if n is not None) \
        if any(n is not None for n in audit_rows_inserted_by_source.values()) else None
    new_cwds = resolve_new_cwds(warehouse_conn)
    return {
        "verdict_rows_inserted": verdict_rows_inserted,
        "verdict_detail": verdict_detail,
        "audit_rows_inserted": audit_rows_inserted,
        "audit_rows_inserted_by_source": audit_rows_inserted_by_source,
        "audit_detail_by_source": audit_detail_by_source,
        "new_cwds_resolved": len(new_cwds),
    }


def run_slow(warehouse_conn, run_id, tessera_db_path, registry_db_path=DEFAULT_REGISTRY_DB):
    """The SLOW plane (FORE-276): registry refresh, git history, full re-resolve, and the DQ
    checks. Everything whose cost is proportional to the world rather than to the tail.

    The full re-resolve belongs here and not in the fast plane for a reason that is easy to lose:
    it re-resolves cwds that were already resolved, because the REGISTRY they resolve against is
    refreshed a few lines above. The fast plane cannot change that registry, so its incremental
    resolve is not a cheaper version of this one -- it answers a different question.

    ATLASSN-172: "registry refresh" above was true of this docstring before it was true of this
    function's body -- registry_pull.refresh_registry() existed, was tested, and had zero real
    callers anywhere in atlas/ or scripts/ (SHIP-READINESS-REVIEW-ATLASSN-110-20260916-MARCUS.md).
    Called here, in the same warehouse_conn transaction every other slow-plane source uses
    (sync_dim_project/tessera_pull/gitrepo_pull all take warehouse_conn and commit their own
    work), so a batch run through run() -- the real entrypoint atlas_ingest_cron.sh's launchd job
    invokes every 6 hours -- now actually refreshes registry_assertion instead of leaving it at
    ARCHITECTURE.md section 34.7's bootstrap-day-one 0 rows forever. registry_db_path defaults to
    registry_pull's own real default rather than restating the path, so the two can never drift.

    run_id is injected for the same reason it is in run_fast."""
    tessera_conn = _open_tessera_readonly(tessera_db_path)
    try:
        projects_synced = sync_dim_project(warehouse_conn, tessera_conn)
        tessera_events_ingested = tessera_pull.pull_tessera_events(
            warehouse_conn, tessera_conn, run_id
        )
    finally:
        tessera_conn.close()

    registry_assertions_ingested, registry_error = registry_pull.refresh_registry(
        warehouse_conn, registry_db_path
    )
    warehouse_conn.commit()

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

    resolve_results = resolve_real_cwds(warehouse_conn)
    resolution_counts = {}
    for res in resolve_results.values():
        resolution_counts[res] = resolution_counts.get(res, 0) + 1

    dq_results = dq_runner.run_all(warehouse_conn, run_id)
    # Split by REAL severity, not just pass/fail -- dq_check.severity distinguishes 'contract'
    # (withholds the source from every gated view) from 'advisory' (records and alerts without
    # gating). A failed name list with no severity split would have mislabeled real advisory
    # findings (e.g. handler_denominator_nonzero, the TESS-88 class -- ARCHITECTURE.md section 8
    # documents this as an EXPECTED real finding on the real corpus, not a defect) as if they
    # were withholding trust, which they structurally do not.
    check_severity = dict(warehouse_conn.execute("SELECT check_name, severity FROM dq_check"))
    return {
        "projects_synced": projects_synced,
        "tessera_events_ingested": tessera_events_ingested,
        "registry_assertions_ingested": registry_assertions_ingested,
        "registry_error": registry_error,
        "git_repos_pulled": git_repos_pulled,
        "git_commits_seen": git_commits_seen,
        "cwds_resolved": len(resolve_results),
        "resolution_counts": resolution_counts,
        "dq_checks_run": len(dq_results),
        "dq_contract_failures": [
            name for name, r in dq_results.items()
            if not r.passed and check_severity.get(name) == "contract"
        ],
        "dq_advisory_failures": [
            name for name, r in dq_results.items()
            if not r.passed and check_severity.get(name) == "advisory"
        ],
    }


def run(warehouse_db_path, tessera_db_path,
        verdicts_source=DEFAULT_VERDICTS_SOURCE, audit_sources=DEFAULT_AUDIT_SOURCES,
        registry_db_path=DEFAULT_REGISTRY_DB):
    """The batch entrypoint, unchanged in behaviour: one run_id, both planes, one summary.

    FORE-276 split the body into run_fast and run_slow but deliberately kept this, because the
    ticket's own third acceptance criterion is that batch results stay row-identical to the
    pre-split run -- which is a claim about THIS function and is testable only if it still
    exists. The planes are called fast-then-slow rather than in the pre-split statement order
    (registry, git, tails, resolve, DQ); tests/test_run_pull_plane_split.py executes both orders
    against identical fixture warehouses and diffs every table, rather than arguing from the
    absence of a dependency.

    registry_db_path (ATLASSN-172) is threaded through to run_slow the same way tessera_db_path
    already is -- a new slow-plane input, not a new decision about ordering or transaction shape.

    Raises on a real TESSERA-connection failure (a missing/unreadable tessera.db is not a valid
    empty state the way a missing verdicts.jsonl is -- TESSERA is a required dependency this
    script cannot proceed without at all, while the streaming sources degrade to a named,
    reported skip per source, F3's failure signature)."""
    warehouse_conn = migrate.connect(str(warehouse_db_path))
    # PART 1 of the run-75 trap fix (ATLASSN-82's mechanism, at its exact call site).
    #
    # This used to pass status="ok" at INSERT, before either plane had done any work, which is
    # why twenty consecutive crashed runs (75-94) all reported a green status column while the
    # verdict watermark sat frozen for four days. The outage was not subtle; it was INVISIBLE,
    # and the status field is what made it so.
    #
    # new_ingest_run's own default is already "running" -- the schema anticipated this
    # (ingest_run's CHECK admits 'running','ok','failed'), and the only thing that ever made the
    # column lie was this call site overriding a correct default. So this is not a new state
    # machine, it is using the one already declared.
    #
    # A run that dies now stays 'running' or is marked 'failed', and v_atlas_status -- which
    # reads MAX(run_id) regardless of status and requires status='ok' AND checks_evaluated>0 --
    # refuses with an accurate reason instead of the misleading "checks-not-evaluated" it gives
    # today for a run that claims to have succeeded.
    run_id = migrate.new_ingest_run(warehouse_conn)
    try:
        fast = run_fast(warehouse_conn, run_id, verdicts_source, audit_sources)
        # PROMOTED TO 'ok' HERE, BETWEEN THE PLANES, AND THE REASON IS A REAL COUPLING found by
        # running the suite rather than by reading: five dq_runner checks select their comparison
        # points WHERE status='ok' (ARCHITECTURE.md section 2750). Leaving the run 'running'
        # through run_slow hides the in-flight run from its own checks, and
        # test_run_pull's real-corpus case caught it -- dq_advisory_failures came back empty.
        #
        # Section 2832 warned about exactly this and said the sweep had not been done: "Any
        # consumer elsewhere that reasons about ingest_run has not been swept, and a missed one
        # degrades exactly as silently as the five did." This is that missed consumer, surfaced.
        #
        # Promoting between the planes keeps both properties. The fast plane is where the
        # poison-line crash happens, so a run that dies there never reaches 'ok' and
        # v_atlas_status refuses loudly -- which is the whole point of part 1. A crash in the
        # slow plane is still rewritten to 'failed' below. The only window where a run reads
        # 'ok' before it is finished is run_slow's own execution, which is exactly the window
        # today's code has for the ENTIRE run, so this is strictly narrower than the status quo
        # rather than a new exposure.
        # finished=False, and this is the ONLY call site that passes it. The promotion buys
        # dq_runner its comparison points; it must not also claim the run is over, because
        # set_ingest_run_status's whole reason for existing is the invariant
        # "finished_at IS NOT NULL <=> terminal". Stamping a finish time here would plant this
        # change's own only counterexample to the signal it introduces, and would mislead
        # exactly the reader who had learned to trust it.
        migrate.set_ingest_run_status(warehouse_conn, run_id, "ok", finished=False)
        slow = run_slow(warehouse_conn, run_id, tessera_db_path, registry_db_path)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt or SystemExit mid-ingest leaves the
        # run just as unfinished, and a run that was killed must not keep claiming 'running'
        # forever either. Re-raised immediately -- this records what happened, it never swallows.
        migrate.set_ingest_run_status(warehouse_conn, run_id, "failed")
        warehouse_conn.close()
        raise
    migrate.set_ingest_run_status(warehouse_conn, run_id, "ok")
    warehouse_conn.close()
    summary = {"run_id": run_id}
    summary.update(slow)
    summary.update(fast)
    return summary


def run_fast_tick(warehouse_db_path,
                  verdicts_source=DEFAULT_VERDICTS_SOURCE,
                  audit_sources=DEFAULT_AUDIT_SOURCES):
    """The FAST plane's own entrypoint (FORE-276), so the plane can be invoked by a scheduler
    rather than only called as a function.

    Without this, run_fast was reachable from tests and from run() and from nowhere else -- the
    plane was built, equivalence-proven, and had no command line, so the split was correct and
    inert. That is the actual gap this closes; the launchd job is downstream of it.

    Stamps plane='fast'. That stamp is the entire point of the entrypoint existing separately:
    dq_runner.SLOW_PLANE_RUNS excludes exactly this value, so a fast tick that reached ingest_run
    unstamped would be indistinguishable from a batch run and would turn the five run-over-run
    checks into comparisons across seconds. They would keep passing. Vacuous rather than noisy is
    the failure mode ATLASSN-72 was written against.

    Takes no tessera_db_path, deliberately. Nothing in the fast plane opens TESSERA, and not
    accepting the argument is what stops a future edit from quietly adding something that does."""
    warehouse_conn = migrate.connect(str(warehouse_db_path))
    # SECOND CALL SITE of ATLASSN-82, found by Iris probing v2. run() was fixed and this was
    # not: status="ok" stamped at creation, before run_fast has done anything. The try/finally
    # below closes the connection on a crash but leaves the status green, so a crashed fast tick
    # reports exactly what runs 75-94 reported.
    #
    # NOT LIVE TODAY, checked rather than assumed: this entrypoint's launchd job is not
    # registered and does not appear in launchctl list. It becomes live the moment that job is
    # wired -- which is precisely the moment nobody would think to re-audit the status field.
    # Fixed now for that reason, not because it is currently burning.
    #
    # plane="fast" is preserved: dq_runner.SLOW_PLANE_RUNS excludes exactly that value, and an
    # unstamped fast tick would make the five run-over-run checks compare across seconds and
    # keep passing. Vacuous rather than noisy is the failure ATLASSN-72 was written against.
    run_id = migrate.new_ingest_run(warehouse_conn, plane="fast")
    try:
        fast = run_fast(warehouse_conn, run_id, verdicts_source, audit_sources)
    except BaseException:
        migrate.set_ingest_run_status(warehouse_conn, run_id, "failed")
        raise
    else:
        migrate.set_ingest_run_status(warehouse_conn, run_id, "ok")
    finally:
        warehouse_conn.close()
    summary = {"run_id": run_id, "plane": "fast"}
    summary.update(fast)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-db", default=str(DEFAULT_WAREHOUSE_DB))
    parser.add_argument("--tessera-db", default=str(DEFAULT_TESSERA_DB))
    parser.add_argument("--verdicts-source", default=str(DEFAULT_VERDICTS_SOURCE))
    parser.add_argument("--registry-db", default=str(DEFAULT_REGISTRY_DB))
    parser.add_argument(
        "--plane", choices=("batch", "fast"), default="batch",
        help="batch (default) runs both planes under one run_id and stamps plane NULL, which is "
             "the pre-split behaviour byte for byte. fast runs only the tails-plus-incremental- "
             "resolve plane and stamps plane='fast', which excludes the tick from every "
             "run-over-run DQ comparison point.",
    )
    args = parser.parse_args()

    if args.plane == "fast":
        summary = run_fast_tick(Path(args.warehouse_db), Path(args.verdicts_source))
        print(f"[run_pull] warehouse={args.warehouse_db} run_id={summary['run_id']} plane=fast")
        print(f"[run_pull] verdict ledger: {summary['verdict_detail']}")
        for source_name, detail in summary["audit_detail_by_source"].items():
            print(f"[run_pull] audit plane ({source_name}): {detail}")
        print(f"[run_pull] resolve: {summary['new_cwds_resolved']} newly-resolved cwds")
        return 0

    summary = run(Path(args.warehouse_db), Path(args.tessera_db), Path(args.verdicts_source),
                  registry_db_path=Path(args.registry_db))
    print(f"[run_pull] warehouse={args.warehouse_db} run_id={summary['run_id']}")
    print(f"[run_pull] dim_project synced: {summary['projects_synced']} real TESSERA projects")
    print(f"[run_pull] tessera_event rows ingested this pass: {summary['tessera_events_ingested']}")
    registry_status = (f"error: {summary['registry_error']}" if summary["registry_error"]
                        else f"{summary['registry_assertions_ingested']} ingested")
    print(f"[run_pull] registry: {registry_status}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
