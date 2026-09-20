"""The data-quality runner: evaluates every check registered in dq_check against the current
database state and writes one dq_check_run row per (run_id, check_name), pass or fail, every
run -- ARCHITECTURE.md section 16's own stated purpose ("Every check writes a row every run,
pass or fail. An empty table here is indistinguishable from 'no check has ever run', which is
the TESS-87 / tessguard failure this exists to not repeat.").

Each checker is a function (conn, run_id) -> CheckResult. Registered in CHECKERS by check_name,
matched against dq_check's seeded rows at run() time -- an unregistered check_name is a startup
error (RuntimeError), never a silent skip (this component's own F1 failure signature: a guard
that returns passed=1, or silently does nothing, on a condition it cannot actually evaluate).

Checkers operate on whatever this database's tables hold. Several (ingest_rows_match_bytes,
watermark_le_filesize) reference a real file on disk via ingest_source.source_path -- when that
path does not exist (as in a unit test using a synthetic fixture file, or before ingest has run
at all), the checker reports passed=0 with a detail naming the missing path, not a silent pass;
real end-to-end correctness against the live verdict ledger is foreman:integration-test's job,
not this component's (see GOALS.json out_of_scope).

HAZARD for the next checker comparing two `ts` columns against a literal second/day bound
(ATLASSN-91, caught in `_turn_final_day_stats` during CHV2-22's review): never write
`strftime(...) <= strftime(...) + N`. SQLite's strftime() returns TEXT; TEXT + N coerces only
the right-hand side to INTEGER, and SQLite's storage-class ordering sorts TEXT above every
NUMERIC value regardless of the digits -- so a TEXT-vs-INTEGER comparison built this way is
silently FALSE for every real pair of timestamps, not just an edge case. Measured directly: a
30-second gap evaluated as outside a 60-second window. Write the comparison as a subtraction on
both sides instead -- `strftime(...) - strftime(...) > 0 AND strftime(...) - strftime(...) <= N`
-- which forces INTEGER on both operands. `_turn_final_day_stats` already does this; ARCHITECTURE.md
section 30.3 has the design-level account, this is the code-level warning for whoever writes the
next one."""

import datetime
import json
import sqlite3
from collections import namedtuple
from pathlib import Path

from ..ingest.stream import default_quarantine_path

CheckResult = namedtuple("CheckResult", ["passed", "observed_value", "baseline_value", "detail"])

VALID_VERDICTS = {"fire", "silent", "error", "stolen"}
VALID_DECISIONS = {"deny", "defer", "ask", "allow"}

# ATLASSN-72, designed in ARCHITECTURE.md section 27. Every run-over-run check picks its
# comparison points out of ingest_run, and FORE-276's fast plane writes roughly 5,760 rows a day
# there against the current four. Without this predicate a fast tick is indistinguishable from a
# batch run and each of those checks silently changes meaning: the delta-shaped ones start
# comparing 15 seconds instead of hours, which does not make them noisy, it makes them VACUOUS
# (resolution_rate_delta fails on a drop of more than 5 points, and 15 seconds of ingest cannot
# move the rate 5 points on a 467,000-row corpus). Section 10 named the gate that cannot pass;
# this is the same defect with the sign flipped and much harder to notice, because nothing ever
# goes red.
#
# `plane IS NULL OR plane = 'slow'`, not `plane = 'slow'`: every run that existed when migration
# 11 landed was a batch run, and batch is the slow plane, so NULL already means the right thing
# and nothing was backfilled (27.5). This is deliberately the opposite of section 25's
# ledger_origin NULL, which is a real third category -- there the value was never recorded, here
# it is known.
#
# One string, used by all five call sites, so they cannot drift onto different predicates. Only
# ever interpolated into SQL as a fixed literal defined here; it takes no caller input.
SLOW_PLANE_RUNS = "status = 'ok' AND (plane IS NULL OR plane = 'slow')"

# The same predicate with ingest_run aliased, for the one call site that joins rather than
# selects from the table directly. Derived from SLOW_PLANE_RUNS rather than retyped, so the two
# cannot drift apart -- which is the whole reason SLOW_PLANE_RUNS is a single string.
SLOW_PLANE_ALIASED = SLOW_PLANE_RUNS.replace("status", "i.status").replace("plane", "i.plane")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------- source / hook_verdict checks

def check_verdict_domain_closed(conn, run_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM hook_verdict WHERE verdict NOT IN (?,?,?,?)",
        tuple(VALID_VERDICTS),
    ).fetchone()
    bad = row[0]
    return CheckResult(
        passed=(bad == 0),
        observed_value=bad,
        baseline_value=0,
        detail=f"{bad} rows with a verdict outside {sorted(VALID_VERDICTS)}"
        if bad else "0 violations",
    )


def check_stolen_implies_target(conn, run_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM hook_verdict "
        "WHERE (verdict = 'stolen') != (target IS NOT NULL)"
    ).fetchone()
    bad = row[0]
    return CheckResult(
        passed=(bad == 0), observed_value=bad, baseline_value=0,
        detail=f"{bad} rows where verdict='stolen' and target IS NOT NULL disagree",
    )


def check_decision_domain_closed(conn, run_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM hook_verdict "
        "WHERE decision IS NOT NULL AND decision NOT IN (?,?,?,?)",
        tuple(VALID_DECISIONS),
    ).fetchone()
    bad = row[0]
    return CheckResult(
        passed=(bad == 0), observed_value=bad, baseline_value=0,
        detail=f"{bad} rows with a non-NULL decision outside {sorted(VALID_DECISIONS)}"
        if bad else "0 violations",
    )


def check_watermark_le_filesize(conn, run_id):
    rows = conn.execute("SELECT source_name, source_path, byte_offset FROM ingest_source").fetchall()
    if not rows:
        return CheckResult(True, 0, 0, "no ingest_source rows yet -- vacuously satisfied")
    violations = []
    for source_name, source_path, byte_offset in rows:
        p = Path(source_path)
        if not p.is_file():
            violations.append(f"{source_name}: {source_path} does not exist on disk")
            continue
        real_size = p.stat().st_size
        if byte_offset > real_size:
            violations.append(
                f"{source_name}: byte_offset {byte_offset} exceeds real file size {real_size}"
            )
    return CheckResult(
        passed=(len(violations) == 0), observed_value=len(violations), baseline_value=0,
        detail="; ".join(violations) if violations else "0 violations",
    )


def _transcript_watermark_violations(conn, table):
    """Shared body for the two transcript watermark checks (ATLASSN-102, ARCHITECTURE.md
    section 33.3). Returns (violations, present, missing, modified_since_pull).

    Fails on exactly one state: byte_offset past the end of a file that a pull has ALREADY seen
    in its current form. Two other states are counted, disclosed in the detail, and do not fail.

      - source file gone. Transcript retention deletes these constantly -- measured 1,531 of
        2,367 session_transcript rows and 956 of 1,648 subagent_transcript rows the day this was
        written. Failing on them would put v_atlas_status.contract_failures above zero
        permanently, and the query facade refuses EVERY gated view system-wide on that count.
        That is section 10's "gate that cannot pass", repeated.

      - file modified since the last pull (mtime > updated_at). The next tick either resets the
        offset to zero (rotation, session_pull.upsert_session) or advances it, so the condition
        is transient. Deliberately NOT expressed as "recompute stream_id and compare": that
        function lives in atlas/ingest/transcript_parse.py and this module is the `warehouse`
        component, which section 15 gives no edge back into `ingest`.

    The mtime form is also sharper than a stream_id comparison would be. Truncation in place
    preserves the first 4096 bytes, so stream_id is unchanged and that test would wave it
    through -- while session_pull's own `size <= start_offset` early return means the watermark
    is never repaired. Here the early return still bumps updated_at, so one tick later
    updated_at >= mtime and the check fires, which is correct: that IS the broken state.

    `table` is interpolated into SQL as a fixed literal from the two call sites below and never
    takes caller input -- the same discipline SLOW_PLANE_RUNS states for itself."""
    rows = conn.execute(
        f"SELECT source_path, byte_offset, updated_at FROM {table}"
    ).fetchall()
    if not rows:
        return [], 0, 0, 0
    violations = []
    present = missing = modified_since_pull = 0
    for source_path, byte_offset, updated_at in rows:
        p = Path(source_path)
        if not p.is_file():
            missing += 1
            continue
        present += 1
        stat = p.stat()
        if byte_offset <= stat.st_size:
            continue
        if _file_changed_since(stat.st_mtime, updated_at):
            modified_since_pull += 1
            continue
        violations.append(
            f"{source_path}: byte_offset {byte_offset} exceeds file size {stat.st_size} "
            f"and the last pull ({updated_at}) already saw the file at this mtime"
        )
    return violations, present, missing, modified_since_pull


def _file_changed_since(mtime_epoch, updated_at):
    """True when the file was modified after the pull last touched its row, i.e. the next tick
    has not run yet. An unparseable updated_at is treated as "changed", which is the
    non-failing branch -- a timestamp this module cannot read is not evidence of a defect."""
    try:
        pulled = datetime.datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return True
    if pulled.tzinfo is None:
        pulled = pulled.replace(tzinfo=datetime.timezone.utc)
    modified = datetime.datetime.fromtimestamp(mtime_epoch, datetime.timezone.utc)
    return modified > pulled


def _transcript_watermark_result(conn, table):
    violations, present, missing, modified = _transcript_watermark_violations(conn, table)
    if not violations:
        detail = (
            f"0 violations over {present + missing} {table} rows "
            f"({present} with the source file present, {missing} whose source file is gone, "
            f"{modified} modified since the last pull)"
        )
    else:
        detail = "; ".join(violations[:5])
        if len(violations) > 5:
            detail += f"; and {len(violations) - 5} more"
    return CheckResult(
        passed=(len(violations) == 0), observed_value=len(violations), baseline_value=0,
        detail=detail,
    )


def check_session_watermark_le_filesize(conn, run_id):
    return _transcript_watermark_result(conn, "session_transcript")


def check_subagent_watermark_le_filesize(conn, run_id):
    return _transcript_watermark_result(conn, "subagent_transcript")


def _calls_ingested_matches(conn, transcript_table, fact_table):
    """Shared body for the two CONTRACT transcript checks (ATLASSN-102, ARCHITECTURE.md 33.3).

    A transcript row's calls_ingested must equal the number of fact rows that reference it.
    Deliberately a pure in-database invariant with no filesystem read: contract severity is
    summed into v_atlas_status.contract_failures, which makes the query facade refuse EVERY
    gated view system-wide, so the one check that carries it must not be trippable by anything
    outside the warehouse. Section 33.3 documents the reachable, permanent filesystem state that
    ruled the watermark check out of this severity and left it advisory.

    Same defect class as the existing contract check ingest_rows_match_bytes, one grain down.

    Both table names are fixed literals from the two call sites below and never take caller
    input -- the same discipline SLOW_PLANE_RUNS states for itself."""
    rows = conn.execute(
        f"SELECT t.transcript_id, t.calls_ingested, "
        f"       (SELECT COUNT(*) FROM {fact_table} f WHERE f.transcript_id = t.transcript_id) "
        f"FROM {transcript_table} t"
    ).fetchall()
    if not rows:
        return CheckResult(True, 0, 0,
                           f"no {transcript_table} rows yet -- vacuously satisfied")
    violations = [
        f"transcript_id {tid}: calls_ingested {claimed} but {fact_table} holds {actual}"
        for tid, claimed, actual in rows if claimed != actual
    ]
    detail = f"0 violations over {len(rows)} {transcript_table} rows"
    if violations:
        detail = "; ".join(violations[:5])
        if len(violations) > 5:
            detail += f"; and {len(violations) - 5} more"
    return CheckResult(
        passed=(len(violations) == 0), observed_value=len(violations), baseline_value=0,
        detail=detail,
    )


def check_session_calls_ingested_matches(conn, run_id):
    return _calls_ingested_matches(conn, "session_transcript", "session_tool_call")


def check_subagent_calls_ingested_matches(conn, run_id):
    return _calls_ingested_matches(conn, "subagent_transcript", "subagent_tool_call")


def check_session_tool_call_evidence_attrs_present(conn, run_id):
    """ATLASSN-143, decision (A) in ticket comment 2594: promotes session_tool_call to
    contract severity on FOUR-ATTRIBUTE COMPLETENESS only -- session_id, tool_use_id, tool_name
    and tool_input_json must all be present on every row. This is deliberately NOT a uniqueness
    or idempotency check; that stronger, currently-violated invariant (99 known duplicate groups,
    ATLASSN-144) is a separate, later promotion the same comment defers on purpose. Landed ahead
    of its dq_check seed row per this repo's documented code-before-DDL order (ARCHITECTURE.md
    26.2 / 29.4) -- see test_no_registered_checker_is_left_without_a_seed_row, which is expected
    to report this name until the migration-18 seed lands.

    All four columns are nullable in the live DDL and carry zero nulls today (verified against
    the live warehouse when this ticket's criteria were frozen), so this check is real -- it can
    fail -- rather than the vacuous pass the ticket's own text warns against."""
    row = conn.execute(
        "SELECT COUNT(*) FROM session_tool_call WHERE session_id IS NULL OR tool_use_id IS NULL "
        "OR tool_name IS NULL OR tool_input_json IS NULL OR tool_input_json = ''"
    ).fetchone()
    bad = row[0]
    return CheckResult(
        passed=(bad == 0), observed_value=bad, baseline_value=0,
        detail=f"{bad} session_tool_call rows missing session_id, tool_use_id, tool_name or "
               f"tool_input_json" if bad else "0 violations",
    )


def check_fail_open_not_double_counted(conn, run_id):
    # TESS-86 class: a real hook fail-open is recorded on BOTH ledgers. Pair those
    # incidents on handler + cwd + ts within 2 seconds. Unmatched rows on EITHER
    # side fail the contract. Shell writers (laa-commit-flow-advisory.sh) emit
    # verdict='error' and never write HOOK_ERROR; including them made this check
    # fail forever for a question they cannot answer. Measured 2026-08-24: 13/13
    # hook errors pair; all unmatched rows were *.sh.
    #
    # ATLASSN-35: ADVISORY as of migration 5, not contract -- see ARCHITECTURE.md section 21.
    # run_id is always the current (latest) run, so ingest_run_id <= run_id below is every row
    # ingested so far, i.e. unbounded -- identical to this check's pre-migration-5 query, just
    # expressed through the same helper check_fail_open_pairing_delta uses, so the two checks
    # can never silently drift onto different pairing logic.
    unmatched_errors, unmatched_hooks, n_errors, n_hooks = (
        _unmatched_fail_open_count_as_of(conn, run_id)
    )
    unmatched = unmatched_errors + unmatched_hooks
    if unmatched:
        detail = (
            f"{unmatched_errors} of {n_errors} hook error rows unpaired; "
            f"{unmatched_hooks} of {n_hooks} HOOK_ERROR rows unpaired"
        )
    else:
        detail = f"all {n_errors} hook errors and {n_hooks} HOOK_ERROR rows paired"
    return CheckResult(
        passed=(unmatched == 0), observed_value=unmatched, baseline_value=0, detail=detail,
    )


def _git_repo_fraction(conn):
    total = conn.execute("SELECT COUNT(*) FROM dim_project").fetchone()[0]
    if total == 0:
        return None
    real = conn.execute(
        "SELECT COUNT(*) FROM dim_project WHERE root_state = 'git-repo'"
    ).fetchone()[0]
    return real / total


def _distinct_cwd_resolution(conn):
    """The cwd-weighted companion to check_project_resolution_floor's row-weighted rate.
    Returns (unique_cwds, total_cwds) over hook_verdict's DISTINCT cwd values.

    FORE-275 item 2, sourced from ATLAS-COVERAGE-STRUCTURAL-FINDING.md ("The denominators are
    rows, and the losses are cwds"). A registered repo running for a month produces hundreds of
    thousands of verdict rows; a scratchpad experiment produces a dozen denies and vanishes.
    Row-weighting is the weighting that makes the experiments invisible -- a handful of
    well-registered directories can carry the row rate over the floor while most directories the
    harness actually ran in resolve to nothing. Measured on the live warehouse 2026-09-04 at
    ingest run 49: 432,987 of 467,169 rows resolve uniquely (0.9268, clears the 0.9000 floor)
    while only 189 of 1,559 distinct cwds do (0.1212). The row rate has never reported that gap.

    NULL cwds are excluded from both sides rather than counted as unresolved: COUNT(DISTINCT)
    ignores NULL anyway, so including them in the denominator alone would understate the rate by
    construction. Rows with no cwd at all are a different finding and belong to a different
    check."""
    total = conn.execute(
        "SELECT COUNT(DISTINCT cwd) FROM hook_verdict WHERE cwd IS NOT NULL"
    ).fetchone()[0]
    if total == 0:
        return 0, 0
    unique = conn.execute(
        "SELECT COUNT(DISTINCT v.cwd) FROM hook_verdict v JOIN cwd_project cp ON cp.cwd = v.cwd "
        "WHERE cp.resolution = 'unique'"
    ).fetchone()[0]
    return unique, total


def check_project_resolution_floor(conn, run_id):
    # ADVISORY per FATAL-1's fix -- see ARCHITECTURE.md section 10. Reports the comparison but
    # never blocks; dq_check.severity='advisory' is what the runner (below) uses to decide that.
    #
    # FORE-275 item 2 adds a SECOND floor, on distinct cwds; the check now passes only when both
    # clear. Three choices here are deliberate and named rather than inherited silently:
    #
    # 1. observed_value STAYS the row-weighted rate. dq_check_run.observed_value is a single
    #    numeric column and this check already has run history in it. Repointing it at the cwd
    #    share would make that series silently incomparable across the 2026-09-04 boundary, which
    #    is a worse loss than the cwd share being text-only. The cwd share goes in detail. A
    #    machine-readable cwd series wants its own dq_check row, which needs a migration, and was
    #    deliberately not folded into migration 10.
    #
    #    THE COST THAT BUYS, named on claude-hooks-v2-e5's review rather than left implicit: this
    #    check can now record passed=0 while observed_value >= baseline_value, because the row
    #    rate cleared and the cwd rate did not. To any consumer reading the dq_check_run series
    #    numerically, without parsing detail text, that pair is self-contradictory. Tolerable at
    #    advisory severity, where the row exists to be read by a person. Before this check could
    #    ever carry contract severity the cwd share needs its own dq_check row -- the deferred
    #    migration -- so the recorded numbers can explain their own verdict.
    #
    # 2. The floor is REUSED, not recalibrated. _git_repo_fraction is the fraction of registered
    #    projects that are real git repos -- a statement about the registry with no bearing on how
    #    broadly the harness's cwds ought to be registered, so reusing it here is not principled
    #    and is not claimed to be. Inventing a calibrated cwd floor from a single measurement
    #    would be exactly the "floor set to pass" ATLAS-COVERAGE-STRUCTURAL-FINDING.md names as
    #    the defect. It is reused so the gap is measured and recorded every run; setting a real
    #    cwd floor is a measure-then-decide question needing a distribution nothing has collected
    #    yet, and this check starts collecting one now.
    #
    # 3. It is expected to FAIL on the live warehouse from its first run, and that failure is the
    #    reported finding, not a reason to tune it (FORE-275's own acceptance criteria say so).
    #    Safe here and would not be under a contract severity: FATAL-1 was a CONTRACT gate made
    #    unsatisfiable, which withheld hook_verdict from every gated view. An advisory reporting
    #    an unflattering true number every run is the opposite failure mode.
    floor = _git_repo_fraction(conn)
    total = conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]
    if total == 0 or floor is None:
        return CheckResult(True, None, floor, "no hook_verdict rows or no dim_project rows yet")
    unique = conn.execute(
        "SELECT COUNT(*) FROM hook_verdict v JOIN cwd_project cp ON cp.cwd = v.cwd "
        "WHERE cp.resolution = 'unique'"
    ).fetchone()[0]
    rate = unique / total
    unique_cwds, total_cwds = _distinct_cwd_resolution(conn)
    if total_cwds == 0:
        # Every hook_verdict row carries a NULL cwd. Named as NOT EVALUATED rather than folded
        # into a pass -- this component's own F1 signature is a guard that returns passed on a
        # condition it could not actually evaluate.
        return CheckResult(
            passed=(rate >= floor), observed_value=round(rate, 4), baseline_value=round(floor, 4),
            detail=f"row-weighted resolution {rate:.4f} vs floor {floor:.4f}; distinct-cwd floor "
            f"NOT EVALUATED -- no hook_verdict row carries a cwd",
        )
    cwd_rate = unique_cwds / total_cwds
    return CheckResult(
        passed=(rate >= floor and cwd_rate >= floor),
        observed_value=round(rate, 4), baseline_value=round(floor, 4),
        detail=f"row-weighted resolution {rate:.4f} ({unique:,}/{total:,}) vs floor {floor:.4f} "
        f"[{'ok' if rate >= floor else 'BELOW'}]; distinct-cwd resolution {cwd_rate:.4f} "
        f"({unique_cwds:,}/{total_cwds:,}) vs the same floor "
        f"[{'ok' if cwd_rate >= floor else 'BELOW'}]. The floor is the fraction of registered "
        f"projects that are real git repos, reused for the cwd share rather than calibrated for "
        f"it -- read the cwd verdict as a measured gap, not as a threshold judgment",
    )


def check_handler_denominator_nonzero(conn, run_id):
    rows = conn.execute(
        "SELECT handler_id FROM v_handler_denominator "
        "WHERE trust_state = 'ok' AND structural_zero_denominator = 1"
    ).fetchall()
    names = [r[0] for r in rows]
    # Advisory: always "passes" in the sense of not blocking, but records what it found so the
    # alert is real -- matches handler_denominator_nonzero's severity='advisory' seed row.
    return CheckResult(
        passed=(len(names) == 0), observed_value=len(names), baseline_value=0,
        detail=f"structural-zero-denominator handlers: {names}" if names else "none",
    )


# ---------------------------------------------------------------- source / audit_event checks

def check_audit_envelope_wellformed(conn, run_id):
    rows = conn.execute(
        "SELECT stream_id, byte_offset, schema_version, ts, event_type FROM audit_event"
    ).fetchall()
    bad = []
    for stream_id, byte_offset, schema_version, ts, event_type in rows:
        if not schema_version or not ts or not event_type:
            bad.append(f"{stream_id}:{byte_offset}")
            continue
        try:
            datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            bad.append(f"{stream_id}:{byte_offset} (unparseable ts {ts!r})")
    return CheckResult(
        passed=(len(bad) == 0), observed_value=len(bad), baseline_value=0,
        detail=f"{len(bad)} malformed rows: {bad[:5]}" if bad else "0 violations",
    )


# ARCHITECTURE.md section 10's audit_payload_size_bounded dq_check seed row -- a named 'lean'
# threshold, not an invariant, against a real measured max of 682,196 bytes. Recalibration
# trigger, per that same section: "the first payload above 1 MiB."
AUDIT_PAYLOAD_BOUND_BYTES = 1024 * 1024  # CITED: ARCHITECTURE.md section 10, lean threshold


def check_audit_payload_size_bounded(conn, run_id):
    row = conn.execute(
        "SELECT MAX(payload_bytes) FROM audit_event"
    ).fetchone()
    max_bytes = row[0]
    if max_bytes is None:
        return CheckResult(True, 0, AUDIT_PAYLOAD_BOUND_BYTES, "no audit_event rows yet")
    return CheckResult(
        passed=(max_bytes <= AUDIT_PAYLOAD_BOUND_BYTES),
        observed_value=max_bytes, baseline_value=AUDIT_PAYLOAD_BOUND_BYTES,
        detail=f"max payload {max_bytes} bytes vs {AUDIT_PAYLOAD_BOUND_BYTES} bound",
    )


# adversarial-code-review Check 4 (SERIOUS, verification stage): auditplane_to_ingest is
# classified secret-bearing (section 0/4 -- payloads carry verbatim shell command text, the
# exact channel this project's own CLAUDE.md records a real Gemini key exposure through), and
# section 1 states "ten credential patterns scanned... zero hits today" -- but that was a
# one-time authorship-time measurement, never wired as a running check. A caller of
# v_fail_open_incident (query facade's own ALLOWED_VIEWS) reads audit_detail = payload_json
# verbatim, unredacted. This check makes "zero hits" a continuously-verified, alerting fact
# rather than a stale claim -- advisory, not contract: a real credential hit is a genuine
# incident to alert on, not grounds to withhold the whole audit_event source from every view
# (the same asymmetry audit_ledger_partition_by_cwd already uses). It does NOT redact payloads
# at read time; whether the query/snapshot layer should additionally redact secret-bearing
# payloads before serving them is a separate, larger design question, tracked (not decided
# here) rather than silently assumed.
CREDENTIAL_PATTERNS = {
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "private_key_header": r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "slack_token": r"xox[baprs]-[0-9A-Za-z-]{10,}",
    "github_token": r"gh[pousr]_[A-Za-z0-9]{36,}",
    "bearer_token": r"[Bb]earer\s+[A-Za-z0-9\-_.]{20,}",
    "literal_export_assignment": r"\bexport\s+\w*(KEY|SECRET|TOKEN|PASSWORD)\w*\s*=\s*\S+",
    "generic_api_key_assignment": r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]",
    "password_assignment": r"(?i)password\s*[:=]\s*['\"][^'\"]{6,}['\"]",
    "credentialed_url": r"://[^/\s:]+:[^/\s@]+@",
    "generic_secret_keyword_assignment": r"(?i)\bsecret\w*\s*[:=]\s*\S{12,}",
}


def check_audit_payload_credential_scan(conn, run_id):
    import re
    rows = conn.execute("SELECT stream_id, byte_offset, payload_json FROM audit_event").fetchall()
    hits = []
    for stream_id, byte_offset, payload_json in rows:
        for pattern_name, pattern in CREDENTIAL_PATTERNS.items():
            if re.search(pattern, payload_json or ""):
                hits.append(f"{stream_id}:{byte_offset} ({pattern_name})")
                break
    return CheckResult(
        passed=(len(hits) == 0), observed_value=len(hits), baseline_value=0,
        detail=f"{len(hits)} rows matched a credential pattern: {hits[:5]}" if hits
        else f"0 hits across {len(rows)} rows, {len(CREDENTIAL_PATTERNS)} patterns",
    )


def check_audit_ledger_partition_by_cwd(conn, run_id):
    # FORE-40 class: audit rows whose cwd resolves to a registered project but landed in a
    # ledger this run recorded under a DIFFERENT project's expectations. Advisory: this table
    # has no per-row "which ledger file" column beyond source_path, so the check is scoped to
    # what warehouse's own schema can see -- rows whose cwd is registered but whose source_path
    # does not match any project's own source_root prefix.
    rows = conn.execute(
        "SELECT a.stream_id, a.byte_offset, a.source_path, a.cwd FROM audit_event a WHERE a.cwd IS NOT NULL"
    ).fetchall()
    projects = conn.execute("SELECT project_prefix, source_root FROM dim_project WHERE source_root IS NOT NULL").fetchall()
    misrouted = 0
    for stream_id, byte_offset, source_path, cwd in rows:
        matched_project_for_cwd = any(cwd == root or cwd.startswith(root.rstrip("/") + "/") for _, root in projects)
        if not matched_project_for_cwd:
            continue
        if source_path and "global-safety" in source_path:
            misrouted += 1
    return CheckResult(
        passed=(misrouted == 0), observed_value=misrouted, baseline_value=0,
        detail=f"{misrouted} rows with a registered cwd landed in the shared global ledger"
        if misrouted else "0 found",
    )


# ---------------------------------------------------------------- source / tessera_event checks

def check_tessera_event_id_unique(conn, run_id):
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT event_id) FROM tessera_event"
    ).fetchone()
    total, distinct = row
    bad = total - distinct
    return CheckResult(
        passed=(bad == 0), observed_value=bad, baseline_value=0,
        detail=f"{bad} duplicate event_id rows in tessera_event (event_id is this table's own "
        f"PRIMARY KEY, so this is a structural guarantee; the deeper risk -- TESSERA's own "
        f"v_flat comment-join fanning out on a shared (ticket_id, created_ts) -- is checked at "
        f"ingest time, before rows reach this table, per ARCHITECTURE.md section 1)",
    )


# ---------------------------------------------------------------- source / git_commit checks

def check_git_ticket_prefix_registered(conn, run_id):
    rows = conn.execute(
        "SELECT DISTINCT gct.project_prefix, gct.ticket_id FROM git_commit_ticket gct "
        "LEFT JOIN dim_project dp ON dp.project_prefix = ("
        "  SELECT p2.project_prefix FROM tessera_event te "
        "  JOIN dim_project p2 ON p2.project_prefix = te.project_prefix "
        "  WHERE te.ticket_id = gct.ticket_id LIMIT 1"
        ") WHERE dp.project_prefix IS NULL"
    ).fetchall()
    return CheckResult(
        passed=(len(rows) == 0), observed_value=len(rows), baseline_value=0,
        detail=f"{len(rows)} git_commit_ticket rows reference a ticket_id with no registered "
        f"project" if rows else "0 violations; git_ticket_candidate_dropped holds what was "
        f"correctly excluded at ingest time",
    )


# ---------------------------------------------------------------- pipeline checks

def check_ingest_rows_match_bytes(conn, run_id):
    rows = conn.execute(
        "SELECT source_name, stream_id, source_path, byte_offset, rows_ingested FROM ingest_source"
    ).fetchall()
    if not rows:
        return CheckResult(True, 0, 0, "no ingest_source rows yet")

    # ATLASSN-171. run75-ingest-trap's Part 3 (landed 2026-09-13) gives a row a legitimate reason
    # to be absent from rows_ingested -- a CHECK-constraint reject, quarantined by
    # stream.record_quarantine() rather than silently dropped -- but this comparison had no
    # notion of quarantine, so a source with even one quarantined row failed this contract check
    # forever, on a gap that is fully accounted for (verified live, run 115: a 15-row gap exactly
    # matching 15 quarantine records, all CHECK-constraint rejects, not data loss). Fixed per the
    # run75-ingest-trap README's own prescription: lines == rows_ingested + rows_quarantined.
    #
    # Quarantine count read once, up front, from the same sidecar record_quarantine() writes to
    # (default_quarantine_path -- reused from atlas.ingest.stream rather than re-derived here, so
    # this check can never disagree with the writer about where the file lives), keyed by
    # (source_name, stream_id) -- ingest_source's own primary key -- so a source that has since
    # rotated to a new stream_id does not inherit a prior stream's quarantine count.
    quarantined_by_source = {}
    malformed_quarantine_lines = 0
    quarantine_path = default_quarantine_path(conn)
    if quarantine_path and quarantine_path.is_file():
        for line in quarantine_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # The sidecar's own writer never raises and tolerates a torn tail from a short
                # write (record_quarantine's own healing logic, round 3) -- an occasional
                # unparseable line here is expected, not a crash condition. But silently
                # undercounting quarantine could mask a real mismatch elsewhere, so it is
                # disclosed in the result below rather than swallowed.
                malformed_quarantine_lines += 1
                continue
            key = (record.get("source_name"), record.get("stream_id"))
            quarantined_by_source[key] = quarantined_by_source.get(key, 0) + 1

    mismatches = []
    for source_name, stream_id, source_path, byte_offset, rows_ingested in rows:
        p = Path(source_path)
        if not p.is_file():
            mismatches.append(f"{source_name}: {source_path} does not exist")
            continue
        data = p.read_bytes()[:byte_offset]
        # Non-blank elements of a split on \n -- a trailing-newline file always yields one
        # empty final element, which this must not double-subtract (section 8's own recorded
        # first-run defect: 125,776 vs 125,777, an off-by-one in this exact predicate).
        lines = [ln for ln in data.split(b"\n") if ln.strip()]
        rows_quarantined = quarantined_by_source.get((source_name, stream_id), 0)
        expected = rows_ingested + rows_quarantined
        if len(lines) != expected:
            mismatches.append(
                f"{source_name}: {len(lines)} non-blank lines in byte range vs "
                f"rows_ingested={rows_ingested} + rows_quarantined={rows_quarantined} "
                f"= {expected}"
            )
    if malformed_quarantine_lines:
        mismatches.append(
            f"quarantine sidecar: {malformed_quarantine_lines} unparseable line(s) skipped when "
            f"counting rows_quarantined -- an undercount here could mask a real mismatch above"
        )
    return CheckResult(
        passed=(len(mismatches) == 0), observed_value=len(mismatches), baseline_value=0,
        detail="; ".join(mismatches) if mismatches else "0 mismatches",
    )


def check_ingest_rows_monotonic(conn, run_id):
    rows = conn.execute(
        "SELECT run_id, (SELECT COUNT(*) FROM hook_verdict h WHERE h.ingest_run_id <= ingest_run.run_id) "
        f"FROM ingest_run WHERE {SLOW_PLANE_RUNS} ORDER BY run_id"
    ).fetchall()
    prev = None
    violations = 0
    for _, count in rows:
        if prev is not None and count < prev:
            violations += 1
        prev = count
    return CheckResult(
        passed=(violations == 0), observed_value=violations, baseline_value=0,
        detail=f"{violations} run-over-run decreases in cumulative hook_verdict row count"
        if violations else "monotonic across all ok runs",
    )


def _unmatched_fail_open_count_as_of(conn, as_of_run_id):
    """Shared by check_fail_open_not_double_counted and check_fail_open_pairing_delta so the two
    checks can never silently drift onto different pairing logic. as_of_run_id scopes BOTH outer
    populations, by ingest_run_id.

    ATLASSN-52 corrected this. A prior version scoped the hook_verdict side only, and said so
    deliberately: audit_event was not run-scoped in the original level check, so it was left
    unscoped here too. Inheriting that is harmless for a LEVEL check, which counts the whole
    table anyway, and wrong for a DELTA check. With the audit-side outer population unfrozen, a
    brand-new audit_event HOOK_ERROR row with no hook_verdict pair is counted as unmatched in the
    prior-run snapshot AND the latest-run snapshot at once, so it can never register as an
    increase -- and check_fail_open_pairing_delta fails only on an increase. That left the check
    structurally blind to audit-only orphans, which is exactly the shape its own docstring names
    as a regression it should catch ("a new hook bypassing hook_common.py entirely"), and the
    FORE-190 direction named in ARCHITECTURE.md section 20. audit_event.ingest_run_id is
    NOT NULL REFERENCES ingest_run, so no schema change was needed."""
    unmatched_errors, unmatched_hooks, n_errors, n_hooks = _unmatched_fail_open_rows_as_of(
        conn, as_of_run_id)
    return len(unmatched_errors), len(unmatched_hooks), n_errors, n_hooks


def _unmatched_fail_open_rows_as_of(conn, as_of_run_id):
    """The row-level form of the above, added by FORE-312 so the run-scoped attribution in
    check_fail_open_pairing_delta reads the SAME pairing predicate rather than a second copy of
    it. The count helper now derives from this one, which is what keeps them from drifting -- the
    hazard the count helper's own docstring already names for its two callers.

    Returns (unmatched_errors, unmatched_hooks, n_errors, n_hooks) where the two unmatched
    entries are lists of (ingest_run_id, ts) for the rows that failed to pair.

    THE OUTER POPULATION IS RESTRICTED TO SCRIPT HANDLERS, and the second half of that predicate
    is new (ATLASSN-68, the atlas-side half of FORE-314). The pre-existing NOT LIKE '%.sh' clause
    excludes shell writers because they never write HOOK_ERROR, so they cannot produce the
    right-hand side of the join this check performs. Exactly the same is true of a handler_id that
    is not a script name at all: those rows are the guard test suites exercising hook_common.run()
    with the ledger writer live, so verdicts.jsonl records the TEST RUNNER'S ARGV -- 'python3 -m
    unittest', '-c' -- where a hook name belongs, and no hook ran, so no HOOK_ERROR exists.

    Measured before the clause was written, on the live warehouse 2026-09-04. Every distinct
    $.hook value in audit_event is a .py script name. Of the 42 non-script error rows, 0 pair; of
    the 660 .sh error rows the check already excluded, 0 pair; the .py population is the only one
    where pairing occurs at all. The exclusion is a statement about the check's DOMAIN -- a writer
    that cannot produce the counterpart is not a pairing failure -- not a judgment about who ran
    the code. That distinction matters: filtering rows by whether they look like test traffic
    belongs in the writer (ATLAS-COVERAGE-STRUCTURAL-FINDING.md is explicit that it does not
    belong in a DQ check), while restricting a join to writers capable of both sides is the same
    move this predicate already made once, for the same reason.

    A shape test is a PROXY for dim_handler.writer_impl, which is the principled home and is
    unpopulated (zero rows on the live warehouse). Named as a proxy rather than presented as the
    answer; FORE-314 carries populating that dimension, and until it lands this predicate also
    narrows silently for any future real hook that is not a *.py file.

    NOT LIKE '%.sh' IS NOW REDUNDANT beside LIKE '%.py' and is kept deliberately, belt-and-braces
    (claude-hooks-v2-e5's review asked that this be said rather than left ambiguous). There is no
    third population between the two clauses. It stays because it carries ATLASSN-31's own
    rationale in the code -- shell writers never write HOOK_ERROR -- and deleting it would erase
    the reason from the place a future reader looks, while leaving the .py clause looking like an
    unexplained shape test."""
    errors = conn.execute(
        "SELECT handler_id, cwd, ts, ingest_run_id FROM hook_verdict "
        "WHERE verdict='error' AND handler_id NOT LIKE '%.sh' AND handler_id LIKE '%.py' "
        "AND ingest_run_id <= ?",
        (as_of_run_id,),
    ).fetchall()
    unmatched_errors = []
    for handler_id, cwd, ts, ingest_run_id in errors:
        row = conn.execute(
            "SELECT 1 FROM audit_event WHERE event_type='HOOK_ERROR' "
            "AND json_extract(payload_json,'$.hook') = ? "
            "AND cwd IS ? "
            "AND ABS(strftime('%s', ts) - strftime('%s', ?)) <= 2 LIMIT 1",
            (handler_id, cwd, ts),
        ).fetchone()
        if row is None:
            unmatched_errors.append((ingest_run_id, ts))
    hooks = conn.execute(
        "SELECT json_extract(payload_json,'$.hook'), cwd, ts, ingest_run_id FROM audit_event "
        "WHERE event_type='HOOK_ERROR' AND ingest_run_id <= ?",
        (as_of_run_id,),
    ).fetchall()
    unmatched_hooks = []
    for hook, cwd, ts, ingest_run_id in hooks:
        row = conn.execute(
            "SELECT 1 FROM hook_verdict WHERE verdict='error' AND handler_id = ? "
            "AND cwd IS ? AND ingest_run_id <= ? "
            "AND ABS(strftime('%s', ts) - strftime('%s', ?)) <= 2 LIMIT 1",
            (hook, cwd, as_of_run_id, ts),
        ).fetchone()
        if row is None:
            unmatched_hooks.append((ingest_run_id, ts))
    return unmatched_errors, unmatched_hooks, len(errors), len(hooks)


def _parse_ts(value):
    """Both timestamp shapes this warehouse stores, normalised. hook_verdict.ts is written with a
    trailing Z ('2026-09-03T20:08:56.025391Z') and ingest_run.started_at with an explicit offset
    ('2026-09-03T19:32:28.761160+00:00'). Comparing those two as STRINGS happens to work today,
    because the suffix is only reached when everything before it is equal, but it is a
    correctness accident rather than a property, so the comparison is done on parsed values.
    Returns None on anything unparseable; the caller decides what that means rather than this
    function guessing."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def check_fail_open_pairing_delta(conn, run_id):
    """ATLASSN-35. resolution_rate_delta's exact pattern applied to the same pairing gap
    fail_open_not_double_counted checks: that check's historical residue (7 rows unpaired
    before the 2026-08-28 hook_common.py fix, ARCHITECTURE.md section 21) fails it forever on a
    fixed population no pipeline behavior can move. This check compares the unmatched count
    between the two most recent ok ingest runs and fails only on an INCREASE -- a regression is
    causally tied to the count moving, a fixed historical level is not.

    FORE-312 adds the run-scoped cohort. ATLAS-COVERAGE-STRUCTURAL-FINDING.md named the delta's
    own weakness: "It cannot distinguish '24 new fail-open errors happened since the last run'
    from '24 old fail-open errors were ingested for the first time since the last run.' Both are
    an increase in a count." An increase is only a regression to the extent it is attributable to
    events that OCCURRED in the window, so the unpaired rows this run brought in are now split by
    whether their own ts predates the prior run's start, and the check passes when the increase is
    entirely late-arriving history.

    MEASURED BEFORE BUILDING, and the result is worth carrying: over every increase this check has
    recorded (runs 12, 13, 16, 27, 43, 45, 46, 48, 49) the history component is ZERO in all nine.
    The confusion the source hypothesised -- its own wording was conditional, "IF tonight's re-sync
    advanced a watermark or picked up a rotated stream" -- has never occurred on this warehouse. So
    this attribution changes no verdict today and is not what will turn this check green. What
    holds it red is that 42 of the 78 verdict='error' rows are the guard test harness writing into
    the real ledger under its own argv ('python3 -m unittest' 40, '-c' 2), which is FORE-314 and
    belongs in the writer, not here. Recorded so nobody re-derives the same dead end."""
    runs = conn.execute(
        f"SELECT run_id FROM ingest_run WHERE {SLOW_PLANE_RUNS} ORDER BY run_id DESC LIMIT 2"
    ).fetchall()
    if len(runs) < 2:
        return CheckResult(True, None, None, "fewer than 2 ok ingest_run rows -- nothing to diff yet")
    latest_run_id, prior_run_id = runs[0][0], runs[1][0]

    prior_e, prior_h, _, _ = _unmatched_fail_open_count_as_of(conn, prior_run_id)
    latest_errs, latest_hooks, n_errors, n_hooks = _unmatched_fail_open_rows_as_of(
        conn, latest_run_id)
    prior_total = prior_e + prior_h
    latest_total = len(latest_errs) + len(latest_hooks)
    delta = latest_total - prior_total

    boundary = _parse_ts(conn.execute(
        "SELECT started_at FROM ingest_run WHERE run_id = ?", (prior_run_id,)).fetchone()[0])
    history = news = undatable = 0
    # ingest_run_id > prior_run_id, NOT == latest_run_id. Corrected on claude-hooks-v2-e5's
    # review of ATLASSN-67: both snapshots are cumulative (ingest_run_id <= X), so the increase
    # comprises every unpaired row carried in by ANY run after the prior one, not only by the
    # latest ok one. A row from a run between them -- reachable if a non-ok run ever commits rows
    # -- counted toward delta while being attributed to neither history nor news, so news could
    # read 0 on an increase this check had not actually dated, and pass. Unreachable on the live
    # warehouse today (all 49 ingest runs are status ok and no hook_verdict row comes from a
    # non-ok run, both verified), which is exactly why it needed fixing rather than documenting:
    # the assumption was invisible and the failure would have been a silent pass.
    for ingest_run_id, ts in latest_errs + latest_hooks:
        if ingest_run_id <= prior_run_id:
            continue  # already inside the prior snapshot, so it is not part of the increase
        parsed = _parse_ts(ts)
        if boundary is None or parsed is None:
            # Fails toward NEWS, deliberately. An unreadable timestamp must never be the reason a
            # real regression is excused as late-arriving history; the safe direction for a
            # contract check is to keep reporting, and the count is disclosed separately below.
            undatable += 1
            news += 1
        elif parsed < boundary:
            history += 1
        else:
            news += 1

    attributed = f"{history} late-arriving history, {news} newly occurred"
    if undatable:
        attributed += f" ({undatable} with an unreadable ts, counted as newly occurred)"
    return CheckResult(
        passed=(delta <= 0 or news == 0), observed_value=delta, baseline_value=0,
        detail=f"unmatched fail-open rows moved from {prior_total} to {latest_total} "
        f"({delta:+d}); of the unpaired rows ingested after run {prior_run_id}: {attributed}; "
        f"latest: {len(latest_errs)} of {n_errors} hook error rows unpaired, "
        f"{len(latest_hooks)} of {n_hooks} HOOK_ERROR rows unpaired",
    )


def check_resolution_rate_delta(conn, run_id):
    runs = conn.execute(
        f"SELECT run_id FROM ingest_run WHERE {SLOW_PLANE_RUNS} ORDER BY run_id DESC LIMIT 2"
    ).fetchall()
    if len(runs) < 2:
        return CheckResult(True, None, None, "fewer than 2 ok ingest_run rows -- nothing to diff yet")
    latest_run_id, prior_run_id = runs[0][0], runs[1][0]

    def rate_as_of(as_of_run_id):
        total = conn.execute(
            "SELECT COUNT(*) FROM hook_verdict WHERE ingest_run_id <= ?", (as_of_run_id,)
        ).fetchone()[0]
        if total == 0:
            return None
        unique = conn.execute(
            "SELECT COUNT(*) FROM hook_verdict v JOIN cwd_project cp ON cp.cwd = v.cwd "
            "WHERE v.ingest_run_id <= ? AND cp.resolution = 'unique'", (as_of_run_id,)
        ).fetchone()[0]
        return unique / total

    prior_rate = rate_as_of(prior_run_id)
    latest_rate = rate_as_of(latest_run_id)
    if prior_rate is None or latest_rate is None:
        return CheckResult(True, None, None, "no hook_verdict rows at one of the two runs yet")
    delta_points = (prior_rate - latest_rate) * 100
    return CheckResult(
        passed=(delta_points <= 5.0), observed_value=round(delta_points, 2), baseline_value=5.0,
        detail=f"resolution rate moved from {prior_rate:.4f} to {latest_rate:.4f} "
        f"({delta_points:+.2f} points)",
    )


def check_handler_set_stable(conn, run_id):
    runs = conn.execute(
        f"SELECT run_id FROM ingest_run WHERE {SLOW_PLANE_RUNS} ORDER BY run_id DESC LIMIT 2"
    ).fetchall()
    if len(runs) < 2:
        return CheckResult(True, 0, 0, "fewer than 2 ok ingest_run rows -- nothing to diff yet")
    latest_run_id, prior_run_id = runs[0][0], runs[1][0]
    prior_handlers = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT handler_id FROM hook_verdict WHERE ingest_run_id <= ?", (prior_run_id,)
        )
    }
    latest_handlers = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT handler_id FROM hook_verdict WHERE ingest_run_id <= ?", (latest_run_id,)
        )
    }
    vanished = prior_handlers - latest_handlers
    return CheckResult(
        passed=(len(vanished) == 0), observed_value=len(vanished), baseline_value=0,
        detail=f"vanished handlers: {sorted(vanished)}" if vanished else "none vanished",
    )


def check_watermark_advanced(conn, run_id):
    rows = conn.execute(
        "SELECT source_name, source_path, byte_offset FROM ingest_source"
    ).fetchall()
    if not rows:
        return CheckResult(True, 0, 0, "no ingest_source rows yet")
    stalled = []
    for source_name, source_path, byte_offset in rows:
        p = Path(source_path)
        if not p.is_file():
            continue
        real_size = p.stat().st_size
        if byte_offset < real_size:
            stalled.append(f"{source_name}: watermark {byte_offset} < file size {real_size}")
    return CheckResult(
        passed=(len(stalled) == 0), observed_value=len(stalled), baseline_value=0,
        detail="; ".join(stalled) if stalled else "watermark caught up (or source did not grow)",
    )


# ---------------------------------------------------------------- snapshot checks

def check_snapshot_ticket_rollup_identity(conn, run_id, ticket_rollup=None):
    # FATAL-3's fix. ticket_rollup, if provided, is the snapshot publisher's in-memory rollup
    # dict {prefix: {open_total, open_by_severity, open_null_severity, ...}} -- passed explicitly
    # because this check runs against a live TESSERA read the warehouse never stores (section 5,
    # section 6), not against a warehouse table like every other checker here.
    if ticket_rollup is None:
        return CheckResult(True, None, None, "no ticket_rollup supplied this run (not a snapshot-publish run)")
    violations = []
    for prefix, data in ticket_rollup.items():
        total = data.get("open_total", 0)
        null_sev = data.get("open_null_severity", 0)
        by_sev_sum = sum((data.get("open_by_severity") or {}).values())
        if total != null_sev + by_sev_sum:
            violations.append(f"{prefix}: open_total={total} != null_sev={null_sev} + Σby_sev={by_sev_sum}")
    return CheckResult(
        passed=(len(violations) == 0), observed_value=len(violations), baseline_value=0,
        detail="; ".join(violations) if violations else "identity holds for every project",
    )


LEDGER_JOIN_COVERAGE_FLOOR = 0.90


def _ledger_join_coverage_now(conn):
    """The current join-coverage fraction, shared by check_ledger_join_coverage and
    check_ledger_join_coverage_delta (ATLASSN-63) so the LEVEL and the DROP can never be computed
    from different bucket arithmetic. Returns (coverage, joined, denominator, buckets), with
    coverage None when the denominator is empty.

    Deliberately does NOT catch sqlite3.OperationalError. A warehouse below migration 4 has no
    such view, and what that means for a verdict differs between the two callers -- not evaluated
    and failing for the level, not evaluated for the drop -- so each decides for itself rather
    than this helper picking one and hiding the other."""
    buckets = dict(conn.execute(
        "SELECT join_bucket, rows_total FROM v_ledger_join_coverage"))
    joined = sum(n for b, n in buckets.items() if b.startswith("joined-"))
    unjoined = buckets.get("no-transcript-on-disk", 0)
    denominator = joined + unjoined
    coverage = (joined / denominator) if denominator else None
    return coverage, joined, denominator, buckets


def check_ledger_join_coverage(conn, run_id):
    """ATLASSN-33. What fraction of hook_verdict rows carrying a tool_use_id join to a transcript
    ATLAS actually holds? Measured 33.0% before main-session ingest, 99.3% after.

    ADVISORY, not contract, and the precedent is this project's own FATAL-1: project_resolution_
    floor was made a contract gate against a level the AREM/FORE shared-root ambiguity rendered
    structurally unsatisfiable, and it would have withheld hook_verdict from every gated view
    permanently from the first run. Join coverage has that shape too -- the level decays on its
    own as the harness's retention removes old session files while verdicts.jsonl only grows -- so
    a contract floor here is a gate that fails on the calendar. The run-over-run DROP is what could
    carry contract severity, exactly as resolution_rate_delta does for resolution, and it is not
    built. Named as unbuilt rather than implied.

    Rows with no tool_use_id are excluded from the denominator rather than counted as misses:
    35,526 of the 35,793 such rows carry decision IS NULL and were never about a tool call, so
    there is nothing for them to join TO. v_ledger_join_coverage reports them as their own bucket,
    and the 224 that DID gate a real call are FORE-191."""
    try:
        coverage, joined, denominator, buckets = _ledger_join_coverage_now(conn)
    except sqlite3.OperationalError as exc:
        # A warehouse migrated below version 4 has no such view. Reported as a real, named
        # not-evaluated result rather than a pass -- a check that cannot run must never look
        # like a check that ran and found nothing.
        return CheckResult(
            passed=False, observed_value=None, baseline_value=LEDGER_JOIN_COVERAGE_FLOOR,
            detail=f"v_ledger_join_coverage unavailable ({exc}) -- warehouse predates "
                   f"migration 4, coverage NOT evaluated",
        )
    if coverage is None:
        return CheckResult(
            passed=True, observed_value=None, baseline_value=LEDGER_JOIN_COVERAGE_FLOOR,
            detail="no hook_verdict rows carry a tool_use_id yet -- vacuously clean, and said "
                   "so rather than reported as 1.0",
        )
    unjoined = buckets.get("no-transcript-on-disk", 0)
    return CheckResult(
        passed=(coverage >= LEDGER_JOIN_COVERAGE_FLOOR),
        observed_value=round(coverage, 4), baseline_value=LEDGER_JOIN_COVERAGE_FLOOR,
        detail=f"{joined:,} of {denominator:,} tool_use_id-bearing rows join to an ingested "
               f"transcript ({100*coverage:.1f}%); {unjoined:,} have no transcript on disk. "
               f"Buckets: {buckets}",
    )


# ATLASSN-63 / ARCHITECTURE.md section 26.4. Calibrated, not chosen: dq_check_run holds 36
# recorded observations of ledger_join_coverage across ingest runs 14 to 49, giving 35
# run-over-run deltas spanning -0.030 to +0.040 points with six drops, the worst -0.030. This
# tolerance is about 17x that worst drop and still about 20x tighter than the 9 points of slack
# LEDGER_JOIN_COVERAGE_FLOOR carries against a measured 99.3%. A zero tolerance would have fired
# six times in 36 runs on movement at the series' own 0.01-point rounding resolution.
LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS = 0.5


def check_ledger_join_coverage_delta(conn, run_id):
    """ATLASSN-63, designed in ARCHITECTURE.md section 26. The run-over-run drop
    check_ledger_join_coverage's own docstring names as unbuilt.

    ADVISORY, not contract, and section 26.3 argues it rather than assuming it. "Could carry
    contract severity" is a capability claim, not a mandate. v_ledger_join_coverage is computed at
    query time against transcripts CURRENTLY ON DISK, so a retention sweep moves rows out of
    joined-session and into no-transcript-on-disk in the very next run with no pipeline defect
    anywhere; the 35 deltas this tolerance was calibrated from contain no such event. Gating on a
    regime never observed is FATAL-1 repeated, on the very check section 20.3 already refused to
    gate for that reason. The promotion trigger is named in 26.3: observe a real retention event,
    confirm this check can separate retention decay from a joiner regression -- which bucket counts
    alone currently cannot, since both move rows the same direction -- and only then promote.

    THE PRIOR VALUE IS READ, NOT RECOMPUTED, and that is the structural difference from
    resolution_rate_delta. That check recomputes both sides with ingest_run_id <= ? because
    resolution is a function of stored rows alone. Join coverage is not: whether a transcript is on
    disk is a fact about NOW, so historical coverage cannot be reconstructed as-of a past run at
    all. The prior value therefore comes from dq_check_run, scoped strictly run_id < this run --
    never "most recent row" -- because run_all() iterates dq_check's names in table order and
    inserts each result as it goes, so ledger_join_coverage's row for the CURRENT run may already
    exist by the time this check runs. Strict-less-than makes the verdict identical whichever
    order the two happen to execute in."""
    try:
        coverage, joined, denominator, _ = _ledger_join_coverage_now(conn)
    except sqlite3.OperationalError as exc:
        return CheckResult(
            passed=False, observed_value=None,
            baseline_value=LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS,
            detail=f"v_ledger_join_coverage unavailable ({exc}) -- warehouse predates "
                   f"migration 4, drop NOT evaluated",
        )
    if coverage is None:
        return CheckResult(
            passed=True, observed_value=None,
            baseline_value=LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS,
            detail="no hook_verdict rows carry a tool_use_id yet -- nothing to diff, and said so "
                   "rather than reported as a zero drop",
        )

    # The prior row must belong to an OK ingest run, not merely to an earlier one. Added on
    # claude-hooks-v2-e5's review: every other run-over-run check in this module selects its
    # comparison points with `WHERE status='ok'`, and this lookup did not, so a failed or
    # abandoned run that still managed to record a dq_check_run row would have become the
    # baseline a drop is measured against. Unreachable today -- all 49 ingest runs are status ok
    # -- and the point is that it was an undocumented assumption rather than a stated one.
    prior = conn.execute(
        "SELECT r.run_id, r.observed_value FROM dq_check_run r "
        "JOIN ingest_run i ON i.run_id = r.run_id "
        "WHERE r.check_name = 'ledger_join_coverage' AND r.run_id < ? "
        f"AND r.observed_value IS NOT NULL AND ({SLOW_PLANE_ALIASED}) "
        "ORDER BY r.run_id DESC LIMIT 1", (run_id,)).fetchone()
    if prior is None:
        # Two different facts, separated rather than collapsed into one vacuous pass: there may be
        # no earlier run at all, or there may be earlier runs whose recorded observation was NULL
        # (check_ledger_join_coverage's own two degenerate cases). Neither is a zero drop.
        any_earlier = conn.execute(
            "SELECT COUNT(*) FROM dq_check_run r JOIN ingest_run i ON i.run_id = r.run_id "
            f"WHERE r.check_name = 'ledger_join_coverage' AND r.run_id < ? AND ({SLOW_PLANE_ALIASED})",
            (run_id,)).fetchone()[0]
        reason = ("no earlier ledger_join_coverage observation recorded -- first run after this "
                  "check was registered" if any_earlier == 0 else
                  f"{any_earlier} earlier ledger_join_coverage row(s) exist but every one "
                  f"recorded a NULL observation -- no comparable prior")
        return CheckResult(
            passed=True, observed_value=None,
            baseline_value=LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS,
            detail=f"drop NOT evaluated: {reason}",
        )

    prior_run_id, prior_coverage = prior
    drop_points = (prior_coverage - coverage) * 100
    return CheckResult(
        passed=(drop_points <= LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS),
        observed_value=round(drop_points, 3),
        baseline_value=LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS,
        detail=f"join coverage moved from {prior_coverage:.4f} at run {prior_run_id} to "
               f"{coverage:.4f} now ({-drop_points:+.3f} points, {joined:,} of {denominator:,}); "
               f"tolerance is a drop of {LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS} points. A drop "
               f"here does not by itself distinguish a joiner regression from a transcript "
               f"retention sweep -- both move rows into no-transcript-on-disk (section 26.3)",
    )


# ------------------------------------------------- session contract checks (ATLASSN-84, -85)

# ATLASSN-84, from the 2026-09-04 orchestrator-stall post-mortem section 7 item 2. The
# post-mortem's own words: ATLAS "has 21 DQ checks and none of them is 'a session with N tool
# calls and fewer than N hook verdicts,' which is the one check that would have named both
# sessions within a minute."
HOOK_COVERAGE_MIN_CALLS = 20      # CITED: post-mortem section 7 item 2, "more than 20 tool calls"
HOOK_COVERAGE_MIN_RATIO = 1.0     # CITED: same, "verdicts divided by calls below 1.0"


def verdict_stream_watermark(conn):
    """The latest hook_verdict actually ingested. Any clock hour extending past this is only
    PARTIALLY covered by the verdict stream, and a coverage ratio computed over it measures
    ingest lag rather than hook coverage.

    This is not a hypothetical. Measured on the live warehouse 2026-09-04: session_tool_call
    reached 16:16Z while hook_verdict reached 13:03Z, a lag of over three hours, and a naive
    "last hour" implementation of this check fired on 10 of 15 recent session-hours -- every one
    of them a healthy session whose verdicts had simply not landed yet. A check that flags
    two-thirds of healthy sessions is the "check nobody reads" R2 names, so the window is
    watermark-gated rather than clock-gated."""
    return conn.execute("SELECT MAX(ts) FROM hook_verdict").fetchone()[0]


def check_hook_coverage_per_session(conn, run_id):
    """Contract: a session doing real work is producing hook verdicts. For the most recent hour
    fully covered by the verdict stream, any session with more than HOOK_COVERAGE_MIN_CALLS tool
    calls and fewer than one verdict per call is named as a contract failure.

    Evaluates ONE hour -- the most recent complete, watermark-covered one -- rather than a
    rolling window, so the observed value means the same thing on every run. The cost is that a
    failure ages out of view once the hour does; the audit trail is dq_check_run's own history,
    which keeps a row per run either way."""
    watermark = verdict_stream_watermark(conn)
    if watermark is None:
        return CheckResult(
            passed=False, observed_value=None, baseline_value=HOOK_COVERAGE_MIN_RATIO,
            detail="hook_verdict is empty, so hook coverage cannot be evaluated at all -- "
                   "reported as a failure rather than a vacuous pass (this component's own F1 "
                   "signature: a guard that passes on a condition it cannot evaluate)",
        )

    # Strictly before the watermark's own hour: that hour is still being written into.
    evaluated_hour = conn.execute(
        "SELECT MAX(substr(ts,1,13)) FROM session_tool_call WHERE substr(ts,1,13) < ?",
        (watermark[:13],),
    ).fetchone()[0]
    if evaluated_hour is None:
        return CheckResult(
            passed=True, observed_value=0, baseline_value=HOOK_COVERAGE_MIN_RATIO,
            detail=f"no session_tool_call hour completed below the verdict watermark "
                   f"{watermark}; nothing evaluable this run",
        )

    rows = conn.execute(
        "SELECT tc.session_id, tc.calls, COALESCE(hv.verdicts, 0) "
        "FROM (SELECT session_id, COUNT(*) calls FROM session_tool_call "
        "      WHERE substr(ts,1,13) = ? GROUP BY session_id) tc "
        "LEFT JOIN (SELECT session_id, COUNT(*) verdicts FROM hook_verdict "
        "           WHERE substr(ts,1,13) = ? GROUP BY session_id) hv "
        "  ON hv.session_id = tc.session_id "
        "WHERE tc.calls > ?",
        (evaluated_hour, evaluated_hour, HOOK_COVERAGE_MIN_CALLS),
    ).fetchall()

    failing = []
    for session_id, calls, verdicts in rows:
        ratio = verdicts / calls
        if ratio < HOOK_COVERAGE_MIN_RATIO:
            failing.append((session_id, calls, verdicts, ratio))
    failing.sort(key=lambda item: item[3])

    named = ", ".join(
        f"{session_id} ({verdicts}/{calls} = {ratio:.2f})"
        for session_id, calls, verdicts, ratio in failing
    )
    return CheckResult(
        passed=(len(failing) == 0),
        observed_value=len(failing),
        baseline_value=0,
        detail=(
            f"hour {evaluated_hour} (verdict watermark {watermark}), "
            f"{len(rows)} session(s) above {HOOK_COVERAGE_MIN_CALLS} calls: "
            + (f"{len(failing)} below {HOOK_COVERAGE_MIN_RATIO} verdicts/call -- {named}"
               if failing else "all at or above one verdict per call")
        ),
    )


# ATLASSN-85, post-mortem section 7 item 5. Three corrections to the ticket's stated definition,
# each forced by the live data and each recorded here because a reader will otherwise assume the
# ticket text was implemented literally:
#
# 1. Scratchpad writes are NOT build signal. 48% of .py write/edit calls on 2026-09-04 targeted
#    /private/tmp/.../scratchpad/, which is throwaway exploration. Counting them would credit a
#    session that wrote no product code at all as building, which inverts the metric.
# 2. "A declared build run" has no representation in this schema -- there is no session-kind,
#    run-kind or intent column anywhere, and dim_session is empty. The scope predicate is
#    therefore an activity floor, not a declaration, and that substitution is a real weakening
#    of the ticket's intent rather than an implementation detail.
# 3. git_commit carries no session_id, so a commit cannot be attributed to the session that made
#    it. Commit signal is taken from the session's own Bash calls instead.
BUILD_SHARE_MIN_SIGNAL_CALLS = 5   # floor below which an hour is too quiet to judge
BUILD_SHARE_CONSECUTIVE_HOURS = 2  # CITED: ticket, "zero for two consecutive hours"

# Unbounded, this check reports every zero-build streak in the corpus's whole history and never
# returns to green: measured against the live warehouse it found 50 sessions going back to
# 2026-08-26, which is a permanently-red row nobody would read twice -- R2's "a check nobody
# reads" arriving by a different route than the usual one. The window makes the observed value
# mean "right now" the way every other check here does. 48 rather than 24 hours so a streak that
# straddles a day boundary is still visible on the following day.
BUILD_SHARE_LOOKBACK_HOURS = 48

SCRATCH_PATH_FRAGMENTS = ("/private/tmp/", "/tmp/", "/scratchpad/")


def build_share_signal_sql():
    """One SQL expression classifying each tool call as build, process, or neither. Kept as a
    single function so the two callers (the checker and its test) cannot drift onto different
    definitions of what counts as building."""
    scratch = " OR ".join(
        f"json_extract(tool_input_json,'$.file_path') LIKE '%{fragment}%'"
        for fragment in SCRATCH_PATH_FRAGMENTS
    )
    return f"""
        CASE
          WHEN tool_name IN ('Write','Edit','MultiEdit')
               AND json_extract(tool_input_json,'$.file_path') LIKE '%.py'
               AND NOT ({scratch}) THEN 'build'
          WHEN tool_name = 'Bash'
               AND json_extract(tool_input_json,'$.command') LIKE '%git commit%' THEN 'build'
          WHEN tool_name IN ('Write','Edit','MultiEdit')
               AND json_extract(tool_input_json,'$.file_path') LIKE '%.md' THEN 'process'
          WHEN tool_name = 'Bash'
               AND json_extract(tool_input_json,'$.command') LIKE '%tessera.api.cli%' THEN 'process'
          WHEN tool_name = 'SendMessage' THEN 'process'
          ELSE 'neither'
        END
    """


def check_build_process_ratio(conn, run_id):
    """Contract: a session producing process artifacts hour after hour with zero build signal is
    reported. Fails when any session has BUILD_SHARE_CONSECUTIVE_HOURS consecutive hours of at
    least BUILD_SHARE_MIN_SIGNAL_CALLS classified calls and zero build signal in every one.

    This does NOT reproduce the acceptance case written on ATLASSN-85 ("would have fired at
    00:00 on 2026-09-04 and every hour after"). That case was measured against a warehouse whose
    git ingest had not caught up: ingest run 51 landed 21 commits timestamped 04:40 to 05:33 at
    07:10Z, six minutes after the post-mortem's stated 07:04Z data horizon, and those commits
    include 22 .py files. The window the ticket calls a no-build window is populated in the data
    this check reads. Reported rather than reconciled by loosening the metric until it fires."""
    latest = conn.execute("SELECT MAX(ts) FROM session_tool_call").fetchone()[0]
    if latest is None:
        return CheckResult(
            passed=True, observed_value=0, baseline_value=0,
            detail="session_tool_call is empty; no build-share window to evaluate",
        )
    window_start = (
        datetime.datetime.strptime(latest[:13], "%Y-%m-%dT%H")
        - datetime.timedelta(hours=BUILD_SHARE_LOOKBACK_HOURS)
    ).strftime("%Y-%m-%dT%H")

    classified = conn.execute(
        f"SELECT substr(ts,1,13) hour, session_id, {build_share_signal_sql()} kind, COUNT(*) n "
        f"FROM session_tool_call WHERE substr(ts,1,13) >= ? GROUP BY hour, session_id, kind",
        (window_start,),
    ).fetchall()

    totals = {}
    for hour, session_id, kind, count in classified:
        slot = totals.setdefault((session_id, hour), {"build": 0, "process": 0})
        if kind in slot:
            slot[kind] += count

    zero_build_hours = {}
    for (session_id, hour), slot in totals.items():
        if slot["build"] + slot["process"] < BUILD_SHARE_MIN_SIGNAL_CALLS:
            continue
        if slot["build"] == 0:
            zero_build_hours.setdefault(session_id, []).append(hour)

    paged = []
    for session_id, hours in zero_build_hours.items():
        hours.sort()
        run = [hours[0]]
        for previous, current in zip(hours, hours[1:]):
            if _hours_adjacent(previous, current):
                run.append(current)
            else:
                if len(run) >= BUILD_SHARE_CONSECUTIVE_HOURS:
                    paged.append((session_id, run[0], run[-1], len(run)))
                run = [current]
        if len(run) >= BUILD_SHARE_CONSECUTIVE_HOURS:
            paged.append((session_id, run[0], run[-1], len(run)))

    paged.sort(key=lambda item: (-item[3], item[0]))
    named = "; ".join(
        f"{session_id} {first}..{last} ({length}h)" for session_id, first, last, length in paged[:5]
    )
    return CheckResult(
        passed=(len(paged) == 0),
        observed_value=len(paged),
        baseline_value=0,
        detail=(
            f"{len(paged)} session(s) with {BUILD_SHARE_CONSECUTIVE_HOURS}+ consecutive hours of "
            f"zero build signal over {BUILD_SHARE_MIN_SIGNAL_CALLS}+ classified calls"
            + (f": {named}" if paged else "")
            + ". Scope is an activity floor, not a declared build run -- no such declaration "
              "exists in this schema"
        ),
    )


def _hours_adjacent(previous, current):
    """True when two 'YYYY-MM-DDTHH' strings are one hour apart. Compares real timestamps rather
    than the hour field alone, so 2026-09-04T23 -> 2026-09-05T00 is adjacent and
    2026-09-04T09 -> 2026-09-05T10 is not, which naive integer arithmetic on the last two
    characters gets wrong in both directions."""
    fmt = "%Y-%m-%dT%H"
    start = datetime.datetime.strptime(previous, fmt)
    end = datetime.datetime.strptime(current, fmt)
    return (end - start) == datetime.timedelta(hours=1)


# ------------------------------------------- turn-final / SendMessage byte deltas (ATLASSN-88)

# HOOKS-VS-PDP-AND-SUMMARY-BLOAT-20260904.md Part 2 item 6. Acceptance signal for CHV2-5's
# delivery mechanisms (CHV2-7/CHV2-12/CHV2-13/CHV2-14): "both series fall after those land, and
# the .md/comment bytes do not rise to compensate." Only the two named series are built here --
# the .md/comment half of that sentence is not a check this ticket names, and adding one would be
# scope this ticket did not freeze.
#
# Both are DELTA checks, on the resolution_rate_delta / fail_open_pairing_delta /
# ledger_join_coverage_delta precedent: there is no floor to gate on before CHV2-5 lands, only a
# direction, so each fails ONLY on a day-over-day INCREASE in average bytes per session. lean, not
# calibrated -- CHECKERS has never produced a dq_check_run history for either name, so there is no
# distribution to derive a tolerance from, and inventing one now would be the "floor set to pass"
# defect check_project_resolution_floor's own comments already name. Zero tolerance, exactly like
# fail_open_pairing_delta's own delta <= 0.
TURN_FINAL_GAP_SECONDS = 60  # CITED: source doc, "no tool call within 60s after"


def _complete_day_pair(conn, table, ts_column="ts", extra_where=None):
    """The two most recent COMPLETE UTC days ('YYYY-MM-DD') carrying a row in `table` (matching
    extra_where, if given), latest first. 'Complete' means a later day's row exists in the same
    table -- the same 'today is still accumulating' guard hook_coverage_per_session's watermark
    and build_process_ratio's window both apply, so a still-open day is never compared against a
    finished one. Returns (latest_complete_day, prior_complete_day); either is None when fewer
    than the corresponding number of complete days exist."""
    where = f"WHERE {ts_column} IS NOT NULL" + (f" AND {extra_where}" if extra_where else "")
    days = [r[0] for r in conn.execute(
        f"SELECT DISTINCT substr({ts_column},1,10) AS d FROM {table} {where} ORDER BY d DESC"
    ).fetchall()]
    if len(days) < 2:
        return None, None
    if len(days) == 2:
        return days[1], None
    return days[1], days[2]


def _turn_final_day_stats(conn, day):
    """(total turn-final bytes, distinct sessions with at least one turn-final block) for one
    UTC day. An assistant text block is turn-final when no session_tool_call sharing its
    transcript_id has a ts within TURN_FINAL_GAP_SECONDS after it.

    The gap is computed as a SUBTRACTION on both sides of each comparison
    (strftime(...) - strftime(...)), never strftime(...) <= strftime(...) + N. SQLite's strftime
    returns TEXT; TEXT + N forces the right side to INTEGER while the bare left side stays TEXT,
    and SQLite's type-ordering rule (TEXT always sorts above INTEGER/REAL, regardless of the
    digits) makes that comparison silently false for every real pair of timestamps -- measured
    directly: 30 seconds apart evaluated as NOT within the 60-second window. Subtracting on both
    sides forces INTEGER on both, which is what compares correctly against a literal bound."""
    row = conn.execute(
        "SELECT COALESCE(SUM(a.text_bytes), 0), COUNT(DISTINCT t.session_id) "
        "FROM session_assistant_text a "
        "JOIN session_transcript t ON t.transcript_id = a.transcript_id "
        "WHERE substr(a.ts,1,10) = ? AND a.ts IS NOT NULL "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM session_tool_call c "
        "  WHERE c.transcript_id = a.transcript_id AND c.ts IS NOT NULL "
        "  AND strftime('%s', c.ts) - strftime('%s', a.ts) > 0 "
        f"  AND strftime('%s', c.ts) - strftime('%s', a.ts) <= {TURN_FINAL_GAP_SECONDS}"
        ")",
        (day,),
    ).fetchone()
    return row[0], row[1]


def check_turn_final_bytes_per_session_day(conn, run_id):
    """ATLASSN-88. Average turn-final bytes per session, most recent complete UTC day versus the
    day before it. Fails only when the average INCREASED -- see the module-level note above for
    why this is a delta check with a zero, uncalibrated tolerance rather than a level against an
    invented floor.

    DEPARTURE FROM A LITERAL 'PER SESSION PER DAY' SERIES, named rather than left implicit: a
    single dq_check_run row carries one observed_value, so this reports the FLEET AVERAGE for the
    day (total bytes / distinct sessions with any turn-final block), not one row per session.
    CORRECTION (ATLASSN-90): earlier text here and in ARCHITECTURE.md section 30.2 claimed the
    per-session breakdown lives in `detail`'s top-5 list. It does not -- no branch below builds
    one; `detail` carries the same fleet-average sentence as observed_value, nothing more. A day
    with far fewer active sessions than its comparison day can move this average without any
    single session's behavior changing -- the same denominator hazard build_process_ratio's own
    'activity floor, not a declaration' departure already names for a different check."""
    latest_day, prior_day = _complete_day_pair(conn, "session_assistant_text")
    if latest_day is None:
        return CheckResult(
            True, None, None,
            "fewer than 2 distinct UTC days in session_assistant_text -- nothing evaluable yet",
        )
    latest_bytes, latest_sessions = _turn_final_day_stats(conn, latest_day)
    if latest_sessions == 0:
        return CheckResult(True, None, None, f"no turn-final blocks on {latest_day}")
    latest_avg = latest_bytes / latest_sessions

    if prior_day is None:
        return CheckResult(
            True, round(latest_avg, 1), None,
            f"{latest_day}: {latest_bytes:,} turn-final bytes across {latest_sessions} "
            f"session(s), avg {latest_avg:,.1f} bytes/session; no earlier complete day to "
            f"compare against yet",
        )
    prior_bytes, prior_sessions = _turn_final_day_stats(conn, prior_day)
    if prior_sessions == 0:
        return CheckResult(
            True, round(latest_avg, 1), None,
            f"{latest_day} avg {latest_avg:,.1f} bytes/session; {prior_day} had no turn-final "
            f"blocks, so the delta is not evaluable",
        )
    prior_avg = prior_bytes / prior_sessions
    delta = latest_avg - prior_avg
    return CheckResult(
        passed=(delta <= 0),
        observed_value=round(delta, 1),
        baseline_value=0,
        detail=f"avg turn-final bytes/session moved from {prior_avg:,.1f} ({prior_day}, "
        f"{prior_sessions} sessions) to {latest_avg:,.1f} ({latest_day}, {latest_sessions} "
        f"sessions), {delta:+,.1f}",
    )


def check_sendmessage_bytes_per_session_day(conn, run_id):
    """ATLASSN-88. Companion delta to check_turn_final_bytes_per_session_day: average SendMessage
    bytes per session, most recent complete UTC day versus the day before it. tool_input_bytes is
    the serialized size of the whole SendMessage call as ingested, the same quantity the source
    doc's 'SendMessage bodies' figure measured -- not just the message field alone. Same departure
    as the turn-final check: one observed_value is the fleet average for the day, not a
    per-session row, and (ATLASSN-90 correction) `detail` carries no per-session breakdown either
    -- see check_turn_final_bytes_per_session_day's docstring for the full correction."""
    latest_day, prior_day = _complete_day_pair(
        conn, "session_tool_call", extra_where="tool_name = 'SendMessage'"
    )
    if latest_day is None:
        return CheckResult(
            True, None, None,
            "fewer than 2 distinct UTC days of SendMessage calls in session_tool_call -- "
            "nothing evaluable yet",
        )

    def stats(day):
        row = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(tool_input_bytes, 0)), 0), "
            "COUNT(DISTINCT session_id) FROM session_tool_call "
            "WHERE tool_name = 'SendMessage' AND substr(ts,1,10) = ? AND ts IS NOT NULL "
            "AND session_id IS NOT NULL",
            (day,),
        ).fetchone()
        return row[0], row[1]

    latest_bytes, latest_sessions = stats(latest_day)
    if latest_sessions == 0:
        return CheckResult(True, None, None, f"no SendMessage calls with a session_id on {latest_day}")
    latest_avg = latest_bytes / latest_sessions

    if prior_day is None:
        return CheckResult(
            True, round(latest_avg, 1), None,
            f"{latest_day}: {latest_bytes:,} SendMessage bytes across {latest_sessions} "
            f"session(s), avg {latest_avg:,.1f} bytes/session; no earlier complete day to "
            f"compare against yet",
        )
    prior_bytes, prior_sessions = stats(prior_day)
    if prior_sessions == 0:
        return CheckResult(
            True, round(latest_avg, 1), None,
            f"{latest_day} avg {latest_avg:,.1f} bytes/session; {prior_day} had no SendMessage "
            f"calls with a session_id, so the delta is not evaluable",
        )
    prior_avg = prior_bytes / prior_sessions
    delta = latest_avg - prior_avg
    return CheckResult(
        passed=(delta <= 0),
        observed_value=round(delta, 1),
        baseline_value=0,
        detail=f"avg SendMessage bytes/session moved from {prior_avg:,.1f} ({prior_day}, "
        f"{prior_sessions} sessions) to {latest_avg:,.1f} ({latest_day}, {latest_sessions} "
        f"sessions), {delta:+,.1f}",
    )


CHECKERS = {
    "hook_coverage_per_session": check_hook_coverage_per_session,
    "build_process_ratio": check_build_process_ratio,
    "turn_final_bytes_per_session_day_delta": check_turn_final_bytes_per_session_day,
    "sendmessage_bytes_per_session_day_delta": check_sendmessage_bytes_per_session_day,
    "ledger_join_coverage": check_ledger_join_coverage,
    "ledger_join_coverage_delta": check_ledger_join_coverage_delta,
    "verdict_domain_closed": check_verdict_domain_closed,
    "stolen_implies_target": check_stolen_implies_target,
    "decision_domain_closed": check_decision_domain_closed,
    "watermark_le_filesize": check_watermark_le_filesize,
    # ATLASSN-102, migration 17. The two calls_ingested checks carry contract severity and
    # the two watermark checks are advisory -- see ARCHITECTURE.md section 33.3 for why the
    # filesystem-reading pair must not be the one that can close the facade.
    "session_calls_ingested_matches": check_session_calls_ingested_matches,
    "subagent_calls_ingested_matches": check_subagent_calls_ingested_matches,
    # ATLASSN-143 decision (A), comment 2594. Landed ahead of its dq_check seed row on purpose
    # (code-before-DDL, ARCHITECTURE.md 26.2/29.4) -- inert until migration 18 seeds the row;
    # see test_no_registered_checker_is_left_without_a_seed_row for the expected-red marker.
    "session_tool_call_evidence_attrs_present": check_session_tool_call_evidence_attrs_present,
    "session_watermark_le_filesize": check_session_watermark_le_filesize,
    "subagent_watermark_le_filesize": check_subagent_watermark_le_filesize,
    "fail_open_not_double_counted": check_fail_open_not_double_counted,
    "project_resolution_floor": check_project_resolution_floor,
    "handler_denominator_nonzero": check_handler_denominator_nonzero,
    "audit_envelope_wellformed": check_audit_envelope_wellformed,
    "audit_payload_size_bounded": check_audit_payload_size_bounded,
    "audit_payload_credential_scan": check_audit_payload_credential_scan,
    "audit_ledger_partition_by_cwd": check_audit_ledger_partition_by_cwd,
    "tessera_event_id_unique": check_tessera_event_id_unique,
    "git_ticket_prefix_registered": check_git_ticket_prefix_registered,
    "ingest_rows_match_bytes": check_ingest_rows_match_bytes,
    "ingest_rows_monotonic": check_ingest_rows_monotonic,
    "resolution_rate_delta": check_resolution_rate_delta,
    "fail_open_pairing_delta": check_fail_open_pairing_delta,
    "handler_set_stable": check_handler_set_stable,
    "watermark_advanced": check_watermark_advanced,
    "snapshot_ticket_rollup_identity": check_snapshot_ticket_rollup_identity,
}


class UnregisteredCheckError(RuntimeError):
    pass


def run_all(conn, run_id, ticket_rollup=None):
    """Evaluates every check_name in dq_check against CHECKERS, writes one dq_check_run row
    each. Raises UnregisteredCheckError -- does not silently skip -- if dq_check names a check
    with no registered function (this component's own C7 criterion, F1 failure signature)."""
    check_names = [r[0] for r in conn.execute("SELECT check_name FROM dq_check")]
    missing = [name for name in check_names if name not in CHECKERS]
    if missing:
        raise UnregisteredCheckError(
            f"dq_check names {missing} with no registered checker function in CHECKERS -- "
            f"refusing to silently skip them"
        )

    results = {}
    now = _now()
    for name in check_names:
        fn = CHECKERS[name]
        if name == "snapshot_ticket_rollup_identity":
            result = fn(conn, run_id, ticket_rollup=ticket_rollup)
        else:
            result = fn(conn, run_id)
        conn.execute(
            "INSERT INTO dq_check_run (run_id, check_name, run_at, observed_value, "
            "baseline_value, passed, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, name, now,
                result.observed_value if isinstance(result.observed_value, (int, float)) else None,
                result.baseline_value if isinstance(result.baseline_value, (int, float)) else None,
                1 if result.passed else 0,
                result.detail,
            ),
        )
        results[name] = result
    conn.commit()
    return results
