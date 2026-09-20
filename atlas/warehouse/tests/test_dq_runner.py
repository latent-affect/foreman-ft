"""GOALS.json C4, C7. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_dq_runner -v
"""

import json
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
        """The safety invariant, and it is one-directional because the hazard is.

        A seeded check with no function is an OUTAGE: run_all() raises UnregisteredCheckError on a
        name it cannot dispatch, and two launchd jobs call connect() every 60 seconds. A function
        with no seeded check is INERT.

        This asserted equality until 2026-09-04. Equality is symmetric and the hazard is not, so
        it reported the safe interim state (code landed ahead of DDL, which is the ordering
        ARCHITECTURE.md 26.2 and 29.4 both prescribe) identically to the dangerous one, and
        following the documented landing order put the suite red while telling you nothing about
        which side you were on. Found by foreman-v2-a2's independent 29 review, Finding 1, by
        running the guard rather than reading it."""
        seeded = {r[0] for r in self.conn.execute("SELECT check_name FROM dq_check")}
        orphaned = seeded - set(dq_runner.CHECKERS.keys())
        self.assertEqual(
            orphaned, set(),
            f"dq_check seeds {sorted(orphaned)} with no function in CHECKERS -- run_all() will "
            f"raise UnregisteredCheckError on every 60-second tick until this is fixed",
        )

    def test_no_registered_checker_is_left_without_a_seed_row(self):
        """The tidiness property, split out of the assertion above per Finding 1's recommendation.

        Failing this is not an outage. It means a checker function exists that nothing dispatches,
        which is the expected and safe state midway through a landing that follows the prescribed
        code-before-DDL order. Kept as its own test so that when it is red, the red names which
        side of the asymmetry you are on."""
        unseeded = set(dq_runner.CHECKERS.keys()) - {
            r[0] for r in self.conn.execute("SELECT check_name FROM dq_check")
        }
        self.assertEqual(
            unseeded, set(),
            f"CHECKERS carries {sorted(unseeded)} with no dq_check seed row -- inert rather than "
            f"dangerous, and expected mid-landing, but it means nothing dispatches them",
        )

    def test_unregistered_check_name_raises_not_silently_skipped(self):
        run_id = migrate.new_ingest_run(self.conn, status="ok")
        self.conn.execute(
            "INSERT INTO dq_check (check_name, source_table, scope, severity, threshold_kind, "
            "description, implemented) VALUES ('not_a_real_check', 'hook_verdict', 'source', "
            "'advisory', 'invariant', 'deliberately unregistered', 1)"
        )
        with self.assertRaises(dq_runner.UnregisteredCheckError):
            dq_runner.run_all(self.conn, run_id)


class SessionToolCallEvidenceAttrsPresentTests(unittest.TestCase):
    """ATLASSN-143 criteria C2/C3. The promoted check must genuinely evaluate (not vacuous) and
    must FAIL when EACH of the four required attributes is missing on its own, while a fully
    populated row passes -- the ticket's own named vacuous-pass trap and comment 2594's mutation
    requirement. Each mutation is its own row so a single test failure names exactly which
    column stopped being checked. Runs against a disposable tempdir database only."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.conn.execute(
            "INSERT INTO subagent_pull_run (pull_run_id, started_at, status) "
            "VALUES (1, '2026-01-01T00:00:00Z', 'ok')"
        )
        self.conn.execute(
            "INSERT INTO session_transcript (transcript_id, source_path, stream_id, "
            "project_dir, session_id, first_seen_at, updated_at) VALUES "
            "(1, '/tmp/atlassn143-test.jsonl', 'stream-1', '/tmp', 'sess-1', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _insert_call(self, byte_offset, session_id="sess-1", tool_use_id="toolu_1",
                      tool_name="Bash", tool_input_json='{"command":"ls"}'):
        self.conn.execute(
            "INSERT INTO session_tool_call (transcript_id, byte_offset, block_index, "
            "pull_run_id, session_id, tool_use_id, tool_name, tool_input_json) "
            "VALUES (1, ?, 0, 1, ?, ?, ?, ?)",
            (byte_offset, session_id, tool_use_id, tool_name, tool_input_json),
        )
        self.conn.commit()

    def test_passes_on_a_fully_populated_row(self):
        self._insert_call(byte_offset=100)
        result = dq_runner.check_session_tool_call_evidence_attrs_present(self.conn, run_id=1)
        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_fails_on_a_null_session_id(self):
        self._insert_call(byte_offset=200, session_id=None)
        result = dq_runner.check_session_tool_call_evidence_attrs_present(self.conn, run_id=1)
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)

    def test_fails_on_a_null_tool_use_id(self):
        self._insert_call(byte_offset=300, tool_use_id=None)
        result = dq_runner.check_session_tool_call_evidence_attrs_present(self.conn, run_id=1)
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)

    def test_fails_on_a_null_tool_name(self):
        self._insert_call(byte_offset=400, tool_name=None)
        result = dq_runner.check_session_tool_call_evidence_attrs_present(self.conn, run_id=1)
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)

    def test_fails_on_a_null_tool_input_json(self):
        self._insert_call(byte_offset=500, tool_input_json=None)
        result = dq_runner.check_session_tool_call_evidence_attrs_present(self.conn, run_id=1)
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)

    def test_fails_on_an_empty_tool_input_json(self):
        self._insert_call(byte_offset=600, tool_input_json="")
        result = dq_runner.check_session_tool_call_evidence_attrs_present(self.conn, run_id=1)
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)

    def test_registered_under_the_frozen_literal_name(self):
        self.assertIn("session_tool_call_evidence_attrs_present", dq_runner.CHECKERS)
        self.assertIs(
            dq_runner.CHECKERS["session_tool_call_evidence_attrs_present"],
            dq_runner.check_session_tool_call_evidence_attrs_present,
        )


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


class IngestRowsMatchBytesQuarantineTests(unittest.TestCase):
    """ATLASSN-171. The run75-ingest-trap fix (Part 3) gives a row a legitimate reason to be
    absent from rows_ingested -- quarantined, not lost -- but this contract check had no notion
    of quarantine and failed forever on a gap that is fully accounted for. These prove the fix
    both ways: a quarantine-covered gap now passes, and a gap quarantine does NOT cover still
    fails -- the second is the one a naive "just always add rows_quarantined from somewhere"
    fix could get wrong by over-crediting, and the third proves the credit is scoped to the
    right stream_id, not just the right source_name."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.conn = migrate.connect(str(self.root / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        self.quarantine_path = self.root / "quarantine" / "ingest-quarantine.jsonl"

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _make_source_file(self, name, n_lines):
        p = self.root / name
        p.write_text("".join(f'{{"row": {i}}}\n' for i in range(n_lines)))
        return p

    def _seed_ingest_source(self, source_name, stream_id, source_path, byte_offset, rows_ingested):
        self.conn.execute(
            "INSERT INTO ingest_source (source_name, stream_id, source_path, byte_offset, "
            "rows_ingested, first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (source_name, stream_id, str(source_path), byte_offset, rows_ingested),
        )

    def _write_quarantine_records(self, records):
        self.quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.quarantine_path, "a", encoding="utf-8") as fh:
            for source_name, stream_id in records:
                fh.write(json.dumps({
                    "quarantined_at": "2026-01-01T00:00:00Z", "source_name": source_name,
                    "stream_id": stream_id, "byte_offset": 0, "constraint_error": "CHECK",
                }) + "\n")

    def test_quarantined_rows_no_longer_fail_the_contract(self):
        """The bug as filed: run 115's real shape, 15 lines quarantined out of a real file,
        reproduced at small scale -- 10 real lines, 3 quarantined, rows_ingested=7."""
        p = self._make_source_file("verdicts.jsonl", 10)
        self._seed_ingest_source("verdicts", "s1", p, p.stat().st_size, rows_ingested=7)
        self._write_quarantine_records([("verdicts", "s1")] * 3)
        self.conn.commit()
        result = dq_runner.check_ingest_rows_match_bytes(self.conn, self.run_id)
        self.assertTrue(result.passed, result.detail)

    def test_a_genuine_gap_not_covered_by_quarantine_still_fails(self):
        """The negative control this fix must not break: real data loss (rows_ingested short by
        more than quarantine accounts for) must still fail, or the fix has become "never fail",
        not "account for quarantine"."""
        p = self._make_source_file("verdicts.jsonl", 10)
        self._seed_ingest_source("verdicts", "s1", p, p.stat().st_size, rows_ingested=7)
        self._write_quarantine_records([("verdicts", "s1")] * 2)  # only 2, not the 3 needed
        self.conn.commit()
        result = dq_runner.check_ingest_rows_match_bytes(self.conn, self.run_id)
        self.assertFalse(result.passed, result.detail)
        self.assertIn("verdicts", result.detail)

    def test_quarantine_credit_is_scoped_to_the_sources_own_stream_id(self):
        """The over-crediting trap: quarantine records for the SAME source_name under a
        DIFFERENT stream_id (a prior generation of the file, e.g. after a rotation) must not
        cover today's gap. Naive credit keyed on source_name alone would pass this wrongly."""
        p = self._make_source_file("verdicts.jsonl", 10)
        self._seed_ingest_source("verdicts", "s1", p, p.stat().st_size, rows_ingested=7)
        self._write_quarantine_records([("verdicts", "OLD-STREAM")] * 3)  # wrong stream_id
        self.conn.commit()
        result = dq_runner.check_ingest_rows_match_bytes(self.conn, self.run_id)
        self.assertFalse(result.passed, result.detail)

    def test_malformed_quarantine_line_is_disclosed_not_silently_dropped(self):
        self.quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        self.quarantine_path.write_text("not valid json{{{\n")
        p = self._make_source_file("verdicts.jsonl", 10)
        self._seed_ingest_source("verdicts", "s1", p, p.stat().st_size, rows_ingested=10)
        self.conn.commit()
        result = dq_runner.check_ingest_rows_match_bytes(self.conn, self.run_id)
        self.assertFalse(result.passed, result.detail)
        self.assertIn("unparseable", result.detail)


class FailOpenNotDoubleCountedTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_shell_writer_errors_do_not_fail_the_hook_pairing_contract(self):
        _make_verdict(
            self.conn, self.run_id, "s1", 0, "2026-01-01T00:00:01Z",
            "laa-commit-flow-advisory.sh", "error", cwd="/proj/a",
        )
        self.conn.commit()
        result = dq_runner.check_fail_open_not_double_counted(self.conn, self.run_id)
        self.assertTrue(result.passed, result.detail)
        self.assertEqual(result.observed_value, 0)

    def test_unpaired_hook_error_fails(self):
        _make_verdict(
            self.conn, self.run_id, "s1", 0, "2026-01-01T00:00:01Z",
            "architecture_gate.py", "error", cwd="/proj/a",
        )
        self.conn.commit()
        result = dq_runner.check_fail_open_not_double_counted(self.conn, self.run_id)
        self.assertFalse(result.passed)
        self.assertGreater(result.observed_value, 0)

    def test_paired_hook_error_passes(self):
        ts = "2026-01-01T00:00:01Z"
        _make_verdict(
            self.conn, self.run_id, "s1", 0, ts,
            "architecture_gate.py", "error", cwd="/proj/a",
        )
        self.conn.execute(
            "INSERT INTO audit_event (stream_id, byte_offset, ingest_run_id, source_path, "
            "ts, event_type, cwd, payload_json, payload_bytes) "
            "VALUES ('a', 0, ?, '/tmp/safety.jsonl', ?, 'HOOK_ERROR', '/proj/a', "
            "'{\"hook\": \"architecture_gate.py\"}', 30)",
            (self.run_id, ts),
        )
        self.conn.commit()
        result = dq_runner.check_fail_open_not_double_counted(self.conn, self.run_id)
        self.assertTrue(result.passed, result.detail)
        self.assertEqual(result.observed_value, 0)


if __name__ == "__main__":
    unittest.main()
