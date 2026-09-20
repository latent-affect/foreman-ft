"""ATLASSN-102, ARCHITECTURE.md section 33. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest \
        atlas.warehouse.tests.test_transcript_source_visibility -v

The point of this file is the NEGATIVE controls. Both new checks are contract severity, and
v_atlas_status.contract_failures is summed across every source and read by the query facade's
system-wide trust gate -- so a check that silently cannot fail would manufacture trust across the
whole warehouse, and a check that fails on a benign state would close it. Each check is therefore
asserted to fail on a real positive AND to pass on each of the two states section 33.3
deliberately tolerates.
"""

import datetime
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from atlas.ingest import session_pull
from atlas.warehouse import dq_runner, migrate


def nowIso(offsetSeconds=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=offsetSeconds)).isoformat()


def newPullRun(conn):
    """session_tool_call.pull_run_id is NOT NULL and references subagent_pull_run, so a fixture
    cannot pass NULL. Both pulls share that run table by design -- see session_pull.run()."""
    cur = conn.execute(
        "INSERT INTO subagent_pull_run (started_at, status) VALUES (?, 'ok')", (nowIso(),))
    conn.commit()
    return cur.lastrowid


class TranscriptWatermarkCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.conn = migrate.connect(str(self.root / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def addSession(self, name, contents, byteOffset, updatedAt=None, mtimeOffset=None):
        path = self.root / name
        path.write_bytes(contents)
        if mtimeOffset is not None:
            stamp = time.time() + mtimeOffset
            os.utime(path, (stamp, stamp))
        self.conn.execute(
            "INSERT INTO session_transcript (source_path, stream_id, project_dir, session_id, "
            "byte_offset, calls_ingested, first_seen_at, updated_at) "
            "VALUES (?, 'sid', 'proj', ?, ?, 0, ?, ?)",
            (str(path), name, byteOffset, nowIso(), updatedAt or nowIso()),
        )
        self.conn.commit()
        return path

    def run_check(self):
        return dq_runner.check_session_watermark_le_filesize(self.conn, run_id=1)

    def test_empty_table_passes_vacuously(self):
        self.assertTrue(self.run_check().passed)

    def test_watermark_within_the_file_passes(self):
        self.addSession("a.jsonl", b"x" * 100, byteOffset=100)
        result = self.run_check()
        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_watermark_past_eof_on_a_settled_file_FAILS(self):
        """The negative control. A watermark past the end of a file the pull has already seen at
        this mtime is the resume logic silently skipping real bytes, and it must fail -- without
        this assertion, every passing result below is worthless."""
        # mtime pushed into the past so updated_at (now) is unambiguously at or after it
        self.addSession("b.jsonl", b"x" * 10, byteOffset=999, mtimeOffset=-3600)
        result = self.run_check()
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)
        self.assertIn("exceeds file size", result.detail)

    def test_missing_source_file_does_not_fail(self):
        """Transcript retention deletes these constantly. Failing here would put the warehouse's
        system-wide contract_failures above zero permanently."""
        path = self.addSession("c.jsonl", b"x" * 10, byteOffset=999, mtimeOffset=-3600)
        path.unlink()
        result = self.run_check()
        self.assertTrue(result.passed)
        self.assertIn("source file is gone", result.detail)

    def test_file_modified_since_the_last_pull_does_not_fail(self):
        """Rotation and truncation both land here for one tick. The next pull resets or advances
        the offset, so failing would convert a transient into a permanent red."""
        self.addSession("d.jsonl", b"x" * 10, byteOffset=999,
                        updatedAt=nowIso(-3600), mtimeOffset=0)
        result = self.run_check()
        self.assertTrue(result.passed)
        self.assertIn("modified since the last pull", result.detail)

    def test_truncation_in_place_is_caught_one_tick_later(self):
        """The case a stream_id comparison would have missed forever: the first bytes are
        unchanged, so the stream identity is unchanged, but the file is shorter than the
        watermark. Benign on the tick it happens, a real violation once the pull has re-seen
        it -- which is exactly what session_pull's `size <= start_offset` early return produces,
        because that path still bumps updated_at."""
        path = self.addSession("e.jsonl", b"x" * 10, byteOffset=999,
                               updatedAt=nowIso(-3600), mtimeOffset=0)
        self.assertTrue(self.run_check().passed)
        # the pull's early-return branch: nothing new to read, updated_at bumped anyway
        self.conn.execute(
            "UPDATE session_transcript SET updated_at = ? WHERE source_path = ?",
            (nowIso(), str(path)),
        )
        self.conn.commit()
        self.assertFalse(self.run_check().passed)

    def test_subagent_check_uses_its_own_table(self):
        self.addSession("f.jsonl", b"x" * 10, byteOffset=999, mtimeOffset=-3600)
        self.assertFalse(dq_runner.check_session_watermark_le_filesize(self.conn, 1).passed)
        self.assertTrue(dq_runner.check_subagent_watermark_le_filesize(self.conn, 1).passed)


class CallsIngestedContractTests(unittest.TestCase):
    """The CONTRACT pair. These are the two checks whose failure closes the query facade
    system-wide, so their negative control matters more than any other in this file: a check that
    silently cannot fail would attest integrity across the whole warehouse on no evidence."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def addTranscript(self, sessionId, claimedCalls):
        cur = self.conn.execute(
            "INSERT INTO session_transcript (source_path, stream_id, project_dir, session_id, "
            "byte_offset, calls_ingested, first_seen_at, updated_at) "
            "VALUES (?, 'sid', 'proj', ?, 0, ?, ?, ?)",
            (f"/tmp/{sessionId}.jsonl", sessionId, claimedCalls, nowIso(), nowIso()),
        )
        return cur.lastrowid

    def addCalls(self, transcriptId, n):
        runId = newPullRun(self.conn)
        for i in range(n):
            self.conn.execute(
                "INSERT INTO session_tool_call (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, session_id, cwd, git_branch, tool_use_id, "
                "tool_name, tool_input_json, tool_input_bytes) "
                "VALUES (?, ?, 0, ?, '2026-09-01T00:00:00Z', ?, 's', '/x', 'main', ?, "
                "'Bash', '{}', 2)",
                (transcriptId, i, runId, f"u{transcriptId}-{i}",
                 f"toolu_{transcriptId}_{i}"),
            )
        self.conn.commit()

    def run_check(self):
        return dq_runner.check_session_calls_ingested_matches(self.conn, run_id=1)

    def test_empty_table_passes_vacuously(self):
        self.assertTrue(self.run_check().passed)

    def test_matching_counts_pass(self):
        tid = self.addTranscript("s1", claimedCalls=3)
        self.addCalls(tid, 3)
        result = self.run_check()
        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_overcount_FAILS(self):
        """The negative control: bookkeeping claims more rows than were ingested."""
        tid = self.addTranscript("s2", claimedCalls=5)
        self.addCalls(tid, 3)
        result = self.run_check()
        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)
        self.assertIn("calls_ingested 5", result.detail)

    def test_undercount_FAILS(self):
        """The other direction, which a one-sided comparison would miss: fact rows exist that the
        watermark never accounted for, i.e. the resume pointer could re-read and duplicate them."""
        tid = self.addTranscript("s3", claimedCalls=1)
        self.addCalls(tid, 4)
        self.assertFalse(self.run_check().passed)

    def test_zero_calls_with_zero_claimed_passes(self):
        """The common real shape: 1,531 of 2,367 live rows point at a file retention deleted, and
        133 of those still carry calls_ingested > 0 -- so this must not be waved through by a
        rule that ignores empty transcripts."""
        self.addTranscript("s4", claimedCalls=0)
        self.conn.commit()
        self.assertTrue(self.run_check().passed)

    def test_a_deleted_source_file_is_irrelevant_to_this_check(self):
        """Deliberately no filesystem read: that is the whole reason this pair carries contract
        severity and the watermark pair does not."""
        tid = self.addTranscript("s5", claimedCalls=2)
        self.addCalls(tid, 2)
        self.assertTrue(self.run_check().passed)
        self.assertNotIn("does not exist", self.run_check().detail)

    def test_subagent_check_uses_its_own_tables(self):
        tid = self.addTranscript("s6", claimedCalls=9)
        self.addCalls(tid, 1)
        self.assertFalse(dq_runner.check_session_calls_ingested_matches(self.conn, 1).passed)
        self.assertTrue(dq_runner.check_subagent_calls_ingested_matches(self.conn, 1).passed)


class QueryableSourceTests(unittest.TestCase):
    """The whole reason the two checks exist: v_queryable_source can only name a source_table
    that some contract-severity dq_check names."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_both_transcript_sources_are_registered_as_contract_sources(self):
        rows = dict(self.conn.execute(
            "SELECT check_name, source_table FROM dq_check WHERE severity = 'contract'"))
        self.assertEqual(rows.get("session_calls_ingested_matches"), "session_transcript")
        self.assertEqual(rows.get("subagent_calls_ingested_matches"), "subagent_transcript")

    def test_the_filesystem_reading_checks_are_advisory_not_contract(self):
        """ARCHITECTURE.md 33.3. If either of these is ever promoted to contract, one truncated
        transcript closes every gated view in the warehouse until a human edits a row."""
        rows = dict(self.conn.execute(
            "SELECT check_name, severity FROM dq_check WHERE check_name IN "
            "('session_watermark_le_filesize', 'subagent_watermark_le_filesize')"))
        self.assertEqual(rows.get("session_watermark_le_filesize"), "advisory")
        self.assertEqual(rows.get("subagent_watermark_le_filesize"), "advisory")

    def test_transcript_sources_become_queryable_after_a_clean_run(self):
        """The end the whole migration exists for: v_queryable_source can finally name them."""
        run_id = migrate.new_ingest_run(self.conn, status="ok")
        dq_runner.run_all(self.conn, run_id)
        self.conn.execute("UPDATE ingest_run SET status = 'ok' WHERE run_id = ?", (run_id,))
        self.conn.commit()
        queryable = {r[0] for r in self.conn.execute(
            "SELECT source_table FROM v_queryable_source")}
        self.assertIn("session_transcript", queryable)
        self.assertIn("subagent_transcript", queryable)

    def test_v_source_freshness_names_both_corpora(self):
        names = {r[0] for r in self.conn.execute(
            "SELECT source_name FROM v_source_freshness")}
        self.assertIn("session_transcript", names)
        self.assertIn("subagent_transcript", names)

    def test_v_source_freshness_reports_zeros_not_a_missing_row_when_empty(self):
        row = self.conn.execute(
            "SELECT source_path, byte_offset, rows_ingested, updated_at, staleness_minutes "
            "FROM v_source_freshness WHERE source_name = 'session_transcript'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], 0)
        self.assertIsNone(row[3])
        self.assertIsNone(row[4])

    def test_existing_ingest_source_rows_still_pass_through_unchanged(self):
        self.conn.execute(
            "INSERT INTO ingest_source (source_name, stream_id, source_path, byte_offset, "
            "rows_ingested, first_seen_at, updated_at) "
            "VALUES ('verdicts', 'abc', '/tmp/v.jsonl', 42, 7, ?, ?)",
            (nowIso(), nowIso()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT source_path, stream_id, byte_offset, rows_ingested FROM v_source_freshness "
            "WHERE source_name = 'verdicts'").fetchone()
        self.assertEqual(row, ("/tmp/v.jsonl", "abc", 42, 7))

    def test_every_registered_check_has_a_checker_function(self):
        """run_all raises rather than skipping an unregistered check, so a migration that seeds a
        dq_check row without a CHECKERS entry breaks every ingest run."""
        names = {r[0] for r in self.conn.execute("SELECT check_name FROM dq_check")}
        self.assertEqual(names - set(dq_runner.CHECKERS), set())


class RefreshDimSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def addTranscript(self, sessionId, path):
        cur = self.conn.execute(
            "INSERT INTO session_transcript (source_path, stream_id, project_dir, session_id, "
            "byte_offset, calls_ingested, first_seen_at, updated_at) "
            "VALUES (?, 'sid', 'proj', ?, 0, 0, ?, ?)",
            (path, sessionId, nowIso(), nowIso()),
        )
        return cur.lastrowid

    def test_empty_input_produces_no_rows_and_does_not_raise(self):
        self.assertEqual(session_pull.refresh_dim_session(self.conn), 0)

    def test_timestamps_come_from_assistant_text_when_there_are_no_tool_calls(self):
        """The measurement that shaped the derivation: most transcripts carry assistant text and
        no tool calls, so taking timestamps from session_tool_call alone would leave the
        dimension 69% NULL."""
        tid = self.addTranscript("s1", "/tmp/s1.jsonl")
        for offset, ts in ((0, "2026-09-01T00:00:00Z"), (1, "2026-09-01T02:00:00Z")):
            self.conn.execute(
                "INSERT INTO session_assistant_text (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, text, text_bytes, truncated) "
                "VALUES (?, ?, 0, ?, ?, ?, 'x', 1, 0)",
                (tid, offset, newPullRun(self.conn), ts, f"u{offset}"),
            )
        self.conn.commit()
        session_pull.refresh_dim_session(self.conn)
        row = self.conn.execute(
            "SELECT started_ts, ended_ts, start_cwd, git_branch, claude_version "
            "FROM dim_session WHERE session_id = 's1'").fetchone()
        self.assertEqual(row[0], "2026-09-01T00:00:00Z")
        self.assertEqual(row[1], "2026-09-01T02:00:00Z")
        self.assertIsNone(row[2])
        self.assertIsNone(row[3])
        self.assertIsNone(row[4])

    def test_cwd_and_branch_come_from_the_earliest_tool_call_carrying_a_cwd(self):
        tid = self.addTranscript("s2", "/tmp/s2.jsonl")
        rows = [
            (0, "2026-09-01T00:00:00Z", None, None),
            (1, "2026-09-01T01:00:00Z", "/repo/first", "main"),
            (2, "2026-09-01T03:00:00Z", "/repo/later", "feature"),
        ]
        for offset, ts, cwd, branch in rows:
            self.conn.execute(
                "INSERT INTO session_tool_call (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, session_id, cwd, git_branch, tool_use_id, "
                "tool_name, tool_input_json, tool_input_bytes) "
                "VALUES (?, ?, 0, ?, ?, ?, 's2', ?, ?, ?, 'Bash', '{}', 2)",
                (tid, offset, newPullRun(self.conn), ts, f"r{offset}", cwd, branch,
                 f"toolu_{offset}"),
            )
        self.conn.commit()
        session_pull.refresh_dim_session(self.conn)
        row = self.conn.execute(
            "SELECT started_ts, ended_ts, start_cwd, git_branch FROM dim_session "
            "WHERE session_id = 's2'").fetchone()
        self.assertEqual(row[0], "2026-09-01T00:00:00Z")
        self.assertEqual(row[1], "2026-09-01T03:00:00Z")
        self.assertEqual(row[2], "/repo/first")
        self.assertEqual(row[3], "main")

    def test_a_session_with_nothing_ingested_still_gets_a_row(self):
        self.addTranscript("s3", "/tmp/s3.jsonl")
        self.conn.commit()
        session_pull.refresh_dim_session(self.conn)
        row = self.conn.execute(
            "SELECT session_id, started_ts FROM dim_session WHERE session_id = 's3'").fetchone()
        self.assertEqual(row[0], "s3")
        self.assertIsNone(row[1])

    def test_a_session_whose_transcript_row_is_gone_is_reaped(self):
        """INSERT OR REPLACE never deletes, and session_transcript is not append-only --
        rebuild() does DELETE FROM session_transcript. Without the reaper one --rebuild orphans
        every dim_session row for a session no longer on disk, permanently."""
        self.addTranscript("gone", "/tmp/gone.jsonl")
        self.conn.commit()
        session_pull.refresh_dim_session(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM dim_session "
                              "WHERE session_id = 'gone'").fetchone()[0], 1)
        self.conn.execute("DELETE FROM session_transcript WHERE session_id = 'gone'")
        self.conn.commit()
        session_pull.refresh_dim_session(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM dim_session "
                              "WHERE session_id = 'gone'").fetchone()[0], 0)

    def test_refresh_is_idempotent_and_never_empties_the_table(self):
        self.addTranscript("s4", "/tmp/s4.jsonl")
        self.conn.commit()
        first = session_pull.refresh_dim_session(self.conn)
        second = session_pull.refresh_dim_session(self.conn)
        self.assertEqual(first, second)
        self.assertEqual(second, 1)

    def test_one_row_per_session_even_when_a_session_has_several_transcripts(self):
        """session_transcript is keyed by source_path, and 2,367 rows carry 2,361 distinct
        session ids -- a session CAN appear twice. dim_session is keyed by session_id, so the
        derivation must collapse them rather than raise."""
        self.addTranscript("s5", "/tmp/s5-a.jsonl")
        self.addTranscript("s5", "/tmp/s5-b.jsonl")
        self.conn.commit()
        session_pull.refresh_dim_session(self.conn)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM dim_session WHERE session_id = 's5'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
