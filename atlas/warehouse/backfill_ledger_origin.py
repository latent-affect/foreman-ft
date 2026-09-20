"""One-time recovery pass for hook_verdict.ledger_origin/origin_signal (ATLASSN-64).

ARCHITECTURE.md section 25 names three NULL categories for these two columns. This module
addresses exactly one: rows whose source line in verdicts.jsonl DOES carry ledger_origin, but
were ingested before migration 9 added the columns, so the value was dropped on the floor at
insert time and never written. It does NOT touch pre-migration-9 history (the origin was never
in the source line -- unrecoverable) or ongoing unstamped guard_* traffic (FORE-311, not
warehouse-side).

Frozen by the WATERMARK, not by INSERT OR IGNORE. atlas/ingest/verdicts.py uses a plain INSERT on
purpose (a (stream_id, byte_offset) collision means the tailer re-read committed bytes, a real
bug that should raise). What actually freezes these rows is that ingest_source.byte_offset only
advances and the tailer never re-reads bytes below it -- the rows are unreachable, not
overwritten-and-ignored. Re-ingestion is therefore not a candidate fix: rewinding the watermark
would make the tailer re-read committed bytes and hit hook_verdict's (stream_id, byte_offset)
PRIMARY KEY, which is exactly the loud failure verdicts.py's own comment is protecting.

Matches on the primary key (stream_id, byte_offset), so it cannot touch a row it did not come
from, and requires ledger_origin IS NULL, so it cannot overwrite a value the ingester already
wrote correctly. Reuses verdicts.map_verdict_row for the same ts/handler_id/verdict validation
the real ingester applies -- one parser for these bytes, not a second, differently-written one.

Reversal is `UPDATE hook_verdict SET ledger_origin = NULL, origin_signal = NULL` over the same
key set -- a real reversal, not a restore-from-backup, because nothing else derives from these
two columns except the origin split itself."""

import json

from ..ingest.transcript_parse import split_complete_lines
from ..ingest.verdicts import map_verdict_row


def _iter_range_lines(path, start_offset, end_offset):
    """Yields (byte_offset, obj) for each complete JSON line in [start_offset, end_offset).

    Offset bookkeeping matches stream.tail() exactly: byte_offset is the position of the line's
    first byte, tracked by walking forward from start_offset. Raises if end_offset does not land
    on a line boundary -- a partial trailing line inside the requested range means the caller
    passed a boundary that does not correspond to a real ingest watermark, and silently dropping
    or mis-parsing that tail would recover the wrong bytes."""
    with open(path, "rb") as fh:
        fh.seek(start_offset)
        data = fh.read(end_offset - start_offset)
    complete_lines, consumed = split_complete_lines(data)
    if consumed != len(data):
        raise ValueError(
            f"range [{start_offset}, {end_offset}) does not end on a line boundary -- "
            f"{len(data) - consumed} trailing bytes are a partial line"
        )
    offset = start_offset
    for line in complete_lines:
        this_offset = offset
        offset += len(line) + 1
        if not line.strip():
            continue
        obj = json.loads(line)
        yield this_offset, obj


def backfill_range(conn, verdicts_path, start_offset, end_offset, stream_id):
    """Recovers ledger_origin/origin_signal for rows in [start_offset, end_offset) whose source
    line carries them but whose warehouse row is still NULL. Returns a dict: lines_total,
    lines_stamped (carry a non-null ledger_origin in the source), rows_updated (actually flipped
    NULL -> a value -- less than lines_stamped whenever a row already carries a value or the
    (stream_id, byte_offset) pair is not in hook_verdict at all, e.g. a row the mapper itself
    would reject)."""
    lines_total = 0
    lines_stamped = 0
    rows_updated = 0
    for byte_offset, obj in _iter_range_lines(verdicts_path, start_offset, end_offset):
        lines_total += 1
        if obj.get("ledger_origin") is None:
            continue
        lines_stamped += 1
        row = map_verdict_row(obj)
        if row is None:
            continue
        cur = conn.execute(
            "UPDATE hook_verdict SET ledger_origin = ?, origin_signal = ? "
            "WHERE stream_id = ? AND byte_offset = ? AND ledger_origin IS NULL",
            (row["ledger_origin"], row["origin_signal"], stream_id, byte_offset),
        )
        rows_updated += cur.rowcount
    conn.commit()
    return {
        "lines_total": lines_total,
        "lines_stamped": lines_stamped,
        "rows_updated": rows_updated,
    }
