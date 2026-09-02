"""monitoring/GOALS.json C1-C4 (Check 6 finding, dev-harness-33 independent). Real fixtures --
a real ATLAS warehouse db migrated and made genuinely clean, a real TESSERA-shaped projects
table -- not mocks standing in for the whole system.

    python3 -m unittest monitoring.foreman-status.test_foreman_status_dashboard_data -v

(monitoring/foreman-status has a hyphen, so it isn't a valid Python package path -- run this
file directly instead: /path/to/venv/bin/python3 monitoring/foreman-status/test_foreman_status_dashboard_data.py)
"""
import importlib.util
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from atlas.query.facade import QueryRefused  # noqa: E402
from atlas.query.tests._helpers import make_clean_run  # noqa: E402
from atlas.warehouse import migrate  # noqa: E402

MODULE_PATH = Path(__file__).resolve().parent / "foreman_status_dashboard_data.py"


def _load_module_with_dbs(warehouse_db, tessera_db):
    """Loads foreman_status_dashboard_data.py fresh with WAREHOUSE_DB/TESSERA_DB patched to the
    given temp fixtures -- both are module-level constants read at import time, so a fresh load
    per test (rather than reusing one cached import) is what lets each test point them
    somewhere different."""
    spec = importlib.util.spec_from_file_location("foreman_status_dashboard_data_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WAREHOUSE_DB = Path(warehouse_db)
    module.TESSERA_DB = Path(tessera_db)
    return module


def _make_clean_warehouse(db_path):
    """Uses the atlas query test suite's own shared fixture (atlas/query/tests/_helpers.py),
    not a from-scratch one: make_clean_run's default with_verdicts=True inserts one real
    hook_verdict row, which is what keeps v_hook_verdict (and everything built on it, including
    v_decision_outcome_rate) out of the documented 'trusted but empty' sentinel branch
    (docs/atlas-architecture.md line ~259) -- a genuinely empty-but-clean warehouse hits that
    sentinel and trips a real, pre-existing, out-of-scope NULL-arithmetic bug in this module's
    own gates_by_prefix aggregation (see the note left for agent-remediation-af below)."""
    conn = migrate.connect(str(db_path))
    make_clean_run(conn)
    conn.close()


def _make_tessera_fixture(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE projects (prefix TEXT, codename TEXT, source_root TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO projects VALUES ('TEST', 'TESTPROJ', '/tmp/testproj', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()


class FacadeExceptionHandlingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.warehouse_db = self.tmp / "atlas.db"
        self.tessera_db = self.tmp / "tessera.db"
        _make_clean_warehouse(self.warehouse_db)
        _make_tessera_fixture(self.tessera_db)

    # ---------------------------------------------------------------- C1

    def test_c1_query_refused_mid_fetch_does_not_crash_and_returns_none_not_zero(self):
        module = _load_module_with_dbs(self.warehouse_db, self.tessera_db)
        real_fetch = module.QueryFacade.fetch
        call_count = {"n": 0}

        def flaky_fetch(self, view_name, where_sql=None, params=()):
            call_count["n"] += 1
            if call_count["n"] == 3:  # after status() was already clean; mid-run
                raise QueryRefused("simulated: warehouse state flipped mid-script")
            return real_fetch(self, view_name, where_sql, params)

        with mock.patch.object(module.QueryFacade, "fetch", flaky_fetch):
            snapshot = module.build_snapshot()  # must not raise

        self.assertIsNotNone(snapshot["warehouse"])
        self.assertIn("fetch_error", snapshot["warehouse"])
        self.assertIn("simulated", snapshot["warehouse"]["fetch_error"])
        self.assertTrue(snapshot["projects"], "the tessera fixture project must still appear")
        for p in snapshot["projects"]:
            self.assertIsNone(p["tickets"], "must be None, not 0 or {}, on a failed fetch")
            self.assertIsNone(p["gates"], "must be None, not 0 or {}, on a failed fetch")

    # ---------------------------------------------------------------- C2

    def test_c2_raw_sqlite_error_mid_fetch_is_also_caught(self):
        module = _load_module_with_dbs(self.warehouse_db, self.tessera_db)
        real_fetch = module.QueryFacade.fetch
        call_count = {"n": 0}

        def flaky_fetch(self, view_name, where_sql=None, params=()):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise sqlite3.OperationalError("simulated: database is locked")
            return real_fetch(self, view_name, where_sql, params)

        with mock.patch.object(module.QueryFacade, "fetch", flaky_fetch):
            snapshot = module.build_snapshot()  # must not raise

        self.assertIn("fetch_error", snapshot["warehouse"])
        self.assertIn("simulated", snapshot["warehouse"]["fetch_error"])
        for p in snapshot["projects"]:
            self.assertIsNone(p["tickets"])
            self.assertIsNone(p["gates"])

    # ---------------------------------------------------------------- C3

    def test_c3_facade_connection_is_genuinely_closed_even_on_mid_fetch_exception(self):
        module = _load_module_with_dbs(self.warehouse_db, self.tessera_db)
        real_fetch = module.QueryFacade.fetch
        captured_facade = {}
        real_init = module.QueryFacade.__init__

        def capturing_init(self, db_path):
            real_init(self, db_path)
            captured_facade["facade"] = self

        def flaky_fetch(self, view_name, where_sql=None, params=()):
            raise QueryRefused("simulated: fails on the very first fetch")

        with mock.patch.object(module.QueryFacade, "__init__", capturing_init), \
             mock.patch.object(module.QueryFacade, "fetch", flaky_fetch):
            module.build_snapshot()

        self.assertIn("facade", captured_facade, "QueryFacade must have been constructed")
        with self.assertRaises(sqlite3.ProgrammingError):
            captured_facade["facade"]._conn.execute("SELECT 1")

    # ---------------------------------------------------------------- C4 (unaffected paths)

    def test_c4_construction_time_unavailable_path_unaffected(self):
        module = _load_module_with_dbs(self.tmp / "does-not-exist.db", self.tessera_db)
        snapshot = module.build_snapshot()
        self.assertFalse(snapshot["warehouse"]["available"])
        self.assertIn("reason", snapshot["warehouse"])
        for p in snapshot["projects"]:
            self.assertIsNone(p["tickets"])
            self.assertIsNone(p["gates"])

    def test_c4_not_clean_at_first_check_path_unaffected(self):
        dirty_db = self.tmp / "dirty.db"
        conn = migrate.connect(str(dirty_db))
        conn.close()  # migrated, but never ingested -- status() reports never-run
        module = _load_module_with_dbs(dirty_db, self.tessera_db)
        snapshot = module.build_snapshot()
        self.assertTrue(snapshot["warehouse"]["available"])
        self.assertEqual(snapshot["warehouse"]["state"], "never-run")
        self.assertNotIn("fetch_error", snapshot["warehouse"])
        for p in snapshot["projects"]:
            self.assertIsNone(p["tickets"])
            self.assertIsNone(p["gates"])

    def test_success_path_still_populates_real_data_and_closes(self):
        """Not a numbered criterion -- the baseline this whole fix must not have broken."""
        module = _load_module_with_dbs(self.warehouse_db, self.tessera_db)
        snapshot = module.build_snapshot()
        self.assertTrue(snapshot["warehouse"]["available"])
        self.assertEqual(snapshot["warehouse"]["state"], "clean")
        self.assertNotIn("fetch_error", snapshot["warehouse"])
        self.assertIn("source_freshness", snapshot["global"])


if __name__ == "__main__":
    unittest.main()
