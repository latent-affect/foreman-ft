"""ATLASSN-33: main-session transcript ingest and join coverage.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_session_pull -v
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import session_pull, transcript_parse
from atlas.warehouse import dq_runner, migrate

REAL_WAREHOUSE = Path(__file__).resolve().parents[2] / "warehouse" / "atlas.db"


def writeSession(project_dir, session_id, records):
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def assistantRecord(ts, session_id, blocks, uuid="u1"):
    return {"type": "assistant", "timestamp": ts, "sessionId": session_id, "cwd": "/proj",
            "gitBranch": "main", "uuid": uuid, "message": {"content": blocks}}


def toolUse(tid, name, tool_input):
    return {"type": "tool_use", "id": tid, "name": name, "input": tool_input}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_finds_main_session_transcripts(self):
        p = writeSession(self.root / "-Users-m5-proj", "sess-1", [])
        self.assertIn(p, session_pull.discover_sessions(self.root))

    def test_does_not_reach_subagent_transcripts(self):
        """The two sources must not double-ingest. A subagent transcript is two levels deeper
        than this flat glob can reach, which is a structural guarantee rather than a filter."""
        sub = self.root / "-Users-m5-proj" / "sess-1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-aaa.jsonl").write_text("{}\n", encoding="utf-8")
        found = session_pull.discover_sessions(self.root)
        self.assertTrue(all("subagents" not in str(p) for p in found))

    def test_missing_root_is_empty_not_a_crash(self):
        self.assertEqual(session_pull.discover_sessions(self.root / "nope"), [])

    def test_path_identity(self):
        p = writeSession(self.root / "-Users-m5-proj", "sess-1", [])
        identity = session_pull.parse_session_path(p, self.root)
        self.assertEqual(identity, {"project_dir": "-Users-m5-proj", "session_id": "sess-1"})

    def test_a_subagent_path_is_rejected_by_identity_too(self):
        sub = self.root / "-Users-m5-proj" / "sess-1" / "subagents"
        sub.mkdir(parents=True)
        p = sub / "agent-aaa.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        self.assertIsNone(session_pull.parse_session_path(p, self.root))


class PullTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.projects = self.root / "projects"
        self.db = self.root / "atlas.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_a_real_pull_lands_calls_and_texts(self):
        writeSession(self.projects / "-Users-m5-proj", "sess-1", [
            assistantRecord("2026-01-01T00:00:00Z", "sess-1", [
                {"type": "text", "text": "about to run it"},
                toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ])
        summary = session_pull.run(self.db, self.projects)
        self.assertEqual(summary["calls_inserted"], 1)
        self.assertEqual(summary["texts_inserted"], 1)
        conn = migrate.connect(str(self.db))
        row = conn.execute("SELECT session_id, project_dir FROM session_transcript").fetchone()
        conn.close()
        self.assertEqual(row, ("sess-1", "-Users-m5-proj"))

    def test_second_consecutive_pull_inserts_nothing(self):
        writeSession(self.projects / "-Users-m5-proj", "sess-1", [
            assistantRecord("2026-01-01T00:00:00Z", "sess-1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ])
        session_pull.run(self.db, self.projects)
        self.assertEqual(session_pull.run(self.db, self.projects)["calls_inserted"], 0)

    def test_appended_bytes_read_from_the_watermark(self):
        p = writeSession(self.projects / "-Users-m5-proj", "sess-1", [
            assistantRecord("2026-01-01T00:00:00Z", "sess-1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ])
        session_pull.run(self.db, self.projects)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(assistantRecord(
                "2026-01-01T00:00:05Z", "sess-1",
                [toolUse("toolu_2", "Read", {"file_path": "/b"})], uuid="u2")) + "\n")
        self.assertEqual(session_pull.run(self.db, self.projects)["calls_inserted"], 1)

    def test_a_session_pull_does_not_move_v_atlas_status(self):
        """Same constraint the subagent pull carries: the live plane must never poison the batch
        trust gate."""
        writeSession(self.projects / "-Users-m5-proj", "sess-1", [
            assistantRecord("2026-01-01T00:00:00Z", "sess-1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ])
        conn = migrate.connect(str(self.db))
        migrate.new_ingest_run(conn, status="ok")
        before = conn.execute(
            "SELECT run_id, status, checks_evaluated FROM v_atlas_status").fetchone()
        conn.close()
        session_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        after = conn.execute(
            "SELECT run_id, status, checks_evaluated FROM v_atlas_status").fetchone()
        conn.close()
        self.assertEqual(before, after)

    def test_ledger_deny_route_supplies_a_remedy_no_prefix_could_match(self):
        """rule_frame_probe.py composes situation-specific prose, so no prefix set can match it.
        68 real denies had no remedy text until the ledger route existed."""
        prose = ("You are about to hand-roll this. There is a step that finds a maintained "
                 "library first.")
        writeSession(self.projects / "-Users-m5-proj", "sess-1", [
            assistantRecord("2026-01-01T00:00:00Z", "sess-1",
                            [toolUse("toolu_X", "Write", {"file_path": "/p/x.py"})]),
            {"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_X", "content": prose}]}},
        ])
        self.assertIsNone(transcript_parse.extract_deny_text(prose),
                          "no prefix should match this prose")

        conn = migrate.connect(str(self.db))
        run_id = migrate.new_ingest_run(conn, status="ok")
        conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, verdict, decision, tool_use_id) VALUES "
            "('s', 0, ?, '2026-01-01T00:00:00Z', 1000, 'microsecond', 'rule_frame_probe.py', "
            "'fire', 'deny', 'toolu_X')", (run_id,))
        conn.commit()
        conn.close()

        session_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        is_deny, deny_text = conn.execute(
            "SELECT is_hook_deny, deny_text FROM session_tool_result").fetchone()
        conn.close()
        self.assertEqual(is_deny, 0, "the PREFIX flag must stay 0 -- the two routes are kept "
                                     "distinguishable so FORE-190's disagreement stays visible")
        self.assertEqual(deny_text, prose, "but the remedy must still be captured, via the "
                                           "authoritative ledger route")


class JoinCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def addVerdict(self, offset, tool_use_id, decision="deny"):
        self.conn.execute(
            "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
            "ts_resolution, handler_id, verdict, decision, tool_use_id) VALUES "
            "('s', ?, ?, '2026-01-01T00:00:00Z', 1000, 'microsecond', 'h.py', 'fire', ?, ?)",
            (offset, self.run_id, decision, tool_use_id))
        self.conn.commit()

    def test_buckets_are_total(self):
        """A bucket view that silently dropped rows would be the section-3 failure reproduced in
        the tool built to prevent it."""
        self.addVerdict(0, "toolu_a")
        self.addVerdict(1, None, decision=None)
        self.addVerdict(2, None, decision="deny")
        total = sum(r[0] for r in self.conn.execute(
            "SELECT rows_total FROM v_ledger_join_coverage"))
        self.assertEqual(
            total, self.conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0])

    def test_the_three_unjoinable_shapes_land_in_distinct_buckets(self):
        self.addVerdict(0, "toolu_missing")
        self.addVerdict(1, None, decision=None)
        self.addVerdict(2, None, decision="deny")
        buckets = dict(self.conn.execute(
            "SELECT join_bucket, rows_total FROM v_ledger_join_coverage"))
        self.assertEqual(buckets.get("no-transcript-on-disk"), 1)
        self.assertEqual(buckets.get("no-tool-use-id-not-a-tool-call"), 1)
        self.assertEqual(buckets.get("no-tool-use-id-gated-a-call"), 1)

    def test_coverage_check_reports_not_evaluated_rather_than_passing_when_it_cannot_run(self):
        """A check that cannot run must never look like a check that ran and found nothing."""
        bare = Path(self.tmpdir.name) / "bare.db"
        conn = sqlite3.connect(str(bare))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate.apply(conn)
        result = dq_runner.check_ledger_join_coverage(conn, None)
        conn.close()
        self.assertFalse(result.passed)
        self.assertIn("NOT evaluated", result.detail)

    def test_coverage_check_is_registered_and_seeded_as_advisory(self):
        self.assertIn("ledger_join_coverage", dq_runner.CHECKERS)
        severity = self.conn.execute(
            "SELECT severity FROM dq_check WHERE check_name='ledger_join_coverage'").fetchone()
        self.assertEqual(
            severity[0], "advisory",
            "contract severity here would gate the whole source on retention decay -- FATAL-1's "
            "exact shape; see ARCHITECTURE.md 20.3")

    def test_empty_denominator_is_stated_not_reported_as_perfect(self):
        result = dq_runner.check_ledger_join_coverage(self.conn, self.run_id)
        self.assertTrue(result.passed)
        self.assertIsNone(result.observed_value)
        self.assertIn("vacuously", result.detail)


class RealCorpusCoverageTests(unittest.TestCase):
    @unittest.skipUnless(REAL_WAREHOUSE.is_file(), "no real warehouse on this machine")
    def test_ledger_join_coverage_clears_the_floor_on_the_live_warehouse(self):
        conn = sqlite3.connect(f"file:{REAL_WAREHOUSE}?mode=ro", uri=True)
        try:
            result = dq_runner.check_ledger_join_coverage(conn, None)
        finally:
            conn.close()
        if result.observed_value is None:
            self.skipTest(f"coverage not evaluable here: {result.detail}")
        self.assertGreaterEqual(
            result.observed_value, dq_runner.LEDGER_JOIN_COVERAGE_FLOOR,
            f"ledger join coverage fell below the floor: {result.detail}")

    @unittest.skipUnless(REAL_WAREHOUSE.is_file(), "no real warehouse on this machine")
    def test_every_ledger_confirmed_deny_in_the_view_carries_a_named_remedy(self):
        """The two routes together must leave no deny without its remedy text. A handler whose
        messages the prefix set cannot match is covered by the ledger route; a handler neither
        covers is a real gap and fails here."""
        conn = sqlite3.connect(f"file:{REAL_WAREHOUSE}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT deny_handlers, COUNT(*), "
                "SUM(CASE WHEN named_remedy IS NULL THEN 1 ELSE 0 END) "
                "FROM v_transcript_deny_join WHERE trust_state = 'live' "
                "AND deny_handlers IS NOT NULL GROUP BY deny_handlers"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            self.skipTest(f"live plane not migrated: {exc}")
        finally:
            conn.close()
        if not rows:
            self.skipTest("no denies ingested yet")
        gaps = {h: missing for h, _, missing in rows if missing}
        self.assertEqual(
            gaps, {},
            f"handlers whose denies carry no named remedy: {gaps}. Either add a fixed opening to "
            f"DENY_BODY_PREFIXES, or confirm the ledger route covers it.")


if __name__ == "__main__":
    unittest.main()
