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
import os
import sqlite3
import sys
from collections import namedtuple
from pathlib import Path

STREAM_ID_HEAD_BYTES = 4096

# Distinguishes 'caller did not mention quarantine' (derive a default) from an explicit
# None (contain and count, but write no durable record -- what an in-memory test wants).
QUARANTINE_PATH_UNSET = object()

TailResult = namedtuple(
    "TailResult",
    ["rows_inserted", "rows_skipped", "skip_details", "rotation_detected", "error",
     "rows_rejected_by_mapper", "rows_quarantined"],
    defaults=(0,),
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


# Constraint violations the DATA is responsible for -- the row is wrong, containment is right.
BAD_DATA_CONSTRAINTS = frozenset({
    "SQLITE_CONSTRAINT_CHECK",
    "SQLITE_CONSTRAINT_NOTNULL",
})

# Constraint violations the PIPELINE is responsible for. A collision on (stream_id, byte_offset)
# means offset-tracking re-read bytes it already committed. Never contained -- see
# verdicts.insert_hook_verdict_row's own comment, which is the guarantee this set exists to keep.
PIPELINE_FAULT_CONSTRAINTS = frozenset({
    "SQLITE_CONSTRAINT_PRIMARYKEY",
    "SQLITE_CONSTRAINT_UNIQUE",
    # FOREIGNKEY moved here from bad-data in v3 (Iris's second probe). The general taxonomy --
    # "a constraint the DATA violated" versus "a constraint the PIPELINE violated" -- is right,
    # but FK's side of that line is SCHEMA-SPECIFIC and in THIS schema it is unambiguous: the
    # only FK on the insert path is hook_verdict.ingest_run_id -> ingest_run(run_id), and a
    # violation can only mean the pipeline handed the insert a run_id that does not exist. That
    # is a sequencing bug, not row content, and quarantining it silently absorbs exactly the
    # class PRIMARYKEY and UNIQUE were split out to keep loud.
    #
    # Stated as schema-specific rather than universal: in a schema where an FK referenced a
    # value carried IN the row, FK really would be bad data and would belong on the other side.
    "SQLITE_CONSTRAINT_FOREIGNKEY",
})

BINDABLE_TYPES = (type(None), int, float, str, bytes)


def unsupported_type_fields(row):
    """Field names whose value is of a TYPE sqlite3 cannot bind at all.

    RENAMED AND NARROWED IN v3, because v2's version claimed more than it could deliver. It was
    called unbindable_fields and its job was "can sqlite3 bind this", which isinstance CANNOT
    answer: a ~40-digit int passes isinstance(int) and then raises OverflowError AT BIND TIME.
    That is a type-level proxy for a value-level constraint, and Iris's probe found it -- the
    original trap with an extra step.

    The honest division of labour, and the reason this function survives at all rather than
    being deleted: only sqlite3 can answer the VALUE question, so that one is answered at the
    insert (OverflowError, caught below). What this check CAN answer soundly is the TYPE
    question, and answering it here is what makes the remaining ProgrammingError unambiguous --
    with dict/list already excluded, a ProgrammingError that still escapes can only be
    "Incorrect number of bindings supplied", which is a mapper CODE defect and must propagate
    rather than be quarantined on every row.

    So the pre-check is not the bindability guarantee any more. It is the thing that makes the
    exception handling below able to tell a data problem from a code problem without matching
    on message text.
    """
    return tuple(sorted(k for k, v in row.items() if not isinstance(v, BINDABLE_TYPES)))


def is_bad_data_constraint(exc):
    """True only for a constraint the ROW violated. False -- meaning re-raise -- for a pipeline
    fault, and False when the class cannot be determined at all, so an unclassifiable failure
    keeps its loud behaviour rather than inheriting containment by default."""
    name = getattr(exc, "sqlite_errorname", None)
    if name in PIPELINE_FAULT_CONSTRAINTS:
        return False
    return name in BAD_DATA_CONSTRAINTS


def default_quarantine_path(conn):
    """Sidecar beside the warehouse the connection is attached to, or None if that cannot be
    determined (an in-memory database in a test). None means quarantine rows are still COUNTED
    and still contained -- only the durable record is skipped -- so a caller can never read a
    missing sidecar as "nothing was quarantined"."""
    try:
        for _, name, filename in conn.execute("PRAGMA database_list"):
            if name == "main" and filename:
                return Path(filename).parent / "quarantine" / "ingest-quarantine.jsonl"
    except sqlite3.Error:
        return None
    return None


def record_quarantine(conn, quarantine_path, source_name, stream_id, byte_offset, error):
    """Append one coordinate-only record. Never raises: a failure to WRITE the quarantine log
    must not become a second way to kill the ingest run it exists to keep alive."""
    if quarantine_path is None:
        return
    try:
        path = Path(quarantine_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = json.dumps({
            "quarantined_at": _now(),
            "source_name": source_name,
            "stream_id": stream_id,
            "byte_offset": byte_offset,
            "constraint_error": error,
        }) + "\n"

        # HEAL A NEWLINE-LESS TAIL BEFORE APPENDING. Without this, a sidecar whose last line was
        # cut short merges the next record onto it, and BOTH lines parse as neither -- so the
        # cost of one short write is not one lost record, it is every record from then on. That
        # is the run-75 trap's own shape (one failure making all subsequent ones fail
        # identically) reappearing inside the log built to survive it. Measured, not reasoned:
        # a write cut short by RLIMIT_FSIZE leaves the file ending mid-record, and the next
        # append then reads back 1 parseable line out of 2.
        #
        # A SIGKILL between write and close does NOT produce this state (the buffer is simply
        # lost, 0 bytes written), and three processes appending 900 records concurrently did not
        # produce it either, at 150B and at 40KB records. The one mechanism that reaches it is a
        # SHORT WRITE -- ENOSPC, a quota, an fsize limit -- which is also the one this function's
        # own except-OSError already expects and swallows. So the fix belongs on the write side,
        # here, next to the failure that causes it.
        #
        # A separate read-only open rather than seeking on the append handle: text-mode seek only
        # accepts cookies returned by tell(), so a raw byte offset is not portable. This is an
        # error path, not a hot one, and a second open costs nothing that matters here.
        prefix = ""
        if path.exists() and path.stat().st_size:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    prefix = "\n"

        # One write, prefix included, so a concurrent appender cannot land between the healing
        # newline and the record it heals. Two processes both finding a torn tail is possible and
        # costs one blank line, which every JSONL reader here already skips.
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(prefix + record)
    except OSError as exc:
        print(f"[stream] quarantine record failed ({exc}); the row is still contained and "
              f"counted, but this occurrence has no durable record", file=sys.stderr)


def tail(conn, source_name, path, row_mapper, insert_row, quarantine_path=QUARANTINE_PATH_UNSET):
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
        return TailResult(0, 0, [], False, error=f"{path} does not exist",
                          rows_rejected_by_mapper=0, rows_quarantined=0)

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

    if quarantine_path is QUARANTINE_PATH_UNSET:
        quarantine_path = default_quarantine_path(conn)

    inserted = 0
    quarantined = 0
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
        unsupported = unsupported_type_fields(mapped)
        if unsupported:
            # BREAK 1 (Iris's probe, ATLASSN-109). map_verdict_row copies ~15 fields through
            # with no type check, so a ledger line carrying a JSON object or array where a
            # scalar belongs makes conn.execute raise sqlite3.ProgrammingError -- a DIFFERENT
            # class from IntegrityError, so the first version of this fix did not catch it and
            # gave zero protection against the identical self-repeating trap.
            #
            # Checked BEFORE the insert rather than caught after, deliberately. Catching
            # ProgrammingError would also swallow "Incorrect number of bindings supplied",
            # which is a CODE defect in the mapper, not bad data -- quarantining that would
            # hide a real bug on every row. Discriminating the two after the fact means
            # matching on exception message text, which is fragile. Asking "is this value
            # bindable" before binding it is precise, needs no message parsing, and leaves
            # ProgrammingError free to keep meaning "this code is wrong".
            quarantined += 1
            record_quarantine(conn, quarantine_path, source_name, stream_id, this_line_offset,
                              f"unsupported field type(s): {unsupported}")
            continue
        try:
            insert_row(conn, stream_id, this_line_offset, mapped)
            inserted += 1
        except OverflowError as exc:
            # v3, Iris's break. A JSON integer larger than sqlite3 can bind (outside int64)
            # passes every type check and fails HERE. OverflowError is not a sqlite3.Error
            # subclass, so the IntegrityError handler below never saw it and the run died
            # uncaught -- watermark null, same self-repeating trap.
            #
            # Caught here rather than pre-checked, deliberately: the boundary is sqlite3's, not
            # Python's, and re-implementing "what magnitude can sqlite3 bind" in this file would
            # be the same type-level-proxy mistake one layer along. Asking by attempting is the
            # only way to ask the component that actually decides.
            #
            # Unambiguous where ProgrammingError is not: nothing else on this path raises
            # OverflowError, so containing it cannot swallow a code defect.
            quarantined += 1
            record_quarantine(conn, quarantine_path, source_name, stream_id, this_line_offset,
                              f"value out of range for sqlite3 binding: {exc}")
        except sqlite3.IntegrityError as exc:
            # PARTS 2 AND 3 of the run-75 trap fix.
            #
            # PART 2, CONTAINMENT. This call was unguarded, so ONE row violating a CHECK
            # constraint killed the whole tail(), the byte offset (committed only after this
            # loop) never advanced, and every subsequent run resumed at the same byte and hit
            # the same row. Twenty runs, four days, no progress. A self-repeating trap.
            #
            # This is CONFORMANCE, not a new behaviour: this function's own docstring already
            # promises "Never raises on a malformed line or a missing source file." Malformed
            # lines were guarded; the insert was not. Same shape as CHV2-74's dead_man_switch,
            # which published the identical guarantee and was defeated by an uncaught
            # UnicodeDecodeError -- a published never-raises usually holds only for the failure
            # its author had in mind, and the docstring is what makes the gap invisible.
            #
            # CAUGHT NARROWLY. IntegrityError means "this ROW violates a constraint" -- a data
            # problem, and the only class it is safe to contain per-row. An OperationalError
            # (disk full, database locked) is a STORE-level failure where continuing would
            # quarantine every remaining row for a reason that has nothing to do with them, so
            # it is deliberately left to propagate and fail the run.
            #
            # PART 3, QUARANTINE RATHER THAN SKIP. A silent skip is data loss with a shrug. The
            # row is recorded so the loss is counted and reviewable, and rows_quarantined
            # carries the count to every caller.
            #
            # RECORDS COORDINATES, NEVER CONTENT. The offending line stays in the source ledger;
            # this sidecar records where to find it and what it violated. That follows this
            # project's own data-minimization rule for exactly this situation (the AI-5 schema
            # doc's finding-4 disposition: identify flagged rows by coordinates, never reproduce
            # their text outside the store that already holds it).
            #
            # A SIDECAR JSONL BECAUSE A QUARANTINE TABLE IS BLOCKED, not because it is better.
            # A table would be DDL, DDL lives in ARCHITECTURE.md, and editing that document
            # unbinds the architecture review -- the constraint recorded in
            # BLOCKER-ATLASSN-143-153-ARCHITECTURE-DDL-20260912.md. Whoever unblocks that path
            # should treat this as a decision still waiting, not a settled preference.
            # BREAK 2 (Iris's probe), and this one is the serious direction. The first
            # version caught EVERY IntegrityError, which silently swallowed a PRIMARY KEY
            # collision on (stream_id, byte_offset). verdicts.insert_hook_verdict_row says in
            # as many words why that must never happen: a plain INSERT is used "deliberately
            # not OR IGNORE" because a collision "means the offset-tracking in stream.tail()
            # re-read bytes it already committed -- a real bug that should raise and fail a
            # test loudly, not be silently deduplicated away."
            #
            # So the broadened catch did not merely leave a gap, it REMOVED AN EXISTING
            # DELIBERATE GUARANTEE and traded a loud known failure for a quiet unknown one.
            #
            # Discriminated by SQLite's extended result code, not by message text.
            # sqlite_errorname (Python 3.11+) gives SQLITE_CONSTRAINT_CHECK,
            # SQLITE_CONSTRAINT_NOTNULL, SQLITE_CONSTRAINT_PRIMARYKEY, SQLITE_CONSTRAINT_UNIQUE
            # as distinct values, which is exactly the distinction needed: a constraint the DATA
            # violated versus a constraint the PIPELINE violated.
            #
            # FAIL LOUD WHEN UNSURE. An unrecognised or unavailable errorname re-raises rather
            # than quarantining, so a constraint class nobody anticipated keeps the old, noisy
            # behaviour instead of silently inheriting containment.
            if not is_bad_data_constraint(exc):
                raise
            quarantined += 1
            record_quarantine(conn, quarantine_path, source_name, stream_id,
                               this_line_offset, str(exc))

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
        rows_rejected_by_mapper=rejected_by_mapper, rows_quarantined=quarantined,
    )
