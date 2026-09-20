"""GOALS.json C3, C4 (corrected), C12, C13. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.query.tests.test_fetch_allowlist -v
"""

import sqlite3
import unittest

from atlas.query.facade import QueryFacade, QueryRefused
from atlas.query.tests._helpers import TempWarehouse, make_clean_run
from atlas.warehouse import migrate


def fail_one_source(conn, check_name):
    """ATLASSN-103: a clean run except ONE named check fails -- the fixture C4's own corrected
    text describes ('a genuinely clean run where exactly one source has a real contract
    failure'). Returns run_id. Reuses make_clean_run rather than reimplementing the seed, then
    flips exactly one row, same pattern test_status.py's own pre-existing contract-failure test
    already used."""
    run_id = make_clean_run(conn)
    conn.execute(
        "UPDATE dq_check_run SET passed=0 WHERE run_id=? AND check_name=?",
        (run_id, check_name),
    )
    conn.commit()
    return run_id


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


class PerSourceContractFailureTests(unittest.TestCase):
    """ATLASSN-103 (A1/A2), GOALS.json C4 (corrected) and C12. The load-bearing behavior change
    this whole ticket exists for: a contract failure on one source must refuse only the views
    that actually depend on it, and the refusal must name the source and its real failing-check
    count -- not a generic message."""

    def setUp(self):
        self.wh = TempWarehouse()
        # tessera_event_id_unique is a real contract-severity check with source_table
        # 'tessera_event' (ARCHITECTURE.md section 16 seed data) -- the same example C4's own
        # corrected text names.
        fail_one_source(self.wh.setup_conn, "tessera_event_id_unique")
        self.facade = QueryFacade(self.wh.db_path)

    def tearDown(self):
        self.facade.close()
        self.wh.close()

    def test_run_level_state_is_clean_despite_the_contract_failure(self):
        """The run-level gate (A1) no longer folds a contract failure into FAILED_RUN -- see
        test_status.py's own corrected test for the direct assertion; checked again here since
        it is the precondition every other test in this class depends on."""
        self.assertTrue(self.facade.status().is_clean())

    def test_a_view_that_does_not_depend_on_the_failing_source_is_served(self):
        """v_hook_verdict's only dependency is hook_verdict (A2's declared map) -- a
        tessera_event failure must not touch it."""
        columns, rows = self.facade.fetch("v_hook_verdict")
        self.assertGreater(len(rows), 0)

    def test_a_view_that_depends_on_the_failing_source_is_refused(self):
        """v_ticket_diff_binding's declared map names tessera_event -- it must refuse."""
        with self.assertRaises(QueryRefused):
            self.facade.fetch("v_ticket_diff_binding")

    def test_the_refusal_names_the_blocking_source_and_its_real_failing_check_count(self):
        """C12, verbatim: 'the refusal names the blocking source(s) and their failing check
        counts, pulled from v_source_trust -- not a generic refusal message.'"""
        with self.assertRaises(QueryRefused) as ctx:
            self.facade.fetch("v_ticket_diff_binding")
        message = str(ctx.exception)
        self.assertIn("tessera_event", message)
        # Real count, not a placeholder: exactly one failing check on this source in this
        # fixture (tessera_event_id_unique), and v_source_trust's own checks_run column
        # confirms it was actually counted rather than guessed.
        self.assertIn("1 of", message)

    def test_an_unrelated_source_failure_does_not_block_a_plumbing_view(self):
        """C13, the converse direction: even with a real contract failure present, the five
        trust-plumbing views stay servable."""
        for view in ("v_atlas_status", "v_pipeline_selfcheck", "v_queryable_source",
                     "v_snapshot_publishable", "v_source_trust"):
            columns, rows = self.facade.fetch(view)
            self.assertGreaterEqual(len(rows), 0, f"{view} raised or misbehaved")


class PlumbingViewNeverRunTests(unittest.TestCase):
    """C13's other half: v_atlas_status self-describes never-run and must be servable for that
    state too, unlike every other gated view."""

    def test_v_atlas_status_is_servable_on_a_virgin_warehouse(self):
        wh = TempWarehouse()
        try:
            facade = QueryFacade(wh.db_path)
            columns, rows = facade.fetch("v_atlas_status")
            self.assertEqual(len(rows), 1, "v_atlas_status must always report exactly one row")
            facade.close()
        finally:
            wh.close()

    def test_every_other_plumbing_view_still_refuses_on_never_run(self):
        """A3 says these four stay behind the run-level gate -- only v_atlas_status is the
        self-describing exception. Confirmed here so the exception does not silently widen."""
        wh = TempWarehouse()
        try:
            facade = QueryFacade(wh.db_path)
            for view in ("v_pipeline_selfcheck", "v_queryable_source",
                         "v_snapshot_publishable", "v_source_trust"):
                with self.assertRaises(QueryRefused):
                    facade.fetch(view)
            facade.close()
        finally:
            wh.close()


if __name__ == "__main__":
    unittest.main()
