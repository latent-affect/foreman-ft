"""GOALS.json C1-C4, C8. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.resolve.tests.test_matching -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.resolve import run
from atlas.resolve.tests._helpers import TempDb, register_project


class PathComponentMatchingTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDb()

    def tearDown(self):
        self.db.close()

    def test_string_prefix_collision_does_not_falsely_match(self):
        # The real ARCHITECTURE.md section 3 case: example-project is a bare string prefix of
        # example-project-ft, but never its path-component ancestor.
        register_project(self.db.conn, "EXPJ", "/path/to/example-project")
        register_project(self.db.conn, "EXPJFT", "/path/to/example-project-ft")
        results = run.resolve_all(self.db.conn, ["/path/to/example-project-ft/src/main.py"])
        self.assertEqual(results["/path/to/example-project-ft/src/main.py"], "unique")
        row = self.db.conn.execute(
            "SELECT project_prefix FROM cwd_project WHERE cwd = ?",
            ("/path/to/example-project-ft/src/main.py",),
        ).fetchone()
        self.assertEqual(row[0], "EXPJFT")

    def test_unique_match_populates_consistent_triple(self):
        register_project(self.db.conn, "TESS", "/path/to/ticket-system")
        run.resolve_all(self.db.conn, ["/path/to/ticket-system/tessera/api.py"])
        row = self.db.conn.execute(
            "SELECT matched_root, project_prefix, candidate_count, resolution FROM cwd_project "
            "WHERE cwd = ?", ("/path/to/ticket-system/tessera/api.py",),
        ).fetchone()
        self.assertEqual(row, ("/path/to/ticket-system", "TESS", 1, "unique"))

    def test_shared_root_resolves_ambiguous_with_both_candidates_recorded(self):
        register_project(self.db.conn, "AREM", "/path/to/agent-remediation")
        register_project(self.db.conn, "FORE", "/path/to/agent-remediation")
        run.resolve_all(self.db.conn, ["/path/to/agent-remediation"])
        row = self.db.conn.execute(
            "SELECT project_prefix, candidate_count, resolution FROM cwd_project WHERE cwd = ?",
            ("/path/to/agent-remediation",),
        ).fetchone()
        self.assertEqual(row, (None, 2, "ambiguous"))
        candidates = {
            r[0] for r in self.db.conn.execute(
                "SELECT project_prefix FROM cwd_project_candidate WHERE cwd = ?",
                ("/path/to/agent-remediation",),
            )
        }
        self.assertEqual(candidates, {"AREM", "FORE"})

    def test_no_matching_root_resolves_unregistered(self):
        register_project(self.db.conn, "TESS", "/path/to/ticket-system")
        run.resolve_all(self.db.conn, ["/path/to/some-other-project"])
        row = self.db.conn.execute(
            "SELECT matched_root, project_prefix, candidate_count, resolution FROM cwd_project "
            "WHERE cwd = ?", ("/path/to/some-other-project",),
        ).fetchone()
        self.assertEqual(row, (None, None, 0, "unregistered"))


if __name__ == "__main__":
    unittest.main()
