import tempfile
import unittest
from pathlib import Path

from ..store import Store


class StageHeadsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_stage_heads_only_written_via_events_and_rebuild_covers_it(self):
        self.store.record_stage_promotion("dev", "commit1", "agent")
        head = self.store.get_stage_head("dev")
        self.assertEqual(head["commit_sha"], "commit1")

        events = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='StagePromoted'"
        ).fetchone()[0]
        self.assertEqual(events, 1)

        self.store.record_stage_rollback("dev", "commit0", "agent")
        head2 = self.store.get_stage_head("dev")
        self.assertEqual(head2["commit_sha"], "commit0")
        events2 = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='StageRolledBack'"
        ).fetchone()[0]
        self.assertEqual(events2, 1)

        # rebuild_projection() must include stage_heads and match the live table.
        rebuilt = self.store.rebuild_projection()
        self.assertIn("stage_heads", rebuilt)
        live = self.store.live_projection()
        self.assertEqual(sorted(live["stage_heads"]), sorted(rebuilt["stage_heads"]))

        # And a bare projection write with no backing event is detectable as drift.
        self.store.conn_internal().execute(
            "UPDATE stage_heads SET commit_sha='tampered' WHERE stage='dev'"
        )
        self.store.conn_internal().commit()
        live2 = self.store.live_projection()
        rebuilt2 = self.store.rebuild_projection()
        self.assertNotEqual(sorted(live2["stage_heads"]), sorted(rebuilt2["stage_heads"]))


if __name__ == "__main__":
    unittest.main()
