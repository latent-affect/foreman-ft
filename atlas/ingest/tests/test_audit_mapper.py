"""Covers atlas/ingest/audit.py, added during integration testing when auditplane_to_ingest
was found to have no real row-mapper yet (only the generic stream.tail() machinery existed).
Not a new GOALS.json criterion -- exercises the same already-frozen, already-tested generic
tail() contract with a new mapper, the same relationship verdicts.py already has to it.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_audit_mapper -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import audit, stream
from atlas.warehouse import migrate


class AuditMapperTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_ingests_a_real_shaped_audit_line(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        line = json.dumps({
            "schema_version": "audit-1", "ts": "2026-01-01T00:00:00.000000Z",
            "event_type": "SAFETY_ASK", "severity": "medium", "session_id": None,
            "cwd": "/proj", "ledger": "global-safety",
        })
        fixture.write_text(line + "\n")

        import functools
        mapper = audit.make_row_mapper(str(fixture))
        insert = functools.partial(audit.insert_audit_event_row, ingest_run_id=self.run_id)
        result = stream.tail(self.conn, "audit", str(fixture), mapper, insert)

        self.assertEqual(result.rows_inserted, 1)
        row = self.conn.execute(
            "SELECT event_type, ledger_claim, payload_bytes FROM audit_event"
        ).fetchone()
        self.assertEqual(row[0], "SAFETY_ASK")
        self.assertEqual(row[1], "global-safety")
        self.assertEqual(row[2], len(line.encode("utf-8")))

    def test_missing_event_type_is_skipped(self):
        fixture = Path(self.tmpdir.name) / "safety.jsonl"
        fixture.write_text(json.dumps({"ts": "2026-01-01T00:00:00Z"}) + "\n")

        import functools
        mapper = audit.make_row_mapper(str(fixture))
        insert = functools.partial(audit.insert_audit_event_row, ingest_run_id=self.run_id)
        result = stream.tail(self.conn, "audit", str(fixture), mapper, insert)

        self.assertEqual(result.rows_inserted, 0)


if __name__ == "__main__":
    unittest.main()
