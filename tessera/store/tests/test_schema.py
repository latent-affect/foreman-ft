import sqlite3
import tempfile
import unittest
from pathlib import Path

from .. import schema
from ..store import Store
from ..exceptions import UnsupportedSQLiteVersion


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_events_append_only(self):
        conn = self.store.conn_internal()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash, created_at)"
            " VALUES ('X','a','{}','GENESIS','h1','t1')"
        )
        conn.execute("COMMIT")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET actor='b' WHERE id=1")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM events WHERE id=1")

    def test_prev_hash_not_null_unique_genesis(self):
        conn = self.store.conn_internal()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash,"
                " created_at) VALUES ('X','a','{}',NULL,'h2','t2')"
            )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash, created_at)"
            " VALUES ('X','a','{}','GENESIS','h3','t3')"
        )
        conn.execute("COMMIT")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash,"
                " created_at) VALUES ('X','a','{}','GENESIS','h4','t4')"
            )

    def test_orphan_prev_hash_rejected(self):
        conn = self.store.conn_internal()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash,"
                " created_at) VALUES ('X','a','{}','not-a-real-hash','h5','t5')"
            )

    def test_actor_ref_column_is_additive_and_nullable(self):
        """REQ-6 (Foreman v2.0 PRD): actor_ref exists on a fresh database via DDL directly,
        is nullable, and does not disturb the existing actor field or the hash-chain."""
        conn = self.store.conn_internal()
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(events)")}
        self.assertIn("actor_ref", cols)
        self.assertEqual(cols["actor_ref"][3], 0, "actor_ref must be nullable (notnull=0)")
        self.assertEqual(cols["actor"][3], 1, "actor must remain NOT NULL, unchanged")

    def test_ensure_actor_ref_column_migrates_a_pre_existing_database(self):
        """REQ-6: a database created BEFORE this column existed in DDL (simulated here by
        creating the events table with the old, pre-REQ-6 column set directly) gets the
        column added by the real ALTER TABLE migration path, idempotently."""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                ticket_id TEXT,
                project_id INTEGER,
                actor TEXT NOT NULL,
                payload TEXT NOT NULL,
                idempotency_key TEXT,
                prev_hash TEXT NOT NULL UNIQUE,
                event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash, created_at)"
            " VALUES ('X','a','{}','GENESIS','h1','t1')"
        )
        conn.commit()
        cols_before = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
        self.assertNotIn("actor_ref", cols_before, "premise: the old table has no actor_ref yet")

        schema.ensure_actor_ref_column(conn)
        cols_after = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
        self.assertIn("actor_ref", cols_after)

        row = conn.execute("SELECT actor, actor_ref FROM events WHERE id=1").fetchone()
        self.assertEqual(row, ("a", None), "existing row preserved, actor_ref defaults to NULL")

        # Idempotent: calling it again on an already-migrated table must not raise.
        schema.ensure_actor_ref_column(conn)

    def test_identity_registry_table_exists_and_is_genuinely_mutable(self):
        """REQ-6: identity_registry is a separate, mutable, NOT hash-chained table -- unlike
        events, it must accept UPDATE and DELETE with no trigger blocking them."""
        conn = self.store.conn_internal()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO identity_registry (actor_ref, display_name, created_at, updated_at)"
            " VALUES ('ref-1', 'Name', 't1', 't1')"
        )
        conn.execute("UPDATE identity_registry SET display_name='Updated' WHERE actor_ref='ref-1'")
        conn.execute("DELETE FROM identity_registry WHERE actor_ref='ref-1'")
        conn.execute("COMMIT")

    def test_identity_registry_is_not_in_projection_tables(self):
        """REQ-6: identity_registry is deliberately outside event-sourcing -- it must not be
        derived from or compared against the replayed event log."""
        self.assertNotIn("identity_registry", schema.PROJECTION_TABLES)

    def test_hash_chain_verification_holds_after_actor_ref_migration(self):
        """End-to-end: a real ticket creation (which appends a real event) still produces a
        clean hash chain after this schema change -- not just a column-presence check."""
        self.store.create_ticket(
            ticket_type="Task", reporter="me", actor="agent",
            priority=2, severity=2, summary="test",
        )
        result = self.store.verify_chain()
        self.assertEqual(result["hash_mismatches"], 0)
        self.assertEqual(result["orphans"], 0)

    def test_sqlite_version_check_fails_loud(self):
        actual = schema.parse_sqlite_version(sqlite3.sqlite_version)
        self.assertGreaterEqual(actual, schema.MIN_SQLITE_VERSION)

        original = schema.MIN_SQLITE_VERSION
        try:
            schema.MIN_SQLITE_VERSION = (99, 0, 0)
            tmp2 = Path(self.tmp_dir.name) / "test2.db"
            with self.assertRaises(UnsupportedSQLiteVersion):
                Store(tmp2, codename="X", prefix="X")
        finally:
            schema.MIN_SQLITE_VERSION = original


if __name__ == "__main__":
    unittest.main()
