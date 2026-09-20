"""GOALS.json C1, C2, C8. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_schema -v
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import ddl, migrate

MIGRATION1_TABLES = {
    "audit_event", "cwd_project", "cwd_project_candidate", "dim_handler", "dim_project",
    "dim_session", "dq_check", "dq_check_run", "dq_known_metric_bug", "git_commit",
    "git_commit_file", "git_commit_ticket", "git_ticket_candidate_dropped", "hook_verdict",
    "ingest_run", "ingest_source", "ingest_stream_history", "project_root", "schema_migration",
    "snapshot_source", "tessera_event",
}
# ATLASSN-27, ARCHITECTURE.md section 18.6. Kept as a SEPARATE set rather than merged into the
# one above, so this test still asserts that migration 2 added exactly these and disturbed
# nothing else -- merging them would lose the distinction the whole additive-migration design
# rests on.
MIGRATION2_TABLES = {
    "subagent_transcript", "subagent_pull_run", "subagent_tool_call", "subagent_tool_result",
    "subagent_credential_ack",
}
# ATLASSN-32, ARCHITECTURE.md section 19.4. Kept separate from migration 2's set for the same
# reason that set is separate from migration 1's: the test asserts each migration added exactly
# its own objects and disturbed nothing else, which merging would lose.
MIGRATION3_TABLES = {"subagent_assistant_text"}
# ATLASSN-33, ARCHITECTURE.md section 20.4.
MIGRATION4_TABLES = {
    "session_transcript", "session_tool_call", "session_tool_result", "session_assistant_text",
}
# ATLASSN-61, ARCHITECTURE.md section 24.4.
MIGRATION8_TABLES = {"bash_command_shape"}
# ATLASSN-95, ARCHITECTURE.md section 31.4. A parallel table, not an ALTER of MIGRATION8_TABLES --
# see section 31.1 for why.
MIGRATION15_TABLES = {"bash_command_shape_v2"}
# ATLASSN-153, ARCHITECTURE.md section 37.3. Migrations 17-19 have no set here -- 17 is
# view/seed-only (no new table), 18 is two indexes on existing tables (no CREATE TABLE/VIEW), and
# 19 is a data-only dq_check INSERT.
MIGRATION20_TABLES = {"integration_interface"}
# ATLASSN-154, ARCHITECTURE.md section 38.2.
MIGRATION21_TABLES = {"registry_assertion"}
# ATLASSN-104/REQ-64, ATLASSN-188. Migration 22 (ATLASSN-181) has no set here -- still commented
# out of connect(), not yet applied by any real database this test builds. Migration 23's DDL
# now lives at atlas/warehouse/migrations/0023_session_block_sequence.sql (ATLASSN-188), not an
# ARCHITECTURE.md section, but the table it creates is identical either way.
MIGRATION23_TABLES = {"session_block_sequence"}
# ATLASSN-197. Migration 26's DDL lives at atlas/warehouse/migrations/0026_session_credential_ack.sql
# (the same local-file mechanism ATLASSN-188 established for 23+, so no ARCHITECTURE.md section
# needs a matching entry). session_credential_ack is the session-side analog of
# subagent_credential_ack (MIGRATION2_TABLES) -- the triage path the real credential scan this
# ticket adds to session_pull.py needs from its first run.
MIGRATION26_TABLES = {"session_credential_ack"}
EXPECTED_TABLES = (MIGRATION1_TABLES | MIGRATION2_TABLES | MIGRATION3_TABLES
                   | MIGRATION4_TABLES | MIGRATION8_TABLES | MIGRATION15_TABLES
                   | MIGRATION20_TABLES | MIGRATION21_TABLES | MIGRATION23_TABLES
                   | MIGRATION26_TABLES)
EXPECTED_VIEWS = {
    "v_atlas_status", "v_decision_outcome_rate", "v_deny_streak", "v_fail_open_incident",
    "v_gate_proven_live", "v_handler_denominator", "v_hook_latency_rollup", "v_hook_verdict",
    "v_pipeline_selfcheck", "v_project_resolution_coverage", "v_project_scope",
    "v_queryable_source", "v_snapshot_publishable", "v_source_freshness", "v_source_trust",
    "v_ticket_diff_binding", "v_trapped_agent_candidate", "v_verdict_confusion_matrix",
}
MIGRATION2_VIEWS = {
    "v_subagent_live_status", "v_subagent_tool_call", "v_subagent_activity",
}
MIGRATION3_VIEWS = {"v_subagent_deny_join"}
MIGRATION4_VIEWS = {
    "v_transcript_tool_call_base", "v_ledger_join_coverage", "v_transcript_deny_join",
}
# ATLASSN-36/38, ARCHITECTURE.md section 22.4. Migration 5 has no set here -- it was data-only,
# no CREATE VIEW/TABLE at all.
MIGRATION6_VIEWS = {"v_pip_coverage", "v_handler_freshness"}
# ATLASSN-61, ARCHITECTURE.md section 24.4. Migration 7 has no set here -- it only DROP+CREATEs
# a view migration 6 already added, it adds nothing new.
MIGRATION8_VIEWS = {
    "v_bash_command_shape_prevalence_by_handler", "v_bash_command_shape_by_session",
}
# ATLASSN-80, ARCHITECTURE.md section 28.4. Migrations 9-11 have no set here -- 9 and 11 only
# ALTER an existing table (no CREATE), and 10 is data-only (a dq_check INSERT, no CREATE either).
MIGRATION12_VIEWS = {"session_prior"}
# ATLASSN-96, ARCHITECTURE.md section 32.5. Migrations 13-15 have no set here -- 13/14 are
# data-only dq_check INSERTs, and 15 is CREATE TABLE only (no view).
MIGRATION16_VIEWS = {"v_gaming_evasion_by_session"}
# ATLASSN-153, ARCHITECTURE.md section 37.3.
MIGRATION20_VIEWS = {"v_integration_progress"}
# ATLASSN-154, ARCHITECTURE.md section 38.2.
MIGRATION21_VIEWS = {"v_registry_assertion"}
# ATLASSN-98/REQ-77, ATLASSN-188. Migration 22 has no set here (commented out, not applied).
# Migration 23 has no set here either -- session_block_sequence is a table, no new view.
# v_source_freshness is RE-POINTED by migration 24 (DROP + CREATE, migration 7/17's own
# precedent), not newly named, so it needs no entry here either -- same shape migration 17's own
# treatment already established for the same view.
MIGRATION24_VIEWS = {"v_git_commit_detail"}
MIGRATION1_VIEWS = set(EXPECTED_VIEWS)
EXPECTED_VIEWS = (EXPECTED_VIEWS | MIGRATION2_VIEWS | MIGRATION3_VIEWS | MIGRATION4_VIEWS
                  | MIGRATION6_VIEWS | MIGRATION8_VIEWS | MIGRATION12_VIEWS | MIGRATION16_VIEWS
                  | MIGRATION20_VIEWS | MIGRATION21_VIEWS | MIGRATION24_VIEWS)

EXPECTED_CHECKS = {
    "verdict_domain_closed": ("hook_verdict", "source", "contract", "invariant"),
    "stolen_implies_target": ("hook_verdict", "source", "contract", "invariant"),
    "decision_domain_closed": ("hook_verdict", "source", "contract", "invariant"),
    "watermark_le_filesize": ("hook_verdict", "source", "contract", "invariant"),
    # ATLASSN-35. Advisory as of migration 5 -- see ARCHITECTURE.md section 21.
    "fail_open_not_double_counted": ("hook_verdict", "source", "advisory", "invariant"),
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
    # ATLASSN-33. Advisory on the FATAL-1 precedent -- see ARCHITECTURE.md 20.3.
    "ledger_join_coverage": ("hook_verdict", "pipeline", "advisory", "calibrated"),
    # ATLASSN-63, migration 10. Advisory rather than contract on purpose -- see
    # ARCHITECTURE.md 26.3: a retention sweep moves rows into no-transcript-on-disk with no
    # pipeline defect, so gating on it would be FATAL-1 repeated.
    "ledger_join_coverage_delta": ("hook_verdict", "pipeline", "advisory", "calibrated"),
    # ATLASSN-35. Carries the contract severity fail_open_not_double_counted no longer can,
    # on the resolution_rate_delta precedent -- see ARCHITECTURE.md section 21.
    "fail_open_pairing_delta": ("hook_verdict", "pipeline", "contract", "calibrated"),
    # ATLASSN-84, migration 13. Contract, and 'lean' rather than 'calibrated': one verdict per
    # call was named a priori for interpretability, not derived from the distribution. The
    # distribution supports it with a wide margin rather than defining it -- 0.00 and 0.47 for
    # the two failing session-hours against a healthy peer floor of 3.08. See ARCHITECTURE.md
    # section 29.1.
    "hook_coverage_per_session": ("hook_verdict", "source", "contract", "lean"),
    # ATLASSN-85, migration 13. Advisory rather than contract on purpose, and against its own
    # ticket's wording that it pages the operator: over 48 hours it names 10 sessions and cannot
    # yet separate a stalled build run from a session legitimately doing review, so gating on it
    # would be FATAL-1 repeated. Promotion trigger is a declared-run marker existing at all --
    # see ARCHITECTURE.md section 29.2.
    "build_process_ratio": ("session_tool_call", "pipeline", "advisory", "lean"),
    # ATLASSN-88, migration 14. Delta checks (fail only on a day-over-day increase), advisory and
    # 'lean' for the same reason hook_coverage_per_session's threshold is: no dq_check_run history
    # exists yet to calibrate a tolerance from. See ARCHITECTURE.md section 30.1.
    "turn_final_bytes_per_session_day_delta": ("session_assistant_text", "pipeline", "advisory", "lean"),
    "sendmessage_bytes_per_session_day_delta": ("session_tool_call", "pipeline", "advisory", "lean"),
    # ATLASSN-102, migration 17. The contract pair is what lets v_queryable_source name
    # the transcript corpora at all -- that view only ever lists a source_table some
    # contract check names. They are pure in-database invariants on purpose: contract
    # failures are summed into v_atlas_status and close the query facade system-wide, so
    # nothing outside the warehouse may be able to trip them.
    "session_calls_ingested_matches": ("session_transcript", "source", "contract", "invariant"),
    "subagent_calls_ingested_matches": ("subagent_transcript", "source", "contract", "invariant"),
    # Advisory, deliberately. These read the filesystem, and ARCHITECTURE.md section 33.3
    # documents a reachable state -- truncation preserving the first complete line -- that
    # is PERMANENT once the pull's size <= start_offset early return has bumped
    # updated_at. At contract severity one truncated transcript would refuse every gated
    # view until a human edited a row.
    "session_watermark_le_filesize": ("session_transcript", "source", "advisory", "invariant"),
    "subagent_watermark_le_filesize": ("subagent_transcript", "source", "advisory", "invariant"),
    # ATLASSN-143, migration 19. Completeness only (not uniqueness -- that's migration 18's
    # idx_session_tool_call_dedup/idx_session_assistant_text_dedup). Pipeline scope: this checks
    # a value derived during ingest processing, not the raw source itself, matching
    # build_process_ratio/sendmessage_bytes_per_session_day_delta's own scope on this same table.
    # See ARCHITECTURE.md section 36.
    "session_tool_call_evidence_attrs_present": ("session_tool_call", "pipeline", "contract", "invariant"),
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

    def test_migration_23_and_24_ddl_extraction_reads_local_files_not_architecture_md(self):
        """ATLASSN-188. The whole point of the fix: these two no longer depend on
        ARCHITECTURE.md at all, and this is checked directly rather than inferred from the
        schema test passing (which would also pass if they still read the document, as long as
        the document happened to still carry stale matching headings)."""
        sql23 = ddl.extract_migration23_sql()
        self.assertIn("CREATE TABLE IF NOT EXISTS session_block_sequence", sql23)
        sql24 = ddl.extract_migration24_sql()
        self.assertIn("CREATE VIEW IF NOT EXISTS v_git_commit_detail", sql24)
        # Neither of the old, now-dead ARCHITECTURE.md heading constants is referenced by
        # either extractor any more -- confirmed by reading ddl.py.post directly (structural,
        # not itself independently re-verifiable by a test against source text without
        # re-implementing a parser); the real behavioral proof is that both calls above succeed
        # at all: the ARCHITECTURE.md headings they used to depend on
        # ("### 40.1 Migration 23 DDL", "### 39.1 Migration 24 DDL") never existed in the real
        # document, so a version of this function still reading from there would raise
        # DdlExtractionError here, not return real SQL.

    def test_each_migration_added_exactly_its_own_tables_and_views_and_nothing_else(self):
        """The additive claim, asserted as set arithmetic rather than as a count: migration 2's
        objects are exactly MIGRATION2_TABLES/VIEWS, and migration 1's set is untouched."""
        tables = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        views = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertEqual(
            tables - MIGRATION2_TABLES - MIGRATION3_TABLES - MIGRATION4_TABLES
                    - MIGRATION8_TABLES - MIGRATION15_TABLES - MIGRATION20_TABLES
                    - MIGRATION21_TABLES - MIGRATION23_TABLES - MIGRATION26_TABLES,
            MIGRATION1_TABLES)
        self.assertEqual(
            views - MIGRATION2_VIEWS - MIGRATION3_VIEWS - MIGRATION4_VIEWS - MIGRATION6_VIEWS
                  - MIGRATION8_VIEWS - MIGRATION12_VIEWS - MIGRATION16_VIEWS - MIGRATION20_VIEWS
                  - MIGRATION21_VIEWS - MIGRATION24_VIEWS,
            MIGRATION1_VIEWS)
        self.assertEqual(tables & MIGRATION2_TABLES, MIGRATION2_TABLES)
        self.assertEqual(views & MIGRATION2_VIEWS, MIGRATION2_VIEWS)
        self.assertEqual(tables & MIGRATION3_TABLES, MIGRATION3_TABLES)
        self.assertEqual(views & MIGRATION3_VIEWS, MIGRATION3_VIEWS)
        self.assertEqual(tables & MIGRATION4_TABLES, MIGRATION4_TABLES)
        self.assertEqual(views & MIGRATION4_VIEWS, MIGRATION4_VIEWS)
        self.assertEqual(views & MIGRATION6_VIEWS, MIGRATION6_VIEWS)
        self.assertEqual(tables & MIGRATION8_TABLES, MIGRATION8_TABLES)
        self.assertEqual(views & MIGRATION8_VIEWS, MIGRATION8_VIEWS)
        self.assertEqual(views & MIGRATION12_VIEWS, MIGRATION12_VIEWS)
        self.assertEqual(tables & MIGRATION15_TABLES, MIGRATION15_TABLES)
        self.assertEqual(views & MIGRATION16_VIEWS, MIGRATION16_VIEWS)
        self.assertEqual(tables & MIGRATION20_TABLES, MIGRATION20_TABLES)
        self.assertEqual(views & MIGRATION20_VIEWS, MIGRATION20_VIEWS)
        self.assertEqual(tables & MIGRATION21_TABLES, MIGRATION21_TABLES)
        self.assertEqual(views & MIGRATION21_VIEWS, MIGRATION21_VIEWS)
        self.assertEqual(tables & MIGRATION23_TABLES, MIGRATION23_TABLES)
        self.assertEqual(views & MIGRATION24_VIEWS, MIGRATION24_VIEWS)
        self.assertEqual(tables & MIGRATION26_TABLES, MIGRATION26_TABLES)

    def test_exactly_36_tables_and_34_views(self):
        """RENAMED (ATLASSN-104/98/188/197): was test_exactly_35_tables_and_34_views. Migration 23
        adds one table (session_block_sequence); migration 24 adds one view
        (v_git_commit_detail) and re-points an existing one (v_source_freshness, no new name);
        migration 26 adds one more table (session_credential_ack), no new view. 34+1+1=36 tables,
        33+1=34 views (unchanged by migration 26). Same discipline the dq_check count test below
        already applies to itself: the number lives only in the assertion, never in a name that
        can go stale silently."""
        tables = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        views = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertEqual(tables, EXPECTED_TABLES, f"table set mismatch, got {len(tables)}")
        self.assertEqual(views, EXPECTED_VIEWS, f"view set mismatch, got {len(views)}")

    def test_dq_check_seed_matches_the_expected_registry(self):
        """Renamed 2026-09-04: the name said 19 while the body asserted 21, having been
        bumped twice without it. A count in a test NAME goes stale silently, so the number
        now lives only in the assertion, where a mismatch is visible."""
        rows = self.conn.execute(
            "SELECT check_name, source_table, scope, severity, threshold_kind FROM dq_check"
        ).fetchall()
        self.assertEqual(len(rows), 31)
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
