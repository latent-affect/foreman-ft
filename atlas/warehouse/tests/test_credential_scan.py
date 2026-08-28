"""Regression test for adversarial-code-review's SERIOUS verification finding: auditplane_to_ingest
is secret-bearing (ARCHITECTURE.md section 0/4) and no live check ever verified the "zero hits"
claim section 1 makes. Not a new frozen GOALS.json criterion on its own -- covered by warehouse's
existing C4 (checker evaluation) and C7 (dispatch completeness), which the amended C2 (19 rows)
now includes this check in; this file verifies the checker's own actual detection logic.

Run from the repo root:
    /path/to/venv/bin/python3 -m unittest atlas.warehouse.tests.test_credential_scan -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import dq_runner, migrate


def _insert_audit_row(conn, run_id, stream_id, byte_offset, payload_json):
    conn.execute(
        "INSERT INTO audit_event (stream_id, byte_offset, ingest_run_id, source_path, ts, "
        "event_type, payload_json, payload_bytes) VALUES (?, ?, ?, 'test.jsonl', "
        "'2026-01-01T00:00:00Z', 'SAFETY_ASK', ?, ?)",
        (stream_id, byte_offset, run_id, payload_json, len(payload_json)),
    )


class CredentialScanTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_clean_payloads_pass(self):
        _insert_audit_row(self.conn, self.run_id, "s1", 0, '{"cwd": "/proj", "cmd": "git status"}')
        self.conn.commit()
        result = dq_runner.check_audit_payload_credential_scan(self.conn, self.run_id)
        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_real_recorded_incident_shape_is_caught(self):
        # This project's own recorded incident (CLAUDE.md): a Gemini API key exposed via a
        # literal `export KEY=value` argument.
        payload = '{"cwd": "/proj", "cmd": "export GEMINI_API_KEY=AIzaSyD-realistic-fake-value-here"}'
        _insert_audit_row(self.conn, self.run_id, "s1", 0, payload)
        self.conn.commit()
        result = dq_runner.check_audit_payload_credential_scan(self.conn, self.run_id)
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)

    def test_aws_key_shape_is_caught(self):
        payload = '{"cwd": "/proj", "cmd": "echo AKIAABCDEFGHIJKLMNOP"}'
        _insert_audit_row(self.conn, self.run_id, "s1", 0, payload)
        self.conn.commit()
        result = dq_runner.check_audit_payload_credential_scan(self.conn, self.run_id)
        self.assertFalse(result.passed)

    def test_registered_in_dispatch_table(self):
        self.assertIn("audit_payload_credential_scan", dq_runner.CHECKERS)


if __name__ == "__main__":
    unittest.main()
