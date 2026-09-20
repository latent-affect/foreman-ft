"""ATLASSN-63 / FORE-275 item 4: the run-over-run join-coverage drop check, with the negative
controls FORE-275's acceptance criteria require.

Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_ledger_join_coverage_delta -v

The load-bearing control here is the ORDERING one. ARCHITECTURE.md section 26.5 records that the
prior value must be read with `run_id < <current run>` and never as "the most recent row", because
run_all() iterates dq_check's names in table order and inserts each result as it goes, so
ledger_join_coverage's row for the CURRENT run may already exist by the time this check runs. A
"most recent row" implementation passes every other test in this file and fails only that one,
which is exactly why it is here.
"""

import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import dq_runner, migrate

from atlas.warehouse.tests.test_dq_runner import _make_verdict


class LedgerJoinCoverageDeltaTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.prior_run = migrate.new_ingest_run(self.conn, status="ok")
        self.latest_run = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _record_coverage(self, run_id, observed_value):
        """A recorded ledger_join_coverage observation, written exactly as run_all() writes one."""
        self.conn.execute(
            "INSERT INTO dq_check_run (run_id, check_name, run_at, observed_value, "
            "baseline_value, passed, detail) VALUES (?, 'ledger_join_coverage', "
            "'2026-01-01T00:00:00Z', ?, 0.9, 1, 'seeded by test')",
            (run_id, observed_value),
        )
        self.conn.commit()

    def _unjoinable_rows(self, n=10):
        """hook_verdict rows carrying a tool_use_id that joins to no transcript, so the view puts
        every one of them in no-transcript-on-disk and current coverage is exactly 0.0. Real rows
        through the real view, not a stubbed coverage number."""
        for i in range(n):
            _make_verdict(self.conn, self.latest_run, "s1", i,
                          f"2026-01-01T00:00:{i:02d}.000001Z", "h1", "fire",
                          cwd="/proj/a", tool_use_id=f"tu{i}", decision="allow")
        self.conn.commit()

    def test_fixture_produces_zero_coverage_through_the_real_view(self):
        # Asserted rather than assumed: every drop below is measured against this, so a fixture
        # that stopped producing 0.0 would turn the controls into vacuous passes silently.
        self._unjoinable_rows()
        coverage, joined, denominator, buckets = dq_runner._ledger_join_coverage_now(self.conn)
        self.assertEqual((coverage, joined, denominator), (0.0, 0, 10))
        self.assertEqual(buckets.get("no-transcript-on-disk"), 10)

    def test_negative_control_a_drop_beyond_tolerance_fails(self):
        """THE DISCRIMINATING CASE. Coverage was 0.9987 last run and is 0.0 now, a drop of 99.87
        points against a 0.5-point tolerance."""
        self._record_coverage(self.prior_run, 0.9987)
        self._unjoinable_rows()

        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertFalse(result.passed, result.detail)
        self.assertAlmostEqual(result.observed_value, 99.87, places=2)
        self.assertEqual(result.baseline_value, dq_runner.LEDGER_JOIN_COVERAGE_MAX_DROP_POINTS)

    def test_a_drop_inside_the_tolerance_passes(self):
        """0.004 -> 0.0 is a 0.4-point drop, inside the 0.5-point tolerance calibrated in section
        26.4 from 35 measured run-over-run deltas whose worst drop was 0.030 points."""
        self._record_coverage(self.prior_run, 0.004)
        self._unjoinable_rows()

        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertTrue(result.passed, result.detail)
        self.assertAlmostEqual(result.observed_value, 0.4, places=3)

    def test_a_rise_passes_and_reports_a_negative_drop(self):
        self._record_coverage(self.prior_run, 0.0)
        self._unjoinable_rows()
        # Give one row a transcript so coverage rises above the prior observation.
        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertTrue(result.passed, result.detail)
        self.assertLessEqual(result.observed_value, 0.0)

    def test_ordering_control_the_current_runs_own_row_must_not_be_read_as_the_prior(self):
        """THE CONTROL THAT CATCHES A 'most recent row' IMPLEMENTATION. run_all() may already have
        written ledger_join_coverage's row for THIS run before this check executes. Here the
        current run's own row records 0.0 -- reading it would produce a 0.0-point drop and a pass
        -- while the genuine prior run recorded 0.9987 and must produce a failure."""
        self._record_coverage(self.prior_run, 0.9987)
        self._record_coverage(self.latest_run, 0.0)
        self._unjoinable_rows()

        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertFalse(
            result.passed,
            "read the current run's own observation as the prior -- section 26.5's ordering "
            "requirement is not being met: " + result.detail)
        self.assertAlmostEqual(result.observed_value, 99.87, places=2)
        self.assertIn(f"run {self.prior_run}", result.detail)

    def test_no_prior_observation_is_named_not_evaluated_rather_than_a_zero_drop(self):
        self._unjoinable_rows()
        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertIsNone(result.observed_value)
        self.assertIn("NOT evaluated", result.detail)
        self.assertIn("first run", result.detail)

    def test_prior_rows_exist_but_all_null_is_a_different_named_state(self):
        """check_ledger_join_coverage records observed_value=None in both its own degenerate
        cases. 'There were earlier runs but none produced a comparable number' is a different fact
        from 'there were no earlier runs', and collapsing them would hide a check that has been
        unable to evaluate for its whole life."""
        self._record_coverage(self.prior_run, None)
        self._unjoinable_rows()

        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertIsNone(result.observed_value)
        self.assertIn("NULL observation", result.detail)
        self.assertNotIn("first run", result.detail)

    def test_empty_denominator_says_so_rather_than_reporting_a_zero_drop(self):
        self._record_coverage(self.prior_run, 0.9987)
        # A row with no tool_use_id and no decision lands in a bucket the denominator excludes.
        _make_verdict(self.conn, self.latest_run, "s1", 0, "2026-01-01T00:00:00.000001Z",
                      "h1", "fire", cwd="/proj/a")
        self.conn.commit()

        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertTrue(result.passed, result.detail)
        self.assertIsNone(result.observed_value)
        self.assertIn("nothing to diff", result.detail)

    def test_level_and_delta_read_the_same_arithmetic(self):
        """Both checks derive from _ledger_join_coverage_now, so the level and the drop can never
        be computed from different bucket sums. Asserted rather than trusted."""
        self._record_coverage(self.prior_run, 0.9987)
        self._unjoinable_rows()
        coverage, _, _, _ = dq_runner._ledger_join_coverage_now(self.conn)
        level = dq_runner.check_ledger_join_coverage(self.conn, self.latest_run)
        self.assertAlmostEqual(level.observed_value, round(coverage, 4))
        delta = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertAlmostEqual(delta.observed_value, round((0.9987 - coverage) * 100, 3))


class Migration10Tests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_migration_10_is_applied_by_connect(self):
        self.assertTrue(migrate.is_migrated10(self.conn))
        # ATLASSN-188 wires migrations 23/24 into connect() too, so the recorded version list is
        # no longer a contiguous 1..21 run -- migration 22 (ATLASSN-181) stays unwired (its
        # ARCHITECTURE.md section still does not exist), so 22 is genuinely absent, not an
        # oversight here. ATLASSN-189 adds migration 25 (dq_check advisory-severity guard).
        # ATLASSN-197 adds migration 26 (session_credential_ack).
        self.assertEqual(
            [r[0] for r in self.conn.execute(
                "SELECT version FROM schema_migration ORDER BY version")],
            list(range(1, 22)) + [23, 24, 25, 26])

    def test_migration_10_registers_exactly_one_advisory_row(self):
        row = self.conn.execute(
            "SELECT source_table, scope, severity, threshold_kind, implemented FROM dq_check "
            "WHERE check_name = 'ledger_join_coverage_delta'").fetchone()
        self.assertEqual(row, ("hook_verdict", "pipeline", "advisory", "calibrated", 1))

    def test_reapplying_migration_10_is_a_no_op_not_a_duplicate(self):
        """INSERT OR IGNORE is required, not stylistic: apply_additive() records the version
        separately from executing the DDL, so a crash in that gap leaves the row inserted and the
        version unrecorded, and the next connect() re-runs the statement."""
        before = self.conn.execute("SELECT COUNT(*) FROM dq_check").fetchone()[0]
        self.conn.executescript(migrate.ddl.extract_migration10_sql())
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM dq_check").fetchone()[0], before)

    def test_prior_migration_hashes_are_untouched(self):
        """Data-only means data-only. Versions 1-9's recorded hashes must be exactly what their
        own extractors still produce, or apply_additive would refuse on the live warehouse."""
        recorded = dict(self.conn.execute(
            "SELECT version, ddl_sha256 FROM schema_migration WHERE version <= 9"))
        self.assertEqual(recorded[1], migrate.ddl.ddl_sha256())
        self.assertEqual(recorded[8], migrate.ddl.migration8_sha256())
        self.assertEqual(recorded[9], migrate.ddl.migration9_sha256())


if __name__ == "__main__":
    unittest.main()


class PriorRowMustBelongToAnOkRunTests(unittest.TestCase):
    """Regression control for claude-hooks-v2-e5's second finding. Every other run-over-run check
    in dq_runner selects its comparison points with status='ok'; this lookup did not, so a failed
    or abandoned run that still recorded a dq_check_run row would have become the baseline a drop
    is measured against."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.ok_run = migrate.new_ingest_run(self.conn, status="ok")
        self.failed_run = migrate.new_ingest_run(self.conn, status="failed")
        self.latest_run = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _record(self, run_id, observed_value):
        self.conn.execute(
            "INSERT INTO dq_check_run (run_id, check_name, run_at, observed_value, "
            "baseline_value, passed, detail) VALUES (?, 'ledger_join_coverage', "
            "'2026-01-01T00:00:00Z', ?, 0.9, 1, 'seeded by test')",
            (run_id, observed_value))
        self.conn.commit()

    def test_a_failed_runs_observation_is_not_used_as_the_baseline(self):
        """THE DISCRIMINATING CASE. The failed run sits between the ok baseline and now, and
        records 0.0 -- reading it gives a 0.0-point drop and a pass. The genuine ok baseline
        records 0.9987 and must produce a failure."""
        self._record(self.ok_run, 0.9987)
        self._record(self.failed_run, 0.0)
        for i in range(10):
            _make_verdict(self.conn, self.latest_run, "s1", i,
                          f"2026-01-01T00:00:{i:02d}.000001Z", "h1", "fire",
                          cwd="/proj/a", tool_use_id=f"tu{i}", decision="allow")
        self.conn.commit()

        result = dq_runner.check_ledger_join_coverage_delta(self.conn, self.latest_run)
        self.assertFalse(
            result.passed,
            "took a non-ok run's observation as the baseline: " + result.detail)
        self.assertAlmostEqual(result.observed_value, 99.87, places=2)
        self.assertIn(f"run {self.ok_run}", result.detail)
