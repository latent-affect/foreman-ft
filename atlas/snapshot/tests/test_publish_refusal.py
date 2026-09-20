"""GOALS.json C4. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.snapshot.tests.test_publish_refusal -v
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

    def test_refuses_when_status_is_bad_but_checks_were_recorded(self):
        # ATLASSN-139/SW1: isolates the run-status gate alone. The suite's only prior not-clean
        # fixture (test_refuses_when_warehouse_is_not_clean, above) seeds status='failed' via
        # TempWarehouse(clean=False), which never records any dq_check_run rows either -- so that
        # fixture trips this gate AND the vacuous-pass gate at once, and removing either alone
        # stays green (confirmed by mutation below).
        #
        # First attempt at this fixture used TempWarehouse(clean=False) + a single failed run with
        # checks attached, and the mutation check below caught a real construction bug: with no
        # 'ok' run EVER recorded, v_source_trust has no anchor to compute a trust state from, so
        # v_snapshot_publishable ALSO reports blocked -- disabling the run-status gate alone still
        # left the fixture refused, just via gate 3 instead of gate 1, silently. Fixed by seeding a
        # genuine clean 'ok' run first (TempWarehouse(clean=True), giving v_source_trust a real
        # anchor and leaving v_snapshot_publishable at publishable=1), then adding a SECOND, newer
        # run marked 'failed' with its own full dq_check_run rows -- v_atlas_status reads the
        # latest run_id, so this one gate alone is what can fail.
        wh = TempWarehouse(clean=True)
        run_id = migrate.new_ingest_run(wh.conn, status="failed")
        check_names = [r[0] for r in wh.conn.execute("SELECT check_name FROM dq_check")]
        for name in check_names:
            wh.conn.execute(
                "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
                "VALUES (?, ?, '2026-01-01T00:00:00Z', 1, 'synthetic-pass')",
                (run_id, name),
            )
        wh.conn.commit()
        # Confirm the fixture actually isolates the gate before trusting the refusal below.
        status_row = wh.conn.execute("SELECT status, checks_evaluated FROM v_atlas_status").fetchone()
        self.assertEqual(status_row, ("failed", len(check_names)),
                          "fixture setup error: checks_evaluated must be non-zero here")
        publishable_row = wh.conn.execute(
            "SELECT publishable, blocking_sources FROM v_snapshot_publishable"
        ).fetchone()
        self.assertEqual(publishable_row, (1, None),
                          "fixture setup error: snapshot_source must read clean, via the earlier "
                          "ok run, so only the status gate can be what refuses below")
        before = _snapshot_snapshot(self.snapshot_root)

        with self.assertRaises(PublishRefused) as ctx:
            publish(str(self.snapshot_root), wh.conn, **publish_kwargs())
        self.assertIn("not 'ok'", str(ctx.exception))
        self.assertNotIn("vacuous", str(ctx.exception))

        after = _snapshot_snapshot(self.snapshot_root)
        self.assertEqual(before, after)
        wh.close()

    def test_refuses_when_run_ok_but_zero_checks_recorded(self):
        # ATLASSN-139/SW1: isolates the vacuous-pass gate alone. status='ok' but this run's own
        # dq_check_run count is genuinely zero, so only the vacuous-pass branch can fire.
        wh = TempWarehouse(clean=False)
        migrate.new_ingest_run(wh.conn, status="ok")
        status_row = wh.conn.execute("SELECT status, checks_evaluated FROM v_atlas_status").fetchone()
        self.assertEqual(status_row, ("ok", 0),
                          "fixture setup error: status must be ok with zero checks evaluated")
        before = _snapshot_snapshot(self.snapshot_root)

        with self.assertRaises(PublishRefused) as ctx:
            publish(str(self.snapshot_root), wh.conn, **publish_kwargs())
        self.assertIn("vacuous pass", str(ctx.exception))

        after = _snapshot_snapshot(self.snapshot_root)
        self.assertEqual(before, after)
        wh.close()

    def test_refuses_when_a_snapshot_source_check_fails(self):
        # ATLASSN-139/SW1: isolates the per-source publishable gate alone, in its BLOCKING
        # direction -- the suite previously only tested the negative direction
        # (test_does_not_refuse_when_an_unrelated_non_snapshot_source_check_fails, above), never
        # the positive gate it guards actually refusing. verdict_domain_closed is source_table=
        # hook_verdict, which IS in snapshot_source (ARCHITECTURE.md section 5's own cited
        # example: "failing verdict_domain_closed gives publishable = 0 with
        # blocking_sources = hook_verdict").
        wh = TempWarehouse(clean=True)
        wh.fail_check("verdict_domain_closed")

        row = wh.conn.execute(
            "SELECT publishable, blocking_sources FROM v_snapshot_publishable"
        ).fetchone()
        self.assertEqual(row, (0, "hook_verdict"),
                          "fixture setup error: this check should block snapshot_source")
        before = _snapshot_snapshot(self.snapshot_root)

        with self.assertRaises(PublishRefused) as ctx:
            publish(str(self.snapshot_root), wh.conn, **publish_kwargs())
        self.assertIn("blocked by snapshot_source", str(ctx.exception))

        after = _snapshot_snapshot(self.snapshot_root)
        self.assertEqual(before, after)
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
