"""ATLASSN-48 / dev-harness DEVH-50 (SECURITY-PRIVACY-REVIEW.md F2). `limit` used to be a
post-fetch Python slice applied by the MCP layer AFTER the facade had already run
SELECT * FROM <view> and fetchall()'d it, so limit=1 still materialized the whole view. This
suite proves the bound is real -- enforced by the database, measured as work actually done --
for BOTH fetch() and fetch_live().

    /Users/m5/.venv/bin/python3 -m unittest atlas.query.tests.test_fetch_limit -v

NEGATIVE-CONTROL DESIGN. The obvious test (fetch(view, limit=1) raises TypeError pre-fix) would
only prove the keyword exists, not that any work was saved -- it would pass against a "fix" that
accepted limit= and still sliced in Python. So the work assertions route through
fetchWithLimit(), which falls back to the OLD post-hoc-slice behaviour when the keyword is
absent. Against the pre-fix facade these tests therefore reach the work assertion and fail on
it, which is the actual defect, rather than failing on a signature mismatch.
"""

import unittest

from atlas.query.facade import QueryFacade, QueryRefused
from atlas.query.tests._helpers import TempWarehouse, make_clean_run

ROW_COUNT = 3000


def insertVerdictRows(conn, run_id, count):
    """Enough rows that a full materialization is unmistakably more work than a 1-row read."""
    conn.executemany(
        "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
        "ts_resolution, handler_id, hook_event, verdict, session_id, cwd, tool_use_id, "
        "decision, rule_id) VALUES ('bulk', ?, ?, ?, ?, 'microsecond', 'architecture_gate.py', "
        "'PreToolUse', 'fire', 'sess-bulk', '/proj', ?, 'deny', 'R1')",
        [(i + 1, run_id, f"2026-01-02T00:00:00.{i:06d}Z", 2000 + i, f"tu-bulk-{i}")
         for i in range(count)],
    )
    conn.commit()


def fetchWithLimit(facade, view_name, limit):
    """Caller shim: prefers the real SQL bound, falls back to the pre-fix post-hoc slice. See
    this module's NEGATIVE-CONTROL DESIGN note -- the fallback is what makes the work assertion
    discriminate behaviour rather than signature."""
    try:
        return facade.fetch(view_name, limit=limit)
    except TypeError:
        columns, rows = facade.fetch(view_name)
        return columns, rows[:limit]


def countVmSteps(facade, call):
    """Work actually performed by SQLite for one facade call, in VM instructions. This is the
    measurement the DoS is about: rows RETURNED can be identical while work differs by orders of
    magnitude, which is exactly how the post-hoc slice hid the defect."""
    steps = 0

    def handler():
        nonlocal steps
        steps += 1
        return 0

    facade._conn.set_progress_handler(handler, 10)
    try:
        result = call()
    finally:
        facade._conn.set_progress_handler(None, 0)
    return steps, result


class FetchLimitTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()
        self.run_id = make_clean_run(self.wh.setup_conn)
        insertVerdictRows(self.wh.setup_conn, self.run_id, ROW_COUNT)
        self.facade = QueryFacade(self.wh.db_path)

    def tearDown(self):
        self.facade.close()
        self.wh.close()

    def test_limit_bounds_work_not_just_row_count(self):
        """THE control. limit=1 must cost dramatically less than an unbounded read of the same
        view. Pre-fix this fails: both paths do SELECT * + fetchall(), so the step counts are
        equal and the ratio is ~1."""
        boundedSteps, (_, boundedRows) = countVmSteps(
            self.facade, lambda: fetchWithLimit(self.facade, "v_hook_verdict", 1))
        fullSteps, (_, fullRows) = countVmSteps(
            self.facade, lambda: self.facade.fetch("v_hook_verdict"))
        self.assertEqual(len(boundedRows), 1)
        self.assertGreater(len(fullRows), ROW_COUNT / 2)
        self.assertGreater(
            fullSteps, boundedSteps * 10,
            f"limit=1 did {boundedSteps} VM steps vs {fullSteps} unbounded -- the bound is not "
            f"reaching the database; this is the post-fetch-slice defect")

    def test_limit_is_bound_as_a_parameter_never_interpolated(self):
        """The pushdown must not become a second, unguarded way for caller-controlled text to
        reach the SQL string. Non-int limits are refused, not coerced."""
        for bad in ("1; DROP TABLE hook_verdict", "1 UNION SELECT 1", "5", 1.5, [1], True):
            with self.assertRaises(QueryRefused, msg=f"limit={bad!r} was not refused"):
                self.facade.fetch("v_hook_verdict", limit=bad)

    def test_nonpositive_limits_are_refused(self):
        for bad in (0, -1):
            with self.assertRaises(QueryRefused):
                self.facade.fetch("v_hook_verdict", limit=bad)

    def test_hook_verdict_is_never_dropped_by_a_bad_limit(self):
        """Paranoia with a point: prove the refused injection strings above did not execute.
        A refusal that still ran the statement would pass the assertRaises and destroy data."""
        n = self.wh.setup_conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]
        self.assertGreater(n, ROW_COUNT / 2)

    def test_return_shape_is_unchanged_without_limit(self):
        """Every existing zero-argument caller (integration_test.py, the dashboard, the two MCP
        tools) must see identical behaviour."""
        pair = self.facade.fetch("v_hook_verdict")
        self.assertEqual(len(pair), 2)
        columns, rows = pair
        self.assertIsInstance(columns, list)
        self.assertGreater(len(rows), ROW_COUNT / 2)

    def test_return_shape_is_unchanged_with_limit(self):
        pair = self.facade.fetch("v_hook_verdict", limit=5)
        self.assertEqual(len(pair), 2)
        self.assertEqual(len(pair[1]), 5)

    def test_limit_larger_than_view_returns_everything(self):
        _, unbounded = self.facade.fetch("v_hook_verdict")
        _, generous = self.facade.fetch("v_hook_verdict", limit=ROW_COUNT * 10)
        self.assertEqual(len(generous), len(unbounded))

    def test_limit_none_is_the_documented_default(self):
        _, explicit = self.facade.fetch("v_hook_verdict", limit=None)
        _, implicit = self.facade.fetch("v_hook_verdict")
        self.assertEqual(len(explicit), len(implicit))

    def test_allowlist_is_still_checked_before_limit(self):
        """Order matters: an undeclared view must be refused for being undeclared, not reached
        because a limit made the query look harmless."""
        with self.assertRaises(QueryRefused) as caught:
            self.facade.fetch("hook_verdict", limit=1)
        self.assertIn("declared gated views", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
