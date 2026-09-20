"""ATLASSN-153: integration_interface_pull and v_integration_progress.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_integration_interface_pull -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import integration_interface_pull as iip
from atlas.warehouse import migrate


def writeGoals(project_root, criteria):
    foreman = project_root / ".foreman"
    foreman.mkdir(parents=True, exist_ok=True)
    path = foreman / "GOALS.integration.json"
    path.write_text(json.dumps({
        "criteria": criteria,
        "integrity": {"criteria_hash_at_freeze": "sha256:test"},
    }), encoding="utf-8")
    return path


def criterion(cid, components=None, verifiable=True, statement="s", verification="v"):
    return {
        "id": cid, "components": components or ["a", "b"], "statement": statement,
        "verification": verification, "verifiable": verifiable, "added": "2026-01-01T00:00:00Z",
    }


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db = self.root / "atlas.db"
        self.projA = self.root / "projA"
        self.projB = self.root / "projB"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_a_real_declaration_lands(self):
        writeGoals(self.projA, [criterion("I1"), criterion("I2", verifiable=False)])
        results = iip.run(self.db, {"projA": self.projA})
        self.assertEqual(results["projA"], {"declared": 2, "error": None})
        conn = migrate.connect(str(self.db))
        rows = conn.execute(
            "SELECT interface_id, verifiable FROM integration_interface "
            "WHERE project='projA' ORDER BY interface_id"
        ).fetchall()
        conn.close()
        self.assertEqual(rows, [("I1", 1), ("I2", 0)])

    def test_a_project_with_no_file_declares_zero_not_an_error(self):
        results = iip.run(self.db, {"projA": self.projA})
        self.assertEqual(results["projA"], {"declared": 0, "error": None})

    def test_reap_removes_a_withdrawn_interface(self):
        """The mirror-image of ATLASSN-144's problem: a criterion withdrawn from the file must
        not leave a stale row behind, the same discipline refresh_dim_session() uses."""
        writeGoals(self.projA, [criterion("I1"), criterion("I2")])
        iip.run(self.db, {"projA": self.projA})
        writeGoals(self.projA, [criterion("I1")])
        iip.run(self.db, {"projA": self.projA})
        conn = migrate.connect(str(self.db))
        ids = [r[0] for r in conn.execute(
            "SELECT interface_id FROM integration_interface WHERE project='projA'"
        ).fetchall()]
        conn.close()
        self.assertEqual(ids, ["I1"])

    def test_reap_to_zero_when_the_file_is_deleted(self):
        path = writeGoals(self.projA, [criterion("I1")])
        iip.run(self.db, {"projA": self.projA})
        path.unlink()
        iip.run(self.db, {"projA": self.projA})
        conn = migrate.connect(str(self.db))
        n = conn.execute(
            "SELECT COUNT(*) FROM integration_interface WHERE project='projA'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_reingest_is_idempotent_no_duplicate_rows(self):
        writeGoals(self.projA, [criterion("I1")])
        iip.run(self.db, {"projA": self.projA})
        iip.run(self.db, {"projA": self.projA})
        iip.run(self.db, {"projA": self.projA})
        conn = migrate.connect(str(self.db))
        n = conn.execute(
            "SELECT COUNT(*) FROM integration_interface WHERE project='projA' AND interface_id='I1'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    def test_updated_content_overwrites_in_place(self):
        writeGoals(self.projA, [criterion("I1", statement="old")])
        iip.run(self.db, {"projA": self.projA})
        writeGoals(self.projA, [criterion("I1", statement="new")])
        iip.run(self.db, {"projA": self.projA})
        conn = migrate.connect(str(self.db))
        statement = conn.execute(
            "SELECT statement FROM integration_interface WHERE project='projA' AND interface_id='I1'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(statement, "new")

    def test_two_projects_do_not_clobber_each_other(self):
        writeGoals(self.projA, [criterion("I1")])
        writeGoals(self.projB, [criterion("I1"), criterion("I2")])
        iip.run(self.db, {"projA": self.projA, "projB": self.projB})
        conn = migrate.connect(str(self.db))
        a = conn.execute(
            "SELECT COUNT(*) FROM integration_interface WHERE project='projA'"
        ).fetchone()[0]
        b = conn.execute(
            "SELECT COUNT(*) FROM integration_interface WHERE project='projB'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual((a, b), (1, 2))

    def test_a_read_failure_leaves_the_prior_row_set_untouched(self):
        """A transient read/parse error must not reap real rows to zero -- that would be worse
        than serving one cycle's stale data."""
        writeGoals(self.projA, [criterion("I1")])
        iip.run(self.db, {"projA": self.projA})
        (self.projA / ".foreman" / "GOALS.integration.json").write_text(
            "{not valid json", encoding="utf-8")
        results = iip.run(self.db, {"projA": self.projA})
        self.assertIsNotNone(results["projA"]["error"])
        conn = migrate.connect(str(self.db))
        n = conn.execute(
            "SELECT COUNT(*) FROM integration_interface WHERE project='projA'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db = self.root / "atlas.db"
        self.proj = self.root / "proj"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_verifiable_status_and_components_join(self):
        writeGoals(self.proj, [criterion("I1", components=["x", "y", "z"], verifiable=False,
                                          verification="BLOCKED -- reason")])
        iip.run(self.db, {"proj": self.proj})
        conn = migrate.connect(str(self.db))
        row = conn.execute(
            "SELECT components, status, notes FROM v_integration_progress "
            "WHERE project='proj' AND interface_id='I1'"
        ).fetchone()
        conn.close()
        components, status, notes = row
        self.assertEqual(components, "x, y, z")
        self.assertEqual(status, "not-verifiable")
        self.assertIn("BLOCKED -- reason", notes)

    def test_declared_not_exercised_status_never_fabricates_a_pass(self):
        writeGoals(self.proj, [criterion("I1", verifiable=True)])
        iip.run(self.db, {"proj": self.proj})
        conn = migrate.connect(str(self.db))
        row = conn.execute(
            "SELECT status, evidence_ref, exercised_by, exercised_at FROM v_integration_progress "
            "WHERE project='proj' AND interface_id='I1'"
        ).fetchone()
        conn.close()
        status, evidence_ref, exercised_by, exercised_at = row
        self.assertEqual(status, "declared-not-exercised")
        self.assertIsNone(evidence_ref)
        self.assertIsNone(exercised_by)
        self.assertIsNone(exercised_at)

    def test_a_real_result_overrides_the_constructed_default(self):
        """The view's status column must prefer a real recorded result over the constructed
        default the moment one exists -- proven by seeding one directly at the row level, since
        the ingest itself never fabricates one."""
        writeGoals(self.proj, [criterion("I1")])
        iip.run(self.db, {"proj": self.proj})
        conn = migrate.connect(str(self.db))
        conn.execute(
            "UPDATE integration_interface SET result_status='pass', "
            "result_evidence_ref='run-42', result_exercised_by='dana-okafor', "
            "result_exercised_at='2026-09-14T00:00:00Z' "
            "WHERE project='proj' AND interface_id='I1'"
        )
        conn.commit()
        row = conn.execute(
            "SELECT status, evidence_ref, exercised_by, notes FROM v_integration_progress "
            "WHERE project='proj' AND interface_id='I1'"
        ).fetchone()
        conn.close()
        status, evidence_ref, exercised_by, notes = row
        self.assertEqual(status, "pass")
        self.assertEqual(evidence_ref, "run-42")
        self.assertEqual(exercised_by, "dana-okafor")
        self.assertEqual(notes, "real execution result")


if __name__ == "__main__":
    unittest.main()
