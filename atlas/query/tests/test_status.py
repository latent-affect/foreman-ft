"""GOALS.json C2. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.query.tests.test_status -v
"""

import unittest

from atlas.query.facade import QueryFacade, Status
from atlas.query.tests._helpers import TempWarehouse, make_clean_run
from atlas.warehouse import migrate


class StatusFourStatesTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()

    def tearDown(self):
        self.wh.close()

    def test_never_run(self):
        facade = QueryFacade(self.wh.db_path)
        status = facade.status()
        self.assertEqual(status.state, Status.NEVER_RUN)
        self.assertFalse(status.is_clean())
        facade.close()

    def test_failed_run(self):
        migrate.new_ingest_run(self.wh.setup_conn, status="failed")
        facade = QueryFacade(self.wh.db_path)
        status = facade.status()
        self.assertEqual(status.state, Status.FAILED_RUN)
        self.assertFalse(status.is_clean())
        facade.close()

    def test_checks_not_evaluated_is_not_confused_with_clean(self):
        migrate.new_ingest_run(self.wh.setup_conn, status="ok")  # ok status, ZERO checks run
        facade = QueryFacade(self.wh.db_path)
        status = facade.status()
        self.assertEqual(status.state, Status.CHECKS_NOT_EVALUATED)
        self.assertFalse(status.is_clean())
        self.assertNotEqual(status.state, Status.CLEAN)
        facade.close()

    def test_genuinely_clean(self):
        make_clean_run(self.wh.setup_conn)
        facade = QueryFacade(self.wh.db_path)
        status = facade.status()
        self.assertEqual(status.state, Status.CLEAN)
        self.assertTrue(status.is_clean())
        self.assertEqual(status.contract_failures, 0)
        self.assertGreater(status.checks_evaluated, 0)
        facade.close()

    def test_run_ok_with_checks_but_a_real_contract_failure_is_failed_not_clean(self):
        run_id = make_clean_run(self.wh.setup_conn)
        self.wh.setup_conn.execute(
            "UPDATE dq_check_run SET passed=0 WHERE run_id=? AND check_name='verdict_domain_closed'",
            (run_id,),
        )
        self.wh.setup_conn.commit()
        facade = QueryFacade(self.wh.db_path)
        status = facade.status()
        self.assertEqual(status.state, Status.FAILED_RUN)
        self.assertGreater(status.contract_failures, 0)
        facade.close()


if __name__ == "__main__":
    unittest.main()
