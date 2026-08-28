import tempfile
import unittest
from pathlib import Path

from ..store import Store


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")
        self.store.register_project("OTHERPROJ", "OP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_list_datasets_defaults_to_one_per_project(self):
        # "right now is probably just projects" -- with zero custom datasets, list_datasets
        # returns exactly one implicit entry per registered project.
        datasets = self.store.list_datasets()
        by_name = {d["name"]: d for d in datasets}
        self.assertEqual(set(by_name), {"TP", "OP"})
        self.assertEqual(by_name["TP"]["kind"], "project")
        self.assertEqual(by_name["TP"]["projects"], [{"prefix": "TP", "id": 1}])

    def test_create_custom_dataset_spanning_multiple_projects(self):
        self.store.create_dataset("everything", "agent")
        self.store.add_project_to_dataset("everything", "TP", "agent")
        self.store.add_project_to_dataset("everything", "OP", "agent")
        datasets = self.store.list_datasets()
        custom = next(d for d in datasets if d["name"] == "everything")
        self.assertEqual(custom["kind"], "custom")
        self.assertEqual({p["prefix"] for p in custom["projects"]}, {"TP", "OP"})
        # The implicit per-project entries are still present alongside the custom one.
        self.assertIn("TP", {d["name"] for d in datasets})

    def test_duplicate_dataset_name_rejected(self):
        self.store.create_dataset("dup", "agent")
        with self.assertRaises(ValueError):
            self.store.create_dataset("dup", "agent")

    def test_unknown_dataset_and_project_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_project_to_dataset("nope", "TP", "agent")
        self.store.create_dataset("real", "agent")
        with self.assertRaises(ValueError):
            self.store.add_project_to_dataset("real", "NOPE", "agent")

    def test_rebuild_equals_live_for_datasets(self):
        self.store.create_dataset("everything", "agent")
        self.store.add_project_to_dataset("everything", "TP", "agent")
        self.store.add_project_to_dataset("everything", "OP", "agent")
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["datasets"]), sorted(rebuilt["datasets"]))
        self.assertEqual(sorted(live["dataset_projects"]), sorted(rebuilt["dataset_projects"]))


if __name__ == "__main__":
    unittest.main()
