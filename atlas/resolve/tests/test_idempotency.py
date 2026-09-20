"""GOALS.json C6, C7. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.resolve.tests.test_idempotency -v
"""

import unittest

from atlas.resolve import run
from atlas.resolve.tests._helpers import TempDb, register_project


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDb()

    def tearDown(self):
        self.db.close()

    def test_rerun_against_unchanged_registry_is_a_no_op(self):
        register_project(self.db.conn, "TESS", "/Users/m5/dev/ticket-system")
        cwds = ["/Users/m5/dev/ticket-system/a.py", "/Users/m5/dev/other"]

        run.resolve_all(self.db.conn, cwds)
        first = self.db.conn.execute(
            "SELECT cwd, matched_root, project_prefix, candidate_count, resolution FROM cwd_project ORDER BY cwd"
        ).fetchall()

        run.resolve_all(self.db.conn, cwds)
        second = self.db.conn.execute(
            "SELECT cwd, matched_root, project_prefix, candidate_count, resolution FROM cwd_project ORDER BY cwd"
        ).fetchall()

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)

    def test_registry_change_updates_the_same_row_not_a_new_one(self):
        cwd = "/Users/m5/dev/new-project/src"
        run.resolve_all(self.db.conn, [cwd])
        before = self.db.conn.execute(
            "SELECT resolution, project_prefix FROM cwd_project WHERE cwd = ?", (cwd,)
        ).fetchone()
        self.assertEqual(before, ("unregistered", None))

        register_project(self.db.conn, "NEWP", "/Users/m5/dev/new-project")
        run.resolve_all(self.db.conn, [cwd])
        after_rows = self.db.conn.execute(
            "SELECT resolution, project_prefix FROM cwd_project WHERE cwd = ?", (cwd,)
        ).fetchall()

        self.assertEqual(len(after_rows), 1, "registry change must UPDATE the existing row, not insert a second one")
        self.assertEqual(after_rows[0], ("unique", "NEWP"))


if __name__ == "__main__":
    unittest.main()
