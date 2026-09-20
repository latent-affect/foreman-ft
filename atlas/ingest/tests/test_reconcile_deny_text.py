"""Real-execution tests for reconcile_deny_text() (ATLASSN-194): the self-healing/backfill
mechanism for a deny_text row left NULL because ledger_deny_ids()'s one-shot-per-pull snapshot
missed a verdict that had not yet been ingested into hook_verdict at pull time.

Root cause measured directly against the real live warehouse before this fix was written:
map_transcript_line()'s own deny-extraction logic is correct (proven by calling it directly
against a real affected record with the correct ledger_deny_ids set) -- the gap is that
ledger_deny_ids() is a snapshot taken once per pull run, while the verdict-ledger-to-hook_verdict
ingest path is a separate pipeline that can lag the transcript pull by hours, even days. These
tests reproduce that exact race with a synthetic fixture (no dependency on the real warehouse
existing on the machine running the test) and confirm reconcile_deny_text() closes it."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.ingest.subagent_pull import reconcile_deny_text


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE subagent_transcript (transcript_id INTEGER PRIMARY KEY, source_path TEXT)"
    )
    conn.execute("""
        CREATE TABLE subagent_tool_result (
            transcript_id INTEGER, tool_use_id TEXT, byte_offset INTEGER,
            pull_run_id INTEGER, ts TEXT, is_error INTEGER, result_bytes INTEGER,
            is_hook_deny INTEGER, deny_text TEXT
        )
    """)
    conn.execute("CREATE TABLE hook_verdict (tool_use_id TEXT, decision TEXT)")
    conn.commit()
    return conn


def write_transcript_line(path, tool_use_id, content_body):
    """Writes ONE real JSONL record shaped exactly like a real tool_result record -- a user-role
    message whose content is a one-element list containing a tool_result block. Returns the
    byte length of the written line (including its trailing newline), matching subagent_pull's
    own byte_offset convention (the offset of the line's FIRST byte)."""
    line = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "content": content_body, "tool_use_id": tool_use_id}
        ]},
    })
    with open(path, "w", encoding="utf-8") as f:
        f.write(line + "\n")


class ReconcileDenyTextTests(unittest.TestCase):
    def test_a_late_arriving_verdict_is_reconciled_on_the_next_call(self):
        """THE CORE REPRO: a deny's hook_verdict row does not exist yet at the moment the
        transcript line is pulled (ledger_deny_ids() misses it), so deny_text is inserted NULL --
        matching production exactly, measured against the real warehouse before this fix. Once
        the verdict later lands in hook_verdict, reconcile_deny_text() must find and fill it,
        with no re-run of the original pull needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = Path(tmpdir) / "agent.jsonl"
            tool_use_id = "toolu_test_late_verdict"
            body = "Blocked: a real deny message that arrived late"
            write_transcript_line(transcript_path, tool_use_id, body)

            conn = make_conn()
            conn.execute(
                "INSERT INTO subagent_transcript (transcript_id, source_path) VALUES (1, ?)",
                (str(transcript_path),),
            )
            # Simulates the ORIGINAL pull: at that moment, ledger_deny_ids() found nothing (the
            # verdict had not landed yet), so is_hook_deny=0 and deny_text=NULL -- exactly what
            # the real pull inserts under this exact race.
            conn.execute(
                "INSERT INTO subagent_tool_result (transcript_id, tool_use_id, byte_offset, "
                "pull_run_id, ts, is_error, result_bytes, is_hook_deny, deny_text) "
                "VALUES (1, ?, 0, 1, '2026-01-01T00:00:00Z', NULL, ?, 0, NULL)",
                (tool_use_id, len(body)),
            )
            conn.commit()

            # No hook_verdict row exists yet -- reconciliation must find NOTHING to do.
            result = reconcile_deny_text(conn, "subagent_tool_result", "subagent_transcript")
            self.assertEqual(result["candidates_checked"], 0)
            self.assertEqual(result["rows_updated"], 0)
            row = conn.execute(
                "SELECT deny_text FROM subagent_tool_result WHERE tool_use_id=?", (tool_use_id,)
            ).fetchone()
            self.assertIsNone(row[0])

            # The verdict arrives LATE -- exactly the real-world sequencing this ticket found
            # (measured: 100% of the real affected rows had this shape, one example 3+ hours
            # late).
            conn.execute(
                "INSERT INTO hook_verdict (tool_use_id, decision) VALUES (?, 'deny')",
                (tool_use_id,),
            )
            conn.commit()

            result2 = reconcile_deny_text(conn, "subagent_tool_result", "subagent_transcript")
            self.assertEqual(result2["candidates_checked"], 1)
            self.assertEqual(result2["rows_updated"], 1)
            row2 = conn.execute(
                "SELECT deny_text, is_hook_deny FROM subagent_tool_result WHERE tool_use_id=?",
                (tool_use_id,),
            ).fetchone()
            self.assertEqual(row2[0], body)
            self.assertEqual(row2[1], 0, "is_hook_deny must stay 0 -- reconciliation never "
                                         "claims a prefix match that never happened")
            conn.close()

    def test_a_row_with_no_matching_verdict_is_never_touched(self):
        """Negative control: a deny_text that is NULL because NO handler ever confirmed a deny
        for it must stay NULL forever -- there is nothing to reconcile FROM."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = Path(tmpdir) / "agent.jsonl"
            tool_use_id = "toolu_test_no_verdict_ever"
            write_transcript_line(transcript_path, tool_use_id, "ordinary output, not a deny")
            conn = make_conn()
            conn.execute(
                "INSERT INTO subagent_transcript (transcript_id, source_path) VALUES (1, ?)",
                (str(transcript_path),),
            )
            conn.execute(
                "INSERT INTO subagent_tool_result (transcript_id, tool_use_id, byte_offset, "
                "pull_run_id, ts, is_error, result_bytes, is_hook_deny, deny_text) "
                "VALUES (1, ?, 0, 1, '2026-01-01T00:00:00Z', NULL, 10, 0, NULL)",
                (tool_use_id,),
            )
            conn.commit()
            result = reconcile_deny_text(conn, "subagent_tool_result", "subagent_transcript")
            self.assertEqual(result["candidates_checked"], 0)
            self.assertEqual(result["rows_updated"], 0)
            conn.close()

    def test_a_row_that_already_has_deny_text_is_never_overwritten(self):
        """Never touches a row the ingester already populated correctly, even if hook_verdict
        also confirms a deny for it -- matches backfill_ledger_origin.py's own discipline of
        never overwriting a value the real ingester already wrote."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = Path(tmpdir) / "agent.jsonl"
            tool_use_id = "toolu_test_already_populated"
            write_transcript_line(transcript_path, tool_use_id, "Foreman: some real deny text")
            conn = make_conn()
            conn.execute(
                "INSERT INTO subagent_transcript (transcript_id, source_path) VALUES (1, ?)",
                (str(transcript_path),),
            )
            conn.execute(
                "INSERT INTO subagent_tool_result (transcript_id, tool_use_id, byte_offset, "
                "pull_run_id, ts, is_error, result_bytes, is_hook_deny, deny_text) "
                "VALUES (1, ?, 0, 1, '2026-01-01T00:00:00Z', NULL, 10, 1, 'already correct')",
                (tool_use_id,),
            )
            conn.execute(
                "INSERT INTO hook_verdict (tool_use_id, decision) VALUES (?, 'deny')",
                (tool_use_id,),
            )
            conn.commit()
            result = reconcile_deny_text(conn, "subagent_tool_result", "subagent_transcript")
            # is_hook_deny=1 already -- the candidate query's own `is_hook_deny = 0` guard means
            # this row is never even a candidate.
            self.assertEqual(result["candidates_checked"], 0)
            row = conn.execute(
                "SELECT deny_text FROM subagent_tool_result WHERE tool_use_id=?", (tool_use_id,)
            ).fetchone()
            self.assertEqual(row[0], "already correct")
            conn.close()

    def test_an_unreadable_source_file_is_counted_not_silently_dropped(self):
        conn = make_conn()
        conn.execute(
            "INSERT INTO subagent_transcript (transcript_id, source_path) VALUES (1, ?)",
            ("/nonexistent/path/agent.jsonl",),
        )
        tool_use_id = "toolu_test_unreadable"
        conn.execute(
            "INSERT INTO subagent_tool_result (transcript_id, tool_use_id, byte_offset, "
            "pull_run_id, ts, is_error, result_bytes, is_hook_deny, deny_text) "
            "VALUES (1, ?, 0, 1, '2026-01-01T00:00:00Z', NULL, 10, 0, NULL)",
            (tool_use_id,),
        )
        conn.execute(
            "INSERT INTO hook_verdict (tool_use_id, decision) VALUES (?, 'deny')",
            (tool_use_id,),
        )
        conn.commit()
        result = reconcile_deny_text(conn, "subagent_tool_result", "subagent_transcript")
        self.assertEqual(result["candidates_checked"], 1)
        self.assertEqual(result["rows_updated"], 0)
        self.assertEqual(result["lines_unreadable"], 1)
        conn.close()

    def test_works_identically_for_session_tables(self):
        """Confirms the SAME function serves session_tool_result/session_transcript -- the
        shared-table-shape reuse this fix is built on. Measured against the real live warehouse:
        session_tool_result showed the identical gap (757 affected rows) even though the ticket's
        own repro named only the subagent side; this is not a subagent-only fix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = Path(tmpdir) / "session.jsonl"
            tool_use_id = "toolu_test_session_side"
            body = "Blocked: session-side deny, same shared mechanism"
            write_transcript_line(transcript_path, tool_use_id, body)
            conn = sqlite3.connect(":memory:")
            conn.execute(
                "CREATE TABLE session_transcript (transcript_id INTEGER PRIMARY KEY, "
                "source_path TEXT)"
            )
            conn.execute("""
                CREATE TABLE session_tool_result (
                    transcript_id INTEGER, tool_use_id TEXT, byte_offset INTEGER,
                    pull_run_id INTEGER, ts TEXT, is_error INTEGER, result_bytes INTEGER,
                    is_hook_deny INTEGER, deny_text TEXT
                )
            """)
            conn.execute("CREATE TABLE hook_verdict (tool_use_id TEXT, decision TEXT)")
            conn.execute(
                "INSERT INTO session_transcript (transcript_id, source_path) VALUES (1, ?)",
                (str(transcript_path),),
            )
            conn.execute(
                "INSERT INTO session_tool_result (transcript_id, tool_use_id, byte_offset, "
                "pull_run_id, ts, is_error, result_bytes, is_hook_deny, deny_text) "
                "VALUES (1, ?, 0, 1, '2026-01-01T00:00:00Z', NULL, ?, 0, NULL)",
                (tool_use_id, len(body)),
            )
            conn.execute(
                "INSERT INTO hook_verdict (tool_use_id, decision) VALUES (?, 'deny')",
                (tool_use_id,),
            )
            conn.commit()
            result = reconcile_deny_text(conn, "session_tool_result", "session_transcript")
            self.assertEqual(result["rows_updated"], 1)
            row = conn.execute(
                "SELECT deny_text FROM session_tool_result WHERE tool_use_id=?", (tool_use_id,)
            ).fetchone()
            self.assertEqual(row[0], body)
            conn.close()


if __name__ == "__main__":
    unittest.main()
