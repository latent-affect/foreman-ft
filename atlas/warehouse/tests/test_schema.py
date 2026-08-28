"""GOALS.json C1, C2, C8. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.warehouse.tests.test_schema -v
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import ddl, migrate

EXPECTED_TABLES = {
    "audit_event", "cwd_project", "cwd_project_candidate", "dim_handler", "dim_project",
    "dim_session", "dq_check", "dq_check_run", "dq_known_metric_bug", "git_commit",
    "git_commit_file", "git_commit_ticket", "git_ticket_candidate_dropped", "hook_verdict",
    "ingest_run", "ingest_source", "ingest_stream_history", "project_root", "schema_migration",
    "snapshot_source", "tessera_event",
}
EXPECTED_VIEWS = {
    "v_atlas_status", "v_decision_outcome_rate", "v_deny_streak", "v_fail_open_incident",
    "v_gate_proven_live", "v_handler_denominator", "v_hook_latency_rollup", "v_hook_verdict",
    "v_pipeline_selfcheck", "v_project_resolution_coverage", "v_project_scope",
    "v_queryable_source", "v_snapshot_publishable", "v_source_freshness", "v_source_trust",
    "v_ticket_diff_binding", "v_trapped_agent_candidate", "v_verdict_confusion_matrix",
}

EXPECTED_CHECKS = {
    "verdict_domain_closed": ("hook_verdict", "source", "contract", "invariant"),
    "stolen_implies_target": ("hook_verdict", "source", "contract", "invariant"),
    "decision_domain_closed": ("hook_verdict", "source", "contract", "invariant"),
    "watermark_le_filesize": ("hook_verdict", "source", "contract", "invariant"),
    "fail_open_not_double_counted": ("hook_verdict", "source", "contract", "invariant"),
    "project_resolution_floor": ("hook_verdict", "source", "advisory", "calibrated"),
    "handler_denominator_nonzero": ("hook_verdict", "source", "advisory", "invariant"),
    "audit_envelope_wellformed": ("audit_event", "source", "contract", "invariant"),
    "audit_payload_size_bounded": ("audit_event", "source", "contract", "lean"),
    "audit_payload_credential_scan": ("audit_event", "source", "advisory", "invariant"),
    "audit_ledger_partition_by_cwd": ("audit_event", "source", "advisory", "invariant"),
    "tessera_event_id_unique": ("tessera_event", "source", "contract", "invariant"),
    "git_ticket_prefix_registered": ("git_commit", "source", "contract", "invariant"),
    "ingest_rows_match_bytes": ("hook_verdict", "pipeline", "contract", "invariant"),
    "ingest_rows_monotonic": ("hook_verdict", "pipeline", "contract", "invariant"),
    "resolution_rate_delta": ("hook_verdict", "pipeline", "contract", "calibrated"),
    "handler_set_stable": ("hook_verdict", "pipeline", "advisory", "invariant"),
    "watermark_advanced": ("hook_verdict", "pipeline", "advisory", "invariant"),
    "snapshot_ticket_rollup_identity": ("tessera", "snapshot", "contract", "invariant"),
}


class SchemaMatchesArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.conn = migrate.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_ddl_extraction_is_non_empty_and_hashed(self):
        schema_sql, seed_sql = ddl.extract_schema_and_seed_sql()
        self.assertIn("CREATE TABLE hook_verdict", schema_sql)
        self.assertIn("CREATE VIEW v_hook_verdict", schema_sql)
        self.assertIn("INSERT INTO dq_check", seed_sql)
        digest = ddl.ddl_sha256()
        self.assertEqual(len(digest), 64)

    def test_exactly_21_tables_and_18_views(self):
        tables = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        views = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertEqual(tables, EXPECTED_TABLES, f"table set mismatch, got {len(tables)}")
        self.assertEqual(views, EXPECTED_VIEWS, f"view set mismatch, got {len(views)}")

    def test_dq_check_seed_matches_expected_19_rows(self):
        rows = self.conn.execute(
            "SELECT check_name, source_table, scope, severity, threshold_kind FROM dq_check"
        ).fetchall()
        self.assertEqual(len(rows), 19)
        actual = {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}
        self.assertEqual(actual, EXPECTED_CHECKS)

    def test_dq_known_metric_bug_rejects_orphan_insert(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO dq_known_metric_bug (bug_id, summary, check_name, deferral_id, "
                "implemented) VALUES ('TEST-1', 'a bug', NULL, NULL, 0)"
            )


if __name__ == "__main__":
    unittest.main()
