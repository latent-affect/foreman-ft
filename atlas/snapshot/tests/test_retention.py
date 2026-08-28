"""GOALS.json C3. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.snapshot.tests.test_retention -v
"""

import datetime
import tempfile
import unittest
from pathlib import Path

from atlas.snapshot.clock import FakeClock
from atlas.snapshot.publisher import publish
from atlas.snapshot.tests._helpers import TempWarehouse, publish_kwargs


def _generation_names(snapshot_root):
    return sorted(p.name for p in Path(snapshot_root).iterdir() if p.name.startswith("gen-"))


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_root = Path(self.tmpdir.name) / "snapshot"
        self.wh = TempWarehouse()
        self.clock = FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))

    def tearDown(self):
        self.wh.close()
        self.tmpdir.cleanup()

    def test_newest_3_survive_even_when_old(self):
        # refresh_cadence_seconds=300 -> max_age_seconds=900. Publish 3 generations 1000s
        # apart (each individually older than max_age once the next one lands), then assert
        # all 3 survive because retain_newest keeps them regardless of age.
        for i in range(3):
            publish(str(self.snapshot_root), self.wh.conn, clock=self.clock, **publish_kwargs(warehouse_run_id=i))
            self.clock.advance(1000)
        self.assertEqual(len(_generation_names(self.snapshot_root)), 3)

    def test_a_4th_publish_removes_only_the_generation_that_is_both_old_and_not_newest_3(self):
        for i in range(3):
            publish(str(self.snapshot_root), self.wh.conn, clock=self.clock, **publish_kwargs(warehouse_run_id=i))
            self.clock.advance(1000)  # each > 900s (max_age) older than the next by the time it lands
        before_4th = set(_generation_names(self.snapshot_root))
        self.assertEqual(len(before_4th), 3)

        publish(str(self.snapshot_root), self.wh.conn, clock=self.clock, **publish_kwargs(warehouse_run_id=3))
        after_4th = set(_generation_names(self.snapshot_root))

        # The oldest of the original 3 is now 3000s old (> 900s) and no longer in the newest 3
        # (there are now 4 total) -- it must be gone. The newest 3 (2nd, 3rd, 4th) survive.
        self.assertEqual(len(after_4th), 3)
        oldest_original = min(before_4th)
        self.assertNotIn(oldest_original, after_4th)

    def test_a_recent_generation_outside_the_newest_3_still_survives(self):
        # 5 publishes with only 10s between each -- all well within max_age_seconds=900, so
        # even generations 4th-and-5th-newest survive on AGE grounds alone.
        for i in range(5):
            publish(str(self.snapshot_root), self.wh.conn, clock=self.clock, **publish_kwargs(warehouse_run_id=i))
            self.clock.advance(10)
        self.assertEqual(len(_generation_names(self.snapshot_root)), 5)


if __name__ == "__main__":
    unittest.main()
