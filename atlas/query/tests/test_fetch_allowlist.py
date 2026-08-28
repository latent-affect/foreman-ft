"""GOALS.json C3, C4, C5. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.query.tests.test_fetch_allowlist -v
"""

import sqlite3
import unittest

from atlas.query.facade import QueryFacade, QueryRefused
from atlas.query.tests._helpers import TempWarehouse, make_clean_run
from atlas.warehouse import migrate


class FetchAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()
        make_clean_run(self.wh.setup_conn)
        self.facade = QueryFacade(self.wh.db_path)

    def tearDown(self):
        self.facade.close()
        self.wh.close()

    def test_raw_table_name_is_refused(self):
        with self.assertRaises(QueryRefused):
            self.facade.fetch("hook_verdict")

    def test_unknown_name_is_refused(self):
        with self.assertRaises(QueryRefused):
            self.facade.fetch("not_a_real_view")

    def test_where_sql_is_refused(self):
        with self.assertRaises(QueryRefused) as ctx:
            self.facade.fetch("v_gate_proven_live", where_sql="1=1")
        self.assertIn("where_sql", str(ctx.exception))

    def test_declared_view_is_allowed_when_clean(self):
        columns, rows = self.facade.fetch("v_gate_proven_live")
        self.assertIn("handler_id", columns)
        self.assertGreater(len(rows), 0)

    def test_fetch_result_matches_direct_sql(self):
        columns, rows = self.facade.fetch("v_gate_proven_live")
        direct_conn = sqlite3.connect(self.wh.db_path)
        direct_rows = direct_conn.execute("SELECT * FROM v_gate_proven_live").fetchall()
        direct_conn.close()
        self.assertEqual(rows, direct_rows)


class FetchRefusalWhenNotCleanTests(unittest.TestCase):
    def tearDown(self):
        self.facade.close()
        self.wh.close()

    def test_never_run_refuses_a_valid_view_name(self):
        self.wh = TempWarehouse()
        self.facade = QueryFacade(self.wh.db_path)
        with self.assertRaises(QueryRefused) as ctx:
            self.facade.fetch("v_gate_proven_live")
        self.assertIn("never-run", str(ctx.exception))

    def test_failed_run_refuses_a_valid_view_name(self):
        self.wh = TempWarehouse()
        migrate.new_ingest_run(self.wh.setup_conn, status="failed")
        self.facade = QueryFacade(self.wh.db_path)
        with self.assertRaises(QueryRefused) as ctx:
            self.facade.fetch("v_gate_proven_live")
        self.assertIn("failed-run", str(ctx.exception))

    def test_checks_not_evaluated_refuses_a_valid_view_name(self):
        self.wh = TempWarehouse()
        migrate.new_ingest_run(self.wh.setup_conn, status="ok")
        self.facade = QueryFacade(self.wh.db_path)
        with self.assertRaises(QueryRefused) as ctx:
            self.facade.fetch("v_gate_proven_live")
        self.assertIn("checks-not-evaluated", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
