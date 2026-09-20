"""ATLASSN-72: the five run-over-run selectors, scoped to the slow plane. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_slow_plane_selectors -v

The interesting control is not "the new predicate parses". It is that the OLD predicate, on the
same fixture, picks two fast ticks as its comparison points and reaches a different verdict. That
is proven here by loading dq_runner.py from the commit before the scoping landed and running it
against the identical warehouse, rather than by describing what it would have done.

Section 27.2 names the consequence being prevented: four of the five checks are delta-shaped and
do not become noisy when they compare 15 seconds instead of hours, they become VACUOUS -- a
contract gate that structurally cannot fire. Nothing goes red, which is why this needs a test that
fails loudly rather than a comment.
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import dq_runner, migrate

from atlas.warehouse.tests.test_dq_runner import _make_cwd, _make_project, _make_verdict

REPO_ROOT = Path(__file__).resolve().parents[3]

# The commit before the selectors were scoped. PINNED, never HEAD: a sibling test in this repo
# read HEAD, passed exactly once, and became a comparison of the new code against itself the
# moment its subject was committed. Same trap, avoided by construction rather than by care.
PRE_SCOPING_REV = "d96db07"


def _load_pre_scoping_dq_runner(workdir):
    src = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", PRE_SCOPING_REV + ":atlas/warehouse/dq_runner.py"],
        capture_output=True, text=True, check=True).stdout
    path = Path(workdir) / "dq_runner_pre_scoping.py"
    path.write_text(src, encoding="utf-8")
    name = "atlas.warehouse.dq_runner_pre_scoping"
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "atlas.warehouse"
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SlowPlaneSelectorTests(unittest.TestCase):
    """Three ok runs: two slow batches, then a fast tick. Every check that picks 'the two most
    recent ok runs' must pick the two batches."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        _make_project(self.conn, "PROJA", "/proj/a")
        _make_cwd(self.conn, "/proj/a", "PROJA", resolution="unique")
        _make_cwd(self.conn, "/scratch", None, resolution="unregistered", candidate_count=0)
        self.conn.commit()

        self.slow_a = self._run(plane=None)   # pre-migration-11 shape: NULL means slow
        self.slow_b = self._run(plane="slow")
        self.fast_c = self._run(plane="fast")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _run(self, plane):
        run_id = migrate.new_ingest_run(self.conn, status="ok")
        self.conn.execute("UPDATE ingest_run SET plane = ? WHERE run_id = ?", (plane, run_id))
        self.conn.commit()
        return run_id

    def test_migration_11_added_the_column_connect_applies(self):
        self.assertTrue(migrate.is_migrated11(self.conn))
        self.assertIn("plane", [r[1] for r in self.conn.execute("PRAGMA table_info(ingest_run)")])

    def test_null_plane_is_treated_as_slow_not_as_unknown(self):
        """27.5. Every run that existed when migration 11 landed was a batch run, so NULL already
        means the right thing and nothing was backfilled. A predicate of `plane = 'slow'` would
        have silently excluded all 49 historical runs and left every delta check with no
        comparison points at all."""
        picked = [r[0] for r in self.conn.execute(
            f"SELECT run_id FROM ingest_run WHERE {dq_runner.SLOW_PLANE_RUNS} ORDER BY run_id")]
        self.assertIn(self.slow_a, picked, "a NULL-plane run must count as slow")
        self.assertIn(self.slow_b, picked)
        self.assertNotIn(self.fast_c, picked)

    def test_the_scoped_selector_skips_the_fast_tick_and_the_old_one_does_not(self):
        """THE DISCRIMINATING CASE, at the SQL level. Both predicates are executed against the
        same table; the point is that they disagree about which runs are comparison points."""
        scoped = [r[0] for r in self.conn.execute(
            f"SELECT run_id FROM ingest_run WHERE {dq_runner.SLOW_PLANE_RUNS} "
            "ORDER BY run_id DESC LIMIT 2")]
        unscoped = [r[0] for r in self.conn.execute(
            "SELECT run_id FROM ingest_run WHERE status='ok' ORDER BY run_id DESC LIMIT 2")]
        self.assertEqual(scoped, [self.slow_b, self.slow_a])
        self.assertEqual(unscoped, [self.fast_c, self.slow_b],
                         "fixture no longer discriminates: the old predicate must pick the fast "
                         "tick, or this test proves nothing about the scoping")

    def test_aliased_predicate_is_derived_from_the_same_string(self):
        """One call site joins rather than selects directly, so it needs the alias. Derived, not
        retyped, so the two cannot drift onto different meanings."""
        self.assertEqual(dq_runner.SLOW_PLANE_ALIASED,
                         dq_runner.SLOW_PLANE_RUNS.replace("status", "i.status")
                                                  .replace("plane", "i.plane"))


class ResolutionRateDeltaAcrossAFastTickTests(unittest.TestCase):
    """The behavioural half, on a contract check with a clean threshold. resolution_rate_delta
    fails on a drop of more than 5 points between its two comparison points."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        _make_project(self.conn, "PROJA", "/proj/a")
        _make_cwd(self.conn, "/proj/a", "PROJA", resolution="unique")
        _make_cwd(self.conn, "/scratch", None, resolution="unregistered", candidate_count=0)
        self.conn.commit()

        # Two slow batches, both fully resolved: the rate is 1.0 at each, so the honest
        # batch-over-batch delta is zero.
        self.slow_a = self._run("slow")
        for i in range(50):
            _make_verdict(self.conn, self.slow_a, "s1", i,
                          f"2026-01-01T00:00:{i % 60:02d}.000001Z", "h1", "fire", cwd="/proj/a")
        self.slow_b = self._run("slow")
        for i in range(50, 100):
            _make_verdict(self.conn, self.slow_b, "s1", i,
                          f"2026-01-01T01:00:{i % 60:02d}.000001Z", "h1", "fire", cwd="/proj/a")
        # A fast tick that ingests nothing but unregistered cwds -- a real 33-point drop in the
        # cumulative rate, and exactly the shape a scratch directory produces in seconds.
        self.fast_c = self._run("fast")
        for i in range(100, 150):
            _make_verdict(self.conn, self.fast_c, "s2", i,
                          f"2026-01-01T02:00:{i % 60:02d}.000001Z", "h1", "fire", cwd="/scratch")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _run(self, plane):
        run_id = migrate.new_ingest_run(self.conn, status="ok")
        self.conn.execute("UPDATE ingest_run SET plane = ? WHERE run_id = ?", (plane, run_id))
        self.conn.commit()
        return run_id

    def test_scoped_check_compares_the_two_batches_and_passes(self):
        result = dq_runner.check_resolution_rate_delta(self.conn, self.fast_c)
        self.assertTrue(result.passed, result.detail)
        self.assertAlmostEqual(result.observed_value, 0.0, places=2)

    def test_pre_scoping_code_compares_the_fast_tick_and_fails_on_the_same_warehouse(self):
        """THE FAIL-FIRST PROOF. Same fixture, same connection, the dq_runner.py that shipped
        before the scoping. It picks the fast tick as its latest comparison point, sees a
        33-point drop that no batch caused, and fails a CONTRACT check.

        This is the direction that matters less in production -- a false failure is at least
        visible. The invisible direction is the same mechanism with the fixture inverted: a real
        regression that happens between two batches gets averaged away because the two comparison
        points are 15 seconds apart. Both come from the same defect, and this one is testable."""
        old = _load_pre_scoping_dq_runner(self.tmpdir.name)
        self.assertFalse(hasattr(old, "SLOW_PLANE_RUNS"),
                         f"{PRE_SCOPING_REV} already contains the scoping -- this would be "
                         f"comparing the new code against itself")
        result = old.check_resolution_rate_delta(self.conn, self.fast_c)
        self.assertFalse(result.passed, result.detail)
        self.assertGreater(result.observed_value, 5.0)

    def test_fast_plane_rows_are_still_counted_only_not_used_as_comparison_points(self):
        """The scoping must not make fast-plane DATA invisible. Its rows stay in every cumulative
        population -- they are simply not eligible as the boundary a delta is measured between.
        Getting this wrong would silently discard everything the fast plane ingests, which is a
        worse defect than the one being fixed."""
        total = self.conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]
        self.assertEqual(total, 150)
        from_fast = self.conn.execute(
            "SELECT COUNT(*) FROM hook_verdict WHERE ingest_run_id = ?", (self.fast_c,)).fetchone()[0]
        self.assertEqual(from_fast, 50)
        # The level check reads the whole table and must see all 150.
        level = dq_runner.check_project_resolution_floor(self.conn, self.fast_c)
        self.assertIn("100/150", level.detail.replace(",", ""))


class MonotonicWalkIsBoundedToSlowRunsTests(unittest.TestCase):
    """27.3: ingest_rows_monotonic walks EVERY ok run with a correlated COUNT(*), so its cost is
    runs times rows. Bounding the walk to slow runs is what keeps it at roughly 288 rows a day
    instead of 5,760."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.slow = []
        for _ in range(2):
            self.slow.append(self._run("slow"))
        for _ in range(20):
            self._run("fast")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _run(self, plane):
        run_id = migrate.new_ingest_run(self.conn, status="ok")
        self.conn.execute("UPDATE ingest_run SET plane = ? WHERE run_id = ?", (plane, run_id))
        self.conn.commit()
        return run_id

    def test_the_walk_covers_slow_runs_only(self):
        walked = [r[0] for r in self.conn.execute(
            f"SELECT run_id FROM ingest_run WHERE {dq_runner.SLOW_PLANE_RUNS} ORDER BY run_id")]
        self.assertEqual(walked, self.slow)
        unscoped = self.conn.execute(
            "SELECT COUNT(*) FROM ingest_run WHERE status='ok'").fetchone()[0]
        self.assertEqual(unscoped, 22,
                         "fixture no longer discriminates: the old predicate must walk all 22")
        self.assertEqual(dq_runner.check_ingest_rows_monotonic(self.conn, self.slow[-1]).passed,
                         True)


if __name__ == "__main__":
    unittest.main()
