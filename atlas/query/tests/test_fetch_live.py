"""ATLASSN-27 / ATLASSN-29: the live read path and its separation from the batch one.

Covers atlas/query/GOALS.json C6-C7. The load-bearing assertion here is a negative one: adding a
second gate must not have weakened the first. The batch allowlist is pinned by literal name, so a
later change that quietly folds a live view into it fails this file rather than passing silently.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.query.tests.test_fetch_live -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.query import facade
from atlas.warehouse import migrate

# Pinned literally, not derived from the module under test -- deriving it would make this
# assertion tautological, which is the whole failure mode it exists to catch.
BATCH_VIEWS_BEFORE_ATLASSN27 = frozenset({
    "v_atlas_status", "v_decision_outcome_rate", "v_deny_streak", "v_fail_open_incident",
    "v_gate_proven_live", "v_handler_denominator", "v_hook_latency_rollup", "v_hook_verdict",
    "v_pipeline_selfcheck", "v_project_resolution_coverage", "v_project_scope",
    "v_queryable_source", "v_snapshot_publishable", "v_source_freshness", "v_source_trust",
    "v_ticket_diff_binding", "v_trapped_agent_candidate", "v_verdict_confusion_matrix",
})


def seedCall(conn, tool_use_id="toolu_X", tool_name="Bash"):
    conn.execute(
        "INSERT INTO subagent_transcript (source_path, stream_id, project_dir, "
        "parent_session_id, agent_id, agent_kind, agent_type, parent_tool_use_id, meta_state, "
        "first_seen_at, updated_at) VALUES (?, 'sid', '-proj', 'sess-1', 'a1', 'agent', 'muse', "
        "'toolu_PARENT', 'present', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (f"/p/agent-{tool_use_id}.jsonl",),
    )
    transcript_id = conn.execute(
        "SELECT transcript_id FROM subagent_transcript ORDER BY transcript_id DESC LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO subagent_tool_call (transcript_id, byte_offset, block_index, pull_run_id, "
        "ts, tool_use_id, tool_name, tool_input_json, tool_input_bytes) "
        "VALUES (?, 0, 0, 1, '2026-01-01T00:00:00Z', ?, ?, '{\"command\":\"ls\"}', 17)",
        (transcript_id, tool_use_id, tool_name),
    )
    conn.commit()


def openPullRun(conn, status="ok", offset_seconds=0, credential_hits=0):
    finished = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now', ?)", (f"{offset_seconds} seconds",)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO subagent_pull_run (started_at, finished_at, status, credential_hits) "
        "VALUES (?, ?, ?, ?)", (finished, finished, status, credential_hits))
    conn.commit()


class AllowlistSeparationTests(unittest.TestCase):
    """C6."""

    def test_the_batch_allowlist_is_unchanged_by_this_ticket(self):
        # A subset check, not equality: ATLASSN-27 itself added/removed/renamed nothing here
        # (that's what this test still asserts), but a later, separate ticket legitimately can --
        # ATLASSN-38 added v_handler_freshness. Equality would make this test fail on every
        # future legitimate batch-view addition regardless of which ticket made it.
        self.assertLessEqual(
            BATCH_VIEWS_BEFORE_ATLASSN27, facade.ALLOWED_VIEWS,
            "ATLASSN-27 must not add, remove or rename anything on the batch gate's allowlist")

    def test_the_two_allowlists_do_not_overlap(self):
        self.assertEqual(facade.ALLOWED_VIEWS & facade.ALLOWED_LIVE_VIEWS, frozenset())


class LiveFetchTests(unittest.TestCase):
    """C6, C7."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "test.db"
        self.conn = migrate.connect(str(self.path))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def openFacade(self):
        return facade.QueryFacade(str(self.path))

    def test_live_serves_while_the_batch_gate_refuses_every_non_plumbing_view(self):
        """The point of the whole design. The warehouse here has no clean ingest run at all --
        exactly the state the real warehouse was in when this was written -- so fetch() refuses
        every one of its views except v_atlas_status (19 as of ATLASSN-36/38's
        v_handler_freshness, minus that one A3 exemption -- see the dedicated test below).
        fetch_live() must still answer.

        FIX 2026-09-20 (ATLASSN-103, ticket comment on ATLASSN-103 right after round 3): this
        loop used to iterate ALL of facade.ALLOWED_VIEWS including v_atlas_status. ARCHITECTURE.md
        section 8 addendum amendment A3 (already frozen, already in facade.py's fetch()) exempts
        v_atlas_status from the run-level refusal specifically so it stays servable in every
        run-level state -- refusing it here was this test lagging behind already-decided A3
        behavior, not a bug in ATLASSN-103's fix (confirmed via git stash: this assertion passes
        on the pre-ATLASSN-103 baseline and only fails with the proposal applied, because A3 is
        the new behavior). Excluded here rather than silently left failing."""
        openPullRun(self.conn)
        seedCall(self.conn)
        f = self.openFacade()
        try:
            self.assertFalse(f.status().is_clean())
            for view in sorted(facade.ALLOWED_VIEWS - {"v_atlas_status"}):
                with self.assertRaises(facade.QueryRefused):
                    f.fetch(view)
            columns, rows = f.fetch_live("v_subagent_tool_call")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][columns.index("trust_state")], "live")
            self.assertEqual(rows[0][columns.index("tool_name")], "Bash")
            self.assertEqual(rows[0][columns.index("parent_tool_use_id")], "toolu_PARENT")
        finally:
            f.close()

    def test_v_atlas_status_stays_servable_even_while_the_batch_gate_refuses_everything_else(self):
        """ATLASSN-103, ARCHITECTURE.md section 8 addendum amendment A3: v_atlas_status is the
        one view whose entire job is to report state, so it is exempt from the run-level refusal
        that closes every other gated view here -- a router needing to learn WHY the facade is
        refused could never ask if this view refused too. This is the positive half of the
        exclusion made in the test just above: rather than just dropping v_atlas_status from that
        loop silently, it gets its own real assertion that it is, in fact, still servable."""
        openPullRun(self.conn)
        seedCall(self.conn)
        f = self.openFacade()
        try:
            self.assertFalse(f.status().is_clean())
            columns, rows = f.fetch("v_atlas_status")
            self.assertEqual(len(rows), 1, "v_atlas_status must always report exactly one row")
        finally:
            f.close()

    def test_neither_method_serves_the_others_views(self):
        openPullRun(self.conn)
        seedCall(self.conn)
        f = self.openFacade()
        try:
            with self.assertRaises(facade.QueryRefused) as ctx:
                f.fetch("v_subagent_tool_call")
            self.assertIn("declared gated views", str(ctx.exception))
            with self.assertRaises(facade.QueryRefused) as ctx:
                f.fetch_live("v_hook_verdict")
            self.assertIn("declared live views", str(ctx.exception))
        finally:
            f.close()

    def test_each_closed_live_state_is_named_in_the_refusal(self):
        cases = [
            ({}, "never-run"),
            ({"status": "error"}, "live-pull-failed"),
            ({"offset_seconds": -301}, "live-pull-stale"),
            ({"credential_hits": 1}, "live-credential-hit"),
        ]
        for kwargs, expected in cases:
            self.conn.execute("DELETE FROM subagent_pull_run")
            self.conn.commit()
            if kwargs or expected != "never-run":
                openPullRun(self.conn, **kwargs)
            f = self.openFacade()
            try:
                with self.assertRaises(facade.QueryRefused) as ctx:
                    f.fetch_live("v_subagent_tool_call")
                self.assertIn(expected, str(ctx.exception),
                              f"the refusal must name {expected!r} so a caller can tell 'the "
                              f"pull is dead' from 'a secret was found'")
            finally:
                f.close()

    def test_live_status_reports_the_state_and_staleness(self):
        openPullRun(self.conn, offset_seconds=-30)
        f = self.openFacade()
        try:
            state, detail = f.live_status()
            self.assertEqual(state, "live")
            self.assertGreaterEqual(detail["staleness_seconds"], 29)
            self.assertLessEqual(detail["staleness_seconds"], 31)
        finally:
            f.close()

    def test_a_warehouse_without_migration_two_reports_that_rather_than_raising(self):
        bare = Path(self.tmpdir.name) / "bare.db"
        import sqlite3
        conn = sqlite3.connect(str(bare))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate.apply(conn)
        conn.close()
        f = facade.QueryFacade(str(bare))
        try:
            state, _ = f.live_status()
            self.assertEqual(state, "live-plane-not-migrated")
            with self.assertRaises(facade.QueryRefused) as ctx:
                f.fetch_live("v_subagent_tool_call")
            self.assertIn("live-plane-not-migrated", str(ctx.exception))
        finally:
            f.close()

    def test_the_live_path_never_writes(self):
        openPullRun(self.conn)
        seedCall(self.conn)
        f = self.openFacade()
        try:
            f.fetch_live("v_subagent_activity")
            import sqlite3
            with self.assertRaises(sqlite3.OperationalError):
                f._conn.execute("DELETE FROM subagent_tool_call")
        finally:
            f.close()


if __name__ == "__main__":
    unittest.main()
