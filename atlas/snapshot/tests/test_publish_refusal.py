"""GOALS.json C4. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.snapshot.tests.test_publish_refusal -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.snapshot.publisher import PublishRefused, publish
from atlas.snapshot.tests._helpers import TempWarehouse, publish_kwargs
from atlas.warehouse import migrate


def _snapshot_snapshot(snapshot_root):
    """A cheap 'is anything on disk' check: does the root exist, and if so what's in it."""
    root = Path(snapshot_root)
    if not root.exists():
        return None
    return sorted(p.name for p in root.iterdir())


class PublishRefusalTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_root = Path(self.tmpdir.name) / "snapshot"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_refuses_when_warehouse_is_not_clean(self):
        wh = TempWarehouse(clean=False)
        migrate.new_ingest_run(wh.conn, status="failed")
        before = _snapshot_snapshot(self.snapshot_root)

        with self.assertRaises(PublishRefused) as ctx:
            publish(str(self.snapshot_root), wh.conn, **publish_kwargs())
        self.assertIn("not genuinely clean", str(ctx.exception))

        after = _snapshot_snapshot(self.snapshot_root)
        self.assertEqual(before, after)
        wh.close()

    def test_refuses_when_ticket_rollup_identity_fails(self):
        wh = TempWarehouse(clean=True)
        before = _snapshot_snapshot(self.snapshot_root)

        bad_rollup = {
            "TESS": {"open_total": 999, "open_by_severity": {"S3": 1}, "open_null_severity": 16, "open_null_tier": 15},
        }
        with self.assertRaises(PublishRefused) as ctx:
            publish(str(self.snapshot_root), wh.conn, **publish_kwargs(ticket_rollup=bad_rollup))
        self.assertIn("identity check failed", str(ctx.exception))

        after = _snapshot_snapshot(self.snapshot_root)
        self.assertEqual(before, after)
        wh.close()

    def test_does_not_refuse_when_an_unrelated_non_snapshot_source_check_fails(self):
        # FATAL regression test (adversarial-code-review Check 6): a contract failure on a
        # source the snapshot does NOT read (git_ticket_prefix_registered, source_table=
        # git_commit, not in snapshot_source which is scoped to hook_verdict only) must NOT
        # block publication -- the exact bug ARCHITECTURE.md section 5 documents as already
        # fixed via v_snapshot_publishable, which this component's own _warehouse_status()
        # was found to bypass entirely, querying warehouse-wide v_atlas_status instead.
        wh = TempWarehouse(clean=True)
        wh.fail_check("git_ticket_prefix_registered")

        # Confirm the fixture actually reproduces the scenario: v_snapshot_publishable itself
        # must still report publishable, proving the failure really is unrelated to snapshot_source.
        row = wh.conn.execute("SELECT publishable, blocking_sources FROM v_snapshot_publishable").fetchone()
        self.assertEqual(row, (1, None), "fixture setup error: this check should NOT block snapshot_source")

        gen = publish(str(self.snapshot_root), wh.conn, **publish_kwargs())
        self.assertTrue(gen.is_dir())
        self.assertTrue((gen / "index.json").is_file())
        wh.close()

    def test_a_refusal_after_a_successful_publish_does_not_disturb_current(self):
        wh = TempWarehouse(clean=True)
        publish(str(self.snapshot_root), wh.conn, **publish_kwargs())
        good_state = _snapshot_snapshot(self.snapshot_root)
        current_target_before = (self.snapshot_root / "current").resolve()

        bad_rollup = {"TESS": {"open_total": 999, "open_by_severity": {}, "open_null_severity": 0, "open_null_tier": 0}}
        with self.assertRaises(PublishRefused):
            publish(str(self.snapshot_root), wh.conn, **publish_kwargs(ticket_rollup=bad_rollup))

        self.assertEqual(_snapshot_snapshot(self.snapshot_root), good_state)
        self.assertEqual((self.snapshot_root / "current").resolve(), current_target_before)
        wh.close()


if __name__ == "__main__":
    unittest.main()
