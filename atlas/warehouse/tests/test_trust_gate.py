"""GOALS.json C5, C6 -- the permanent regression tests for FATAL-2 and FATAL-1. Run from the
repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_trust_gate -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import migrate

ELEVEN_HOOK_VERDICT_VIEWS = [
    "v_project_scope", "v_hook_verdict", "v_project_resolution_coverage",
    "v_handler_denominator", "v_verdict_confusion_matrix", "v_fail_open_incident",
    "v_hook_latency_rollup", "v_decision_outcome_rate", "v_deny_streak",
    "v_trapped_agent_candidate", "v_gate_proven_live",
]

SENTINEL = "SOURCE-DISTRUSTED-DO-NOT-USE"


def _pass_all_hook_verdict_checks(conn, run_id, exclude=()):
    names = [
        r[0] for r in conn.execute(
            "SELECT check_name FROM dq_check WHERE source_table='hook_verdict'"
        )
        if r[0] not in exclude
    ]
    for name in names:
        conn.execute(
            "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
            "VALUES (?, ?, '2026-01-01T00:00:00Z', 1, 'synthetic-pass')",
            (run_id, name),
        )
    conn.commit()


def _fail_one_check(conn, run_id, check_name):
    conn.execute(
        "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
        "VALUES (?, ?, '2026-01-01T00:00:01Z', 0, 'synthetic-fail')",
        (run_id, check_name),
    )
    conn.commit()


def _load_synthetic_verdicts(conn, run_id):
    # Deliberately exercises every one of the eleven views with at least one real row when
    # trusted: a plain fire/silent pair, a 3-length deny streak with no intervening completed
    # tool call (v_deny_streak, v_trapped_agent_candidate), and an unpaired verdict='error' row
    # (v_fail_open_incident -- a LEFT JOIN, so it produces one 'verdict-only' row even with no
    # matching audit_event).
    rows = [
        ("s1", 0, "2026-01-01T00:00:00.000001Z", 1000, "architecture_gate.py", "PreToolUse",
         "fire", "sess1", "/proj/a", "tu1", 5.0, "deny", "R1"),
        ("s1", 100, "2026-01-01T00:00:01.000001Z", 1001, "architecture_gate.py", "PreToolUse",
         "fire", "sess1", "/proj/a", "tu2", 5.0, "deny", "R1"),
        ("s1", 200, "2026-01-01T00:00:02.000001Z", 1002, "architecture_gate.py", "PostToolUse",
         "silent", "sess1", "/proj/a", "tu2", 5.0, None, None),
        ("s1", 300, "2026-01-01T00:00:03.000001Z", 1003, "goals_freeze_gate.py", "PreToolUse",
         "fire", "sess2", "/proj/a", "tu3", 5.0, "deny", "R2"),
        ("s1", 400, "2026-01-01T00:00:04.000001Z", 1004, "goals_freeze_gate.py", "PreToolUse",
         "fire", "sess2", "/proj/a", "tu4", 5.0, "deny", "R2"),
        ("s1", 500, "2026-01-01T00:00:05.000001Z", 1005, "goals_freeze_gate.py", "PreToolUse",
         "fire", "sess2", "/proj/a", "tu5", 5.0, "deny", "R2"),
        ("s1", 600, "2026-01-01T00:00:06.000001Z", 1006, "preflight_blocking_gate.py",
         "PreToolUse", "error", "sess3", "/proj/a", "tu6", 5.0, None, None),
    ]
    for stream_id, byte_offset, ts, epoch_ms, handler_id, hook_event, verdict, session_id, cwd, \
            tool_use_id, dur, decision, rule_id in rows:
        conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, hook_event, verdict, session_id, cwd, tool_use_id, "
            "self_duration_ms, decision, rule_id) VALUES (?,?,?,?,?,'microsecond',?,?,?,?,?,?,?,?,?)",
            (stream_id, byte_offset, run_id, ts, epoch_ms, handler_id, hook_event, verdict,
             session_id, cwd, tool_use_id, dur, decision, rule_id),
        )
    conn.commit()


class Fatal2TrustGateRegressionTests(unittest.TestCase):
    """C5. Every hook_verdict-derived view must return real rows when trusted, and collapse to
    exactly one SOURCE-DISTRUSTED-DO-NOT-USE row -- never zero, never real-looking -- when
    hook_verdict's trust is withdrawn."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        _load_synthetic_verdicts(self.conn, self.run_id)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_all_eleven_views_return_real_rows_when_trusted(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id)
        for view in ELEVEN_HOOK_VERDICT_VIEWS:
            with self.subTest(view=view):
                rows = self.conn.execute(f"SELECT trust_state FROM {view}").fetchall()
                self.assertGreater(len(rows), 0, f"{view} returned zero rows while trusted")
                trust_states = {r[0] for r in rows}
                self.assertNotIn(
                    SENTINEL, trust_states,
                    f"{view} returned the distrust sentinel while hook_verdict is trusted",
                )

    def test_all_eleven_views_collapse_to_exactly_one_sentinel_row_when_distrusted(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id, exclude=("verdict_domain_closed",))
        _fail_one_check(self.conn, self.run_id, "verdict_domain_closed")
        for view in ELEVEN_HOOK_VERDICT_VIEWS:
            with self.subTest(view=view):
                rows = self.conn.execute(f"SELECT trust_state FROM {view}").fetchall()
                self.assertEqual(
                    len(rows), 1,
                    f"{view} returned {len(rows)} rows while distrusted, expected exactly 1",
                )
                self.assertEqual(rows[0][0], SENTINEL, f"{view}'s single row is not the sentinel")


class Fatal1SeverityRegressionTests(unittest.TestCase):
    """C6. project_resolution_floor (advisory) failing must not withhold hook_verdict.
    resolution_rate_delta (contract) failing must withhold it."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        _load_synthetic_verdicts(self.conn, self.run_id)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_project_resolution_floor_failing_does_not_withhold_hook_verdict(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id, exclude=("project_resolution_floor",))
        _fail_one_check(self.conn, self.run_id, "project_resolution_floor")
        queryable = {r[0] for r in self.conn.execute("SELECT source_table FROM v_queryable_source")}
        self.assertIn("hook_verdict", queryable)
        rows = self.conn.execute("SELECT trust_state FROM v_hook_verdict").fetchall()
        self.assertEqual({r[0] for r in rows}, {"ok"})

    def test_resolution_rate_delta_failing_withholds_hook_verdict(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id, exclude=("resolution_rate_delta",))
        _fail_one_check(self.conn, self.run_id, "resolution_rate_delta")
        queryable = {r[0] for r in self.conn.execute("SELECT source_table FROM v_queryable_source")}
        self.assertNotIn("hook_verdict", queryable)
        rows = self.conn.execute("SELECT trust_state FROM v_hook_verdict").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], SENTINEL)


class WindowMeasurableRegressionTests(unittest.TestCase):
    """MAJOR-B fix (falsification re-review, 2026-08-22): a deny streak whose epoch_ms bounds
    are NULL must report window_measurable=0 and a NULL completed_tool_calls_in_window -- never
    0, which would be indistinguishable from a genuine trapped agent."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        _load_synthetic_verdicts(self.conn, self.run_id)
        _pass_all_hook_verdict_checks(self.conn, self.run_id)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _insert(self, byte_offset, ts, handler_id, hook_event, verdict, tool_use_id, decision):
        self.conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, hook_event, verdict, session_id, cwd, tool_use_id, "
            "self_duration_ms, decision, rule_id) VALUES "
            "('s2', ?, ?, ?, NULL, 'second', ?, ?, ?, 'sess-nullep', '/proj/b', ?, 5.0, ?, 'R9')",
            (byte_offset, self.run_id, ts, handler_id, hook_event, verdict, tool_use_id, decision),
        )

    def test_null_epoch_ms_streak_reports_unmeasurable_not_zero(self):
        # A real 3-deny streak, ALL rows with epoch_ms=NULL, so streak_start_epoch_ms/
        # streak_end_epoch_ms (MIN/MAX(epoch_ms) over the streak) are also NULL.
        self._insert(700, "2026-01-01T00:01:00Z", "hnull", "PreToolUse", "fire", "tuA", "deny")
        self._insert(701, "2026-01-01T00:01:01Z", "hnull", "PreToolUse", "fire", "tuB", "deny")
        self._insert(702, "2026-01-01T00:01:02Z", "hnull", "PreToolUse", "fire", "tuC", "deny")
        # Two tool calls genuinely complete inside the streak's real (ts-based) window -- the
        # pre-fix predicate could never see them (it compares epoch_ms, which is NULL here), and
        # reported completed_tool_calls_in_window=0, the same shape as a real trapped agent.
        self._insert(703, "2026-01-01T00:01:00.5Z", "hnull", "PostToolUse", "silent", "tuA", None)
        self._insert(704, "2026-01-01T00:01:01.5Z", "hnull", "PostToolUse", "silent", "tuB", None)
        self.conn.commit()

        row = self.conn.execute(
            "SELECT window_measurable, completed_tool_calls_in_window, window_seconds "
            "FROM v_trapped_agent_candidate WHERE session_id = 'sess-nullep' AND handler_id = 'hnull'"
        ).fetchone()
        self.assertIsNotNone(row, "the null-epoch_ms streak did not appear in v_trapped_agent_candidate at all")
        measurable, completed, window_seconds = row
        self.assertEqual(measurable, 0)
        self.assertIsNone(completed, "must be NULL (could-not-check), not 0 (false trapped-agent signature)")
        self.assertIsNone(window_seconds)

    def test_a_real_epoch_ms_streak_still_reports_measurable(self):
        rows = self.conn.execute(
            "SELECT window_measurable, completed_tool_calls_in_window FROM v_trapped_agent_candidate "
            "WHERE session_id = 'sess2' AND handler_id = 'goals_freeze_gate.py'"
        ).fetchone()
        self.assertEqual(rows[0], 1)
        self.assertIsNotNone(rows[1])


if __name__ == "__main__":
    unittest.main()
