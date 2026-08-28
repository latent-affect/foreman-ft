"""GOALS.json C1. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.query.tests.test_readonly -v
"""

import sqlite3
import unittest

from atlas.query.facade import QueryFacade, QueryFacadeUnavailable
from atlas.query.tests._helpers import TempWarehouse


class ReadOnlyConnectionTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()

    def tearDown(self):
        self.wh.close()

    def test_write_through_facade_connection_raises(self):
        facade = QueryFacade(self.wh.db_path)
        with self.assertRaises(sqlite3.OperationalError):
            facade._conn.execute(
                "INSERT INTO ingest_run (started_at, status, atlas_version, host_id) "
                "VALUES ('x', 'ok', 'v', 'h')"
            )
        facade.close()

    def test_opening_a_nonexistent_db_raises_a_named_error(self):
        with self.assertRaises(QueryFacadeUnavailable):
            QueryFacade("/nonexistent/path/does-not-exist.db")


if __name__ == "__main__":
    unittest.main()
