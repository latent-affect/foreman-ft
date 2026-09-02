"""atlas/GOALS.json C1-C6 (DEVH-13, PRD.md R8). Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.ingest.tests.test_audit_scrub -v
"""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from atlas.ingest import audit_scrub
from atlas.warehouse import dq_runner, migrate


def _write_lines(path, objs):
    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n")


def _base_event(**overrides):
    event = {
        "schema_version": "audit-1", "ts": "2026-01-01T00:00:00.000000Z",
        "event_type": "SAFETY_ASK", "severity": "medium", "session_id": "s1",
        "cwd": "/proj", "ledger": "global-safety", "cmd": "clean command",
    }
    event.update(overrides)
    return event


def _snapshot(d: Path):
    out = {}
    if not d.is_dir():
        return out
    for p in d.rglob("*"):
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class AuditScrubTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.conn = migrate.connect(self.db_path)
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _fresh_rows(self):
        """C1: a NEW raw sqlite3 connection, never the ingest connection and never QueryFacade."""
        conn2 = sqlite3.connect(self.db_path)
        try:
            return conn2.execute(
                "SELECT stream_id, byte_offset, payload_json, payload_bytes, payload_redacted, "
                "session_id FROM audit_event"
            ).fetchall()
        finally:
            conn2.close()

    # ---------------------------------------------------------------- C1

    def test_c1_every_planted_pattern_is_redacted_and_verified_from_fresh_connection(self):
        planted = " | ".join(
            {
                "aws_access_key": "AKIAABCDEFGHIJKLMNOP",
                "private_key_header": "-----BEGIN RSA PRIVATE KEY-----",
                "slack_token": "xoxb-FAKETESTFIXTURE-notarealtoken",
                "github_token": "ghp_" + "a" * 36,
                "bearer_token": "Bearer abcdefghijklmnopqrstuvwx1234567890",
                "literal_export_assignment": "export API_KEY=somesecretvalue",
                "generic_api_key_assignment": 'api_key: "abcdefghijklmnop1234"',
                "password_assignment": 'password: "hunterhunter2"',
                "credentialed_url": "https://user:hunter2@example.com/path",
                "generic_secret_keyword_assignment": 'secretvalue = "abcdefghijklmnop"',
            }.values()
        )
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [_base_event(session_id="c1", cmd=planted)])

        result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)
        self.assertEqual(result.dropped_count, 0, result.drops)
        self.assertEqual(result.rows_inserted, 1)

        rows = self._fresh_rows()
        self.assertEqual(len(rows), 1)
        stream_id, byte_offset, payload_json, payload_bytes, payload_redacted, session_id = rows[0]
        self.assertIsNone(dq_runner.CREDENTIAL_PATTERNS and self._still_matches(payload_json))
        json.loads(payload_json)  # must still parse
        self.assertEqual(payload_redacted, 1)
        self.assertEqual(payload_bytes, len(payload_json.encode("utf-8")))

    def _still_matches(self, text):
        import re
        for name, pattern in dq_runner.CREDENTIAL_PATTERNS.items():
            if re.search(pattern, text):
                return name
        return None

    def test_c1_does_not_stop_at_first_match_within_one_line(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [_base_event(
            session_id="multi",
            cmd="AKIAABCDEFGHIJKLMNOP and also xoxb-FAKETESTFIXTURE-notarealtoken",
        )])
        result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)
        self.assertEqual(result.dropped_count, 0, result.drops)
        rows = self._fresh_rows()
        payload_json = rows[0][2]
        self.assertNotIn("AKIA", payload_json)
        self.assertNotIn("xoxb-", payload_json)

    # ---------------------------------------------------------------- C2

    def test_c2_scrub_whose_written_bytes_diverge_from_its_return_value_is_caught(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        planted = "AKIAABCDEFGHIJKLMNOP"
        _write_lines(fixture, [_base_event(session_id="c2", cmd=planted)])

        real_insert = audit_scrub.insert_audit_event_row

        def lying_insert(conn, stream_id, byte_offset, row, ingest_run_id):
            # Simulates exactly the bug GOALS.json C2 names: scrub_row's return value is clean,
            # but what actually gets WRITTEN is the pre-scrub original.
            liar_row = dict(row)
            liar_row["payload_json"] = json.dumps(_base_event(session_id="c2", cmd=planted))
            return real_insert(conn, stream_id, byte_offset, liar_row, ingest_run_id)

        with mock.patch.object(audit_scrub, "insert_audit_event_row", side_effect=lying_insert):
            result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)

        self.assertEqual(result.dropped_count, 1, result.drops)
        self.assertEqual(result.rows_inserted, 0)
        rows = self._fresh_rows()
        self.assertEqual(len(rows), 0, "the lying write must not have reached the live table")

    # ---------------------------------------------------------------- C3

    def test_c3_scrub_that_raises_hard_fails_with_no_stranded_artifact(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [_base_event(session_id="c3", cmd="AKIAABCDEFGHIJKLMNOP")])

        warehouse_dir = Path(self.db_path).parent
        before_warehouse = _snapshot(warehouse_dir)
        # File PATHS only, not content hashes -- this machine runs other concurrent sessions
        # that legitimately touch unrelated files under the shared system temp directory during
        # this test's own runtime, and a content-hash diff there is noise, not signal. What C3
        # actually cares about is whether THIS code strands a NEW file it created; an existing,
        # unrelated file changing size is not that.
        before_tempdir_paths = set(_snapshot(Path(tempfile.gettempdir())))

        with mock.patch.object(
            audit_scrub, "redact_payload", side_effect=RuntimeError("forced scrub failure")
        ):
            result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)

        self.assertEqual(result.dropped_count, 1, result.drops)
        self.assertEqual(result.rows_inserted, 0)
        rows = self._fresh_rows()
        self.assertEqual(len(rows), 0)

        after_tempdir_paths = set(_snapshot(Path(tempfile.gettempdir())))
        new_tempdir_files = after_tempdir_paths - before_tempdir_paths
        self.assertEqual(
            new_tempdir_files, set(),
            "a forced scrub failure must not strand a NEW file in the system temp directory",
        )
        # The warehouse dir legitimately changes (WAL/journal churn from the ingest_run insert
        # and the failed-but-committed transaction) -- what must NOT appear is a NEW file this
        # harness itself created and failed to clean up. This design creates none at all (no
        # OS-level temp file, only a same-connection TEMP TABLE that vanishes with the
        # connection), so assert no *new* filenames appeared, independent of content churn in
        # existing ones.
        after_warehouse = _snapshot(warehouse_dir)
        self.assertEqual(
            set(before_warehouse) - set(after_warehouse), set(),
            "no file should have disappeared",
        )
        new_files = set(after_warehouse) - set(before_warehouse)
        self.assertEqual(new_files, set(), f"unexpected new file(s) in warehouse dir: {new_files}")

    # ---------------------------------------------------------------- C4

    def test_c4_scenario_one_single_rigged_failure_among_five(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [
            _base_event(session_id="s1"),
            _base_event(session_id="s2"),
            _base_event(session_id="POISON", cmd="AKIAABCDEFGHIJKLMNOP"),
            _base_event(session_id="s3"),
            _base_event(session_id="s4"),
        ])

        real_redact = audit_scrub.redact_payload

        def rigged_redact(payload_json):
            if "POISON" in payload_json:
                raise RuntimeError("rigged failure for s c4-scenario-1")
            return real_redact(payload_json)

        with mock.patch.object(audit_scrub, "redact_payload", side_effect=rigged_redact):
            result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)

        self.assertEqual(result.dropped_count, 1, result.drops)
        rows = self._fresh_rows()
        self.assertEqual(len(rows), 4)
        session_ids = {r[5] for r in rows}
        self.assertEqual(session_ids, {"s1", "s2", "s3", "s4"})
        for r in rows:
            self.assertIsNone(self._still_matches(r[2]))

    def test_c4_scenario_two_two_rigged_failures_among_six_count_is_not_a_boolean(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [
            _base_event(session_id="a1"),
            _base_event(session_id="POISON-A", cmd="AKIAABCDEFGHIJKLMNOP"),
            _base_event(session_id="a2"),
            _base_event(session_id="POISON-B", cmd="AKIAABCDEFGHIJKLMNOP"),
            _base_event(session_id="a3"),
            _base_event(session_id="a4"),
        ])

        real_redact = audit_scrub.redact_payload

        def rigged_redact(payload_json):
            if "POISON" in payload_json:
                raise RuntimeError("rigged failure for c4-scenario-2")
            return real_redact(payload_json)

        with mock.patch.object(audit_scrub, "redact_payload", side_effect=rigged_redact):
            result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)

        self.assertEqual(result.dropped_count, 2, result.drops)
        self.assertIsInstance(result.dropped_count, int)
        self.assertNotIsInstance(result.dropped_count, bool)
        rows = self._fresh_rows()
        self.assertEqual(len(rows), 4)
        session_ids = {r[5] for r in rows}
        self.assertEqual(session_ids, {"a1", "a2", "a3", "a4"})

    # ---------------------------------------------------------------- C5

    def test_c5_credential_scan_stays_registered_after_write_time_gate_lands(self):
        self.assertIn("audit_payload_credential_scan", dq_runner.CHECKERS)
        severity = self.conn.execute(
            "SELECT severity FROM dq_check WHERE check_name='audit_payload_credential_scan'"
        ).fetchone()[0]
        self.assertEqual(severity, "contract")

    # ---------------------------------------------------------------- C6

    def test_c6_redaction_marker_differs_between_dirty_and_clean_rows(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [
            _base_event(session_id="dirty", cmd="AKIAABCDEFGHIJKLMNOP"),
            _base_event(session_id="clean", cmd="git status"),
        ])
        result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)
        self.assertEqual(result.dropped_count, 0, result.drops)

        rows = {r[5]: r for r in self._fresh_rows()}
        dirty_payload_json, dirty_payload_bytes, dirty_redacted = (
            rows["dirty"][2], rows["dirty"][3], rows["dirty"][4]
        )
        clean_payload_json, clean_payload_bytes, clean_redacted = (
            rows["clean"][2], rows["clean"][3], rows["clean"][4]
        )

        self.assertEqual(dirty_redacted, 1)
        self.assertEqual(clean_redacted, 0)

        # wellformedness half: actually exercise the fields redaction touches, not just a
        # generic pass/fail on check_audit_envelope_wellformed.
        json.loads(dirty_payload_json)
        json.loads(clean_payload_json)
        self.assertEqual(dirty_payload_bytes, len(dirty_payload_json.encode("utf-8")))
        self.assertEqual(clean_payload_bytes, len(clean_payload_json.encode("utf-8")))
        self.assertNotEqual(
            dirty_payload_bytes,
            len(json.dumps(_base_event(session_id="dirty", cmd="AKIAABCDEFGHIJKLMNOP")).encode("utf-8")),
            "payload_bytes on the redacted row must reflect the STORED (redacted) value, not "
            "the pre-redaction length",
        )

        envelope = dq_runner.check_audit_envelope_wellformed(self.conn, self.run_id)
        self.assertTrue(envelope.passed, envelope.detail)

    # ------------------------------------------------------- shared-connection shadow hygiene

    def test_staging_temp_table_does_not_leak_into_later_reads_on_same_connection(self):
        """Not a numbered GOALS.json criterion on its own -- a real risk this implementation's
        own design (a same-named TEMP TABLE shadowing main.audit_event) creates if the temp
        table were ever left behind: run_pull.py shares ONE connection across every source in a
        run, and dq_runner's checks read audit_event unqualified. If the shadow outlived this
        call, every later unqualified read on the SAME connection would silently see the
        (by-then-empty) staging table instead of the real one."""
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [_base_event(session_id="shadow-check")])
        result = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)
        self.assertEqual(result.dropped_count, 0, result.drops)

        # Unqualified read on the SAME connection, simulating what dq_runner.run_all() does
        # right after run_pull.py's audit-sources loop finishes.
        row = self.conn.execute(
            "SELECT session_id FROM audit_event WHERE session_id='shadow-check'"
        ).fetchone()
        self.assertIsNotNone(row, "the real table must be visible again on this connection")

        temp_tables = self.conn.execute(
            "SELECT name FROM sqlite_temp_master WHERE type='table' AND name='audit_event'"
        ).fetchall()
        self.assertEqual(temp_tables, [], "the staging temp table must not survive past one call")

    def test_zero_new_lines_does_not_drop_the_real_table(self):
        """Regression: caught by the full atlas suite (test_run_pull's second-real-run test),
        not by any test in this file at the time -- a call where stream.tail() finds ZERO new
        lines never invokes insert_row at all, so the staging table was never created, and the
        unconditional cleanup DROP TABLE IF EXISTS audit_event fell through the missing temp
        shadow and dropped main.audit_event for real. Fixed by creating the staging table
        unconditionally, before stream.tail() runs, not lazily inside insert_row."""
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        _write_lines(fixture, [_base_event(session_id="first-run")])

        first = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)
        self.assertEqual(first.dropped_count, 0, first.drops)
        self.assertEqual(first.rows_inserted, 1)

        # Second call against the SAME (now fully-consumed) file: zero new lines this time.
        second = audit_scrub.ingest_audit_source(self.conn, "audit", str(fixture), self.run_id)
        self.assertEqual(second.rows_inserted, 0)
        self.assertIsNone(second.error)

        # The real table must still exist and still hold the first run's row.
        row = self.conn.execute(
            "SELECT session_id FROM audit_event WHERE session_id='first-run'"
        ).fetchone()
        self.assertIsNotNone(row, "main.audit_event must survive a zero-new-lines call")


if __name__ == "__main__":
    unittest.main()
