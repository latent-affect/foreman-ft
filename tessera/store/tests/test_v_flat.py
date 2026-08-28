import sqlite3
import unittest

from ..schema import init_schema

INGEST_COLUMNS = {
    "event_id", "event_type", "event_ts", "actor", "actor_kind", "ticket_id",
    "project_prefix", "ticket_type", "ticket_status", "ticket_is_closed",
    "ticket_priority", "ticket_severity", "ticket_has_frozen_criteria",
    "ticket_criteria_frozen_before_work", "ticket_criteria_count",
    "ticket_claim_count", "ticket_lead_time_hours", "comment_has_code_snippet",
    "status_from", "status_to",
}


class VFlatSchemaTests(unittest.TestCase):
    def test_init_schema_creates_v_flat_with_ingest_columns(self):
        conn = sqlite3.connect(":memory:")
        init_schema(conn)
        cols = {d[0] for d in conn.execute("SELECT * FROM v_flat LIMIT 0").description}
        self.assertTrue(INGEST_COLUMNS.issubset(cols), f"missing {INGEST_COLUMNS - cols}")
        rows = conn.execute("SELECT * FROM v_flat WHERE event_id > 0").fetchall()
        self.assertEqual(rows, [])
        conn.close()
