import json
import tempfile
import unittest
from pathlib import Path

from ..store import Store


class CommentSnippetTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_schema_has_code_snippet_column(self):
        conn = self.store.conn_internal()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(comments)").fetchall()}
        self.assertIn("code_snippet", cols)

    def test_add_comment_with_code_snippet_roundtrip(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.add_comment(tid, "agent", "fixed it", code_snippet="def f():\n    return 1")
        comments = self.store.get_ticket(tid)["comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["code_snippet"], "def f():\n    return 1")

    def test_add_comment_without_code_snippet_unchanged(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.add_comment(tid, "agent", "just a note")
        comments = self.store.get_ticket(tid)["comments"]
        self.assertEqual(comments[0]["code_snippet"], None)
        events = self.store.conn_internal().execute(
            "SELECT payload FROM events WHERE event_type='CommentAdded' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertNotIn("code_snippet", json.loads(events[0]))

    def test_rebuild_replays_historical_comments_as_null_snippet(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.add_comment(tid, "agent", "plain historical comment")
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["comments"]), sorted(rebuilt["comments"]))

    def test_backfill_appends_real_event_not_projection_mutation(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.add_comment(tid, "agent", "old-style: tessera/x.py:\n    def f():\n        pass")
        comment_id = self.store.get_ticket(tid)["comments"][0]["id"]

        events_before = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]

        self.store.set_comment_code_snippet(
            tid, comment_id, "migration-script", "def f():\n    pass"
        )

        events_after = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]
        self.assertEqual(events_after, events_before + 1)

        newest = self.store.conn_internal().execute(
            "SELECT event_type, payload FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(newest[0], "CommentCodeSnippetSet")

        # original CommentAdded body/payload is untouched
        original = self.store.conn_internal().execute(
            "SELECT payload FROM events WHERE event_type='CommentAdded' ORDER BY id LIMIT 1"
        ).fetchone()
        self.assertNotIn("code_snippet", json.loads(original[0]))

        comments = self.store.get_ticket(tid)["comments"]
        self.assertEqual(comments[0]["code_snippet"], "def f():\n    pass")
        self.assertIn("old-style: tessera/x.py", comments[0]["body"])

        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["comments"]), sorted(rebuilt["comments"]))

    def test_chain_integrity_after_backfill(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.add_comment(tid, "agent", "c1")
        self.store.add_comment(tid, "agent", "c2")
        comments = self.store.get_ticket(tid)["comments"]
        self.store.set_comment_code_snippet(tid, comments[0]["id"], "migration-script", "x = 1")
        self.store.set_comment_code_snippet(tid, comments[1]["id"], "migration-script", "y = 2")
        result = self.store.verify_chain()
        self.assertEqual(result["roots"], 1)
        self.assertEqual(result["tips"], 1)
        self.assertEqual(result["orphans"], 0)
        self.assertEqual(result["hash_mismatches"], 0)

    def test_set_comment_code_snippet_rejects_unknown_comment(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        with self.assertRaises(ValueError):
            self.store.set_comment_code_snippet(tid, 999999, "migration-script", "x = 1")
