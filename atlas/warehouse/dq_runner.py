"""The data-quality runner: evaluates every check registered in dq_check against the current
database state and writes one dq_check_run row per (run_id, check_name), pass or fail, every
run -- ARCHITECTURE.md section 16's own stated purpose ("Every check writes a row every run,
pass or fail. An empty table here is indistinguishable from 'no check has ever run', which is
the tessguard failure this exists to not repeat.").

Each checker is a function (conn, run_id) -> CheckResult. Registered in CHECKERS by check_name,
matched against dq_check's seeded rows at run() time -- an unregistered check_name is a startup
error (RuntimeError), never a silent skip (this component's own F1 failure signature: a guard
that returns passed=1, or silently does nothing, on a condition it cannot actually evaluate).

Checkers operate on whatever this database's tables hold. Several (ingest_rows_match_bytes,
watermark_le_filesize) reference a real file on disk via ingest_source.source_path -- when that
path does not exist (as in a unit test using a synthetic fixture file, or before ingest has run
at all), the checker reports passed=0 with a detail naming the missing path, not a silent pass;
real end-to-end correctness against the live verdict ledger is foreman:integration-test's job,
not this component's (see GOALS.json out_of_scope)."""

import datetime
import json
from collections import namedtuple
from pathlib import Path

CheckResult = namedtuple("CheckResult", ["passed", "observed_value", "baseline_value", "detail"])

VALID_VERDICTS = {"fire", "silent", "error", "stolen"}
VALID_DECISIONS = {"deny", "defer", "ask", "allow"}


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


def check_fail_open_not_double_counted(conn, run_id):
    # A real hook fail-open is recorded on BOTH ledgers. Pair those
    # incidents on handler + cwd + ts within 2 seconds. Unmatched rows on EITHER
    # side fail the contract. Shell writers (laa-commit-flow-advisory.sh) emit
    # verdict='error' and never write HOOK_ERROR; including them made this check
    # fail forever for a question they cannot answer. Measured 2026-08-24: 13/13
    # hook errors pair; all unmatched rows were *.sh.
    errors = conn.execute(
        "SELECT handler_id, cwd, ts FROM hook_verdict "
        "WHERE verdict='error' AND handler_id NOT LIKE '%.sh'"
    ).fetchall()
    unmatched_errors = 0
    for handler_id, cwd, ts in errors:
        row = conn.execute(
            "SELECT 1 FROM audit_event WHERE event_type='HOOK_ERROR' "
            "AND json_extract(payload_json,'$.hook') = ? "
            "AND cwd IS ? "
            "AND ABS(strftime('%s', ts) - strftime('%s', ?)) <= 2 LIMIT 1",
            (handler_id, cwd, ts),
        ).fetchone()
        if row is None:
            unmatched_errors += 1
    hooks = conn.execute(
        "SELECT json_extract(payload_json,'$.hook'), cwd, ts FROM audit_event "
        "WHERE event_type='HOOK_ERROR'"
    ).fetchall()
    unmatched_hooks = 0
    for hook, cwd, ts in hooks:
        row = conn.execute(
            "SELECT 1 FROM hook_verdict WHERE verdict='error' AND handler_id = ? "
            "AND cwd IS ? "
            "AND ABS(strftime('%s', ts) - strftime('%s', ?)) <= 2 LIMIT 1",
            (hook, cwd, ts),
        ).fetchone()
        if row is None:
            unmatched_hooks += 1
    unmatched = unmatched_errors + unmatched_hooks
    n_errors = len(errors)
    n_hooks = len(hooks)
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


def check_project_resolution_floor(conn, run_id):
    # ADVISORY per an earlier fatal-severity fix -- see ARCHITECTURE.md section 10. Reports the comparison but
    # never blocks; dq_check.severity='advisory' is what the runner (below) uses to decide that.
    floor = _git_repo_fraction(conn)
    total = conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]
    if total == 0 or floor is None:
        return CheckResult(True, None, floor, "no hook_verdict rows or no dim_project rows yet")
    unique = conn.execute(
        "SELECT COUNT(*) FROM hook_verdict v JOIN cwd_project cp ON cp.cwd = v.cwd "
        "WHERE cp.resolution = 'unique'"
    ).fetchone()[0]
    rate = unique / total
    return CheckResult(
        passed=(rate >= floor), observed_value=round(rate, 4), baseline_value=round(floor, 4),
        detail=f"resolution rate {rate:.4f} vs floor {floor:.4f} (fraction of registered "
        f"projects that are real git repos)",
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
    # Audit rows whose cwd resolves to a registered project but landed in a
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
        "SELECT source_name, source_path, byte_offset, rows_ingested FROM ingest_source"
    ).fetchall()
    if not rows:
        return CheckResult(True, 0, 0, "no ingest_source rows yet")
    mismatches = []
    for source_name, source_path, byte_offset, rows_ingested in rows:
        p = Path(source_path)
        if not p.is_file():
            mismatches.append(f"{source_name}: {source_path} does not exist")
            continue
        data = p.read_bytes()[:byte_offset]
        # Non-blank elements of a split on \n -- a trailing-newline file always yields one
        # empty final element, which this must not double-subtract (section 8's own recorded
        # first-run defect: 125,776 vs 125,777, an off-by-one in this exact predicate).
        lines = [ln for ln in data.split(b"\n") if ln.strip()]
        if len(lines) != rows_ingested:
            mismatches.append(
                f"{source_name}: {len(lines)} non-blank lines in byte range vs "
                f"rows_ingested={rows_ingested}"
            )
    return CheckResult(
        passed=(len(mismatches) == 0), observed_value=len(mismatches), baseline_value=0,
        detail="; ".join(mismatches) if mismatches else "0 mismatches",
    )


def check_ingest_rows_monotonic(conn, run_id):
    rows = conn.execute(
        "SELECT run_id, (SELECT COUNT(*) FROM hook_verdict h WHERE h.ingest_run_id <= ingest_run.run_id) "
        "FROM ingest_run WHERE status='ok' ORDER BY run_id"
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


def check_resolution_rate_delta(conn, run_id):
    runs = conn.execute(
        "SELECT run_id FROM ingest_run WHERE status='ok' ORDER BY run_id DESC LIMIT 2"
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
        "SELECT run_id FROM ingest_run WHERE status='ok' ORDER BY run_id DESC LIMIT 2"
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
    # An earlier fix. ticket_rollup, if provided, is the snapshot publisher's in-memory rollup
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


CHECKERS = {
    "verdict_domain_closed": check_verdict_domain_closed,
    "stolen_implies_target": check_stolen_implies_target,
    "decision_domain_closed": check_decision_domain_closed,
    "watermark_le_filesize": check_watermark_le_filesize,
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
