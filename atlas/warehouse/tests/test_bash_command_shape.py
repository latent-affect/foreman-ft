"""ATLASSN-61, ARCHITECTURE.md section 24. Migration 8 (bash_command_shape) end-to-end against
synthetic rows only -- QA-fork phase, per agent-remediation-5a's explicit REQ-55 decision not to
touch atlas-sonnet/master's live data from this ticket. Proves: the migration is additive and
idempotent, the backfill covers every synthetic Bash call exactly once and is itself idempotent
on re-run, the by-session view's aggregates match hand-computed expectations, the
by-handler view follows this schema's own hook_verdict trust-sentinel convention (collapses to
exactly one sentinel row when hook_verdict is distrusted, real per-handler rows when trusted),
and a malformed tool_input_json still gets exactly one row rather than silently vanishing from
the backfill's coverage.

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_bash_command_shape -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import backfill_bash_command_shape, migrate

SENTINEL = "SOURCE-DISTRUSTED-DO-NOT-USE"


def _pass_all_hook_verdict_checks(conn, run_id, exclude=()):
    names = [
        r[0] for r in conn.execute(
            "SELECT check_name FROM dq_check WHERE source_table='hook_verdict'"
        )
        if r[0] not in exclude
    ]
    for name in names:
        conn.execute(
            "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
            "VALUES (?, ?, '2026-01-01T00:00:00Z', 1, 'synthetic-pass')",
            (run_id, name),
        )
    conn.commit()


def _fail_one_check(conn, run_id, check_name):
    conn.execute(
        "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
        "VALUES (?, ?, '2026-01-01T00:00:01Z', 0, 'synthetic-fail')",
        (run_id, check_name),
    )
    conn.commit()


def _insert_pull_run(conn, finished_offset_seconds=0, status="ok"):
    """finished_at defaults to "just now", matching test_live_plane_schema.py's own
    insertPullRun helper -- v_subagent_live_status's live_state depends on a REAL elapsed-time
    comparison against the 300-second freshness window, so a fixed past timestamp (e.g.
    2026-01-01) would silently read as 'live-pull-stale' regardless of finished_offset_seconds."""
    finished_at = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)", (f"{finished_offset_seconds} seconds",)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO subagent_pull_run (started_at, finished_at, status, credential_hits) "
        "VALUES (?, ?, ?, 0)",
        (finished_at, finished_at, status),
    )
    conn.commit()


def _insert_session_transcript(conn, transcript_id, session_id):
    conn.execute(
        "INSERT INTO session_transcript (transcript_id, source_path, stream_id, project_dir, "
        "session_id, first_seen_at, updated_at) VALUES (?, ?, 'sid', '-proj', ?, "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (transcript_id, f"/p/session-{transcript_id}.jsonl", session_id),
    )
    conn.commit()


def _insert_subagent_transcript(conn, transcript_id, parent_session_id):
    conn.execute(
        "INSERT INTO subagent_transcript (transcript_id, source_path, stream_id, project_dir, "
        "parent_session_id, agent_id, agent_kind, meta_state, first_seen_at, updated_at) "
        "VALUES (?, ?, 'sid', '-proj', ?, 'a', 'agent', 'absent', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (transcript_id, f"/p/agent-{transcript_id}.jsonl", parent_session_id),
    )
    conn.commit()


def _insert_session_bash_call(conn, transcript_id, byte_offset, tool_use_id, command_json,
                                session_id, ts="2026-01-01T00:00:00Z"):
    conn.execute(
        "INSERT INTO session_tool_call (transcript_id, byte_offset, block_index, pull_run_id, "
        "ts, session_id, tool_use_id, tool_name, tool_input_json, tool_input_bytes) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, 'Bash', ?, 1)",
        (transcript_id, byte_offset, byte_offset, ts, session_id, tool_use_id, command_json),
    )
    conn.commit()


def _insert_subagent_bash_call(conn, transcript_id, byte_offset, tool_use_id, command_json,
                                 session_id, ts="2026-01-01T00:00:00Z"):
    conn.execute(
        "INSERT INTO subagent_tool_call (transcript_id, byte_offset, block_index, pull_run_id, "
        "ts, session_id, tool_use_id, tool_name, tool_input_json, tool_input_bytes) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, 'Bash', ?, 1)",
        (transcript_id, byte_offset, byte_offset, ts, session_id, tool_use_id, command_json),
    )
    conn.commit()


class BackfillCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        _insert_pull_run(self.conn)
        _insert_session_transcript(self.conn, 1, "sessA")
        _insert_subagent_transcript(self.conn, 1, "sessA")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_migration8_objects_exist(self):
        names = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name LIKE 'bash_command_shape' OR name LIKE 'v_bash_command_shape%'"
            )
        }
        self.assertEqual(
            names,
            {"bash_command_shape", "v_bash_command_shape_prevalence_by_handler",
             "v_bash_command_shape_by_session"},
        )

    def test_full_backfill_covers_every_bash_call_exactly_once(self):
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "git status"}), "sessA")
        _insert_session_bash_call(self.conn, 1, 1, "tu2", json.dumps({"command": "eval \"$X\""}), "sessA")
        _insert_subagent_bash_call(self.conn, 1, 0, "tu3", json.dumps({"command": "ls -la"}), "sessA")
        # A non-Bash call in the same tables must not be picked up.
        self.conn.execute(
            "INSERT INTO session_tool_call (transcript_id, byte_offset, block_index, "
            "pull_run_id, ts, session_id, tool_use_id, tool_name, tool_input_json, "
            "tool_input_bytes) VALUES (1, 2, 2, 1, '2026-01-01T00:00:00Z', 'sessA', 'tu4', "
            "'Read', '{\"file_path\":\"/x\"}', 1)"
        )
        self.conn.commit()

        counts = backfill_bash_command_shape.run(self.conn)
        self.assertEqual(counts["session"], {"scanned": 2, "inserted": 2})
        self.assertEqual(counts["subagent"], {"scanned": 1, "inserted": 1})

        total = self.conn.execute("SELECT COUNT(*) FROM bash_command_shape").fetchone()[0]
        self.assertEqual(total, 3, "must be exactly one row per Bash call, no more, no fewer")

        eval_row = self.conn.execute(
            "SELECT has_eval_word FROM bash_command_shape WHERE tool_use_id='tu2'"
        ).fetchone()
        self.assertEqual(eval_row[0], 1)

    def test_rerun_is_idempotent_no_duplicate_rows(self):
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "git status"}), "sessA")
        first = backfill_bash_command_shape.run(self.conn)
        second = backfill_bash_command_shape.run(self.conn)
        self.assertEqual(first["session"]["inserted"], 1)
        self.assertEqual(second["session"]["inserted"], 0, "re-run must not insert duplicates")
        total = self.conn.execute("SELECT COUNT(*) FROM bash_command_shape").fetchone()[0]
        self.assertEqual(total, 1)

    def test_new_calls_after_a_prior_backfill_are_picked_up_incrementally(self):
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "git status"}), "sessA")
        backfill_bash_command_shape.run(self.conn)
        _insert_session_bash_call(self.conn, 1, 1, "tu2", json.dumps({"command": "ls"}), "sessA")
        counts = backfill_bash_command_shape.run(self.conn)
        self.assertEqual(counts["session"], {"scanned": 1, "inserted": 1})
        total = self.conn.execute("SELECT COUNT(*) FROM bash_command_shape").fetchone()[0]
        self.assertEqual(total, 2)

    def test_malformed_tool_input_json_still_gets_exactly_one_row(self):
        _insert_session_bash_call(self.conn, 1, 0, "tu1", "not valid json", "sessA")
        _insert_session_bash_call(self.conn, 1, 1, "tu2", json.dumps({"description": "no command key"}), "sessA")
        counts = backfill_bash_command_shape.run(self.conn)
        self.assertEqual(counts["session"], {"scanned": 2, "inserted": 2})
        rows = self.conn.execute(
            "SELECT tool_use_id, parse_failure, token_count FROM bash_command_shape ORDER BY tool_use_id"
        ).fetchall()
        self.assertEqual(rows, [("tu1", 1, None), ("tu2", 1, None)])


class BySessionViewTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        _insert_pull_run(self.conn)
        _insert_session_transcript(self.conn, 1, "sessA")
        _insert_session_transcript(self.conn, 2, "sessB")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_per_session_aggregates_match_hand_computed_expectations(self):
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "git status"}), "sessA")
        _insert_session_bash_call(self.conn, 1, 1, "tu2",
                                   json.dumps({"command": 'python3 -c "os.system(1)"'}), "sessA")
        _insert_session_bash_call(self.conn, 2, 0, "tu3", json.dumps({"command": "eval x"}), "sessB")
        backfill_bash_command_shape.run(self.conn)

        rows = {
            r[0]: r[1:] for r in self.conn.execute(
                "SELECT session_id, n_calls, n_eval_word, n_python_c_os_subprocess "
                "FROM v_bash_command_shape_by_session ORDER BY session_id"
            )
        }
        self.assertEqual(rows["sessA"], (2, 0, 1))
        self.assertEqual(rows["sessB"], (1, 1, 0))

    def test_all_rows_carry_ok_trust_state_when_live_plane_is_healthy(self):
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "git status"}), "sessA")
        backfill_bash_command_shape.run(self.conn)
        rows = self.conn.execute("SELECT trust_state FROM v_bash_command_shape_by_session").fetchall()
        self.assertEqual(rows, [("live",)])

    def test_collapses_to_exactly_one_sentinel_row_when_live_plane_is_stale(self):
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "git status"}), "sessA")
        backfill_bash_command_shape.run(self.conn)
        # A pull that finished 600 seconds ago is past the 300-second freshness window
        # (v_subagent_live_status), even though this row came from session_tool_call, not
        # subagent_tool_call -- the whole view is gated coarsely, per ARCHITECTURE.md section
        # 24.5's decision.
        # A later pull_run row (higher pull_run_id) with a stale finished_at becomes the MAX
        # v_subagent_live_status keys off -- session_tool_call's own pull_run_id FK means the
        # original row can't be deleted, so this makes the LATEST run stale instead.
        _insert_pull_run(self.conn, finished_offset_seconds=-600)
        rows = self.conn.execute("SELECT trust_state FROM v_bash_command_shape_by_session").fetchall()
        self.assertEqual(rows, [("LIVE-PLANE-NOT-SERVABLE-DO-NOT-USE",)])

    def test_ungated_view_would_have_leaked_a_real_looking_row_negative_control(self):
        """Proves the trust gate in section 24.5's view actually does something: rebuild the
        view WITHOUT the live-plane predicate (the shape this view had before agent-remediation-5a's
        decision) and confirm it serves a real, ungated row while the live plane is stale --
        the exact failure a consumer 'forgetting to check trust_state' would hit."""
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "git status"}), "sessA")
        backfill_bash_command_shape.run(self.conn)
        # A later pull_run row (higher pull_run_id) with a stale finished_at becomes the MAX
        # v_subagent_live_status keys off -- session_tool_call's own pull_run_id FK means the
        # original row can't be deleted, so this makes the LATEST run stale instead.
        _insert_pull_run(self.conn, finished_offset_seconds=-600)

        self.conn.executescript(
            "DROP VIEW v_bash_command_shape_by_session;\n"
            "CREATE VIEW v_bash_command_shape_by_session AS "
            "SELECT 'ok' AS trust_state, session_id, COUNT(*) AS n_calls "
            "FROM bash_command_shape WHERE session_id IS NOT NULL GROUP BY session_id;"
        )
        self.conn.commit()
        rows = self.conn.execute("SELECT trust_state, session_id FROM v_bash_command_shape_by_session").fetchall()
        self.assertEqual(
            rows, [("ok", "sessA")],
            "the ungated view leaked a real-looking row while the live plane was stale, "
            "confirming the gated view's sentinel test would catch this exact regression",
        )


class ByHandlerViewTrustGateTests(unittest.TestCase):
    """Follows this schema's own section-8 convention: a view built on hook_verdict must
    collapse to exactly one sentinel row when hook_verdict is distrusted, never zero, never
    real-looking -- proven the same way test_trust_gate.py proves it for the other eleven."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        _insert_pull_run(self.conn)
        _insert_session_transcript(self.conn, 1, "sessA")
        _insert_session_bash_call(self.conn, 1, 0, "tu1", json.dumps({"command": "eval x"}), "sessA")
        _insert_session_bash_call(self.conn, 1, 1, "tu2", json.dumps({"command": "git status"}), "sessA")
        backfill_bash_command_shape.run(self.conn)
        self.conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, verdict, tool_use_id) VALUES "
            "('s1', 0, ?, '2026-01-01T00:00:00Z', 1000, 'microsecond', 'guard_x.py', 'fire', 'tu1')",
            (self.run_id,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_trusted_state_shows_real_per_handler_row(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id)
        rows = self.conn.execute(
            "SELECT trust_state, handler_id, n_calls, n_eval_word "
            "FROM v_bash_command_shape_prevalence_by_handler"
        ).fetchall()
        self.assertEqual(len(rows), 1, "tu2 was never evaluated by any handler and must not appear")
        self.assertEqual(rows[0], ("ok", "guard_x.py", 1, 1))

    def test_distrusted_state_collapses_to_exactly_one_sentinel_row(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id, exclude=("resolution_rate_delta",))
        _fail_one_check(self.conn, self.run_id, "resolution_rate_delta")
        rows = self.conn.execute(
            "SELECT trust_state FROM v_bash_command_shape_prevalence_by_handler"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], SENTINEL)


if __name__ == "__main__":
    unittest.main()
