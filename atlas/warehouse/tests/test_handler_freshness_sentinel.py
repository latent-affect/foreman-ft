"""ATLASSN-51. Migration 6's v_handler_freshness read hook_verdict directly with no trust_state
and no UNION ALL sentinel, against ARCHITECTURE.md section 8's invariant -- a verbatim FATAL-2
recurrence. Migration 7 re-creates it behind the sentinel.

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_handler_freshness_sentinel -v

NEGATIVE-CONTROL DESIGN. test_pre_fix_view_leaks_while_distrusted below rebuilds migration 6's
ORIGINAL view definition and asserts it leaks. If that test ever starts passing trivially (zero
rows) the distrust fixture has stopped working and every other assertion in this file is
vacuous -- so it is the control that proves the rest discriminate, and it is written to fail
loudly rather than to be reassuring.
"""

import sqlite3
import unittest

from atlas.query.tests._helpers import TempWarehouse, make_clean_run
from atlas.warehouse import migrate

# Migration 6's definition, verbatim, kept here ONLY so the negative control can reproduce the
# defect. Never executed against a real warehouse outside this test.
PRE_FIX_VIEW = """
DROP VIEW IF EXISTS v_handler_freshness;
CREATE VIEW v_handler_freshness AS
SELECT handler_id, MIN(ts) AS first_seen, MAX(ts) AS last_seen, COUNT(*) AS row_count
FROM hook_verdict GROUP BY handler_id;
"""


def distrust_hook_verdict(conn):
    """Force the source distrusted the way the trust gate really does -- fail a contract check
    for the latest ok run -- rather than by deleting rows or editing v_queryable_source, so the
    fixture exercises the same path production would."""
    run_id = conn.execute("SELECT MAX(run_id) FROM ingest_run WHERE status='ok'").fetchone()[0]
    check_name = conn.execute(
        "SELECT check_name FROM dq_check WHERE severity='contract' AND source_table='hook_verdict' "
        "LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE dq_check_run SET passed=0, detail='forced-distrust-fixture' "
        "WHERE run_id=? AND check_name=?", (run_id, check_name))
    conn.commit()
    return check_name


class HandlerFreshnessSentinelTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()
        make_clean_run(self.wh.setup_conn)
        self.conn = self.wh.setup_conn

    def tearDown(self):
        self.wh.close()

    def test_fixture_actually_distrusts_the_source(self):
        """Proves the fixture works before anything else relies on it. Without this, a broken
        distrust fixture would make every sentinel assertion below pass for the wrong reason."""
        before = self.conn.execute(
            "SELECT COUNT(*) FROM v_hook_verdict WHERE trust_state='ok'").fetchone()[0]
        self.assertGreater(before, 0, "fixture warehouse has no trusted rows to begin with")
        distrust_hook_verdict(self.conn)
        after = self.conn.execute(
            "SELECT COUNT(*) FROM v_hook_verdict WHERE trust_state='ok'").fetchone()[0]
        self.assertEqual(after, 0, "forcing a contract check to fail did not distrust the source")

    def test_pre_fix_view_leaks_while_distrusted(self):
        """THE negative control. Migration 6's definition serves handler rows from a formally
        disowned source. If this stops leaking, the control has stopped discriminating."""
        self.conn.executescript(PRE_FIX_VIEW)
        self.conn.commit()
        distrust_hook_verdict(self.conn)
        rows = self.conn.execute("SELECT * FROM v_handler_freshness").fetchall()
        self.assertGreater(
            len(rows), 0,
            "pre-fix view returned nothing while distrusted -- the negative control is not "
            "reproducing the defect, so the post-fix assertions below prove nothing")

    def test_post_fix_view_returns_only_the_sentinel_while_distrusted(self):
        migrate.apply_migration7(self.conn)
        distrust_hook_verdict(self.conn)
        rows = self.conn.execute("SELECT * FROM v_handler_freshness").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "SOURCE-DISTRUSTED-DO-NOT-USE")

    def test_post_fix_view_exposes_a_trust_state_column(self):
        """Section 8's invariant is about the COLUMN existing, not only about row counts -- a
        consumer has to be able to see the state without inferring it from cardinality."""
        migrate.apply_migration7(self.conn)
        cols = [d[0] for d in self.conn.execute("SELECT * FROM v_handler_freshness").description]
        self.assertIn("trust_state", cols)

    def test_trusted_state_matches_the_raw_aggregate(self):
        """The fix must not change what the view reports when the source IS trusted."""
        raw = self.conn.execute(
            "SELECT handler_id, MIN(ts), MAX(ts), COUNT(*) FROM hook_verdict "
            "GROUP BY handler_id ORDER BY handler_id").fetchall()
        migrate.apply_migration7(self.conn)
        fixed = self.conn.execute(
            "SELECT handler_id, first_seen, last_seen, row_count FROM v_handler_freshness "
            "WHERE trust_state='ok' ORDER BY handler_id").fetchall()
        self.assertEqual(fixed, raw)

    def test_migration7_is_idempotent_and_recorded_once(self):
        migrate.apply_migration7(self.conn)
        migrate.apply_migration7(self.conn)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM schema_migration WHERE version=?",
            (migrate.MIGRATION7_VERSION,)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_migration7_does_not_disturb_earlier_recorded_hashes(self):
        before = dict(self.conn.execute(
            "SELECT version, ddl_sha256 FROM schema_migration WHERE version <= 6"))
        migrate.apply_migration7(self.conn)
        after = dict(self.conn.execute(
            "SELECT version, ddl_sha256 FROM schema_migration WHERE version <= 6"))
        self.assertEqual(before, after)

    def test_drift_in_section_23_raises_rather_than_proceeding(self):
        """apply_additive's own drift discipline, asserted for migration 7 specifically: a
        recorded version whose section hash no longer matches must raise, not silently pass."""
        migrate.apply_migration7(self.conn)
        self.conn.execute(
            "UPDATE schema_migration SET ddl_sha256='deadbeef' WHERE version=?",
            (migrate.MIGRATION7_VERSION,))
        self.conn.commit()
        with self.assertRaises(migrate.MigrationError):
            migrate.apply_migration7(self.conn)


if __name__ == "__main__":
    unittest.main()
