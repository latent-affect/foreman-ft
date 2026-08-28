"""GOALS.json C2, C3. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.hookclient.tests.test_freshness -v
"""

import datetime
import unittest

from atlas.hookclient.reader import read_index
from atlas.hookclient.tests._helpers import DEFAULT_INDEX, FixtureSnapshot


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureSnapshot()

    def tearDown(self):
        self.fixture.close()

    def test_fresh_snapshot_is_not_stale(self):
        self.fixture.write_generation("gen-1")  # generated_at 2026-01-01T00:00:00, max_age=900
        now = datetime.datetime(2026, 1, 1, 0, 5, 0, tzinfo=datetime.timezone.utc)  # +300s
        result = read_index(str(self.fixture.root), now=now)
        self.assertTrue(result.ok)
        self.assertFalse(result.is_stale)
        self.assertAlmostEqual(result.age_seconds, 300, delta=1)

    def test_aged_out_snapshot_is_stale_but_still_ok_with_data(self):
        self.fixture.write_generation("gen-1")
        now = datetime.datetime(2026, 1, 1, 0, 20, 0, tzinfo=datetime.timezone.utc)  # +1200s > 900
        result = read_index(str(self.fixture.root), now=now)
        self.assertTrue(result.ok, "staleness alone must not be a failure mode")
        self.assertTrue(result.is_stale)
        self.assertIsNotNone(result.index)

    def test_exactly_at_the_boundary_is_not_yet_stale(self):
        self.fixture.write_generation("gen-1")
        now = datetime.datetime(2026, 1, 1, 0, 15, 0, tzinfo=datetime.timezone.utc)  # exactly +900s
        result = read_index(str(self.fixture.root), now=now)
        self.assertFalse(result.is_stale)


if __name__ == "__main__":
    unittest.main()
