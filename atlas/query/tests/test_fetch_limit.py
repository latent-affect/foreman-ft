"""atlas/GOALS.json C8 (DEVH-50 / SECURITY-PRIVACY-REVIEW.md F2, the one finding exploitable by
a caller today). fetch()'s limit must be a real SQL LIMIT, not a Python-side rows[:limit] slice
applied after fetchall() already materialized the whole view -- the pre-fix cost was bounded by
view size, not by anything the caller controlled.

    /Users/m5/.venv/bin/python3 -m unittest atlas.query.tests.test_fetch_limit -v
"""
import unittest

from atlas.query.facade import QueryFacade, QueryRefused
from atlas.query.tests._helpers import TempWarehouse, make_clean_run

ROWS_TO_INSERT = 50


class _CountingCursorProxy:
    """sqlite3.Cursor is a C type and cannot be monkeypatched directly (TypeError: cannot
    set attribute of immutable type). Wrapping the connection instead avoids touching any
    built-in C type -- this proxy is a plain Python object standing in for
    QueryFacade._conn, so fetch()'s own code runs completely unmodified against it."""

    def __init__(self, real_cursor, recorder):
        self._real = real_cursor
        self._recorder = recorder

    @property
    def description(self):
        return self._real.description

    def fetchall(self):
        rows = self._real.fetchall()
        self._recorder["fetchall_row_count"] = len(rows)
        return rows

    def fetchone(self):
        # status() (called internally by fetch() before its own SQL runs) uses fetchone(),
        # not fetchall() -- pass through unrecorded, this test only cares about fetch()'s
        # own SELECT.
        return self._real.fetchone()


class _CountingConnProxy:
    def __init__(self, real_conn):
        self._real = real_conn
        self.recorder = {}

    def execute(self, sql, params=()):
        self.recorder["last_sql"] = sql
        self.recorder["last_params"] = tuple(params)
        real_cursor = self._real.execute(sql, params)
        return _CountingCursorProxy(real_cursor, self.recorder)


def _insert_many_hook_verdict_rows(conn, n):
    for i in range(n):
        conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, hook_event, verdict, session_id, cwd, tool_use_id) "
            "VALUES (?, ?, 1, ?, ?, 'microsecond', 'h', 'PreToolUse', 'fire', 's1', '/proj', ?)",
            (f"s{i}", i, f"2026-01-01T00:00:{i:02d}.000000Z", 1000 + i, f"tu{i}"),
        )
    conn.commit()


class FetchLimitTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()
        make_clean_run(self.wh.setup_conn, with_verdicts=False)
        _insert_many_hook_verdict_rows(self.wh.setup_conn, ROWS_TO_INSERT)
        self.facade = QueryFacade(self.wh.db_path)

    def tearDown(self):
        self.facade.close()
        self.wh.close()

    def test_c8_limit_bounds_rows_returned(self):
        columns, rows = self.facade.fetch("v_hook_verdict", limit=5)
        self.assertLessEqual(len(rows), 5)

    def test_c8_limit_is_pushed_into_sql_not_sliced_after_fetchall(self):
        """The real proof: wrap facade._conn and record how many rows the database engine
        actually handed to fetchall(), plus the exact SQL executed. A Python-side
        rows[:limit] slice would still show fetchall() returning all ROWS_TO_INSERT rows;
        a real SQL LIMIT means fetchall() itself only ever produces the bounded count."""
        proxy = _CountingConnProxy(self.facade._conn)
        self.facade._conn = proxy
        try:
            columns, rows = self.facade.fetch("v_hook_verdict", limit=5)
        finally:
            self.facade._conn = proxy._real

        self.assertIn("fetchall_row_count", proxy.recorder, "fetchall() was never called")
        self.assertLessEqual(
            proxy.recorder["fetchall_row_count"], 5,
            f"the underlying cursor produced {proxy.recorder['fetchall_row_count']} rows for "
            f"limit=5 over {ROWS_TO_INSERT} real rows -- this is the pre-fix defect: "
            f"rows[:limit] applied after an unbounded fetchall()"
        )
        self.assertIn("LIMIT ?", proxy.recorder["last_sql"])
        self.assertEqual(proxy.recorder["last_params"][-1], 5)

    def test_c8_where_sql_still_refused(self):
        with self.assertRaises(QueryRefused):
            self.facade.fetch("v_hook_verdict", where_sql="1=1", limit=5)

    def test_c8_no_limit_argument_returns_two_tuple_unchanged(self):
        """Regression: every existing zero-argument caller must see the exact same return
        shape as before this fix."""
        result = self.facade.fetch("v_hook_verdict")
        self.assertEqual(len(result), 2)
        columns, rows = result
        self.assertEqual(len(rows), ROWS_TO_INSERT)

    def test_c8_invalid_limit_types_are_refused(self):
        for bad in (0, -1, "5", 5.0, True):
            with self.assertRaises(QueryRefused, msg=f"limit={bad!r} should be refused"):
                self.facade.fetch("v_hook_verdict", limit=bad)


if __name__ == "__main__":
    unittest.main()
