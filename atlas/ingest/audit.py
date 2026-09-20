"""Maps a raw audit-plane JSON object (safety.jsonl, misalignment-marker-search/audit.jsonl) to
an audit_event row and inserts it. Mirrors verdicts.py's pattern, using the same source-agnostic
stream.tail() machinery -- ARCHITECTURE.md section 4 names audit-plane as one of ingest's two
append-only sources; this file is the row mapper that was missing (found during integration
testing: stream.tail() was always source-agnostic and audit-plane-ready by design, but no
audit-plane-specific mapper had actually been written yet)."""

import json


def make_row_mapper(source_path):
    """stream.tail()'s row_mapper contract is (json_obj, raw_line) -> dict; source_path is
    fixed per ingest call (one file per tail() invocation), so it's bound here via closure
    rather than threaded through the generic tail() signature, which has no source_path
    parameter of its own -- source_path is the stream's own path, known to the caller, not
    part of a single line's content."""

    def map_audit_row(obj, raw_line):
        """Returns an audit_event-shaped dict, or None if a required field (ts, event_type) is
        missing -- payload_json/payload_bytes are derived from the raw line itself, not from
        the parsed object, so they reflect exactly what was on disk (section 1: 'Largest single
        line: 682,196 bytes... carrying verbatim shell command text')."""
        ts = obj.get("ts")
        event_type = obj.get("event_type")
        if not ts or not event_type:
            return None
        return {
            "source_path": source_path,
            "ledger_claim": obj.get("ledger"),
            "schema_version": obj.get("schema_version"),
            "ts": ts,
            "event_type": event_type,
            "severity": obj.get("severity"),
            "session_id": obj.get("session_id"),
            "cwd": obj.get("cwd"),
            "payload_json": json.dumps(obj),
            "payload_bytes": len(raw_line),
        }

    return map_audit_row


def insert_audit_event_row(conn, stream_id, byte_offset, row, ingest_run_id):
    cols = ["stream_id", "byte_offset", "ingest_run_id"] + list(row.keys())
    values = [stream_id, byte_offset, ingest_run_id] + list(row.values())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    conn.execute(
        f"INSERT INTO audit_event ({col_list}) VALUES ({placeholders})",
        values,
    )
