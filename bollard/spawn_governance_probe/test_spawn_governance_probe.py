"""FORE-698. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest hooks.spawn_governance_probe.test_spawn_governance_probe -v

Every fixture writes real JSONL lines into a real temp file, in the exact shape
hooks/verdict_ledger.py's record() actually produces (session_id, tool_use_id, handler_id, ts) --
read directly from that file this session, not assumed from memory of its general shape.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import spawn_governance_probe as sgp


def write_verdict_line(path, session_id=None, tool_use_id=None, handler_id="some_hook.py",
                       ts="2026-09-20T00:00:00.000000Z", verdict="silent"):
    row = {"ts": ts, "handler_id": handler_id, "verdict": verdict}
    if session_id is not None:
        row["session_id"] = session_id
    if tool_use_id is not None:
        row["tool_use_id"] = tool_use_id
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


class FakeClock:
    """Deterministic replacement for time.monotonic()/time.sleep(), so a timeout test costs
    nothing real and a "row appears after N polls" test doesn't depend on real wall-clock
    scheduling. `sleep` optionally runs a callback on a given poll number, e.g. to simulate a
    hook writing its verdict mid-poll."""

    def __init__(self, on_sleep=None):
        self.t = 0.0
        self.poll_count = 0
        self.on_sleep = on_sleep

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.poll_count += 1
        self.t += seconds
        if self.on_sleep is not None:
            self.on_sleep(self.poll_count)


class VerifyChildGovernedTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmpdir.name) / "verdicts.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_governed_when_probe_row_already_present(self):
        write_verdict_line(self.ledger_path, session_id="s1", tool_use_id="probe-1",
                           handler_id="write_gate.py", ts="2026-09-20T00:00:01Z")
        clock = FakeClock()
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=5.0, ledger_path=self.ledger_path,
            sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.GOVERNED)
        self.assertEqual(result.matched_handler_id, "write_gate.py")
        self.assertEqual(result.matched_ts, "2026-09-20T00:00:01Z")
        self.assertEqual(clock.poll_count, 0, "the row was already there -- no poll should occur")

    def test_governed_when_probe_row_appears_after_a_delay(self):
        """THE DISCRIMINATING CASE: a real hook fires a few polls into the wait, not before it
        starts and not never. Proves this is a genuine poll loop, not a check-once function."""
        clock = FakeClock()

        def append_on_third_poll(poll_count):
            if poll_count == 3:
                write_verdict_line(self.ledger_path, session_id="s1", tool_use_id="probe-1",
                                   handler_id="agent_dispatch_gate.py")

        clock.on_sleep = append_on_third_poll
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=5.0, ledger_path=self.ledger_path,
            poll_interval_s=0.1, sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.GOVERNED)
        self.assertEqual(result.matched_handler_id, "agent_dispatch_gate.py")
        self.assertEqual(clock.poll_count, 3)

    def test_ungoverned_on_timeout_with_only_unrelated_rows_present(self):
        write_verdict_line(self.ledger_path, session_id="other-session", tool_use_id="probe-1")
        write_verdict_line(self.ledger_path, session_id="s1", tool_use_id="unrelated-probe")
        clock = FakeClock()
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=1.0, ledger_path=self.ledger_path,
            poll_interval_s=0.1, sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.UNGOVERNED)
        self.assertIsNone(result.matched_handler_id)
        self.assertIn("s1", result.detail)
        self.assertIn("probe-1", result.detail)

    def test_session_id_match_alone_is_not_enough(self):
        """The row must match BOTH session_id and tool_use_id -- a hook firing for some OTHER
        real tool call in the same session does not prove the PROBE itself reached a live hook,
        only that the session existed."""
        write_verdict_line(self.ledger_path, session_id="s1", tool_use_id="some-other-call")
        clock = FakeClock()
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=0.3, ledger_path=self.ledger_path,
            poll_interval_s=0.1, sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.UNGOVERNED)

    def test_tool_use_id_match_alone_is_not_enough(self):
        """The converse -- a colliding tool_use_id from a DIFFERENT session must not read as
        this child's own hooks being live."""
        write_verdict_line(self.ledger_path, session_id="other-session", tool_use_id="probe-1")
        clock = FakeClock()
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=0.3, ledger_path=self.ledger_path,
            poll_interval_s=0.1, sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.UNGOVERNED)

    def test_ungoverned_when_ledger_file_never_exists_within_the_window(self):
        """No hook has EVER fired on this machine yet -- the ledger file itself does not exist.
        Must not raise, and must not be silently treated as GOVERNED."""
        missing_path = Path(self.tmpdir.name) / "does-not-exist.jsonl"
        clock = FakeClock()
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=0.3, ledger_path=missing_path,
            poll_interval_s=0.1, sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.UNGOVERNED)

    def test_ledger_created_mid_poll_is_still_found(self):
        """The file legitimately does not exist yet when polling starts (this machine's very
        first hook write), then a real hook creates it. Must not require the file to have
        existed before verify_child_governed was called."""
        clock = FakeClock()

        def create_on_second_poll(poll_count):
            if poll_count == 2:
                write_verdict_line(self.ledger_path, session_id="s1", tool_use_id="probe-1")

        clock.on_sleep = create_on_second_poll
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=5.0, ledger_path=self.ledger_path,
            poll_interval_s=0.1, sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.GOVERNED)

    def test_malformed_json_line_does_not_crash_and_is_skipped(self):
        """A concurrent hook process's own fallback marker (verdict_ledger.py's
        _write_fallback_marker) or a torn concurrent write can leave a line this function does
        not need to understand -- it must skip past it and keep scanning, not raise."""
        with open(self.ledger_path, "a", encoding="utf-8") as fh:
            fh.write("{ this is not valid json }\n")
        write_verdict_line(self.ledger_path, session_id="s1", tool_use_id="probe-1")
        clock = FakeClock()
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=1.0, ledger_path=self.ledger_path,
            sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.GOVERNED)

    def test_a_partial_trailing_line_is_not_consumed_and_is_picked_up_once_complete(self):
        """A hook process mid-write when a poll happens to land -- the partial line must not be
        treated as a (failed) parse and must not advance past it; the next poll, once the write
        completes, must still find it."""
        with open(self.ledger_path, "a", encoding="utf-8") as fh:
            fh.write('{"session_id": "s1", "tool_use_id": "probe-1"')  # no closing brace, no \n
        clock = FakeClock()

        def complete_the_line_on_second_poll(poll_count):
            if poll_count == 2:
                with open(self.ledger_path, "a", encoding="utf-8") as fh:
                    fh.write(', "handler_id": "x.py", "ts": "2026-09-20T00:00:00Z"}\n')

        clock.on_sleep = complete_the_line_on_second_poll
        result = sgp.verify_child_governed(
            "s1", "probe-1", timeout_s=5.0, ledger_path=self.ledger_path,
            poll_interval_s=0.1, sleep=clock.sleep, now=clock.now,
        )
        self.assertEqual(result.status, sgp.GOVERNED)

    def test_ledger_unavailable_on_a_real_permission_error(self):
        """A store-level fault, distinct from UNGOVERNED's 'looked and found nothing': the check
        itself could not run. Forces a REAL PermissionError -- an existing file with its read
        bit removed -- not a hand-constructed exception."""
        self.ledger_path.write_text('{"session_id": "s1", "tool_use_id": "probe-1"}\n')
        os.chmod(self.ledger_path, 0o000)
        try:
            clock = FakeClock()
            result = sgp.verify_child_governed(
                "s1", "probe-1", timeout_s=1.0, ledger_path=self.ledger_path,
                sleep=clock.sleep, now=clock.now,
            )
            self.assertEqual(result.status, sgp.LEDGER_UNAVAILABLE)
        finally:
            os.chmod(self.ledger_path, 0o600)


if __name__ == "__main__":
    unittest.main()
