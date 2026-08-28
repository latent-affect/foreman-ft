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
