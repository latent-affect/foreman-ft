"""FORE-393 (FORE-329-FABLE-D): qa_pilot_pull's 4th source, the real Alice/Bob dispatch
by-session directory. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_qa_pilot_pull -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import qa_pilot_pull
from atlas.ingest.tests._helpers import TempDb


def make_by_session_record(dispatch_id="disp-1", kpi_results=None, tool_git_sha="a" * 40,
                            include_qa_verdict=True):
    record = {
        "record_schema": "foreman/dispatch/2",
        "dispatch_id": dispatch_id,
        "target_session_id": "sess-1",
        "status": "open",
    }
    if include_qa_verdict:
        record["qa_verdict"] = {
            "tool_git_sha": tool_git_sha,
            "tree_git_sha": "b" * 40,
            "formula_version": "1",
            "kpi_results": kpi_results if kpi_results is not None else {
                "evasion_shape_pattern5": True,
                "security_advisory": True,
            },
            "kpi_detail": {},
        }
    return record


class QaVerdictMapperTests(unittest.TestCase):
    """Unit-level tests of _rows_from_qa_verdict_record, isolated from the filesystem/db."""

    def test_maps_each_kpi_to_its_own_row(self):
        obj = make_by_session_record(kpi_results={"evasion_shape_pattern5": True, "security_advisory": False})
        rows = qa_pilot_pull._rows_from_qa_verdict_record("proj", Path("/x/r.json"), obj)
        names = {r["metric_name"]: r["metric_value"] for r in rows}
        self.assertEqual(names, {"qa_kpi_evasion_shape_pattern5": 1.0, "qa_kpi_security_advisory": 0.0})
        self.assertTrue(all(r["tool_git_sha"] == "a" * 40 for r in rows))
        self.assertTrue(all(r["report_kind"] == "qa_scorer_verdict" for r in rows))

    def test_missing_qa_verdict_key_yields_no_rows(self):
        """Negative control: a record with no qa_verdict at all (e.g. a hand-edited or
        differently-shaped file) must not crash and must not fabricate rows."""
        self.assertEqual(qa_pilot_pull._rows_from_qa_verdict_record("proj", Path("/x/r.json"), {"status": "open"}), [])

    def test_non_dict_kpi_results_yields_no_rows_not_a_crash(self):
        obj = make_by_session_record()
        obj["qa_verdict"]["kpi_results"] = None
        self.assertEqual(qa_pilot_pull._rows_from_qa_verdict_record("proj", Path("/x/r.json"), obj), [])


class RunDirectorySourceTests(unittest.TestCase):
    """End-to-end: run() against a real temp sqlite db and a real temp by-session directory,
    with the module's own SOURCES list monkeypatched to point at the fixture instead of the
    real 3 fixed paths and the real ~/.claude default -- proves the directory-glob branch
    itself, not whatever happens to exist on this machine's real dispatch root today."""

    def setUp(self):
        self.db = TempDb()
        self.addCleanup(self.db.close)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.by_session_dir = Path(self._tmpdir.name) / "by-session"
        self._orig_sources = qa_pilot_pull.SOURCES
        self.addCleanup(setattr, qa_pilot_pull, "SOURCES", self._orig_sources)

    def _set_sources(self, extra_dir_source=True):
        sources = []
        if extra_dir_source:
            sources.append(("test-fable-h", self.by_session_dir, qa_pilot_pull._rows_from_qa_verdict_record))
        qa_pilot_pull.SOURCES = sources

    def _write_record(self, name, obj):
        self.by_session_dir.mkdir(parents=True, exist_ok=True)
        (self.by_session_dir / name).write_text(json.dumps(obj))

    def test_directory_missing_is_skipped_not_a_crash(self):
        """The real-machine case right now: E1 is unlanded, nothing has ever written to the
        default dispatch root, so the directory does not exist at all yet."""
        self._set_sources()
        inserted, skipped, conn = qa_pilot_pull.run(warehouse_db_path=":memory:")
        conn.close()
        self.assertEqual(inserted, 0)
        self.assertIn(str(self.by_session_dir), skipped)

    def test_directory_present_but_empty_is_skipped(self):
        self.by_session_dir.mkdir(parents=True)
        self._set_sources()
        inserted, skipped, conn = qa_pilot_pull.run(warehouse_db_path=":memory:")
        conn.close()
        self.assertEqual(inserted, 0)
        self.assertIn(str(self.by_session_dir), skipped)

    def test_real_shaped_record_ingests_two_kpi_rows(self):
        self._write_record("sess-1.json", make_by_session_record())
        self._set_sources()
        inserted, skipped, conn = qa_pilot_pull.run(warehouse_db_path=":memory:")
        rows = conn.execute(
            "SELECT metric_name, metric_value, source_project, report_kind FROM qa_pilot_snapshot ORDER BY metric_name"
        ).fetchall()
        conn.close()
        self.assertEqual(inserted, 2, skipped)
        self.assertEqual(rows[0][0], "qa_kpi_evasion_shape_pattern5")
        self.assertEqual(rows[0][1], 1.0)
        self.assertEqual(rows[0][2], "test-fable-h")
        self.assertEqual(rows[0][3], "qa_scorer_verdict")

    def test_multiple_real_dispatch_records_all_ingest(self):
        self._write_record("sess-1.json", make_by_session_record(dispatch_id="disp-1"))
        self._write_record("sess-2.json", make_by_session_record(dispatch_id="disp-2",
                                                                   kpi_results={"evasion_shape_pattern5": False}))
        self._set_sources()
        inserted, skipped, conn = qa_pilot_pull.run(warehouse_db_path=":memory:")
        conn.close()
        self.assertEqual(inserted, 3, skipped)

    def test_malformed_json_file_is_skipped_not_a_crash_for_the_whole_directory(self):
        """Negative control proving one bad file doesn't take down the whole ingest: a real
        record next to a corrupt one, both present, only the real one lands."""
        self._write_record("sess-1.json", make_by_session_record())
        self.by_session_dir.mkdir(parents=True, exist_ok=True)
        (self.by_session_dir / "sess-2.json").write_text("{not valid json")
        self._set_sources()
        inserted, skipped, conn = qa_pilot_pull.run(warehouse_db_path=":memory:")
        conn.close()
        self.assertEqual(inserted, 2)
        self.assertTrue(any("sess-2.json" in s and "unreadable" in s for s in skipped), skipped)

    def test_record_without_qa_verdict_contributes_zero_rows_but_does_not_crash(self):
        self._write_record("sess-1.json", make_by_session_record(include_qa_verdict=False))
        self._set_sources()
        inserted, skipped, conn = qa_pilot_pull.run(warehouse_db_path=":memory:")
        conn.close()
        self.assertEqual(inserted, 0)

    def test_existing_three_fixed_file_sources_are_unaffected_by_the_directory_branch(self):
        """Backward-compatibility check: a fixed-file source (the original 3 SOURCES entries'
        own shape) must still take the file branch, not the new directory branch, and must
        still ingest exactly as before."""
        report_path = Path(self._tmpdir.name) / "quality_baseline_report.json"
        report_path.write_text(json.dumps({
            "meta": {"python_files_analyzed": 12},
            "aggregate": {"simple_average_health_1_to_10": 7.5},
        }))
        qa_pilot_pull.SOURCES = [
            ("fixture-project", report_path, qa_pilot_pull._rows_from_quality_baseline),
        ]
        inserted, skipped, conn = qa_pilot_pull.run(warehouse_db_path=":memory:")
        rows = conn.execute("SELECT metric_name, metric_value FROM qa_pilot_snapshot ORDER BY metric_name").fetchall()
        conn.close()
        # hotspot_signal_degenerate_churn is pre-existing behavior of the untouched
        # _rows_from_quality_baseline mapper (its value defaults to 0, not None, so it is
        # never skipped) -- not something this ticket's change introduces or should mask.
        self.assertEqual(inserted, 3, skipped)
        self.assertEqual(dict(rows), {
            "python_files_analyzed": 12.0,
            "hotspot_signal_degenerate_churn": 0.0,
            "simple_average_health_1_to_10": 7.5,
        })


if __name__ == "__main__":
    unittest.main()
