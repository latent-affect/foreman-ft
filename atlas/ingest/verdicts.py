"""Maps a raw verdicts.jsonl JSON object to a hook_verdict row dict, and inserts it.

ts_resolution derivation is MAJOR-6/MAJOR-7's fix (ARCHITECTURE-REVIEW.md): the naive rule
"'.' in ts" mislabels 15,932 real rows as 'microsecond' when the two shell writers
(session-log.sh, laa-commit-flow-advisory.sh) emit a fake-precision literal '.000000Z' suffix
with real second-resolution timestamps underneath. The correct rule is epoch_ms % 1000 == 0
combined with which writer produced the row -- verdict_ledger.py-produced rows are genuinely
microsecond-resolution even on the rare occasion their epoch_ms happens to be a round thousand.
"""

SHELL_WRITER_HANDLERS = {"session-log.sh", "laa-commit-flow-advisory.sh"}

HOOK_VERDICT_COLUMNS = {
    "ts", "epoch_ms", "handler_id", "hook_event", "verdict", "kind", "session_id", "cwd",
    "tool_name", "tool_use_id", "self_duration_ms", "target", "decision", "rule_id",
    "probe_id", "run_id",
    # ATLASSN-62 / FORE-273. Writer identity, stamped by both claude-hooks-v2 verdict writers.
    # NOISE FILTER, NOT ATTRIBUTION: ledger_origin='harness-heuristic' is forgeable by any local
    # process that sets CLAUDE_PID before spawning a descendant (FORE-291), which is why the value
    # is named for the strength of its own claim. No security or evasion-detection query may read
    # it as authentication. origin_signal carries the evidence for the classification so a
    # misclassification is diagnosable from the warehouse rather than re-argued.
    "ledger_origin", "origin_signal",
}


def derive_ts_resolution(handler_id, epoch_ms):
    """Returns 'second' only for a row from a known shell writer whose epoch_ms is a whole
    multiple of 1000 -- the fake-precision case. Everything else, including a verdict_ledger.py
    row whose epoch_ms happens to be round, is 'microsecond': that writer's ts string genuinely
    carries microsecond precision regardless of what epoch_ms happens to be."""
    if handler_id in SHELL_WRITER_HANDLERS and epoch_ms is not None and epoch_ms % 1000 == 0:
        return "second"
    return "microsecond"


def map_verdict_row(obj, raw_line=None):
    """raw_line is accepted for interface parity with stream.tail()'s row_mapper contract
    (audit.map_audit_row needs it for payload_bytes; hook_verdict has no equivalent column, so
    this mapper simply does not use it).

    Returns a hook_verdict-shaped dict, or None if the object lacks a required field
    (ts, handler_id, verdict are NOT NULL per ARCHITECTURE.md section 16) -- those rows are
    real malformed input, distinct from a JSON parse failure, and are treated the same way:
    skipped, not fatal to the pass. An unrecognized extra key in obj is simply ignored (not
    copied anywhere) rather than rejecting the row -- section 4's 'an unknown field is
    preserved' means the KNOWN fields are not lost because of it, not that hook_verdict has
    a catch-all column."""
    ts = obj.get("ts")
    handler_id = obj.get("handler_id")
    verdict = obj.get("verdict")
    if not ts or not handler_id or not verdict:
        return None

    epoch_ms = obj.get("epoch_ms")
    row = {
        "ts": ts,
        "epoch_ms": epoch_ms,
        "ts_resolution": derive_ts_resolution(handler_id, epoch_ms),
        "handler_id": handler_id,
        "hook_event": obj.get("event"),
        "verdict": verdict,
        "kind": obj.get("kind"),
        "session_id": obj.get("session_id"),
        "cwd": obj.get("cwd"),
        "tool_name": obj.get("tool_name"),
        "tool_use_id": obj.get("tool_use_id"),
        "self_duration_ms": obj.get("self_duration_ms"),
        "target": obj.get("target"),
        "decision": obj.get("decision"),
        "rule_id": obj.get("rule_id"),
        "probe_id": obj.get("probe_id"),
        "run_id": obj.get("run_id"),
        # Absent on any row written before the upstream writers carried these fields. That maps to
        # SQL NULL and means "this row predates the field", which is a different fact from
        # "origin unknown" -- the writers spell the latter 'unknown' explicitly.
        "ledger_origin": obj.get("ledger_origin"),
        "origin_signal": obj.get("origin_signal"),
    }
    return row


def insert_hook_verdict_row(conn, stream_id, byte_offset, row, ingest_run_id):
    # Plain INSERT, deliberately not OR IGNORE: (stream_id, byte_offset) colliding with an
    # existing row means the offset-tracking in stream.tail() re-read bytes it already
    # committed -- a real bug that should raise and fail a test loudly, not be silently
    # deduplicated away into a passing test that shouldn't pass.
    cols = ["stream_id", "byte_offset", "ingest_run_id"] + list(row.keys())
    values = [stream_id, byte_offset, ingest_run_id] + list(row.values())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    conn.execute(
        f"INSERT INTO hook_verdict ({col_list}) VALUES ({placeholders})",
        values,
    )
