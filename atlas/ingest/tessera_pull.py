"""Pulls new rows from TESSERA's own v_flat view into tessera_event.

Not a streaming source -- ARCHITECTURE.md section 15's tessera_to_ingest interface reads a
queryable SQL view, not a JSONL file, so there is no byte-offset watermark to track. The resume
point is derived from the target table itself: tessera_event.event_id is deliberately the SAME
value as TESSERA's own events.id/v_flat.event_id (ARCHITECTURE.md line 672, "mirrored from
v_flat rather than re-derived"), so MAX(event_id) already ingested IS the watermark -- no
separate cursor table, and no state that can desync from the data it describes. A crash mid-pull
rolls back the whole transaction (row inserts happen before commit), so MAX(event_id) simply
stays where it was; nothing is lost or double-counted on retry.

TESSERA_EVENT_COLUMNS is a strict subset of v_flat's real columns (verified against the live
view at /path/to/ticket-system/data/tessera.db, 2026-08-22) -- tessera_event carries only
the dimensions ARCHITECTURE.md's F-6/F-15 need, not v_flat's full analytical column set (raw
comment bodies, per-ticket time-in-status breakdowns, etc. are deliberately not mirrored, per
section 8's "Carried forward without re-litigation" boundary).
"""

TESSERA_EVENT_COLUMNS = [
    "event_type", "event_ts", "actor", "actor_kind", "ticket_id", "project_prefix",
    "ticket_type", "ticket_status", "ticket_is_closed", "ticket_priority", "ticket_severity",
    "ticket_has_frozen_criteria", "ticket_criteria_frozen_before_work", "ticket_criteria_count",
    "ticket_claim_count", "ticket_lead_time_hours", "comment_has_code_snippet",
    "status_from", "status_to",
]


def current_watermark(warehouse_conn):
    row = warehouse_conn.execute("SELECT MAX(event_id) FROM tessera_event").fetchone()
    return row[0] if row and row[0] is not None else 0


def fetch_new_events(tessera_conn, since_event_id):
    """tessera_conn: a connection to TESSERA's own tessera.db, opened read-only by the caller.
    Returns sqlite3.Row objects (row_factory must be sqlite3.Row) ordered by event_id so a
    crash partway through insertion resumes cleanly from the highest committed event_id."""
    columns = ", ".join(["event_id"] + TESSERA_EVENT_COLUMNS)
    return tessera_conn.execute(
        f"SELECT {columns} FROM v_flat WHERE event_id > ? ORDER BY event_id",
        (since_event_id,),
    ).fetchall()


def map_flat_row(row):
    """row: a sqlite3.Row from fetch_new_events. Returns a tessera_event-shaped dict including
    event_id -- unlike hook_verdict's mapper, event_id is not autoincrement-assigned by ATLAS,
    it IS TESSERA's own id, inserted explicitly as the table's primary key."""
    mapped = {"event_id": row["event_id"]}
    for col in TESSERA_EVENT_COLUMNS:
        mapped[col] = row[col]
    return mapped


def insert_tessera_event_row(warehouse_conn, row, ingest_run_id):
    cols = ["ingest_run_id"] + list(row.keys())
    values = [ingest_run_id] + list(row.values())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    warehouse_conn.execute(
        f"INSERT INTO tessera_event ({col_list}) VALUES ({placeholders})",
        values,
    )


def pull_tessera_events(warehouse_conn, tessera_conn, ingest_run_id):
    """Orchestrates one pull pass: fetch everything newer than the current watermark, insert it,
    commit once. Returns the number of rows ingested. An empty result (no new events) is a real,
    valid outcome -- zero is not an error."""
    since = current_watermark(warehouse_conn)
    rows = fetch_new_events(tessera_conn, since)
    for row in rows:
        insert_tessera_event_row(warehouse_conn, map_flat_row(row), ingest_run_id)
    warehouse_conn.commit()
    return len(rows)
