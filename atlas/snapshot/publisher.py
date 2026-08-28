"""Atomic generation-directory publish, ARCHITECTURE.md section 7: shards first, index second,
symlink flip last. A reader resolves `current` once and reads every file beneath the resolved
path, so a torn pair is structurally impossible rather than merely detected.

Retention: a generation is removed only when it is BOTH older than max_age_seconds AND not
among the newest 3 (section 7's age-AND-count rule -- count-only can delete a generation a slow
reader is inside; age-only leaves the directory unbounded).
"""

import json
import os
from pathlib import Path

from . import builder
from .clock import real_clock

CURRENT_LINK_NAME = "current"


class PublishRefused(RuntimeError):
    pass


def _warehouse_status(conn):
    """FATAL fix (adversarial-code-review Check 6, independently re-confirmed): this function
    used to gate publication on v_atlas_status.contract_failures, which is warehouse-WIDE --
    exactly the bug ARCHITECTURE.md section 5 documents as already fixed by introducing
    v_snapshot_publishable, scoped to snapshot_source. That fix existed in the schema; this
    function never queried it, so a contract failure on a table the snapshot doesn't even read
    (e.g. git_ticket_prefix_registered, source_table=git_commit) would still block the
    real-time artifact -- past max_age_seconds, every Foreman hook on this machine fails open.

    Two separate questions, checked against two separate views on purpose:
    1. Did a run even complete and record checks at all? (v_atlas_status -- run-level health,
       not source-scoped; a never-run or vacuous-pass warehouse must not publish regardless of
       what snapshot_source says, since v_snapshot_publishable itself would be built on nothing.)
    2. Is publication specifically blocked by a source the snapshot actually reads?
       (v_snapshot_publishable -- scoped to snapshot_source, the real gate.)
    """
    status_row = conn.execute(
        "SELECT status, checks_evaluated FROM v_atlas_status"
    ).fetchone()
    if status_row is None or status_row[0] == "NO-INGEST-RUN-YET":
        return False, "no ingest_run rows yet"
    run_status, checks_evaluated = status_row
    if run_status != "ok":
        return False, f"most recent run status={run_status!r}, not 'ok'"
    if not checks_evaluated:
        return False, "run is 'ok' but recorded 0 dq_check_run rows (vacuous pass)"

    publishable_row = conn.execute(
        "SELECT publishable, blocking_sources FROM v_snapshot_publishable"
    ).fetchone()
    publishable, blocking_sources = publishable_row
    if not publishable:
        return False, f"blocked by snapshot_source(s): {blocking_sources}"
    return True, "clean"


def _gen_dir_name(dt):
    return "gen-" + dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def publish(snapshot_root, conn, dim_projects, ticket_rollup, verdict_rollups, cursors,
            unresolved_scopes, rate_uninterpretable_handlers, severity_rubric,
            refresh_cadence_seconds, warehouse_run_id, verdict_window_days, clock=real_clock,
            retain_newest=3, unresolved_verdict_rollups=None):
    """dim_projects: list of {prefix, codename, source_root, root_state}.
    ticket_rollup: {prefix: {open_total, open_by_severity, open_null_severity, open_null_tier}}.
    verdict_rollups: {prefix: verdict_rollup_dict} for the per-project shard content.
    unresolved_verdict_rollups: {'<ambiguous>'|'<unregistered>'|'<no-cwd>': verdict_rollup_dict},
    defaults to {} per bucket if not supplied -- the three bucket shards (ambiguous-UNRESOLVED.json
    etc.) are ALWAYS published regardless, matching section 3's 'the snapshot publishes
    <ambiguous>, <unregistered> and <no-cwd> as real shards', found missing by Check 6 during
    integration testing and fixed here.
    Raises PublishRefused, leaving the filesystem untouched, if the warehouse is not genuinely
    clean or the ticket rollup identity check fails -- checked BEFORE any file is written."""
    unresolved_verdict_rollups = unresolved_verdict_rollups or {}
    ok, detail = _warehouse_status(conn)
    if not ok:
        raise PublishRefused(f"warehouse not genuinely clean: {detail}")
    try:
        builder.check_ticket_rollup_identity(ticket_rollup)
    except builder.TicketRollupIdentityError as exc:
        raise PublishRefused(f"ticket rollup identity check failed: {exc}") from exc

    root = Path(snapshot_root)
    root.mkdir(parents=True, exist_ok=True)

    now = clock()
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    gen_dir = root / _gen_dir_name(now)
    gen_dir.mkdir(parents=True, exist_ok=False)
    projects_dir = gen_dir / "projects"
    projects_dir.mkdir()

    index_data, shared_with = builder.build_index(
        dim_projects, ticket_rollup, cursors, unresolved_scopes, rate_uninterpretable_handlers,
        severity_rubric, refresh_cadence_seconds, warehouse_run_id, verdict_window_days,
        generated_at,
    )

    by_prefix = {p["prefix"]: p for p in dim_projects}
    for prefix, dim_project in by_prefix.items():
        shard = builder.build_shard(
            prefix, dim_project, ticket_rollup.get(prefix), verdict_rollups.get(prefix, {}),
            shared_with, generated_at,
        )
        (projects_dir / f"{prefix}.json").write_text(json.dumps(shard, indent=2))

    for scope in builder.UNRESOLVED_SCOPES:
        bucket_shard = builder.build_unresolved_shard(
            scope, unresolved_verdict_rollups.get(scope, {}), generated_at,
        )
        bucket_filename = scope.strip("<>") + "-UNRESOLVED.json"
        (projects_dir / bucket_filename).write_text(json.dumps(bucket_shard, indent=2))

    # Index written last among this generation's own files (shards, then index) -- matches
    # section 7's "shards first, index second, pointer flip last."
    (gen_dir / "index.json").write_text(json.dumps(index_data, indent=2))

    _flip_current(root, gen_dir)
    _apply_retention(root, now, refresh_cadence_seconds * 3, retain_newest)

    return gen_dir


def _flip_current(root, gen_dir):
    # Atomic: symlink a temp name, then os.replace it onto the real link name. Two-step
    # unlink-then-symlink would have a window where `current` does not exist at all --
    # os.replace (a real rename) never has that window on POSIX.
    tmp_link = root / f".{CURRENT_LINK_NAME}.tmp"
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    os.symlink(gen_dir.name, tmp_link)
    os.replace(tmp_link, root / CURRENT_LINK_NAME)


def _apply_retention(root, now, max_age_seconds, retain_newest):
    generations = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith("gen-")),
        key=lambda p: p.name,
    )
    newest = set(g.name for g in generations[-retain_newest:]) if retain_newest > 0 else set()
    for gen in generations:
        if gen.name in newest:
            continue
        ts_str = gen.name[len("gen-"):-1]  # strip "gen-" prefix and trailing "Z"
        import datetime
        gen_time = datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f").replace(
            tzinfo=datetime.timezone.utc
        )
        age = (now - gen_time).total_seconds()
        if age > max_age_seconds:
            _rmtree(gen)


def _rmtree(path):
    import shutil
    shutil.rmtree(path)
