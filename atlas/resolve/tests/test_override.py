"""GOALS.json C5. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.resolve.tests.test_override -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.resolve import run
from atlas.resolve.tests._helpers import TempDb, register_project


class TesseraPrefixOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "shared-root"
        self.root.mkdir()
        self.db = TempDb()

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_valid_override_disambiguates_a_shared_root(self):
        register_project(self.db.conn, "AREM", str(self.root))
        register_project(self.db.conn, "FORE", str(self.root))
        (self.root / ".foreman").mkdir()
        (self.root / ".foreman" / "tessera-prefix").write_text("FORE\n")

        results = run.resolve_all(self.db.conn, [str(self.root)])
        self.assertEqual(results[str(self.root)], "unique")
        row = self.db.conn.execute(
            "SELECT project_prefix, candidate_count FROM cwd_project WHERE cwd = ?",
            (str(self.root),),
        ).fetchone()
        self.assertEqual(row, ("FORE", 1))

    def test_override_naming_a_non_candidate_prefix_is_ignored_not_silently_applied(self):
        register_project(self.db.conn, "AREM", str(self.root))
        register_project(self.db.conn, "FORE", str(self.root))
        (self.root / ".foreman").mkdir()
        (self.root / ".foreman" / "tessera-prefix").write_text("NOT-A-REAL-CANDIDATE\n")

        results = run.resolve_all(self.db.conn, [str(self.root)])
        self.assertEqual(results[str(self.root)], "ambiguous")

    def test_no_override_file_leaves_shared_root_ambiguous(self):
        register_project(self.db.conn, "AREM", str(self.root))
        register_project(self.db.conn, "FORE", str(self.root))
        results = run.resolve_all(self.db.conn, [str(self.root)])
        self.assertEqual(results[str(self.root)], "ambiguous")

    def test_override_supersedes_an_otherwise_unique_match(self):
        # ATLASSN-9, resolved: ARCHITECTURE.md section 3's own words are "prefers it over the
        # registry" -- no ambiguous-only qualifier, matching the real, pre-existing
        # tessera_resolver.py precedent. Registers a SECOND project elsewhere so the override's
        # named prefix is real (a real registered TESSERA prefix, not tied to this root at all)
        # -- the exact shape tessera_resolver.py itself validates against (any known prefix).
        register_project(self.db.conn, "TESS", str(self.root))
        register_project(self.db.conn, "ELSEWHERE", "/some/other/path")
        (self.root / ".foreman").mkdir()
        (self.root / ".foreman" / "tessera-prefix").write_text("ELSEWHERE\n")

        results = run.resolve_all(self.db.conn, [str(self.root)])
        self.assertEqual(results[str(self.root)], "unique")
        row = self.db.conn.execute(
            "SELECT project_prefix FROM cwd_project WHERE cwd = ?", (str(self.root),),
        ).fetchone()
        self.assertEqual(row[0], "ELSEWHERE", "a valid override must win even over a unique match")

    def test_override_naming_an_unregistered_prefix_is_still_rejected(self):
        register_project(self.db.conn, "TESS", str(self.root))
        (self.root / ".foreman").mkdir()
        (self.root / ".foreman" / "tessera-prefix").write_text("NOT-REGISTERED-ANYWHERE\n")

        results = run.resolve_all(self.db.conn, [str(self.root)])
        self.assertEqual(results[str(self.root)], "unique")
        row = self.db.conn.execute(
            "SELECT project_prefix FROM cwd_project WHERE cwd = ?", (str(self.root),),
        ).fetchone()
        self.assertEqual(row[0], "TESS", "an override naming a prefix registered nowhere must not override anything")


if __name__ == "__main__":
    unittest.main()
