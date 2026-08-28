"""Generic append-only-JSONL tailer: content-addressed stream identity, watermark tracking,
rotation/truncation detection, partial-line safety. Shared by every streaming JSONL source
(verdicts.jsonl, audit-plane); the per-line JSON-to-row mapping is supplied by the caller so
this module stays source-agnostic, matching ARCHITECTURE.md section 4's description of ingest:
"It interprets nothing... no value is normalised on the way in."

Row insert and the watermark/ingest_source update happen in ONE SQLite transaction per call to
tail() -- a crash between them is closed by the caller committing once, not once per row.
"""

import datetime
import hashlib
import json
from collections import namedtuple
from pathlib import Path

STREAM_ID_HEAD_BYTES = 4096

TailResult = namedtuple(
    "TailResult",
    ["rows_inserted", "rows_skipped", "skip_details", "rotation_detected", "error",
     "rows_rejected_by_mapper"],
)


class MalformedLine:
    __slots__ = ("byte_offset", "error")

    def __init__(self, byte_offset, error):
        self.byte_offset = byte_offset
        self.error = error


def stream_id_of_bytes(head_bytes):
    return hashlib.sha256(head_bytes[:STREAM_ID_HEAD_BYTES]).hexdigest()


def stream_id_of_path(path):
    with open(path, "rb") as f:
        head = f.read(STREAM_ID_HEAD_BYTES)
    return stream_id_of_bytes(head)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _split_complete_lines(data):
    """Returns (complete_lines: list[bytes], consumed_bytes: int). The trailing element of a
    split on b'\\n' is either b'' (data ended in a newline -- fully consumed) or a genuine
    partial line (data ended mid-line -- NOT consumed, byte_offset stops before it)."""
    parts = data.split(b"\n")
    trailing = parts[-1]
    complete = parts[:-1]
    if trailing == b"":
        consumed = len(data)
    else:
        consumed = len(data) - len(trailing)
    return complete, consumed


def tail(conn, source_name, path, row_mapper, insert_row):
    """
    row_mapper(json_obj: dict, raw_line: bytes) -> dict of destination-table-shaped columns, or
        None to skip this row without counting it as malformed (a real but uninteresting row).
        raw_line is the exact bytes for this line (no trailing newline) -- audit_event's
        payload_bytes needs the real on-disk size, not a re-derived one, so this is passed
        through rather than making every mapper re-encode json_obj to recover it.
    insert_row(conn, stream_id, byte_offset, mapped_row: dict) -> None. Caller-supplied so this
        module stays table-agnostic (hook_verdict today; audit_event uses the same tail()).

    Returns a TailResult. Never raises on a malformed line or a missing source file -- both are
    reported in the result, matching this component's F1/F3 failure signatures.
    """
    p = Path(path)
    if not p.is_file():
        return TailResult(0, 0, [], False, error=f"{path} does not exist", rows_rejected_by_mapper=0)

    with open(path, "rb") as f:
        head = f.read(STREAM_ID_HEAD_BYTES)
    current_stream_id = stream_id_of_bytes(head)
    real_size = p.stat().st_size

    existing = conn.execute(
        "SELECT stream_id, byte_offset, rows_ingested FROM ingest_source WHERE source_name = ?",
        (source_name,),
    ).fetchone()

    rotation_detected = False
    history_reason = None
    prior_stream_id = None
    prior_byte_offset = None

    if existing is None:
        stream_id = current_stream_id
        start_offset = 0
        history_reason = "first-seen"
    else:
        prev_stream_id, prev_offset, prev_rows = existing
        if prev_stream_id != current_stream_id:
            rotation_detected = True
            history_reason = "truncation" if real_size < prev_offset else "rotation"
            prior_stream_id = prev_stream_id
            prior_byte_offset = prev_offset
            stream_id = current_stream_id
            start_offset = 0
        else:
            stream_id = current_stream_id
            start_offset = prev_offset

    with open(path, "rb") as f:
        f.seek(start_offset)
        new_data = f.read()

    complete_lines, consumed = _split_complete_lines(new_data)

    inserted = 0
    rejected_by_mapper = 0
    skip_details = []
    line_start = start_offset
    for line in complete_lines:
        this_line_offset = line_start
        line_start += len(line) + 1  # +1 for the consumed '\n'
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            skip_details.append(MalformedLine(this_line_offset, str(exc)))
            continue
        mapped = row_mapper(obj, line)
        if mapped is None:
            # Check 2 (adversarial-code-review, MEDIUM): a row the mapper rejects for a missing
            # required field (real malformed input, distinct from a JSON parse failure two
            # lines above) used to be counted in neither rows_inserted nor rows_skipped --
            # invisible to any caller. Counted here, separately from skip_details, since it is
            # not a MalformedLine (the JSON parsed fine; the mapper's own domain rules rejected it).
            rejected_by_mapper += 1
            continue
        insert_row(conn, stream_id, this_line_offset, mapped)
        inserted += 1

    new_offset = start_offset + consumed
    now = _now()

    if history_reason:
        conn.execute(
            "INSERT INTO ingest_stream_history (source_name, stream_id, source_path, reason, "
            "prior_stream_id, prior_byte_offset, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source_name, stream_id, str(path), history_reason, prior_stream_id,
             prior_byte_offset, now),
        )

    total_rows = inserted if existing is None or rotation_detected else (existing[2] + inserted)
    conn.execute(
        "INSERT INTO ingest_source (source_name, stream_id, source_path, byte_offset, "
        "rows_ingested, first_seen_at, updated_at, updated_by_run) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
        "ON CONFLICT(source_name, stream_id) DO UPDATE SET "
        "byte_offset=excluded.byte_offset, rows_ingested=excluded.rows_ingested, "
        "updated_at=excluded.updated_at",
        (source_name, stream_id, str(path), new_offset, total_rows, now, now),
    )
    conn.commit()

    return TailResult(
        inserted, len(skip_details), skip_details, rotation_detected, error=None,
        rows_rejected_by_mapper=rejected_by_mapper,
    )
