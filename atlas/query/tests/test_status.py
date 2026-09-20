"""GOALS.json C2, C4 (corrected). Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.query.tests.test_status -v
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

    def test_run_ok_with_checks_but_a_real_contract_failure_is_still_clean_at_run_level(self):
        """CORRECTED 2026-09-20 (ATLASSN-103, ARCHITECTURE.md section 8 addendum, amendment A1).

        This test used to assert the OPPOSITE (state == FAILED_RUN) -- that was the pre-fix
        behavior this whole ticket exists to change, and C4's own corrected text says so
        directly: 'the prior wording stated the CURRENT pre-fix behavior as the criterion
        itself, which is exactly backwards for design-scope criteria meant to gate the fix.'
        A1 keeps the run-level gate global for NEVER_RUN/non-ok-status/vacuous-pass ONLY; a
        contract failure is no longer one of the three states that fold into the run-level
        gate at all -- it is scoped per-source in fetch() instead (see
        test_fetch_allowlist.py's own new per-source tests). status() still reports the real
        contract_failures count on the Status object; it just no longer changes `state`."""
        run_id = make_clean_run(self.wh.setup_conn)
        self.wh.setup_conn.execute(
            "UPDATE dq_check_run SET passed=0 WHERE run_id=? AND check_name='verdict_domain_closed'",
            (run_id,),
        )
        self.wh.setup_conn.commit()
        facade = QueryFacade(self.wh.db_path)
        status = facade.status()
        self.assertEqual(status.state, Status.CLEAN)
        self.assertTrue(status.is_clean())
        self.assertGreater(status.contract_failures, 0)
        facade.close()


if __name__ == "__main__":
    unittest.main()
