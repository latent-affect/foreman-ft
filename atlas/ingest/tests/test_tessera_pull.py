"""GOALS.json C10-C12 (tessera_to_ingest amendment). Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_tessera_pull -v
"""

import sqlite3
import unittest

from atlas.ingest import tessera_pull
from atlas.ingest.tests._helpers import TempDb

V_FLAT_COLUMNS = ["event_id"] + tessera_pull.TESSERA_EVENT_COLUMNS


def make_fake_tessera_db():
    """A real sqlite db with a real v_flat VIEW over a plain base table -- the mapper only
    depends on v_flat's column names, not on how TESSERA computes them, so a hand-built fixture
    with the same column names is a faithful stand-in."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols_sql = ", ".join(f"{c} TEXT" for c in V_FLAT_COLUMNS)
    conn.execute(f"CREATE TABLE flat_source ({cols_sql})")
    conn.execute(f"CREATE VIEW v_flat AS SELECT * FROM flat_source")
    return conn


def insert_flat_row(conn, event_id, **overrides):
    row = {c: None for c in V_FLAT_COLUMNS}
    row["event_id"] = event_id
    row["event_type"] = "TicketCreated"
    row["event_ts"] = f"2026-08-2{event_id}T00:00:00Z"
    row["actor"] = "someone"
    row["actor_kind"] = "human"
    row["ticket_id"] = f"TESS-{event_id}"
    row["project_prefix"] = "TESS"
    row.update(overrides)
    cols = ", ".join(V_FLAT_COLUMNS)
    placeholders = ", ".join("?" for _ in V_FLAT_COLUMNS)
    conn.execute(
        f"INSERT INTO flat_source ({cols}) VALUES ({placeholders})",
        [row[c] for c in V_FLAT_COLUMNS],
    )
    conn.commit()


class TesseraPullTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDb()
        self.tessera_conn = make_fake_tessera_db()

    def tearDown(self):
        self.db.close()
        self.tessera_conn.close()

    def test_watermark_is_zero_on_an_empty_warehouse(self):
        self.assertEqual(tessera_pull.current_watermark(self.db.conn), 0)

    def test_pull_ingests_every_row_when_watermark_is_zero(self):
        insert_flat_row(self.tessera_conn, 1)
        insert_flat_row(self.tessera_conn, 2)
        insert_flat_row(self.tessera_conn, 3)
        n = tessera_pull.pull_tessera_events(self.db.conn, self.tessera_conn, self.db.run_id)
        self.assertEqual(n, 3)
        rows = self.db.conn.execute("SELECT event_id FROM tessera_event ORDER BY event_id").fetchall()
        self.assertEqual([r[0] for r in rows], [1, 2, 3])

    def test_second_pull_with_no_new_events_ingests_zero(self):
        insert_flat_row(self.tessera_conn, 1)
        tessera_pull.pull_tessera_events(self.db.conn, self.tessera_conn, self.db.run_id)
        n = tessera_pull.pull_tessera_events(self.db.conn, self.tessera_conn, self.db.run_id)
        self.assertEqual(n, 0)
        total = self.db.conn.execute("SELECT COUNT(*) FROM tessera_event").fetchone()[0]
        self.assertEqual(total, 1)

    def test_pull_after_new_events_arrive_only_ingests_the_new_ones(self):
        insert_flat_row(self.tessera_conn, 1)
        insert_flat_row(self.tessera_conn, 2)
        tessera_pull.pull_tessera_events(self.db.conn, self.tessera_conn, self.db.run_id)
        insert_flat_row(self.tessera_conn, 3)
        insert_flat_row(self.tessera_conn, 4)
        n = tessera_pull.pull_tessera_events(self.db.conn, self.tessera_conn, self.db.run_id)
        self.assertEqual(n, 2)
        rows = self.db.conn.execute("SELECT event_id FROM tessera_event ORDER BY event_id").fetchall()
        self.assertEqual([r[0] for r in rows], [1, 2, 3, 4])

    def test_mapped_row_carries_declared_columns_and_real_values(self):
        insert_flat_row(
            self.tessera_conn, 5, event_type="StatusChanged", status_from="open", status_to="closed",
            ticket_priority="high", ticket_has_frozen_criteria="1",
        )
        tessera_pull.pull_tessera_events(self.db.conn, self.tessera_conn, self.db.run_id)
        row = self.db.conn.execute(
            "SELECT event_type, status_from, status_to, ticket_priority, ticket_has_frozen_criteria "
            "FROM tessera_event WHERE event_id = 5"
        ).fetchone()
        self.assertEqual(row, ("StatusChanged", "open", "closed", "high", 1))

    def test_a_crash_between_insert_and_commit_leaves_the_watermark_unmoved(self):
        insert_flat_row(self.tessera_conn, 1)
        insert_flat_row(self.tessera_conn, 2)
        since = tessera_pull.current_watermark(self.db.conn)
        rows = tessera_pull.fetch_new_events(self.tessera_conn, since)
        tessera_pull.insert_tessera_event_row(
            self.db.conn, tessera_pull.map_flat_row(rows[0]), self.db.run_id
        )
        self.db.conn.rollback()
        self.assertEqual(tessera_pull.current_watermark(self.db.conn), 0)
        n = tessera_pull.pull_tessera_events(self.db.conn, self.tessera_conn, self.db.run_id)
        self.assertEqual(n, 2, "a rolled-back partial pass must not have advanced the watermark")


if __name__ == "__main__":
    unittest.main()
