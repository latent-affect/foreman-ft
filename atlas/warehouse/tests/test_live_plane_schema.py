"""ATLASSN-27 / ATLASSN-29: migration 2's additivity and the live plane's gate.

Covers atlas/warehouse/GOALS.json C9-C11. The additivity criterion is checked against a byte copy
of the REAL live warehouse when one exists on this machine -- reasoning about whether a migration
disturbs 233,924 real rows is not the same as running it and counting them.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_live_plane_schema -v
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import ddl, migrate

REAL_WAREHOUSE = Path(__file__).resolve().parents[1] / "atlas.db"


def insertPullRun(conn, status="ok", finished_offset_seconds=0, credential_hits=0,
                  finished_at=None):
    """Inserts a real subagent_pull_run row. finished_offset_seconds is relative to now, so a
    staleness test drives the gate with a real timestamp rather than a frozen clock."""
    if finished_at is None:
        finished_at = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)",
            (f"{finished_offset_seconds} seconds",),
        ).fetchone()[0]
    conn.execute(
        "INSERT INTO subagent_pull_run (started_at, finished_at, status, credential_hits) "
        "VALUES (?, ?, ?, ?)",
        (finished_at, finished_at, status, credential_hits),
    )
    conn.commit()
    return finished_at


class MigrationTwoAdditivityTests(unittest.TestCase):
    """C9."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "test.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_connect_applies_both_migrations_and_records_them_separately(self):
        conn = migrate.connect(str(self.path))
        rows = dict(conn.execute("SELECT version, ddl_sha256 FROM schema_migration"))
        # ATLASSN-188: connect() now also applies migrations 23 and 24 (local-file-sourced,
        # migration 22/ATLASSN-181 stays unwired). ATLASSN-189 adds migration 25 (dq_check
        # advisory-severity guard trigger), and ATLASSN-197 adds migration 26
        # (session_credential_ack), so the recorded version set is 1..21 plus 23, 24, 25, 26 --
        # not the contiguous 1..21 run this asserted before ATLASSN-188.
        self.assertEqual(sorted(rows), list(range(1, 22)) + [23, 24, 25, 26])
        self.assertEqual(rows[1], ddl.ddl_sha256())
        self.assertEqual(rows[2], ddl.migration2_sha256())
        self.assertEqual(rows[3], ddl.migration3_sha256())
        self.assertEqual(rows[4], ddl.migration4_sha256())
        self.assertEqual(rows[5], ddl.migration5_sha256())
        self.assertEqual(rows[6], ddl.migration6_sha256())
        self.assertEqual(rows[8], ddl.migration8_sha256())
        self.assertEqual(rows[12], ddl.migration12_sha256())
        self.assertEqual(rows[13], ddl.migration13_sha256())
        self.assertEqual(rows[14], ddl.migration14_sha256())
        self.assertEqual(rows[15], ddl.migration15_sha256())
        self.assertEqual(rows[16], ddl.migration16_sha256())
        self.assertEqual(rows[17], ddl.migration17_sha256())
        self.assertEqual(rows[18], ddl.migration18_sha256())
        self.assertEqual(rows[19], ddl.migration19_sha256())
        self.assertEqual(rows[20], ddl.migration20_sha256())
        self.assertEqual(
            len(set(rows.values())), 25,
            "each migration must hash independently -- that separation is the entire reason "
            "the live plane is a second migration rather than an addition to section 16")
        conn.close()

    def test_applying_migration_two_twice_is_a_no_op(self):
        conn = migrate.connect(str(self.path))
        first = migrate.apply_migration2(conn)
        second = migrate.apply_migration2(conn)
        self.assertEqual(first, second)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM schema_migration WHERE version=2").fetchone()[0], 1)
        conn.close()

    def test_a_drifted_migration_two_raises_rather_than_proceeding(self):
        conn = migrate.connect(str(self.path))
        conn.execute("UPDATE schema_migration SET ddl_sha256='deadbeef' WHERE version=2")
        conn.commit()
        with self.assertRaises(migrate.MigrationError):
            migrate.apply_migration2(conn)
        conn.close()

    def test_migration_two_does_not_change_migration_ones_recorded_hash(self):
        conn = sqlite3.connect(str(self.path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate.apply(conn)
        before = conn.execute(
            "SELECT ddl_sha256 FROM schema_migration WHERE version=1").fetchone()[0]
        migrate.apply_migration2(conn)
        after = conn.execute(
            "SELECT ddl_sha256 FROM schema_migration WHERE version=1").fetchone()[0]
        self.assertEqual(before, after)
        conn.close()

    @unittest.skipUnless(REAL_WAREHOUSE.is_file(), "no real warehouse on this machine")
    def test_against_a_byte_copy_of_the_real_live_warehouse(self):
        """C9's real form. A migration that is additive on an empty database and destructive on a
        loaded one is not additive; the only way to know is to run it on the loaded one."""
        copy_path = Path(self.tmpdir.name) / "real-copy.db"
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(REAL_WAREHOUSE) + suffix)
            if src.exists():
                shutil.copy2(src, str(copy_path) + suffix)

        conn = sqlite3.connect(str(copy_path))
        conn.execute("PRAGMA foreign_keys = ON")
        before_hash = conn.execute(
            "SELECT ddl_sha256 FROM schema_migration WHERE version=1").fetchone()[0]
        before_verdicts = conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]
        before_status = conn.execute(
            "SELECT run_id, status, checks_evaluated, contract_failures FROM v_atlas_status"
        ).fetchone()
        before_runs = conn.execute("SELECT COUNT(*) FROM ingest_run").fetchone()[0]

        migrate.apply_migration2(conn)

        self.assertEqual(
            conn.execute("SELECT ddl_sha256 FROM schema_migration WHERE version=1").fetchone()[0],
            before_hash, "migration 1's recorded hash must survive migration 2 untouched")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0], before_verdicts,
            "migration 2 must not re-ingest or disturb a single verdict row")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM ingest_run").fetchone()[0], before_runs)
        self.assertEqual(
            conn.execute(
                "SELECT run_id, status, checks_evaluated, contract_failures FROM v_atlas_status"
            ).fetchone(),
            before_status, "the batch trust gate must read identically before and after")
        self.assertGreater(before_verdicts, 0, "this test is vacuous on an empty warehouse")
        conn.close()


class LiveGateTests(unittest.TestCase):
    """C10 -- every arm driven by a real row, including the one that never fires today."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def liveState(self):
        return self.conn.execute("SELECT live_state FROM v_subagent_live_status").fetchone()[0]

    def servedTrustState(self):
        row = self.conn.execute("SELECT trust_state FROM v_subagent_tool_call").fetchone()
        return None if row is None else row[0]

    def test_never_run(self):
        self.assertEqual(self.liveState(), "never-run")
        self.assertEqual(self.servedTrustState(), "LIVE-PLANE-NOT-SERVABLE-DO-NOT-USE")

    def test_fresh_ok_run_opens_the_gate(self):
        insertPullRun(self.conn, status="ok", finished_offset_seconds=0)
        self.assertEqual(self.liveState(), "live")

    def test_stale_run_past_the_bound_closes_it(self):
        insertPullRun(self.conn, status="ok", finished_offset_seconds=-301)
        self.assertEqual(self.liveState(), "live-pull-stale")
        self.assertEqual(self.servedTrustState(), "LIVE-PLANE-NOT-SERVABLE-DO-NOT-USE")

    def test_just_inside_the_bound_stays_open(self):
        insertPullRun(self.conn, status="ok", finished_offset_seconds=-299)
        self.assertEqual(self.liveState(), "live")

    def test_failed_run_closes_it(self):
        insertPullRun(self.conn, status="error", finished_offset_seconds=0)
        self.assertEqual(self.liveState(), "live-pull-failed")

    def test_credential_hit_closes_it(self):
        insertPullRun(self.conn, status="ok", finished_offset_seconds=0, credential_hits=1)
        self.assertEqual(self.liveState(), "live-credential-hit")

    def test_unparseable_timestamp_fails_CLOSED_not_open(self):
        """The gate's only fail-open path. julianday() returns NULL on an unparseable timestamp,
        NULL > 300.0 is not true, and without an explicit arm the CASE falls through to 'live' --
        serving arbitrarily stale rows as current, silently. Nothing in normal operation would
        surface this."""
        insertPullRun(self.conn, status="ok", finished_at="NOT-A-TIMESTAMP")
        self.assertEqual(self.liveState(), "live-pull-stale")
        self.assertEqual(self.servedTrustState(), "LIVE-PLANE-NOT-SERVABLE-DO-NOT-USE")

    def test_an_in_flight_pull_does_not_dark_the_view(self):
        """The gate keys off the last FINISHED run. Keying off the last run of any kind would
        put the live plane into its sentinel for the duration of every 60-second tick."""
        insertPullRun(self.conn, status="ok", finished_offset_seconds=0)
        self.conn.execute(
            "INSERT INTO subagent_pull_run (started_at, status) "
            "VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'running')")
        self.conn.commit()
        self.assertEqual(
            self.liveState(), "live",
            "an in-flight pull must not close the gate the previous finished pull opened")


class ViewShapeTests(unittest.TestCase):
    """C11 -- the fan-out the deny side is written to avoid."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        insertPullRun(self.conn, status="ok", finished_offset_seconds=0)
        self.conn.execute(
            "INSERT INTO subagent_transcript (source_path, stream_id, project_dir, "
            "parent_session_id, agent_id, agent_kind, meta_state, first_seen_at, updated_at) "
            "VALUES ('/p/agent-a.jsonl', 'sid', '-proj', 'sess', 'a', 'agent', 'absent', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
        self.conn.execute(
            "INSERT INTO subagent_tool_call (transcript_id, byte_offset, block_index, "
            "pull_run_id, ts, tool_use_id, tool_name, tool_input_json, tool_input_bytes) "
            "VALUES (1, 0, 0, 1, '2026-01-01T00:00:00Z', 'toolu_X', 'Bash', "
            "'{\"command\":\"ls\"}', 17)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def insertDeny(self, stream_id, offset, handler, rule):
        self.conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, verdict, decision, rule_id, tool_use_id) "
            "VALUES (?, ?, ?, '2026-01-01T00:00:00Z', 1000, 'microsecond', ?, 'fire', 'deny', "
            "?, 'toolu_X')",
            (stream_id, offset, self.run_id, handler, rule),
        )
        self.conn.commit()

    def test_two_handlers_denying_one_call_still_yields_exactly_one_row(self):
        self.insertDeny("s1", 0, "architecture_gate.py", "FOREMAN-ARCH-GATE:no-review")
        self.insertDeny("s1", 100, "goals_freeze_gate.py", "FOREMAN-GOALS-FREEZE-GATE:not-frozen")
        rows = self.conn.execute(
            "SELECT tool_use_id, hook_deny_count, hook_deny_rules FROM v_subagent_tool_call"
        ).fetchall()
        self.assertEqual(len(rows), 1, "a LEFT JOIN to hook_verdict would have made this two rows")
        self.assertEqual(rows[0][1], 2)
        self.assertIn("FOREMAN-ARCH-GATE:no-review", rows[0][2])
        self.assertIn("FOREMAN-GOALS-FREEZE-GATE:not-frozen", rows[0][2])

    def test_activity_rollup_totals_match_the_call_view(self):
        self.insertDeny("s1", 0, "architecture_gate.py", "R1")
        calls = self.conn.execute("SELECT COUNT(*) FROM v_subagent_tool_call").fetchone()[0]
        rolled = self.conn.execute(
            "SELECT SUM(tool_calls) FROM v_subagent_activity").fetchone()[0]
        self.assertEqual(calls, rolled)
        bash, denied = self.conn.execute(
            "SELECT bash_calls, hook_denied_calls FROM v_subagent_activity").fetchone()
        self.assertEqual((bash, denied), (1, 1))


class PipCoverageAndHandlerFreshnessTests(unittest.TestCase):
    """ATLASSN-36/38, migration 6."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        insertPullRun(self.conn, status="ok", finished_offset_seconds=0)
        self.conn.execute(
            "INSERT INTO subagent_transcript (source_path, stream_id, project_dir, "
            "parent_session_id, agent_id, agent_kind, meta_state, first_seen_at, updated_at) "
            "VALUES ('/p/agent-a.jsonl', 'sid', '-proj', 'sess1', 'a', 'agent', 'absent', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def insertCall(self, transcript_id, block_index, ts, tool_use_id, tool_name, input_json):
        self.conn.execute(
            "INSERT INTO subagent_tool_call (transcript_id, byte_offset, block_index, "
            "pull_run_id, ts, tool_use_id, tool_name, tool_input_json, tool_input_bytes) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, ?, 1)",
            (transcript_id, block_index, block_index, ts, tool_use_id, tool_name, input_json),
        )
        self.conn.commit()

    def insertHookFire(self, tool_use_id, handler_id, verdict="fire"):
        """decision is NULL, matching real guard_untrusted_web.py rows -- it is a PostToolUse
        wrap that fences content rather than allowing/denying/deferring/asking, so it never
        populates the decision column at all (verified against live data: decision is NULL,
        not empty string, for every one of its 724 real rows)."""
        stream_id = f"s-{tool_use_id}"
        self.conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, verdict, decision, rule_id, tool_use_id) "
            "VALUES (?, 0, ?, '2026-01-01T00:00:00Z', 1000, 'microsecond', ?, ?, NULL, '', ?)",
            (stream_id, self.run_id, handler_id, verdict, tool_use_id),
        )
        self.conn.commit()

    def test_pip_coverage_true_when_wrap_fires_and_no_skill_ran(self):
        self.insertCall(1, 0, "2026-01-01T00:00:00Z", "toolu_WEB1", "WebSearch",
                         '{"query":"x"}')
        self.insertHookFire("toolu_WEB1", "guard_untrusted_web.py")
        rows = self.conn.execute(
            "SELECT hook_wrap_fired, skill_ran_same_transcript FROM v_pip_coverage "
            "WHERE tool_use_id='toolu_WEB1'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], (1, 0))

    def test_pip_coverage_true_when_skill_ran_in_same_transcript(self):
        self.insertCall(1, 0, "2026-01-01T00:00:00Z", "toolu_WEB2", "WebFetch",
                         '{"url":"http://x"}')
        self.insertHookFire("toolu_WEB2", "guard_untrusted_web.py")
        self.insertCall(1, 1, "2026-01-01T00:00:01Z", "toolu_SK1", "Skill",
                         '{"skill":"prompt-injection-protection"}')
        rows = self.conn.execute(
            "SELECT hook_wrap_fired, skill_ran_same_transcript FROM v_pip_coverage "
            "WHERE tool_use_id='toolu_WEB2'"
        ).fetchall()
        self.assertEqual(rows[0], (1, 1))

    def test_pip_coverage_excludes_non_web_tools(self):
        self.insertCall(1, 0, "2026-01-01T00:00:00Z", "toolu_BASH1", "Bash", '{"command":"ls"}')
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM v_pip_coverage WHERE tool_use_id='toolu_BASH1'"
        ).fetchone()
        self.assertEqual(rows[0], 0)

    def test_pip_coverage_dark_when_live_plane_not_live(self):
        self.conn.execute("DELETE FROM subagent_pull_run")
        self.conn.commit()
        rows = self.conn.execute("SELECT trust_state FROM v_pip_coverage").fetchall()
        self.assertEqual(rows, [("LIVE-PLANE-NOT-SERVABLE-DO-NOT-USE",)])

    def test_handler_freshness_first_last_and_count(self):
        for i, ts in enumerate(["2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z",
                                 "2026-01-02T00:00:00Z"]):
            self.conn.execute(
                "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, "
                "epoch_ms, ts_resolution, handler_id, verdict, decision, rule_id, tool_use_id) "
                "VALUES ('sX', ?, ?, ?, 1000, 'microsecond', 'concept_gate.py', 'fire', "
                "'deny', 'R', ?)",
                (i, self.run_id, ts, f"toolu_C{i}"),
            )
        # ATLASSN-51: this fixture records an ok ingest_run but no dq_check_run rows, so the
        # trust gate never opens and hook_verdict is not in v_queryable_source. Migration 6's
        # v_handler_freshness read the raw table and so did not care; the fixed view correctly
        # withholds and returns only its sentinel. Establishing real trust here is the honest
        # fix -- the alternative, asserting against the sentinel, would be testing the fixture's
        # accident rather than the view's behaviour.
        for (name,) in self.conn.execute("SELECT check_name FROM dq_check").fetchall():
            self.conn.execute(
                "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
                "VALUES (?, ?, '2026-01-01T00:00:00Z', 1, 'synthetic-pass')",
                (self.run_id, name))
        self.conn.commit()
        # The view now carries trust_state and a sentinel row, so this filters to the trusted
        # branch. The reported values themselves are unchanged, which is the point -- see
        # test_handler_freshness_sentinel.test_trusted_state_matches_the_raw_aggregate, which
        # asserts that equivalence directly against the raw aggregate.
        first, last, count = self.conn.execute(
            "SELECT first_seen, last_seen, row_count FROM v_handler_freshness "
            "WHERE trust_state='ok' AND handler_id='concept_gate.py'"
        ).fetchone()
        self.assertEqual(first, "2026-01-01T00:00:00Z")
        self.assertEqual(last, "2026-01-03T00:00:00Z")
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
