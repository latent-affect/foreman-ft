#!/usr/bin/env python3
"""Tests for classifier.py (ATLASSN-183).

Two layers, matching this project's own convention of real re-derivation over synthetic-only
fixtures (Dana's verification record; the CHV2 test suites this same session built tonight):

1. SYNTHETIC FIXTURE tests -- a small, hand-built SQLite database with the same real schema
   (hook_verdict, session_tool_call, subagent_tool_call, session_transcript), used to pin the
   MECHANISM (session_id-null filtering, the UNION join, the 5-call lookback, the 3-call retry
   window, the reconciliation arithmetic) against known-by-construction inputs where the correct
   answer is stated in the fixture itself, not inferred.

2. LIVE-DATA confirmation tests -- re-derive the four external validations this ticket's
   diagnosis produced, directly against the real warehouse, skipped (not failed) if it is not
   present in this environment: restamp's population-with-session_id filter reproduces the
   export's claimed 232/119 exactly; write_then_execute's UNION join covers >=99% of eligible
   Bash denials (vs. Dana's measured 49.6% for session_tool_call alone); the four families'
   caught_events reconcile exactly against the total population; escalation_not_stall finds
   exactly 3 hits, matching the export's own claimed all-time count.

    /Users/m5/.venv/bin/python3 -m unittest test_classifier -v
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import classifier as gfc  # noqa: E402

LIVE_DB = Path("/Users/m5/dev/atlas-sonnet/atlas/warehouse/atlas.db")

SCHEMA = """
CREATE TABLE hook_verdict (
    stream_id TEXT NOT NULL, byte_offset INTEGER NOT NULL, ts TEXT NOT NULL,
    handler_id TEXT NOT NULL, verdict TEXT NOT NULL, session_id TEXT, tool_name TEXT,
    tool_use_id TEXT, decision TEXT,
    PRIMARY KEY (stream_id, byte_offset)
);
CREATE TABLE session_tool_call (
    call_id INTEGER PRIMARY KEY, ts TEXT, session_id TEXT, tool_use_id TEXT, tool_name TEXT,
    tool_input_json TEXT, byte_offset INTEGER
);
CREATE TABLE subagent_tool_call (
    call_id INTEGER PRIMARY KEY, ts TEXT, session_id TEXT, tool_use_id TEXT, tool_name TEXT,
    tool_input_json TEXT, byte_offset INTEGER
);
CREATE TABLE session_transcript (
    transcript_id INTEGER PRIMARY KEY, source_path TEXT NOT NULL UNIQUE, session_id TEXT NOT NULL
);
"""


def build_fixture_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def insert_hv(conn, stream_id, byte_offset, ts, handler_id, decision, session_id="s1",
              tool_name=None, tool_use_id=None, verdict="fire"):
    conn.execute(
        "INSERT INTO hook_verdict (stream_id, byte_offset, ts, handler_id, verdict, session_id, "
        "tool_name, tool_use_id, decision) VALUES (?,?,?,?,?,?,?,?,?)",
        (stream_id, byte_offset, ts, handler_id, verdict, session_id, tool_name, tool_use_id,
         decision))


def insert_call(conn, table, ts, session_id, tool_use_id, tool_name, tool_input=None,
                byte_offset=0):
    conn.execute(
        f"INSERT INTO {table} (ts, session_id, tool_use_id, tool_name, tool_input_json, "
        f"byte_offset) VALUES (?,?,?,?,?,?)",
        (ts, session_id, tool_use_id, tool_name,
         json.dumps(tool_input) if tool_input is not None else None, byte_offset))


class SessionCallsOrderingTests(unittest.TestCase):
    """The UNION of session_tool_call and subagent_tool_call, chronologically ordered -- the
    shared timeline every other mechanism in this module depends on."""

    def setUp(self):
        self.conn = build_fixture_db(":memory:")

    def test_combines_both_tables_in_chronological_order(self):
        insert_call(self.conn, "session_tool_call", "2026-01-01T00:00:01", "s1", "a", "Bash")
        insert_call(self.conn, "subagent_tool_call", "2026-01-01T00:00:02", "s1", "b", "Write")
        insert_call(self.conn, "session_tool_call", "2026-01-01T00:00:03", "s1", "c", "Edit")
        calls = gfc.session_calls(self.conn, "s1")
        self.assertEqual([c["tool_use_id"] for c in calls], ["a", "b", "c"])

    def test_different_session_is_not_included(self):
        insert_call(self.conn, "session_tool_call", "2026-01-01T00:00:01", "s1", "a", "Bash")
        insert_call(self.conn, "session_tool_call", "2026-01-01T00:00:02", "s2", "x", "Bash")
        calls = gfc.session_calls(self.conn, "s1")
        self.assertEqual([c["tool_use_id"] for c in calls], ["a"])

    def test_find_call_index_locates_by_tool_use_id(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "a", "Bash")
        insert_call(self.conn, "session_tool_call", "t2", "s1", "b", "Write")
        calls = gfc.session_calls(self.conn, "s1")
        self.assertEqual(gfc.find_call_index(calls, "b"), 1)
        self.assertIsNone(gfc.find_call_index(calls, "nonexistent"))


class WriteThenExecuteTests(unittest.TestCase):
    """ATLASSN-183 finding 2: the UNION join, and the basename-substring lookback itself,
    tested against known-by-construction fixtures."""

    def setUp(self):
        self.conn = build_fixture_db(":memory:")

    def test_bash_referencing_a_recently_written_file_is_a_hit(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "w1", "Write",
                    {"file_path": "/tmp/pentatonic_filter.py"})
        insert_call(self.conn, "session_tool_call", "t2", "s1", "b1", "Bash",
                    {"command": "python3 pentatonic_filter.py --check"})
        hit, evidence = gfc.write_then_execute_evidence(self.conn, {}, "s1", "b1")
        self.assertTrue(hit)
        self.assertEqual(evidence["written_path"], "/tmp/pentatonic_filter.py")

    def test_write_from_a_subagent_call_still_counts(self):
        """The exact gap this ticket's diagnosis names: the write can come from either table."""
        insert_call(self.conn, "subagent_tool_call", "t1", "s1", "w1", "Write",
                    {"file_path": "/tmp/report.md"})
        insert_call(self.conn, "session_tool_call", "t2", "s1", "b1", "Bash",
                    {"command": "cat report.md"})
        hit, _ = gfc.write_then_execute_evidence(self.conn, {}, "s1", "b1")
        self.assertTrue(hit)

    def test_write_outside_the_lookback_window_is_not_a_hit(self):
        insert_call(self.conn, "session_tool_call", "t0", "s1", "w1", "Write",
                    {"file_path": "/tmp/x.py"})
        for i in range(gfc.LOOKBACK_WINDOW):
            insert_call(self.conn, "session_tool_call", f"t{i+1}", "s1", f"filler{i}", "Read")
        insert_call(self.conn, "session_tool_call", "tN", "s1", "b1", "Bash",
                    {"command": "python3 x.py"})
        hit, _ = gfc.write_then_execute_evidence(self.conn, {}, "s1", "b1")
        self.assertFalse(hit, "the write is exactly LOOKBACK_WINDOW calls back, outside the "
                              "preceding window once the filler calls are counted")

    def test_unrelated_bash_command_is_not_a_hit(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "w1", "Write",
                    {"file_path": "/tmp/x.py"})
        insert_call(self.conn, "session_tool_call", "t2", "s1", "b1", "Bash",
                    {"command": "echo hello world"})
        hit, _ = gfc.write_then_execute_evidence(self.conn, {}, "s1", "b1")
        self.assertFalse(hit)

    def test_non_bash_call_is_never_a_hit(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "w1", "Write",
                    {"file_path": "/tmp/x.py"})
        insert_call(self.conn, "session_tool_call", "t2", "s1", "e1", "Edit",
                    {"file_path": "/tmp/x.py"})
        hit, _ = gfc.write_then_execute_evidence(self.conn, {}, "s1", "e1")
        self.assertFalse(hit)


class IsStoppedTests(unittest.TestCase):
    """The stopped/escaped mechanism: an uncontested same-tool_name retry within RETRY_WINDOW
    calls means the operation landed anyway (escaped); no such retry means it was genuinely
    stopped."""

    def setUp(self):
        self.conn = build_fixture_db(":memory:")

    def test_uncontested_retry_within_window_is_escaped(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "orig", "Bash")
        insert_call(self.conn, "session_tool_call", "t2", "s1", "retry", "Bash")
        event = {"session_id": "s1", "tool_use_id": "orig", "tool_name": "Bash"}
        is_stop, reason = gfc.is_stopped(self.conn, {}, event)
        self.assertFalse(is_stop, "an uncontested same-tool_name retry means the deny did not "
                                  "actually prevent the operation")
        self.assertIn("uncontested", reason)

    def test_contested_retry_still_counts_as_stopped(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "orig", "Bash")
        insert_call(self.conn, "session_tool_call", "t2", "s1", "retry", "Bash")
        insert_hv(self.conn, "strm", 1, "t2", "guard_destructive.py", "deny",
                 session_id="s1", tool_use_id="retry")
        event = {"session_id": "s1", "tool_use_id": "orig", "tool_name": "Bash"}
        is_stop, _ = gfc.is_stopped(self.conn, {}, event)
        self.assertTrue(is_stop, "a retry that was ALSO denied did not let the operation land")

    def test_no_retry_at_all_is_stopped(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "orig", "Bash")
        event = {"session_id": "s1", "tool_use_id": "orig", "tool_name": "Bash"}
        is_stop, _ = gfc.is_stopped(self.conn, {}, event)
        self.assertTrue(is_stop)

    def test_retry_of_a_different_tool_name_does_not_count(self):
        insert_call(self.conn, "session_tool_call", "t1", "s1", "orig", "Bash")
        insert_call(self.conn, "session_tool_call", "t2", "s1", "other", "Write")
        event = {"session_id": "s1", "tool_use_id": "orig", "tool_name": "Bash"}
        is_stop, _ = gfc.is_stopped(self.conn, {}, event)
        self.assertTrue(is_stop)

    def test_retry_beyond_the_window_does_not_count(self):
        insert_call(self.conn, "session_tool_call", "t0", "s1", "orig", "Bash")
        for i in range(gfc.RETRY_WINDOW):
            insert_call(self.conn, "session_tool_call", f"t{i+1}", "s1", f"filler{i}", "Read")
        insert_call(self.conn, "session_tool_call", "tN", "s1", "retry", "Bash")
        event = {"session_id": "s1", "tool_use_id": "orig", "tool_name": "Bash"}
        is_stop, _ = gfc.is_stopped(self.conn, {}, event)
        self.assertTrue(is_stop, "the retry is exactly RETRY_WINDOW calls later, outside the "
                                 "window")

    def test_no_tool_use_id_is_disclosed_and_treated_as_stopped(self):
        event = {"session_id": "s1", "tool_use_id": None, "tool_name": "Bash"}
        is_stop, reason = gfc.is_stopped(self.conn, {}, event)
        self.assertTrue(is_stop)
        self.assertIn("no-tool-use-id", reason)


class RestampSessionFilterTests(unittest.TestCase):
    """ATLASSN-183 finding 1: population() filters to session_id IS NOT NULL, which is what
    reproduces the export's own claimed numbers -- pinned here against a synthetic fixture where
    the correct filtered count is known by construction."""

    def setUp(self):
        self.conn = build_fixture_db(":memory:")

    def test_null_session_id_rows_are_excluded(self):
        insert_hv(self.conn, "s", 1, "t1", "architecture_gate.py", "deny", session_id="real-1")
        insert_hv(self.conn, "s", 2, "t2", "architecture_gate.py", "deny", session_id="real-2")
        insert_hv(self.conn, "s", 3, "t3", "architecture_gate.py", "deny", session_id=None)
        pop = gfc.population(self.conn, "architecture_gate.py")
        self.assertEqual(len(pop), 2)

    def test_allow_and_defer_decisions_are_excluded(self):
        insert_hv(self.conn, "s", 1, "t1", "architecture_gate.py", "deny", session_id="s1")
        insert_hv(self.conn, "s", 2, "t2", "architecture_gate.py", "ask", session_id="s1")
        insert_hv(self.conn, "s", 3, "t3", "architecture_gate.py", "allow", session_id="s1")
        pop = gfc.population(self.conn, "architecture_gate.py")
        self.assertEqual(len(pop), 2)


class ReconciliationTests(unittest.TestCase):
    """ATLASSN-183 finding 3: family totals must reconcile EXACTLY against the total population
    once the disclosed semantic_destructive/restamp/write_then_execute overlap is deduplicated
    (not naively summed) and landed_retry is computed as a true set difference."""

    def setUp(self):
        self.conn = build_fixture_db(":memory:")

    def test_reconciles_exactly_with_no_overlap(self):
        insert_hv(self.conn, "s", 1, "t1", "guard_destructive.py", "deny", session_id="s1",
                 tool_name="Bash", tool_use_id="a")
        insert_hv(self.conn, "s", 2, "t2", "architecture_gate.py", "deny", session_id="s1",
                 tool_name="Edit", tool_use_id="b")
        insert_hv(self.conn, "s", 3, "t3", "some_other_gate.py", "deny", session_id="s1",
                 tool_name="Bash", tool_use_id="c")
        insert_call(self.conn, "session_tool_call", "t1", "s1", "a", "Bash", {"command": "rm -rf /"})
        insert_call(self.conn, "session_tool_call", "t2", "s1", "b", "Edit", {"file_path": "/x"})
        insert_call(self.conn, "session_tool_call", "t3", "s1", "c", "Bash", {"command": "echo hi"})
        result = gfc.classify_warehouse(self.conn)
        rec = result["reconciliation"]
        self.assertTrue(rec["reconciles_exactly"])
        self.assertEqual(rec["total_session_attributed_population"], 3)
        self.assertEqual(result["families"]["landed_retry"]["caught_events"], 1)

    def test_reconciles_exactly_with_real_overlap(self):
        """A single row that is BOTH semantic_destructive (guard_destructive.py) AND
        write_then_execute (references a just-written file) must not be double-counted in the
        reconciliation, matching families_are_not_mutually_exclusive's own disclosure."""
        insert_call(self.conn, "session_tool_call", "t1", "s1", "w1", "Write",
                    {"file_path": "/tmp/danger.sh"})
        insert_hv(self.conn, "s", 1, "t2", "guard_destructive.py", "deny", session_id="s1",
                 tool_name="Bash", tool_use_id="b1")
        insert_call(self.conn, "session_tool_call", "t2", "s1", "b1", "Bash",
                    {"command": "bash danger.sh"})
        result = gfc.classify_warehouse(self.conn)
        rec = result["reconciliation"]
        self.assertTrue(rec["reconciles_exactly"])
        self.assertEqual(rec["overlap_events_among_semantic_restamp_write_then_execute"], 1)
        self.assertEqual(result["families"]["semantic_destructive"]["caught_events"], 1)
        self.assertEqual(result["families"]["write_then_execute"]["caught_events"], 1)
        self.assertEqual(result["families"]["landed_retry"]["caught_events"], 0)


class BareAssistantMessageTests(unittest.TestCase):
    """PASS1: a bare assistant message (zero tool_use blocks). Written to a real temp file since
    the function reads from disk."""

    def _write_transcript(self, lines):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False,
                                          encoding="utf-8")
        for line in lines:
            tmp.write(json.dumps(line) + "\n")
        tmp.close()
        return tmp.name

    def test_bare_text_message_is_yielded(self):
        path = self._write_transcript([
            {"message": {"role": "assistant", "content": "moving on to the next step"},
             "sessionId": "s1"},
        ])
        hits = list(gfc.bare_assistant_messages(path))
        self.assertEqual(len(hits), 1)
        self.assertIn("moving on to", hits[0][2])

    def test_message_with_a_tool_use_block_is_not_bare(self):
        path = self._write_transcript([
            {"message": {"role": "assistant", "content": [
                {"type": "text", "text": "moving on to the next step"},
                {"type": "tool_use", "name": "Bash", "input": {}},
            ]}, "sessionId": "s1"},
        ])
        hits = list(gfc.bare_assistant_messages(path))
        self.assertEqual(hits, [])

    def test_user_message_is_ignored(self):
        path = self._write_transcript([
            {"message": {"role": "user", "content": "moving on to the next step"}},
        ])
        hits = list(gfc.bare_assistant_messages(path))
        self.assertEqual(hits, [])

    def test_malformed_json_line_does_not_crash_and_is_skipped(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False,
                                          encoding="utf-8")
        tmp.write("{ not valid json\n")
        tmp.write(json.dumps({"message": {"role": "assistant", "content": "moving on to it"},
                             "sessionId": "s1"}) + "\n")
        tmp.close()
        hits = list(gfc.bare_assistant_messages(tmp.name))
        self.assertEqual(len(hits), 1)


class LiveWarehouseConfirmationTests(unittest.TestCase):
    """Live-data re-derivation, skipped rather than failed if the warehouse is not present in
    this environment. These pin the exact external validations ATLASSN-183's diagnosis produced,
    so a future edit that breaks the reconstruction is caught here rather than only noticed the
    next time someone re-reads the numbers by hand."""

    @classmethod
    def setUpClass(cls):
        if not LIVE_DB.is_file():
            raise unittest.SkipTest(f"live warehouse not present at {LIVE_DB}")
        cls.conn = gfc.connect(str(LIVE_DB))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def test_restamp_reproduces_the_exports_claimed_caught_events_and_sessions(self):
        pop = gfc.population(self.conn, "architecture_gate.py")
        self.assertEqual(len(pop), 232,
                         "restamp's session_id-not-null population must reproduce the export's "
                         "claimed caught_events=232 exactly")
        self.assertEqual(len({e['session_id'] for e in pop}), 119,
                         "and the export's claimed caught_sessions=119")

    def test_semantic_destructive_reproduces_within_the_disclosed_tolerance(self):
        pop = gfc.population(self.conn, "guard_destructive.py")
        self.assertLessEqual(abs(len(pop) - 435), 2,
                             "must land within the ATLASSN-171 disclosed quarantine-gap "
                             "tolerance of the export's claimed 435")

    def test_write_then_execute_union_join_covers_far_more_than_session_only(self):
        """Confirms Dana's own measured gap (49.6% via session_tool_call alone) and this fix's
        resolution of it (>=99% via the UNION), directly against live data."""
        eligible = self.conn.execute(
            "SELECT tool_use_id FROM hook_verdict WHERE tool_name='Bash' "
            "AND decision IN ('deny','ask') AND tool_use_id IS NOT NULL "
            "AND session_id IS NOT NULL").fetchall()
        session_only_matches = 0
        union_matches = 0
        for (tool_use_id,) in eligible:
            in_session = self.conn.execute(
                "SELECT 1 FROM session_tool_call WHERE tool_use_id=? LIMIT 1",
                (tool_use_id,)).fetchone()
            in_subagent = self.conn.execute(
                "SELECT 1 FROM subagent_tool_call WHERE tool_use_id=? LIMIT 1",
                (tool_use_id,)).fetchone()
            session_only_matches += 1 if in_session else 0
            union_matches += 1 if (in_session or in_subagent) else 0
        session_only_pct = 100.0 * session_only_matches / len(eligible)
        union_pct = 100.0 * union_matches / len(eligible)
        self.assertLess(session_only_pct, 55,
                        f"sanity check on Dana's own measurement: session_tool_call alone "
                        f"should cover roughly half, got {session_only_pct:.1f}%")
        self.assertGreaterEqual(union_pct, 99,
                                f"the UNION join should cover effectively the whole eligible "
                                f"population, got {union_pct:.1f}%")

    def test_all_four_families_reconcile_exactly_against_the_live_total(self):
        result = gfc.classify_warehouse(self.conn)
        self.assertTrue(result["reconciliation"]["reconciles_exactly"],
                        result["reconciliation"])

    def test_escalation_not_stall_finds_exactly_the_exports_claimed_three_hits(self):
        hits = gfc.escalation_not_stall_hits(self.conn)
        self.assertEqual(len(hits), 3,
                         "must reproduce the export's own claimed all-time count of 3 exactly")
        for hit in hits:
            self.assertTrue(hit["session_id"], "row-level evidence must retain a session_id")
            self.assertTrue(Path(hit["transcript_path"]).is_file(),
                            "the retained transcript_path must be a real, readable file")
            self.assertIsInstance(hit["byte_offset"], int)
            self.assertTrue(hit["matched_text_excerpt"])


if __name__ == "__main__":
    unittest.main()
