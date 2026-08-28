import tempfile
import unittest
from pathlib import Path

from ..exceptions import SameProjectError
from ..store import Store


class ReassignProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="SOURCE", prefix="SRC")
        self.store.register_project("TARGET", "TGT")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_reassign_mints_new_ticket_in_target_project_and_closes_old(self):
        s = self.store
        old = s.create_ticket(
            ticket_type="Bug", reporter="jon", actor="agent", priority=2, severity=1,
            summary="static key detection", description="details here",
            custom_fields={"team": "core"},
        )
        s.set_reference_docs(old, "agent", ["docs/stems.zip"])
        s.add_comment(old, "agent", "investigation lead: check the keyboard stem")

        new = s.reassign_project(old, "agent", "TGT")
        self.assertTrue(new.startswith("TGT-"))

        new_ticket = s.get_ticket(new)
        self.assertEqual(new_ticket["project_prefix"], "TGT")
        self.assertEqual(new_ticket["status"], "open")
        self.assertEqual(new_ticket["summary"], "static key detection")
        self.assertEqual(new_ticket["description"], "details here")
        self.assertEqual(new_ticket["priority"], 2)
        self.assertEqual(new_ticket["severity"], 1)
        self.assertEqual(new_ticket["custom_fields"], {"team": "core"})
        self.assertEqual(new_ticket["reference_docs"], ["docs/stems.zip"])
        self.assertEqual(len(new_ticket["comments"]), 1)
        self.assertEqual(new_ticket["comments"][0]["body"], "investigation lead: check the keyboard stem")

        old_ticket = s.get_ticket(old)
        self.assertEqual(old_ticket["status"], "closed")
        self.assertEqual(len(old_ticket["comments"]), 2)
        self.assertIn(new, old_ticket["comments"][-1]["body"])

    def test_reassign_to_same_project_rejected(self):
        s = self.store
        old = s.create_ticket(ticket_type="Task", reporter="jon", actor="agent")
        with self.assertRaises(SameProjectError):
            s.reassign_project(old, "agent", "SRC")

    def test_reassign_unknown_ticket_rejected(self):
        s = self.store
        with self.assertRaises(ValueError):
            s.reassign_project("SRC-999", "agent", "TGT")

    def test_reassign_leaves_old_ticket_open_when_blocked(self):
        s = self.store
        old = s.create_ticket(ticket_type="Task", reporter="jon", actor="agent")
        blocker = s.create_ticket(ticket_type="Task", reporter="jon", actor="agent")
        s.add_link(old, blocker, "blocked-by", "agent")

        new = s.reassign_project(old, "agent", "TGT")
        old_ticket = s.get_ticket(old)
        self.assertEqual(old_ticket["status"], "open")
        self.assertTrue(any(new in c["body"] for c in old_ticket["comments"]))

    def test_rebuild_projection_matches_live_after_reassign(self):
        s = self.store
        old = s.create_ticket(
            ticket_type="Bug", reporter="jon", actor="agent",
            summary="s", description="d", custom_fields={"x": 1},
        )
        s.set_reference_docs(old, "agent", ["a.txt", "b.txt"])
        s.add_comment(old, "agent", "note one")
        s.add_comment(old, "agent", "note two")
        s.reassign_project(old, "agent", "TGT")

        live = s.live_projection()
        rebuilt = s.rebuild_projection()
        self.assertEqual(set(live.keys()), set(rebuilt.keys()))
        for table in live:
            self.assertEqual(
                sorted(live[table]), sorted(rebuilt[table]),
                f"table {table!r} diverged between live and rebuilt projections",
            )


if __name__ == "__main__":
    unittest.main()
