import tempfile
import unittest
from pathlib import Path

from ..store import Store


class ColumnDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_set_and_list_column_description(self):
        self.store.set_column_description("tickets", "severity", "S0-S4, lower is worse", "agent")
        descriptions = self.store.list_column_descriptions()
        self.assertEqual(descriptions["tickets"]["severity"], "S0-S4, lower is worse")

    def test_resetting_a_description_overwrites_not_duplicates(self):
        self.store.set_column_description("tickets", "severity", "first", "agent")
        self.store.set_column_description("tickets", "severity", "second", "agent")
        descriptions = self.store.list_column_descriptions()
        self.assertEqual(descriptions["tickets"]["severity"], "second")

    def test_descriptions_grouped_by_table(self):
        self.store.set_column_description("tickets", "severity", "sev", "agent")
        self.store.set_column_description("tickets", "priority", "pri", "agent")
        self.store.set_column_description("comments", "body", "the comment text", "agent")
        descriptions = self.store.list_column_descriptions()
        self.assertEqual(set(descriptions["tickets"]), {"severity", "priority"})
        self.assertEqual(set(descriptions["comments"]), {"body"})

    def test_no_descriptions_set_returns_empty_dict(self):
        self.assertEqual(self.store.list_column_descriptions(), {})

    def test_rebuild_equals_live_for_column_descriptions(self):
        self.store.set_column_description("tickets", "severity", "sev", "agent")
        self.store.set_column_description("tickets", "severity", "sev, revised", "agent")
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["column_descriptions"]), sorted(rebuilt["column_descriptions"]))


if __name__ == "__main__":
    unittest.main()
