"""Sits between audit.py's make_row_mapper (unchanged) and insert_audit_event_row (unchanged
signature) -- PRD.md R8, atlas/GOALS.json (DEVH-13). Redacts every CREDENTIAL_PATTERNS match
from payload_json before a row ever reaches the live warehouse, verified by reading the row
back from a staging temp table rather than trusting the redaction function's own return value --
the exact gap the architecture's toy model found live (a scrub whose return value is clean while
what actually gets persisted is not).

WHY A TEMP TABLE NAMED audit_event, NOT A DIFFERENTLY-NAMED STAGING TABLE
--------------------------------------------------------------------------
SQLite resolves an unqualified table reference to a same-connection TEMP table in preference to
a same-named table in the main database (verified empirically, not assumed -- a temp table
shadows main.audit_event for any unqualified statement on this connection). insert_audit_event_row
keeps its exact existing signature and SQL ("INSERT INTO audit_event ..."), unmodified, and still
lands its row -- just into the shadowing staging table, never directly into the live one. The
final publish step targets main.audit_event explicitly to escape the shadow. This is intra-atlas
only (no new cross-component interface): both files live under atlas/**.

WHY PER-VALUE REDACTION, NOT RAW-TEXT SUBSTITUTION ON payload_json
--------------------------------------------------------------------------
check_audit_payload_credential_scan (dq_runner.py) detects by regexing the raw payload_json
text directly. Redacting that same way was tried first and found to corrupt JSON for real,
non-hypothetical inputs: literal_export_assignment and generic_secret_keyword_assignment both
use \\S+ / \\S{12,}, which is greedy across a JSON string's own closing quote when applied to
the already-encoded text (verified empirically against the project's own recorded incident
shape, "export GEMINI_API_KEY=..."). Parsing payload_json, redacting each string value found at
any depth, and re-serializing is structurally immune to that failure -- json.dumps() cannot
produce invalid JSON from a valid Python structure -- while still removing the same matched
substrings, so the final raw text still shows zero pattern hits to a raw-text checker like
check_audit_payload_credential_scan.

FAIL-CLOSED AT ONE LINE, PER atlas/GOALS.json's design_decision
--------------------------------------------------------------------------
insert_row (returned by make_scrubbing_insert) never raises. A failure anywhere in one line's
scrub-stage-verify-publish chain drops that line, writes zero rows for it, and records it on the
caller-supplied DropTracker -- stream.tail()'s per-line loop always continues to the next line.
The caller (ingest_audit_source) is the one that turns a nonzero drop count into a reported,
non-zero-exit failure; this module never reports success while silently having dropped a line.
"""

import json
import re
from pathlib import Path

from atlas.ingest import stream
from atlas.ingest.audit import insert_audit_event_row, make_row_mapper
from atlas.warehouse.dq_runner import CREDENTIAL_PATTERNS

STAGING_DDL = """
CREATE TEMP TABLE IF NOT EXISTS audit_event (
    stream_id        TEXT NOT NULL,
    byte_offset      INTEGER NOT NULL,
    ingest_run_id    INTEGER,
    source_path      TEXT,
    ledger_claim     TEXT,
    schema_version   TEXT,
    ts               TEXT,
    event_type       TEXT,
    severity         TEXT,
    session_id       TEXT,
    cwd              TEXT,
    payload_json     TEXT,
    payload_bytes    INTEGER,
    payload_redacted INTEGER,
    PRIMARY KEY (stream_id, byte_offset)
)
"""

_PUBLISH_COLUMNS = [
    "source_path", "ledger_claim", "schema_version", "ts", "event_type", "severity",
    "session_id", "cwd", "payload_json", "payload_bytes", "payload_redacted",
]


class ScrubVerificationError(RuntimeError):
    """Raised anywhere in one line's scrub-stage-verify-publish chain. Always caught by
    insert_row -- never escapes to stream.tail()'s per-line loop."""


class DropTracker:
    """Accumulates one line's worth of fail-closed drops across a whole stream.tail() call.
    The caller inspects dropped_count AFTER stream.tail() returns; this is the mechanism that
    lets a per-line-atomic design still make record loss non-silent at the run level."""

    def __init__(self):
        self.dropped_count = 0
        self.drops = []

    def record_drop(self, stream_id, byte_offset, reason):
        self.dropped_count += 1
        self.drops.append({"stream_id": stream_id, "byte_offset": byte_offset, "reason": reason})


def _still_matches_any_pattern(text):
    """Returns the first pattern name still matching `text`, or None. Imported CREDENTIAL_PATTERNS,
    never copied -- an eleventh pattern added to dq_runner.py is picked up automatically here too."""
    for pattern_name, pattern in CREDENTIAL_PATTERNS.items():
        if re.search(pattern, text or ""):
            return pattern_name
    return None


def _redact_value(value):
    """Recurses into a parsed JSON value, redacting every CREDENTIAL_PATTERNS match found in any
    string at any depth. Returns (new_value, was_redacted). Never mutates `value`."""
    if isinstance(value, str):
        text = value
        was_redacted = False
        for pattern_name, pattern in CREDENTIAL_PATTERNS.items():
            text, count = re.subn(pattern, f"[REDACTED:{pattern_name}]", text)
            if count:
                was_redacted = True
        return text, was_redacted
    if isinstance(value, dict):
        was_redacted = False
        new_dict = {}
        for k, v in value.items():
            new_v, r = _redact_value(v)
            new_dict[k] = new_v
            was_redacted = was_redacted or r
        return new_dict, was_redacted
    if isinstance(value, list):
        was_redacted = False
        new_list = []
        for item in value:
            new_item, r = _redact_value(item)
            new_list.append(new_item)
            was_redacted = was_redacted or r
        return new_list, was_redacted
    return value, False


def redact_payload(payload_json):
    """Pure. Returns (redacted_json_text, was_redacted). Raises ScrubVerificationError if
    payload_json is not valid JSON -- make_row_mapper always produces json.dumps(obj) output, so
    a parse failure this deep in the pipeline is a genuine anomaly, not something to paper over."""
    try:
        obj = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ScrubVerificationError(f"payload_json is not valid JSON before scrub: {exc}") from exc
    redacted_obj, was_redacted = _redact_value(obj)
    return json.dumps(redacted_obj), was_redacted


def scrub_row(row):
    """Pure. Returns a NEW row dict (never mutates `row`) with payload_json redacted,
    payload_bytes recomputed from the redacted text (GOALS.json C6 -- reflects the stored value,
    not the pre-redaction length), and payload_redacted set. Raises ScrubVerificationError if the
    redacted text still matches a pattern (should be structurally impossible given redact_payload
    redacts every match, but checked anyway rather than assumed)."""
    redacted_text, was_redacted = redact_payload(row["payload_json"])

    still_matching = _still_matches_any_pattern(redacted_text)
    if still_matching:
        raise ScrubVerificationError(
            f"redacted payload still matches pattern {still_matching!r} in-memory"
        )

    scrubbed = dict(row)
    scrubbed["payload_json"] = redacted_text
    scrubbed["payload_bytes"] = len(redacted_text.encode("utf-8"))
    scrubbed["payload_redacted"] = 1 if was_redacted else 0
    return scrubbed


def make_scrubbing_insert(ingest_run_id, drop_tracker):
    """Returns insert_row(conn, stream_id, byte_offset, row) -> None, matching stream.tail()'s
    exact contract -- drop-in replacement for functools.partial(audit.insert_audit_event_row,
    ingest_run_id=...). Never raises; see module docstring."""

    def insert_row(conn, stream_id, byte_offset, row):
        # Staging table creation deliberately does NOT happen here. It must exist for the
        # ENTIRE duration of the caller's stream.tail() call, including when zero lines are
        # processed (insert_row is then never called at all) -- ingest_audit_source creates it
        # once, up front, precisely so its own unconditional cleanup DROP always has a temp
        # shadow to remove and can never fall through to main.audit_event.
        try:
            scrubbed = scrub_row(row)  # may raise ScrubVerificationError

            # Stage into the shadowing temp table via the UNMODIFIED insert_audit_event_row --
            # its own "INSERT INTO audit_event ..." lands here, not in the live table, for as
            # long as this connection's temp audit_event exists.
            insert_audit_event_row(conn, stream_id, byte_offset, scrubbed, ingest_run_id)

            # Verify by reading the staged row BACK via a fresh SELECT -- never trusting the
            # in-memory `scrubbed` dict scrub_row() returned (GOALS.json F1/C2).
            staged = conn.execute(
                "SELECT " + ", ".join(_PUBLISH_COLUMNS) +
                " FROM audit_event WHERE stream_id=? AND byte_offset=?",
                (stream_id, byte_offset),
            ).fetchone()
            if staged is None:
                raise ScrubVerificationError("staged row not found after insert")
            staged_row = dict(zip(_PUBLISH_COLUMNS, staged))

            still_matching = _still_matches_any_pattern(staged_row["payload_json"])
            if still_matching:
                raise ScrubVerificationError(
                    f"staged payload still matches pattern {still_matching!r} after write"
                )
            json.loads(staged_row["payload_json"])  # raises if the staged bytes are not valid JSON

            # Publish atomically: one INSERT into the REAL table, using the disk-staged and
            # re-verified values, never the in-memory `scrubbed` dict. main.audit_event
            # explicitly, to escape the temp shadow.
            cols = ["stream_id", "byte_offset", "ingest_run_id"] + _PUBLISH_COLUMNS
            values = [stream_id, byte_offset, ingest_run_id] + [staged_row[c] for c in _PUBLISH_COLUMNS]
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO main.audit_event ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
        except Exception as exc:
            drop_tracker.record_drop(stream_id, byte_offset, f"{type(exc).__name__}: {exc}")
        finally:
            conn.execute(
                "DELETE FROM audit_event WHERE stream_id=? AND byte_offset=?",
                (stream_id, byte_offset),
            )

    return insert_row


class AuditIngestResult:
    """Mirrors stream.TailResult's shape, plus the drop accounting TailResult has no field for."""

    def __init__(self, rows_inserted, rows_skipped, rows_rejected_by_mapper, rotation_detected,
                 error, dropped_count, drops):
        self.rows_inserted = rows_inserted
        self.rows_skipped = rows_skipped
        self.rows_rejected_by_mapper = rows_rejected_by_mapper
        self.rotation_detected = rotation_detected
        self.error = error
        self.dropped_count = dropped_count
        self.drops = drops

    @property
    def ok(self):
        """False if the source file was missing, tail() itself errored, or ANY line was
        fail-closed dropped -- a dropped line must never look like a clean run (GOALS.json C4 /
        F5)."""
        return self.error is None and self.dropped_count == 0


def ingest_audit_source(warehouse_conn, source_name, source_path, ingest_run_id):
    """The audit-specific replacement for run_pull.py's generic _pull_stream_source, wrapping
    stream.tail() with the write-time scrub harness. Same F3 missing-file discipline
    _pull_stream_source already uses: a missing source is a named, reported skip, not a crash
    that kills the whole orchestration run over one source.

    Creates the staging temp table for the duration of THIS call only and always drops it
    before returning -- warehouse_conn is shared across every source in run_pull.run() (tessera,
    git, verdicts, dq_runner), so a stale shadow left in place after this function returns would
    make every later unqualified read of audit_event elsewhere in the same run silently see the
    (now-empty) staging table instead of the real one.
    """
    path = Path(source_path)
    if not path.is_file():
        return AuditIngestResult(
            rows_inserted=None, rows_skipped=None, rows_rejected_by_mapper=None,
            rotation_detected=False, error=f"source file not found: {path}",
            dropped_count=0, drops=[],
        )

    drop_tracker = DropTracker()
    # Created unconditionally, BEFORE stream.tail() runs -- not lazily inside insert_row, which
    # is never called at all when there are zero new lines to process. The temp shadow must
    # exist for this whole call's duration so the unconditional cleanup below always has a temp
    # table to remove and can never fall through to main.audit_event (verified empirically: an
    # unqualified DROP TABLE IF EXISTS with no temp shadow present drops the real table).
    warehouse_conn.execute(STAGING_DDL)
    try:
        insert_row = make_scrubbing_insert(ingest_run_id, drop_tracker)
        row_mapper = make_row_mapper(str(path))
        result = stream.tail(warehouse_conn, source_name, str(path), row_mapper, insert_row)
    finally:
        warehouse_conn.execute("DROP TABLE IF EXISTS audit_event")
    warehouse_conn.commit()

    # stream.tail()'s own `inserted` counter increments whenever insert_row() is CALLED, not
    # when it actually persists a row -- true for every other source, where insert_row raising
    # is what tail() would treat as failure. This harness's insert_row deliberately never
    # raises (a fail-closed drop must not abort the rest of the file), so tail()'s count is the
    # number of lines ATTEMPTED, inflated by any this harness silently dropped. Correct it here,
    # once, rather than let a caller read "rows_inserted" and believe every one of them landed.
    true_rows_inserted = (
        result.rows_inserted - drop_tracker.dropped_count if result.rows_inserted is not None
        else result.rows_inserted
    )
    return AuditIngestResult(
        rows_inserted=true_rows_inserted, rows_skipped=result.rows_skipped,
        rows_rejected_by_mapper=result.rows_rejected_by_mapper,
        rotation_detected=result.rotation_detected, error=result.error,
        dropped_count=drop_tracker.dropped_count, drops=drop_tracker.drops,
    )
