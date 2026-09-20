"""GOALS.json C3. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_migration -v
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import migrate


class MigrationIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_reapplying_migration_is_idempotent(self):
        conn = migrate.connect(self.db_path)
        migration_rows_before = conn.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]
        dq_rows_before = conn.execute("SELECT COUNT(*) FROM dq_check").fetchone()[0]
        conn.close()

        # Re-open and re-apply -- migrate.apply() must be a no-op given a matching digest.
        conn2 = sqlite3.connect(self.db_path)
        conn2.execute("PRAGMA foreign_keys = ON")
        digest = migrate.apply(conn2)
        self.assertEqual(len(digest), 64)

        migration_rows_after = conn2.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]
        dq_rows_after = conn2.execute("SELECT COUNT(*) FROM dq_check").fetchone()[0]
        version1_rows = conn2.execute(
            "SELECT COUNT(*) FROM schema_migration WHERE version=1").fetchone()[0]
        conn2.close()

        self.assertEqual(migration_rows_before, migration_rows_after)
        self.assertEqual(dq_rows_before, dq_rows_after)
        # migrate.connect() applies migration 1 and the additive migrations 2 through 21, plus
        # 23, 24, 25 and 26 (ATLASSN-188 wires the two local-file-sourced migrations into
        # connect(); ATLASSN-189 adds a third, migration 25, the dq_check advisory-severity guard
        # trigger; ATLASSN-197 adds a fourth, migration 26, session_credential_ack. migration
        # 22/ATLASSN-181 stays unwired -- its ARCHITECTURE.md section still does not exist, same
        # reason it was already skipped before this proposal). The assertion that actually
        # matters is unchanged -- re-applying adds nothing -- and migration 1 being recorded
        # exactly once is asserted directly rather than inferred from a total each new migration
        # would invalidate.
        self.assertEqual(migration_rows_before, 25)
        self.assertEqual(version1_rows, 1)

    def test_is_migrated_reports_correctly(self):
        conn = sqlite3.connect(self.db_path)
        self.assertFalse(migrate.is_migrated(conn))
        migrate.apply(conn)
        self.assertTrue(migrate.is_migrated(conn))
        conn.close()

    def test_new_ingest_run_creates_a_real_row(self):
        conn = migrate.connect(self.db_path)
        run_id = migrate.new_ingest_run(conn)
        row = conn.execute(
            "SELECT status, atlas_version FROM ingest_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        self.assertEqual(row[0], "running")
        self.assertEqual(row[1], migrate.ATLAS_VERSION)
        conn.close()


if __name__ == "__main__":
    unittest.main()
