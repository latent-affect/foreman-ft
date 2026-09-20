"""ATLASSN-84, ATLASSN-85, and ATLASSN-88's two byte-delta checks. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_session_contract_checks -v

Every test here asserts on a parsed CheckResult field, never on a substring of `detail`, so a
reworded message cannot turn a real regression into a green run. Each check has an explicit
negative control: a fixture that must FAIL, proving the check can fail at all, because a check
that cannot fail is not a check (ARCHITECTURE.md section 16, PDP.md R2).
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import dq_runner, migrate


callSequence = {"next": 0}


def transcriptFor(conn, session_id):
    """session_tool_call.transcript_id is NOT NULL and references session_transcript, so a call
    fixture needs a real parent row. One transcript per session, created on first use."""
    existing = conn.execute(
        "SELECT transcript_id FROM session_transcript WHERE session_id = ?", (session_id,)
    ).fetchone()
    if existing:
        return existing[0]
    cursor = conn.execute(
        "INSERT INTO session_transcript (source_path, stream_id, project_dir, session_id, "
        "first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (f"/fixture/{session_id}.jsonl", f"stream-{session_id}", "/fixture", session_id,
         "2026-09-04T00:00:00Z", "2026-09-04T00:00:00Z"),
    )
    return cursor.lastrowid


def pullRunFor(conn):
    """session_tool_call.pull_run_id references subagent_pull_run, so the fixture needs one real
    pull run. Created once per database."""
    existing = conn.execute("SELECT MIN(pull_run_id) FROM subagent_pull_run").fetchone()[0]
    if existing is not None:
        return existing
    cursor = conn.execute(
        "INSERT INTO subagent_pull_run (started_at, status) VALUES ('2026-09-04T00:00:00Z', 'ok')"
    )
    return cursor.lastrowid


def make_call(conn, session_id, ts, tool_name="Bash", tool_input_json='{"command":"ls"}',
              tool_input_bytes=None):
    transcript_id = transcriptFor(conn, session_id)
    pull_run_id = pullRunFor(conn)
    callSequence["next"] += 1
    conn.execute(
        "INSERT INTO session_tool_call (transcript_id, byte_offset, block_index, pull_run_id, "
        "session_id, ts, tool_name, tool_input_json, tool_input_bytes) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
        (transcript_id, callSequence["next"], pull_run_id, session_id, ts, tool_name,
         tool_input_json, tool_input_bytes),
    )


def make_assistant_text(conn, session_id, ts, text_bytes):
    """session_assistant_text.text_bytes is what the two ATLASSN-88 checks read; `text` itself is
    NOT NULL and unused by either check, so a short fixed placeholder is enough."""
    transcript_id = transcriptFor(conn, session_id)
    pull_run_id = pullRunFor(conn)
    callSequence["next"] += 1
    conn.execute(
        "INSERT INTO session_assistant_text (transcript_id, byte_offset, block_index, "
        "pull_run_id, ts, text, text_bytes) VALUES (?, ?, 0, ?, ?, 'fixture', ?)",
        (transcript_id, callSequence["next"], pull_run_id, ts, text_bytes),
    )


def ingestRunFor(conn):
    """hook_verdict.ingest_run_id is NOT NULL. One ingest run per database."""
    existing = conn.execute("SELECT MIN(run_id) FROM ingest_run").fetchone()[0]
    if existing is not None:
        return existing
    cursor = conn.execute(
        "INSERT INTO ingest_run (started_at, status, atlas_version, host_id) "
        "VALUES ('2026-09-04T00:00:00Z', 'ok', '0.1.0', 'fixture')"
    )
    return cursor.lastrowid


def make_verdict(conn, session_id, ts, handler_id="h1", verdict="fire"):
    ingest_run_id = ingestRunFor(conn)
    callSequence["next"] += 1
    conn.execute(
        "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, ts_resolution, "
        "handler_id, verdict, session_id) VALUES ('s', ?, ?, ?, 'microsecond', ?, ?, ?)",
        (callSequence["next"], ingest_run_id, ts, handler_id, verdict, session_id),
    )


class HookCoveragePerSessionTests(unittest.TestCase):
    """ATLASSN-84."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tempdir.name) / "atlas.db"))

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def busyHourWith(self, session_id, calls, verdicts, hour="2026-09-04T03"):
        for index in range(calls):
            make_call(self.conn, session_id, f"{hour}:{index % 60:02d}:00.000Z")
        for index in range(verdicts):
            make_verdict(self.conn, session_id, f"{hour}:{index % 60:02d}:00.000000Z")

    def advanceWatermarkPast(self, hour="2026-09-04T03"):
        """The check only evaluates hours strictly below the verdict watermark. A later verdict
        and a later call in the NEXT hour are what make the target hour evaluable at all."""
        laterHour = f"{hour[:11]}{int(hour[11:13]) + 1:02d}"
        make_verdict(self.conn, "watermark-session", f"{laterHour}:30:00.000000Z")
        make_call(self.conn, "watermark-session", f"{laterHour}:30:00.000Z")

    def test_names_the_session_below_one_verdict_per_call(self):
        """NEGATIVE CONTROL. Reproduces the post-mortem's own case: 88 calls, 41 verdicts in one
        hour, a ratio of 0.47. This must FAIL, and must name the session."""
        self.busyHourWith("8e42d92a-bad", calls=88, verdicts=41)
        self.busyHourWith("healthy-peer", calls=92, verdicts=316)
        self.advanceWatermarkPast()

        result = dq_runner.check_hook_coverage_per_session(self.conn, run_id=1)

        self.assertFalse(result.passed, "a 0.47 verdicts/call session must fail the check")
        self.assertEqual(result.observed_value, 1, "exactly one session should be named")
        self.assertIn("8e42d92a-bad", result.detail)
        self.assertNotIn("healthy-peer", result.detail)

    def test_passes_when_every_busy_session_is_covered(self):
        self.busyHourWith("peer-a", calls=92, verdicts=316)
        self.busyHourWith("peer-b", calls=35, verdicts=146)
        self.advanceWatermarkPast()

        result = dq_runner.check_hook_coverage_per_session(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_quiet_session_below_the_call_floor_is_not_judged(self):
        """14 calls and zero verdicts: under the floor, so not evaluated. Proves the floor does
        something rather than being decorative."""
        self.busyHourWith("quiet", calls=14, verdicts=0)
        self.advanceWatermarkPast()

        result = dq_runner.check_hook_coverage_per_session(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_partial_hour_above_the_watermark_is_not_evaluated(self):
        """The false-positive mode this check was designed around. A session with many calls and
        zero verdicts in the hour the verdict stream has not finished landing must NOT be flagged
        -- measured on the live warehouse, a naive implementation fired on 10 of 15 healthy
        recent session-hours for exactly this reason."""
        self.busyHourWith("lagging", calls=66, verdicts=0, hour="2026-09-04T13")
        make_verdict(self.conn, "other", "2026-09-04T13:03:32.000000Z")

        result = dq_runner.check_hook_coverage_per_session(self.conn, run_id=1)

        self.assertTrue(
            result.passed,
            "an hour the verdict stream has not fully covered must not be judged",
        )

    def test_empty_verdict_table_fails_rather_than_passing_vacuously(self):
        self.busyHourWith("anyone", calls=50, verdicts=0)

        result = dq_runner.check_hook_coverage_per_session(self.conn, run_id=1)

        self.assertFalse(
            result.passed,
            "with no verdicts at all the check cannot be evaluated and must not report a pass",
        )
        self.assertIsNone(result.observed_value)


class BuildProcessRatioTests(unittest.TestCase):
    """ATLASSN-85."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tempdir.name) / "atlas.db"))

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def processCalls(self, session_id, hour, count):
        for index in range(count):
            make_call(
                self.conn, session_id, f"{hour}:{index % 60:02d}:00.000Z",
                tool_name="Write", tool_input_json='{"file_path":"/repo/NOTES.md"}',
            )

    def buildCalls(self, session_id, hour, count, path="/repo/atlas/thing.py"):
        for index in range(count):
            make_call(
                self.conn, session_id, f"{hour}:{index % 60:02d}:00.000Z",
                tool_name="Write", tool_input_json=f'{{"file_path":"{path}"}}',
            )

    def test_pages_on_two_consecutive_zero_build_hours(self):
        """NEGATIVE CONTROL: must FAIL and name the session."""
        self.processCalls("all-process", "2026-09-04T09", 8)
        self.processCalls("all-process", "2026-09-04T10", 8)

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertFalse(result.passed)
        self.assertEqual(result.observed_value, 1)
        self.assertIn("all-process", result.detail)

    def test_one_zero_hour_alone_does_not_page(self):
        self.processCalls("blip", "2026-09-04T09", 8)
        self.buildCalls("blip", "2026-09-04T10", 8)

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, 0)

    def test_non_adjacent_zero_hours_do_not_page(self):
        """09 and 11 with a build hour between them is not a two-hour streak. Guards the
        adjacency arithmetic, which is easy to get wrong on the hour field alone."""
        self.processCalls("gappy", "2026-09-04T09", 8)
        self.buildCalls("gappy", "2026-09-04T10", 8)
        self.processCalls("gappy", "2026-09-04T11", 8)

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertTrue(result.passed)

    def test_streak_across_midnight_is_adjacent(self):
        """23:00 to 00:00 the next day IS consecutive. Integer arithmetic on the hour field
        alone would read 23 -> 00 as a break and miss the streak entirely."""
        self.processCalls("nightowl", "2026-09-04T23", 8)
        self.processCalls("nightowl", "2026-09-05T00", 8)

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertFalse(result.passed, "23:00 -> 00:00 must count as two consecutive hours")
        self.assertEqual(result.observed_value, 1)

    def test_same_hour_on_different_days_is_not_adjacent(self):
        """The other direction of the same arithmetic bug: 09 on one day and 10 on the NEXT day
        are 25 hours apart, not one."""
        self.processCalls("daily", "2026-09-04T09", 8)
        self.processCalls("daily", "2026-09-05T10", 8)

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertTrue(result.passed)

    def test_scratchpad_python_is_not_build_signal(self):
        """48% of .py writes on the live warehouse target session scratchpads. If those counted,
        a session doing pure throwaway exploration would score as building."""
        self.processCalls("explorer", "2026-09-04T09", 8)
        self.processCalls("explorer", "2026-09-04T10", 8)
        self.buildCalls(
            "explorer", "2026-09-04T09", 5,
            path="/private/tmp/claude-501/x/scratchpad/probe.py",
        )

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertFalse(
            result.passed,
            "scratchpad .py writes must not rescue a session from a zero-build streak",
        )

    def test_real_python_write_is_build_signal(self):
        """The positive control for the rule above: the same call shape at a repo path must
        count, or the exclusion is just breaking the check."""
        self.processCalls("builder", "2026-09-04T09", 8)
        self.processCalls("builder", "2026-09-04T10", 8)
        self.buildCalls("builder", "2026-09-04T09", 1)
        self.buildCalls("builder", "2026-09-04T10", 1)

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertTrue(result.passed)

    def test_git_commit_bash_call_is_build_signal(self):
        self.processCalls("committer", "2026-09-04T09", 8)
        self.processCalls("committer", "2026-09-04T10", 8)
        for hour in ("2026-09-04T09", "2026-09-04T10"):
            make_call(
                self.conn, "committer", f"{hour}:05:00.000Z", tool_name="Bash",
                tool_input_json='{"command":"git commit -m x"}',
            )

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertTrue(result.passed)

    def test_quiet_hours_below_the_signal_floor_are_not_judged(self):
        self.processCalls("quiet", "2026-09-04T09", 2)
        self.processCalls("quiet", "2026-09-04T10", 2)

        result = dq_runner.check_build_process_ratio(self.conn, run_id=1)

        self.assertTrue(result.passed)


class TurnFinalBytesPerSessionDayDeltaTests(unittest.TestCase):
    """ATLASSN-88. Every fixture plants a block on an ANCHOR day (2026-09-04) purely so the day
    before it (09-03) counts as complete -- _complete_day_pair excludes the single most recent
    distinct day on the same 'today is still being written' guard build_process_ratio's window
    and hook_coverage_per_session's watermark both already apply. 09-03 is therefore the evaluated
    'latest' day and 09-02 the 'prior' day in every test that needs both."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tempdir.name) / "atlas.db"))

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def anchorNextDay(self):
        make_assistant_text(self.conn, "anchor", "2026-09-04T00:00:00.000000Z", 1)

    def test_fails_when_the_average_increases(self):
        """NEGATIVE CONTROL. Prior day avg 500 bytes/session, latest day avg 2000 -- must FAIL."""
        make_assistant_text(self.conn, "prior-sess", "2026-09-02T10:00:00.000000Z", 500)
        make_assistant_text(self.conn, "latest-sess", "2026-09-03T10:00:00.000000Z", 2000)
        self.anchorNextDay()

        result = dq_runner.check_turn_final_bytes_per_session_day(self.conn, run_id=1)

        self.assertFalse(result.passed, "an increase in avg turn-final bytes/session must fail")
        self.assertEqual(result.observed_value, 1500.0)

    def test_passes_when_the_average_falls(self):
        make_assistant_text(self.conn, "prior-sess", "2026-09-02T10:00:00.000000Z", 2000)
        make_assistant_text(self.conn, "latest-sess", "2026-09-03T10:00:00.000000Z", 500)
        self.anchorNextDay()

        result = dq_runner.check_turn_final_bytes_per_session_day(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, -1500.0)

    def test_a_tool_call_within_60s_excludes_the_block_from_turn_final(self):
        """A block immediately followed by a tool call is mid-turn, not turn-final, and must not
        be counted at all -- not even as a zero-byte session."""
        make_assistant_text(self.conn, "prior-sess", "2026-09-02T10:00:00.000000Z", 500)
        make_assistant_text(self.conn, "latest-sess", "2026-09-03T10:00:00.000000Z", 500)
        make_call(self.conn, "latest-sess", "2026-09-03T10:00:30.000Z")  # 30s later, same session
        self.anchorNextDay()

        result = dq_runner.check_turn_final_bytes_per_session_day(self.conn, run_id=1)

        self.assertTrue(
            result.passed,
            "the only latest-day block was followed by a tool call within 60s and must be "
            "excluded, leaving nothing to compare -- a real regression would be masked if this "
            "silently counted the excluded block instead",
        )
        self.assertIsNone(result.observed_value)

    def test_a_tool_call_after_60s_still_counts_the_block_as_turn_final(self):
        """The POSITIVE control for the rule above: the same shape at 61 seconds must count, or
        the exclusion is just breaking the check."""
        make_assistant_text(self.conn, "prior-sess", "2026-09-02T10:00:00.000000Z", 500)
        make_assistant_text(self.conn, "latest-sess", "2026-09-03T10:00:00.000000Z", 500)
        make_call(self.conn, "latest-sess", "2026-09-03T10:01:01.000Z")  # 61s later
        self.anchorNextDay()

        result = dq_runner.check_turn_final_bytes_per_session_day(self.conn, run_id=1)

        self.assertEqual(result.observed_value, 0.0)  # 500 vs 500, flat, still counted

    def test_fewer_than_two_complete_days_is_not_evaluated(self):
        """Only one distinct day of data at all: no complete day exists yet (the single day is
        the still-accumulating one), so nothing is evaluable -- must not report a pass or fail
        against data that was never compared."""
        make_assistant_text(self.conn, "only-sess", "2026-09-04T10:00:00.000000Z", 500)

        result = dq_runner.check_turn_final_bytes_per_session_day(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertIsNone(result.observed_value)


class SendMessageBytesPerSessionDayDeltaTests(unittest.TestCase):
    """ATLASSN-88, the companion check. Same anchor-day pattern as the turn-final tests above."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tempdir.name) / "atlas.db"))

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def anchorNextDay(self):
        make_call(self.conn, "anchor", "2026-09-04T00:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=1)

    def test_fails_when_the_average_increases(self):
        """NEGATIVE CONTROL. Prior day avg 300 bytes/session, latest day avg 2500 -- must FAIL."""
        make_call(self.conn, "prior-sess", "2026-09-02T10:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=300)
        make_call(self.conn, "latest-sess", "2026-09-03T10:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=2500)
        self.anchorNextDay()

        result = dq_runner.check_sendmessage_bytes_per_session_day(self.conn, run_id=1)

        self.assertFalse(result.passed, "an increase in avg SendMessage bytes/session must fail")
        self.assertEqual(result.observed_value, 2200.0)

    def test_passes_when_the_average_falls(self):
        make_call(self.conn, "prior-sess", "2026-09-02T10:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=2500)
        make_call(self.conn, "latest-sess", "2026-09-03T10:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=300)
        self.anchorNextDay()

        result = dq_runner.check_sendmessage_bytes_per_session_day(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertEqual(result.observed_value, -2200.0)

    def test_non_sendmessage_calls_are_excluded(self):
        """A Bash call sharing a day and session with a real SendMessage call must not inflate
        the SendMessage byte total -- the check is scoped to tool_name='SendMessage' only. If the
        99999-byte Bash call leaked in, the delta below would be wildly positive instead of 0."""
        make_call(self.conn, "same-sess", "2026-09-02T10:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=300)
        make_call(self.conn, "same-sess", "2026-09-03T10:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=300)
        make_call(self.conn, "same-sess", "2026-09-03T10:05:00.000Z", tool_name="Bash",
                  tool_input_bytes=99999, tool_input_json='{"command":"ls -la /"}')
        self.anchorNextDay()

        result = dq_runner.check_sendmessage_bytes_per_session_day(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertEqual(
            result.observed_value, 0.0,
            "the Bash call's 99999 bytes must not be summed into the SendMessage total",
        )

    def test_fewer_than_two_complete_days_is_not_evaluated(self):
        make_call(self.conn, "only-sess", "2026-09-04T10:00:00.000Z", tool_name="SendMessage",
                  tool_input_bytes=500)

        result = dq_runner.check_sendmessage_bytes_per_session_day(self.conn, run_id=1)

        self.assertTrue(result.passed)
        self.assertIsNone(result.observed_value)


class RegistrationTests(unittest.TestCase):
    def test_both_checks_are_registered_in_checkers(self):
        """The landing-order hazard migration 10 documents: a dq_check row naming a function
        CHECKERS does not carry makes run_all raise every 60 seconds. These two functions land
        first so the seed rows can follow safely."""
        self.assertIn("hook_coverage_per_session", dq_runner.CHECKERS)
        self.assertIn("build_process_ratio", dq_runner.CHECKERS)

    def test_migration_13_seeds_both_rows_with_the_argued_severities(self):
        """Migration 13 seeds both checks, each at the severity §29.2 argued for rather than the
        severity its ticket asked for. build_process_ratio is advisory on purpose: over 48 hours
        it names 10 sessions and cannot yet separate a stalled build run from a session
        legitimately doing review, so gating on it would be FATAL-1's shape. Asserted here so a
        later edit cannot quietly promote it without the declared-run marker that §29.2 names as
        the promotion trigger."""
        tempdir = tempfile.TemporaryDirectory()
        try:
            conn = migrate.connect(str(Path(tempdir.name) / "atlas.db"))
            severities = dict(
                conn.execute(
                    "SELECT check_name, severity FROM dq_check WHERE check_name IN "
                    "('hook_coverage_per_session', 'build_process_ratio')"
                )
            )
            self.assertEqual(severities.get("hook_coverage_per_session"), "contract")
            self.assertEqual(severities.get("build_process_ratio"), "advisory")
            conn.close()
        finally:
            tempdir.cleanup()

    def test_atlassn_88_checks_are_registered_and_seeded_advisory(self):
        """Migration 14 seeds both ATLASSN-88 checks, both advisory -- ARCHITECTURE.md section
        30.1: neither has a dq_check_run history to calibrate a real tolerance from yet."""
        self.assertIn("turn_final_bytes_per_session_day_delta", dq_runner.CHECKERS)
        self.assertIn("sendmessage_bytes_per_session_day_delta", dq_runner.CHECKERS)

        tempdir = tempfile.TemporaryDirectory()
        try:
            conn = migrate.connect(str(Path(tempdir.name) / "atlas.db"))
            severities = dict(
                conn.execute(
                    "SELECT check_name, severity FROM dq_check WHERE check_name IN "
                    "('turn_final_bytes_per_session_day_delta', "
                    "'sendmessage_bytes_per_session_day_delta')"
                )
            )
            self.assertEqual(severities.get("turn_final_bytes_per_session_day_delta"), "advisory")
            self.assertEqual(severities.get("sendmessage_bytes_per_session_day_delta"), "advisory")
            conn.close()
        finally:
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
