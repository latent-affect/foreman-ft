"""FORE-275 item 2 and FORE-312: the two dq_runner changes, each with a DISCRIMINATING
negative control.

Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_dq_cohort_and_cwd_floor -v

FORE-275's acceptance criteria require every dq_runner change to ship with a control proving it
catches the condition it claims to catch, not merely that the code runs. A control that passes
alongside the treatment proves nothing, so each change here is tested against a fixture the OLD
behaviour verifiably gets wrong: the cwd floor against a warehouse whose row-weighted rate clears
the floor while its cwd-weighted rate does not, and the pairing cohort against an increase made
entirely of late-arriving history. Both fixtures are asserted to be discriminating -- the tests
check the old verdict as well as the new one, so a regression that flattens the distinction fails
here rather than passing quietly.
"""

import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import dq_runner, migrate

from atlas.warehouse.tests.test_dq_runner import _make_cwd, _make_project, _make_verdict


def _set_started_at(conn, run_id, started_at):
    """new_ingest_run() stamps started_at with wall-clock now, which is the right behaviour for
    the pipeline and useless for a test that needs a controlled boundary. Rewritten rather than
    parameterised so the production helper stays exactly as the pipeline calls it."""
    conn.execute("UPDATE ingest_run SET started_at = ? WHERE run_id = ?", (started_at, run_id))
    conn.commit()


class DistinctCwdFloorTests(unittest.TestCase):
    """FORE-275 item 2. The floor must stop crediting one heavily-used registered cwd as broad
    coverage."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        # Nine of ten registered projects are real git repos, so _git_repo_fraction() -- the floor
        # both rates are compared against -- is exactly 0.9000. Chosen so the fixture can sit a
        # row rate above it and a cwd rate below it, which is the whole point of the control.
        for i in range(9):
            _make_project(self.conn, f"P{i}", f"/proj/{i}", root_state="git-repo")
        _make_project(self.conn, "P9", "/proj/9", root_state="root-missing")
        _make_cwd(self.conn, "/proj/0", "P0", resolution="unique")
        self.conn.commit()
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _busy_registered_cwd(self, n=100):
        for i in range(n):
            _make_verdict(self.conn, self.run_id, "s1", i,
                          f"2026-01-01T00:00:{i % 60:02d}.000001Z", "h1", "fire", cwd="/proj/0")

    def test_floor_is_0_9_as_the_fixture_intends(self):
        # Asserted rather than assumed: every other test here depends on the floor sitting
        # between the two rates, so a fixture drift that moved it would otherwise turn these
        # controls into vacuous passes without saying so.
        self.assertAlmostEqual(dq_runner._git_repo_fraction(self.conn), 0.9)

    def test_negative_control_row_rate_clears_the_floor_while_cwd_rate_does_not(self):
        """THE DISCRIMINATING CASE. One registered cwd carries 100 rows; five scratch directories
        contribute one row each. Row-weighted resolution is 100/105 = 0.952 and clears 0.9000.
        Distinct-cwd resolution is 1/6 = 0.167 and does not. This is the shape
        ATLAS-COVERAGE-STRUCTURAL-FINDING.md names -- 'the denominators are rows, and the losses
        are cwds' -- and the pre-FORE-275 check passed on it."""
        self._busy_registered_cwd()
        for i in range(5):
            _make_cwd(self.conn, f"/scratch/{i}", None, resolution="unregistered",
                      candidate_count=0)
            _make_verdict(self.conn, self.run_id, "s2", i,
                          f"2026-01-01T01:00:{i:02d}.000001Z", "h1", "fire", cwd=f"/scratch/{i}")
        self.conn.commit()

        result = dq_runner.check_project_resolution_floor(self.conn, self.run_id)

        # The old behaviour, recomputed here rather than described, so this control is proven to
        # discriminate instead of asserted to: on this fixture the row rate alone PASSES.
        row_rate = 100 / 105
        self.assertGreaterEqual(row_rate, 0.9,
                                "fixture no longer discriminates: the row rate must clear the "
                                "floor, or this test cannot show the cwd floor caught anything")
        unique_cwds, total_cwds = dq_runner._distinct_cwd_resolution(self.conn)
        self.assertEqual((unique_cwds, total_cwds), (1, 6))

        self.assertFalse(result.passed, result.detail)
        self.assertIn("BELOW", result.detail)
        # observed_value stays the ROW rate, deliberately, so the recorded dq_check_run series
        # remains comparable across this change. Regressing that is a silent data loss.
        self.assertAlmostEqual(result.observed_value, round(row_rate, 4))

    def test_passes_when_coverage_is_broad_by_both_measures(self):
        self._busy_registered_cwd()
        for i in range(1, 6):
            _make_cwd(self.conn, f"/proj/{i}", f"P{i}", resolution="unique")
            _make_verdict(self.conn, self.run_id, "s2", i,
                          f"2026-01-01T01:00:{i:02d}.000001Z", "h1", "fire", cwd=f"/proj/{i}")
        self.conn.commit()

        result = dq_runner.check_project_resolution_floor(self.conn, self.run_id)
        self.assertTrue(result.passed, result.detail)
        self.assertNotIn("BELOW", result.detail)

    def test_all_null_cwds_reports_not_evaluated_rather_than_passing_the_cwd_floor(self):
        """F1 signature: a guard must never report passed on a condition it could not evaluate.
        With no cwd anywhere the cwd floor has an empty denominator, and that is a different fact
        from 'coverage is broad'."""
        for i in range(10):
            _make_verdict(self.conn, self.run_id, "s1", i,
                          f"2026-01-01T00:00:{i:02d}.000001Z", "h1", "fire", cwd=None)
        self.conn.commit()

        result = dq_runner.check_project_resolution_floor(self.conn, self.run_id)
        self.assertIn("NOT EVALUATED", result.detail)
        self.assertEqual(dq_runner._distinct_cwd_resolution(self.conn), (0, 0))


class FailOpenPairingCohortTests(unittest.TestCase):
    """FORE-312. An increase in the unmatched count is only a regression to the extent it is
    attributable to events that OCCURRED in the window. ATLAS-COVERAGE-STRUCTURAL-FINDING.md:
    'It cannot distinguish "24 new fail-open errors happened since the last run" from "24 old
    fail-open errors were ingested for the first time since the last run." Both are an increase
    in a count.'"""

    BOUNDARY = "2026-03-01T12:00:00.000000+00:00"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.prior_run = migrate.new_ingest_run(self.conn, status="ok")
        _set_started_at(self.conn, self.prior_run, self.BOUNDARY)
        self.latest_run = migrate.new_ingest_run(self.conn, status="ok")
        _set_started_at(self.conn, self.latest_run, "2026-03-01T18:00:00.000000+00:00")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _unpaired_error(self, byte_offset, ts):
        _make_verdict(self.conn, self.latest_run, "s1", byte_offset, ts,
                      "architecture_gate.py", "error", cwd="/proj/a")

    def test_negative_control_increase_made_only_of_late_arriving_history_now_passes(self):
        """THE DISCRIMINATING CASE. Three unpaired error rows arrive in the latest run, but every
        one of them describes an event from BEFORE the prior run started. The count rises by 3 and
        nothing regressed; the pre-FORE-312 check failed on exactly this."""
        for i, ts in enumerate(("2026-02-01T09:00:00.000001Z",
                                "2026-02-14T22:30:00.000001Z",
                                "2026-02-28T23:59:59.000001Z")):
            self._unpaired_error(i, ts)
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, self.latest_run)

        # Proven to discriminate rather than asserted to: the delta really did rise, so the old
        # "fails on any increase" rule would have failed here. If a future change makes the delta
        # zero on this fixture the control has stopped controlling, and this assertion says so.
        self.assertEqual(result.observed_value, 3,
                         "fixture no longer discriminates: the unmatched count must actually "
                         "rise, or passing proves nothing about the cohort split")
        self.assertTrue(result.passed, result.detail)
        self.assertIn("3 late-arriving history, 0 newly occurred", result.detail)

    def test_increase_made_of_newly_occurred_errors_still_fails(self):
        """The other half of the control. Same count, same shape, timestamps after the boundary:
        the check must still fail, or FORE-312 would have disarmed it rather than sharpened it."""
        for i, ts in enumerate(("2026-03-01T13:00:00.000001Z",
                                "2026-03-01T14:00:00.000001Z",
                                "2026-03-01T15:00:00.000001Z")):
            self._unpaired_error(i, ts)
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, self.latest_run)
        self.assertEqual(result.observed_value, 3)
        self.assertFalse(result.passed, result.detail)
        self.assertIn("0 late-arriving history, 3 newly occurred", result.detail)

    def test_mixed_increase_fails_on_the_news_component_alone(self):
        self._unpaired_error(0, "2026-02-01T09:00:00.000001Z")
        self._unpaired_error(1, "2026-03-01T13:00:00.000001Z")
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, self.latest_run)
        self.assertFalse(result.passed, result.detail)
        self.assertIn("1 late-arriving history, 1 newly occurred", result.detail)

    def test_unreadable_timestamp_counts_as_news_and_is_disclosed(self):
        """Fails toward reporting, never toward excusing. A timestamp the checker cannot parse
        must not become the reason a real regression is written off as history, and the count of
        such rows is stated rather than folded silently into the news figure."""
        self._unpaired_error(0, "not-a-timestamp")
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, self.latest_run)
        self.assertFalse(result.passed, result.detail)
        self.assertIn("unreadable ts", result.detail)

    def test_audit_side_orphans_are_attributed_too_not_just_verdict_rows(self):
        """ATLASSN-52 established that this check is two-sided. The cohort split has to cover the
        audit side as well, or an audit-only orphan is silently unattributed."""
        self.conn.execute(
            "INSERT INTO audit_event (stream_id, byte_offset, ingest_run_id, source_path, ts, "
            "event_type, cwd, payload_json, payload_bytes) VALUES ('a', 0, ?, "
            "'/tmp/safety.jsonl', '2026-02-01T09:00:00.000001Z', 'HOOK_ERROR', '/proj/a', "
            "'{\"hook\": \"architecture_gate.py\"}', 40)",
            (self.latest_run,),
        )
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, self.latest_run)
        self.assertEqual(result.observed_value, 1)
        self.assertIn("1 late-arriving history, 0 newly occurred", result.detail)
        self.assertTrue(result.passed, result.detail)

    def test_count_helper_still_agrees_with_the_row_helper_it_now_derives_from(self):
        """The count helper's own docstring warns that its two callers must never drift onto
        different pairing logic. FORE-312 made it derive from the row-level helper; this asserts
        the derivation rather than trusting it."""
        self._unpaired_error(0, "2026-03-01T13:00:00.000001Z")
        self.conn.commit()
        errs, hooks, n_err, n_hook = dq_runner._unmatched_fail_open_rows_as_of(
            self.conn, self.latest_run)
        counts = dq_runner._unmatched_fail_open_count_as_of(self.conn, self.latest_run)
        self.assertEqual(counts, (len(errs), len(hooks), n_err, n_hook))


if __name__ == "__main__":
    unittest.main()


class NonScriptWriterExclusionTests(unittest.TestCase):
    """ATLASSN-68, atlas-side of FORE-314. A handler_id that is not a script name is the guard
    test harness recording its own argv where a hook name belongs. No hook ran, so no
    audit_event HOOK_ERROR exists, so the row can never pair -- exactly the property that already
    excludes shell writers."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_test_runner_argv_rows_are_outside_the_pairing_population(self):
        for i, handler in enumerate(("python3 -m unittest", "-c")):
            _make_verdict(self.conn, self.run_id, "s1", i, f"2026-01-01T00:00:0{i}.000001Z",
                          handler, "error", cwd="/tmp")
        self.conn.commit()

        result = dq_runner.check_fail_open_not_double_counted(self.conn, self.run_id)
        self.assertTrue(result.passed, result.detail)
        self.assertEqual(result.observed_value, 0)
        _, _, n_errors, _ = dq_runner._unmatched_fail_open_rows_as_of(self.conn, self.run_id)
        self.assertEqual(n_errors, 0, "test-runner rows must not enter the outer population")

    def test_a_real_hook_script_is_still_in_the_population_and_still_fails_unpaired(self):
        """The other half of the control. Narrowing the domain must not disarm the check for the
        writers it exists to watch, or ATLASSN-68 would be silencing a gate rather than scoping
        it."""
        _make_verdict(self.conn, self.run_id, "s1", 0, "2026-01-01T00:00:01.000001Z",
                      "nested_agent_notification_guard.py", "error", cwd="/proj/a")
        self.conn.commit()

        result = dq_runner.check_fail_open_not_double_counted(self.conn, self.run_id)
        self.assertFalse(result.passed, result.detail)
        self.assertGreater(result.observed_value, 0)

    def test_shell_writers_remain_excluded_as_before(self):
        _make_verdict(self.conn, self.run_id, "s1", 0, "2026-01-01T00:00:01.000001Z",
                      "laa-commit-flow-advisory.sh", "error", cwd="/proj/a")
        self.conn.commit()

        result = dq_runner.check_fail_open_not_double_counted(self.conn, self.run_id)
        self.assertTrue(result.passed, result.detail)

    def test_the_delta_no_longer_fires_on_an_increase_made_only_of_test_runner_rows(self):
        """The live shape, reduced. Ingest run 43 brought in 24 rows, 22 'python3 -m unittest' and
        2 '-c', and the contract check failed at +24 -- which is what put hook_verdict outside
        v_queryable_source. Same fixture here, and it must now be a pass."""
        prior = self.run_id
        latest = migrate.new_ingest_run(self.conn, status="ok")
        for i in range(24):
            _make_verdict(self.conn, latest, "s2", i, f"2026-01-01T02:{i:02d}:00.000001Z",
                          "python3 -m unittest" if i < 22 else "-c", "error", cwd="/tmp")
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, latest)
        self.assertEqual(result.observed_value, 0)
        self.assertTrue(result.passed, result.detail)
        self.assertGreater(prior, 0)


class CohortIntermediateRunTests(unittest.TestCase):
    """Regression control for claude-hooks-v2-e5's finding on ATLASSN-67. Both snapshots are
    cumulative (ingest_run_id <= X), so the increase comprises every unpaired row carried in by
    ANY run after the prior one -- not only by the latest ok one. Attributing on
    `== latest_run_id` left rows from an intermediate run counted in delta and dated as neither
    history nor news, so news could read 0 on an increase the check had never dated, and pass."""

    BOUNDARY = "2026-03-01T12:00:00.000000+00:00"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.prior_run = migrate.new_ingest_run(self.conn, status="ok")
        _set_started_at(self.conn, self.prior_run, self.BOUNDARY)
        # Between the two ok runs, and deliberately NOT ok: the only way a row can carry an
        # ingest_run_id strictly between the two comparison points.
        self.intermediate_run = migrate.new_ingest_run(self.conn, status="failed")
        _set_started_at(self.conn, self.intermediate_run, "2026-03-01T15:00:00.000000+00:00")
        self.latest_run = migrate.new_ingest_run(self.conn, status="ok")
        _set_started_at(self.conn, self.latest_run, "2026-03-01T18:00:00.000000+00:00")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_an_increase_carried_by_an_intermediate_run_is_dated_and_fails(self):
        """THE DISCRIMINATING CASE. The unpaired row belongs to the intermediate run, and its
        event happened after the prior run started, so it is news and must fail. Attributing on
        `== latest_run_id` skips it entirely, reports '0 late-arriving history, 0 newly occurred',
        and passes on a real +1."""
        _make_verdict(self.conn, self.intermediate_run, "s1", 0,
                      "2026-03-01T16:00:00.000001Z", "architecture_gate.py", "error",
                      cwd="/proj/a")
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, self.latest_run)
        self.assertEqual(result.observed_value, 1,
                         "fixture no longer discriminates: the delta must actually rise")
        self.assertIn("0 late-arriving history, 1 newly occurred", result.detail)
        self.assertFalse(result.passed, result.detail)

    def test_an_intermediate_run_carrying_only_history_still_passes(self):
        """The converse, so the fix is a widening of the attribution rather than a blanket fail."""
        _make_verdict(self.conn, self.intermediate_run, "s1", 0,
                      "2026-02-01T09:00:00.000001Z", "architecture_gate.py", "error",
                      cwd="/proj/a")
        self.conn.commit()

        result = dq_runner.check_fail_open_pairing_delta(self.conn, self.latest_run)
        self.assertEqual(result.observed_value, 1)
        self.assertIn("1 late-arriving history, 0 newly occurred", result.detail)
        self.assertTrue(result.passed, result.detail)
