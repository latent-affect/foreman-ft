import tempfile
import unittest
from pathlib import Path

from ..store import Store


class HotlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")
        self.store.register_project("OTHERPROJ", "OP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_create_add_remove_show_roundtrip(self):
        self.store.create_hotlist("standup-2026-08-16", "agent")
        tp_tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent", project="TP")
        self.store.add_to_hotlist("standup-2026-08-16", tp_tid, "agent", note="mention in standup")
        hotlist = self.store.get_hotlist("standup-2026-08-16")
        self.assertEqual(hotlist["created_by"], "agent")
        self.assertEqual(len(hotlist["items"]), 1)
        self.assertEqual(hotlist["items"][0]["ticket_id"], tp_tid)
        self.assertEqual(hotlist["items"][0]["note"], "mention in standup")

        self.store.remove_from_hotlist("standup-2026-08-16", tp_tid, "agent")
        hotlist = self.store.get_hotlist("standup-2026-08-16")
        self.assertEqual(hotlist["items"], [])

    def test_cross_project_hotlist(self):
        # A hotlist entry is a ticket_id reference, which already discloses its project
        # via prefix -- one hotlist should freely span multiple projects.
        self.store.create_hotlist("acr-batch", "agent")
        tp_tid = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent", project="TP")
        op_tid = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent", project="OP")
        self.store.add_to_hotlist("acr-batch", tp_tid, "agent")
        self.store.add_to_hotlist("acr-batch", op_tid, "agent")
        hotlist = self.store.get_hotlist("acr-batch")
        ids = {item["ticket_id"] for item in hotlist["items"]}
        self.assertEqual(ids, {tp_tid, op_tid})

    def test_duplicate_name_rejected(self):
        self.store.create_hotlist("dup", "agent")
        with self.assertRaises(ValueError):
            self.store.create_hotlist("dup", "agent")

    def test_unknown_hotlist_and_ticket_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_to_hotlist("nope", "TP-1", "agent")
        self.store.create_hotlist("real", "agent")
        with self.assertRaises(ValueError):
            self.store.add_to_hotlist("real", "TP-999", "agent")

    def test_list_hotlists_shows_item_count(self):
        self.store.create_hotlist("empty-list", "agent")
        self.store.create_hotlist("has-one", "agent")
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent", project="TP")
        self.store.add_to_hotlist("has-one", tid, "agent")
        by_name = {h["name"]: h["item_count"] for h in self.store.list_hotlists()}
        self.assertEqual(by_name["empty-list"], 0)
        self.assertEqual(by_name["has-one"], 1)

    def test_rebuild_equals_live_for_hotlists(self):
        self.store.create_hotlist("rebuild-check", "agent")
        a = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent", project="TP")
        b = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent", project="OP")
        self.store.add_to_hotlist("rebuild-check", a, "agent", note="a note")
        self.store.add_to_hotlist("rebuild-check", b, "agent")
        self.store.remove_from_hotlist("rebuild-check", b, "agent")
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["hotlists"]), sorted(rebuilt["hotlists"]))
        self.assertEqual(sorted(live["hotlist_items"]), sorted(rebuilt["hotlist_items"]))


if __name__ == "__main__":
    unittest.main()
