import tempfile
import unittest
from pathlib import Path

from ..store import Store, render_current_state_text


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


class RenderCurrentStateTextTests(unittest.TestCase):
    """REQ-25 (Foreman v2.0 PRD) Phase 1: item-granularity-only truncation, no DB dependency."""

    def _hotlist(self, items):
        return {"name": "test-hotlist", "created_at": "t1", "created_by": "agent", "items": items}

    def _item(self, ticket_id, status, summary):
        return {"ticket_id": ticket_id, "status": status, "summary": summary}

    def test_all_items_included_when_budget_is_generous(self):
        hotlist = self._hotlist([
            self._item("TP-1", "open", "first"),
            self._item("TP-2", "closed", "second"),
        ])
        text = render_current_state_text(hotlist, budget_chars=10_000)
        self.assertIn("TP-1", text)
        self.assertIn("TP-2", text)
        self.assertNotIn("omitted", text)

    def test_item_granularity_truncation_never_cuts_mid_line(self):
        """The real bug FORE-38's own design pass found and fixed: truncating at an
        arbitrary character offset can cut a line in half. This is the permanent regression
        test for that fix -- every line in the output must be a complete, real line from the
        input, never a fragment."""
        hotlist = self._hotlist([
            self._item("TP-1", "open", "a fairly long first summary that takes some real space"),
            self._item("TP-2", "open", "a fairly long second summary that also takes real space"),
            self._item("TP-3", "open", "a third one, also long, to force a real truncation point"),
        ])
        full_lines = {
            f"{item['ticket_id']} [{item['status']}] {item['summary']}"
            for item in hotlist["items"]
        }
        # Budget deliberately sized to land mid-way through the second item's line.
        text = render_current_state_text(hotlist, budget_chars=120)
        body_lines = [
            line for line in text.splitlines()
            if line and not line.startswith("#") and not line.startswith("...")
        ]
        for line in body_lines:
            self.assertIn(line, full_lines, f"line is a fragment, not a real complete line: {line!r}")

    def test_omitted_count_is_accurate(self):
        hotlist = self._hotlist([self._item(f"TP-{i}", "open", "x" * 50) for i in range(10)])
        text = render_current_state_text(hotlist, budget_chars=200)
        included = sum(1 for line in text.splitlines() if line.startswith("TP-"))
        self.assertIn(f"{10 - included} more item(s) omitted", text)

    def test_smallest_possible_budget_still_includes_at_least_one_item(self):
        """Even a budget smaller than the first item's own line must still include that item
        whole, rather than producing an empty or fragmentary result -- the header already
        establishes the item count, so an empty body would be a worse signal than one
        over-budget item."""
        hotlist = self._hotlist([self._item("TP-1", "open", "a summary longer than the budget")])
        text = render_current_state_text(hotlist, budget_chars=5)
        self.assertIn("TP-1", text)

    def test_empty_hotlist_renders_cleanly(self):
        hotlist = self._hotlist([])
        text = render_current_state_text(hotlist, budget_chars=500)
        self.assertIn("0 items", text)
        self.assertNotIn("omitted", text)

    def test_missing_hotlist_returns_none_from_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "test.db", codename="TESTPROJ", prefix="TP")
            self.assertIsNone(store.render_current_state("nonexistent", budget_chars=500))

    def test_render_reflects_live_status_not_a_cached_value(self):
        """REQ-25's core design principle: status is a live fact, read fresh at render time,
        not cached from when the item was added to the hotlist."""
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "test.db", codename="TESTPROJ", prefix="TP")
            tid = store.create_ticket(
                ticket_type="Task", reporter="me", actor="agent",
                priority=2, severity=2, project="TP",
            )
            store.create_hotlist("watch", "agent")
            store.add_to_hotlist("watch", tid, "agent")
            text_before = store.render_current_state("watch", budget_chars=1000)
            self.assertIn("[open]", text_before)

            # A ticket cannot enter in_progress without frozen criteria first (real friction
            # this store enforces -- see REQ-25's own PRD text on this exact behavior).
            store.freeze_ticket_criteria(tid, "agent", [
                {"id": "C1", "statement": "x", "verification": "y", "verifiable": True},
            ])
            store.transition_status(tid, "agent", "in_progress")
            text_after = store.render_current_state("watch", budget_chars=1000)
            self.assertIn("[in_progress]", text_after)
            self.assertNotIn("[open]", text_after)


if __name__ == "__main__":
    unittest.main()
