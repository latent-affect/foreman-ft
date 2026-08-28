"""GOALS.json C4, C7. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.warehouse.tests.test_dq_runner -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import dq_runner, migrate


def _make_project(conn, prefix, source_root, root_state="git-repo"):
    conn.execute(
        "INSERT INTO dim_project (project_prefix, project_codename, source_root, root_state, "
        "refreshed_at) VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z')",
        (prefix, prefix, source_root, root_state),
    )


def _make_cwd(conn, cwd, prefix, resolution="unique", candidate_count=1):
    matched_root = cwd if resolution != "unregistered" else None
    conn.execute(
        "INSERT INTO cwd_project (cwd, matched_root, project_prefix, candidate_count, "
        "resolution, resolved_at) VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
        (cwd, matched_root, prefix if resolution == "unique" else None, candidate_count, resolution),
    )


def _make_verdict(conn, run_id, stream_id, byte_offset, ts, handler_id, verdict, cwd=None, **extra):
    row = {
        "stream_id": stream_id, "byte_offset": byte_offset, "ingest_run_id": run_id,
        "ts": ts, "ts_resolution": "microsecond", "handler_id": handler_id, "verdict": verdict,
        "cwd": cwd,
    }
    row.update(extra)
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO hook_verdict ({cols}) VALUES ({placeholders})", tuple(row.values()))


class DispatchTableCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_every_seeded_check_name_has_a_registered_checker(self):
        seeded = {r[0] for r in self.conn.execute("SELECT check_name FROM dq_check")}
        self.assertEqual(seeded, set(dq_runner.CHECKERS.keys()))

    def test_unregistered_check_name_raises_not_silently_skipped(self):
        run_id = migrate.new_ingest_run(self.conn, status="ok")
        self.conn.execute(
            "INSERT INTO dq_check (check_name, source_table, scope, severity, threshold_kind, "
            "description, implemented) VALUES ('not_a_real_check', 'hook_verdict', 'source', "
            "'advisory', 'invariant', 'deliberately unregistered', 1)"
        )
        with self.assertRaises(dq_runner.UnregisteredCheckError):
            dq_runner.run_all(self.conn, run_id)


class VerdictDomainClosedTests(unittest.TestCase):
    """C4, first half: an invariant-threshold check. hook_verdict's own CHECK constraint
    already blocks an invalid verdict at the engine level, so this test uses
    PRAGMA ignore_check_constraints to insert one anyway -- verifying the CHECKER re-verifies
    the invariant itself (defense in depth) rather than merely trusting the constraint, which
    matters for data that predates a constraint or arrives via a path that bypasses it."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_passes_when_all_verdicts_valid(self):
        _make_verdict(self.conn, self.run_id, "s1", 0, "2026-01-01T00:00:00.000001Z", "h1", "fire")
        _make_verdict(self.conn, self.run_id, "s1", 100, "2026-01-01T00:00:01.000001Z", "h1", "silent")
        self.conn.commit()
        result = dq_runner.check_verdict_domain_closed(self.conn, self.run_id)
        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_fails_on_an_out_of_domain_verdict(self):
        self.conn.execute("PRAGMA ignore_check_constraints = ON")
        _make_verdict(self.conn, self.run_id, "s1", 0, "2026-01-01T00:00:00.000001Z", "h1", "bogus")
        self.conn.commit()
        result = dq_runner.check_verdict_domain_closed(self.conn, self.run_id)
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)


class ResolutionRateDeltaTests(unittest.TestCase):
    """C4, second half: a calibrated-threshold check computed from another queried value."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        _make_project(self.conn, "PROJA", "/proj/a")
        _make_cwd(self.conn, "/proj/a", "PROJA", resolution="unique")
        _make_cwd(self.conn, "/proj/unreg", None, resolution="unregistered", candidate_count=0)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_passes_when_resolution_rate_holds(self):
        run1 = migrate.new_ingest_run(self.conn, status="ok")
        for i in range(10):
            _make_verdict(self.conn, run1, "s1", i, f"2026-01-01T00:00:{i:02d}.000001Z", "h1", "fire", cwd="/proj/a")
        self.conn.commit()
        run2 = migrate.new_ingest_run(self.conn, status="ok")
        for i in range(10, 20):
            _make_verdict(self.conn, run2, "s1", i, f"2026-01-01T00:00:{i:02d}.000001Z", "h1", "fire", cwd="/proj/a")
        self.conn.commit()
        result = dq_runner.check_resolution_rate_delta(self.conn, run2)
        self.assertTrue(result.passed)

    def test_fails_when_resolution_rate_drops_more_than_5_points(self):
        run1 = migrate.new_ingest_run(self.conn, status="ok")
        for i in range(20):
            _make_verdict(self.conn, run1, "s1", i, f"2026-01-01T00:{i:02d}:00.000001Z", "h1", "fire", cwd="/proj/a")
        self.conn.commit()
        run2 = migrate.new_ingest_run(self.conn, status="ok")
        # 20 more rows, all resolving to the UNREGISTERED bucket -- a real resolver regression.
        for i in range(20):
            _make_verdict(self.conn, run2, "s2", i, f"2026-01-01T01:{i:02d}:00.000001Z", "h1", "fire", cwd="/proj/unreg")
        self.conn.commit()
        result = dq_runner.check_resolution_rate_delta(self.conn, run2)
        self.assertFalse(result.passed)
        self.assertGreater(result.observed_value, 5.0)


if __name__ == "__main__":
    unittest.main()
