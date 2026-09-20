"""GOALS.json C1-C4, C8. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.resolve.tests.test_matching -v
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
        # The real ARCHITECTURE.md section 3 case: glasshouse is a bare string prefix of
        # glasshouse-ft, but never its path-component ancestor.
        register_project(self.db.conn, "GLAS", "/Users/m5/dev/glasshouse")
        register_project(self.db.conn, "GLASFT", "/Users/m5/dev/glasshouse-ft")
        results = run.resolve_all(self.db.conn, ["/Users/m5/dev/glasshouse-ft/src/main.py"])
        self.assertEqual(results["/Users/m5/dev/glasshouse-ft/src/main.py"], "unique")
        row = self.db.conn.execute(
            "SELECT project_prefix FROM cwd_project WHERE cwd = ?",
            ("/Users/m5/dev/glasshouse-ft/src/main.py",),
        ).fetchone()
        self.assertEqual(row[0], "GLASFT")

    def test_unique_match_populates_consistent_triple(self):
        register_project(self.db.conn, "TESS", "/Users/m5/dev/ticket-system")
        run.resolve_all(self.db.conn, ["/Users/m5/dev/ticket-system/tessera/api.py"])
        row = self.db.conn.execute(
            "SELECT matched_root, project_prefix, candidate_count, resolution FROM cwd_project "
            "WHERE cwd = ?", ("/Users/m5/dev/ticket-system/tessera/api.py",),
        ).fetchone()
        self.assertEqual(row, ("/Users/m5/dev/ticket-system", "TESS", 1, "unique"))

    def test_shared_root_resolves_ambiguous_with_both_candidates_recorded(self):
        # ATLASSN-73. This must be an isolated tmpdir root, not a real machine path: the
        # original fixture used /Users/m5/agent-remediation, which is a REAL directory carrying
        # a real .foreman/tessera-prefix override naming FORE. resolve_one() consults that
        # override for a unique match too (ATLASSN-9), so the test was silently reading the
        # operator's own disk state and asserting the override didn't exist. It passed everywhere
        # except this machine -- an environment-dependent test, not a resolver defect (see
        # ATLASSN-73's correction comment). A bare tmpdir path guarantees no such file exists.
        with tempfile.TemporaryDirectory() as tmpdir:
            shared_root = str(Path(tmpdir) / "shared-root")
            register_project(self.db.conn, "AREM", shared_root)
            register_project(self.db.conn, "FORE", shared_root)
            run.resolve_all(self.db.conn, [shared_root])
            row = self.db.conn.execute(
                "SELECT project_prefix, candidate_count, resolution FROM cwd_project "
                "WHERE cwd = ?", (shared_root,),
            ).fetchone()
            self.assertEqual(row, (None, 2, "ambiguous"))
            candidates = {
                r[0] for r in self.db.conn.execute(
                    "SELECT project_prefix FROM cwd_project_candidate WHERE cwd = ?",
                    (shared_root,),
                )
            }
            self.assertEqual(candidates, {"AREM", "FORE"})

    def test_no_matching_root_resolves_unregistered(self):
        register_project(self.db.conn, "TESS", "/Users/m5/dev/ticket-system")
        run.resolve_all(self.db.conn, ["/Users/m5/dev/some-other-project"])
        row = self.db.conn.execute(
            "SELECT matched_root, project_prefix, candidate_count, resolution FROM cwd_project "
            "WHERE cwd = ?", ("/Users/m5/dev/some-other-project",),
        ).fetchone()
        self.assertEqual(row, (None, None, 0, "unregistered"))


if __name__ == "__main__":
    unittest.main()
