"""GOALS.json C5. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.snapshot.tests.test_index_shape -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.snapshot.publisher import publish
from atlas.snapshot.tests._helpers import TempWarehouse, publish_kwargs

REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version", "generated_at", "refresh_cadence_seconds", "max_age_seconds",
    "warehouse_run_id", "cursors", "verdict_window_days", "rate_uninterpretable_handlers",
    "severity_rubric", "roots", "unresolved_scopes", "projects",
}

REQUIRED_PROJECT_KEYS = {
    "codename", "source_root", "root_is_shared", "root_state", "tickets", "open_total",
    "open_by_severity", "open_null_severity", "open_null_tier",
}


class IndexShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_root = Path(self.tmpdir.name) / "snapshot"
        self.wh = TempWarehouse()

    def tearDown(self):
        self.wh.close()
        self.tmpdir.cleanup()

    def test_index_json_has_every_documented_top_level_key(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        index = json.loads((gen / "index.json").read_text())
        self.assertEqual(set(index.keys()), REQUIRED_TOP_LEVEL_KEYS)

    def test_max_age_seconds_is_derived_not_independent(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs(refresh_cadence_seconds=120))
        index = json.loads((gen / "index.json").read_text())
        self.assertEqual(index["refresh_cadence_seconds"], 120)
        self.assertEqual(index["max_age_seconds"], 360)

    def test_every_project_has_every_documented_key(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        index = json.loads((gen / "index.json").read_text())
        for prefix, project in index["projects"].items():
            with self.subTest(prefix=prefix):
                self.assertEqual(set(project.keys()), REQUIRED_PROJECT_KEYS)

    def test_ticket_identity_holds_for_every_published_project(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        index = json.loads((gen / "index.json").read_text())
        for prefix, project in index["projects"].items():
            with self.subTest(prefix=prefix):
                by_sev_sum = sum(project["open_by_severity"].values())
                self.assertEqual(project["open_total"], project["open_null_severity"] + by_sev_sum)


if __name__ == "__main__":
    unittest.main()
