"""Regression tests for hook_common.py's run() entry point. No test file existed for this
module before this change, despite it being the most shared, most load-bearing module in the
codebase -- a real, separate, disclosed gap this file only partially closes (scoped to the
exited-without-recording fallback path this specific fix touches, not full coverage).

Real, live bug this covers (ATLASSN-31, 2026-08-27): a hook whose main_fn exits via SystemExit
(a configured timeout, or a stray sys.exit() call) hit run()'s "exited-without-recording"
fallback, which wrote a verdict_ledger "error" row but never the matching audit-plane
HOOK_ERROR entry run_body()'s own except clause writes for an ordinary Exception -- a real,
one-sided gap confirmed against nested_agent_notification_guard.py's own production data (3
unpaired error rows, all handler_id='nested_agent_notification_guard.py').

    python3 -m unittest test_hook_common -v
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc


class RunFallbackPathTests(unittest.TestCase):
    """Covers run()'s three real exit shapes: normal recorded success, an ordinary Exception
    (already wrote HOOK_ERROR before this fix -- regression-tested here), and SystemExit/
    timeout (the real gap this fix closes)."""

    def setUp(self):
        hc.RECORDED = False
        self.addCleanup(setattr, hc, "RECORDED", False)
        self.stdin_patcher = mock.patch.object(sys, "stdin", io.StringIO(json.dumps(
            {"session_id": "s1", "cwd": "/tmp/req-test"})))
        self.stdin_patcher.start()
        self.addCleanup(self.stdin_patcher.stop)
        self.audit_calls = []
        self.audit_patcher = mock.patch.object(
            hc.audit_lib, "audit_append",
            side_effect=lambda event_type, **kw: self.audit_calls.append((event_type, kw)))
        self.audit_patcher.start()
        self.addCleanup(self.audit_patcher.stop)
        self.verdict_calls = []
        self.verdict_patcher = mock.patch.object(
            hc.verdict_ledger, "record",
            side_effect=lambda data, verdict, **kw: self.verdict_calls.append((verdict, kw)))
        self.verdict_patcher.start()
        self.addCleanup(self.verdict_patcher.stop)
        # run() writes to real stdout via _emit()/print() paths in some branches -- silence it
        # so test output stays readable, without affecting the calls under test.
        self.stdout_patcher = mock.patch.object(sys, "stdout", io.StringIO())
        self.stdout_patcher.start()
        self.addCleanup(self.stdout_patcher.stop)

    def test_normal_success_records_once_no_hook_error(self):
        def main_fn(data):
            hc.passthrough()  # a real, do-nothing decision -- run_body auto-records "silent"

        with self.assertRaises(SystemExit):
            hc.run(main_fn)
        self.assertEqual(len(self.verdict_calls), 1)
        self.assertEqual(self.audit_calls, [], "a clean run must not write any HOOK_ERROR")

    def test_ordinary_exception_writes_hook_error_exactly_once(self):
        """Pre-existing behavior (run_body's own except clause) -- regression-tested so this
        fix's new fallback-path write doesn't accidentally double it."""
        def main_fn(data):
            raise ValueError("boom")

        with self.assertRaises(SystemExit):
            hc.run(main_fn)
        self.assertEqual(len(self.audit_calls), 1)
        self.assertEqual(self.audit_calls[0][0], "HOOK_ERROR")
        self.assertIn("ValueError", self.audit_calls[0][1]["error"])
        # run_body's except path also records the ledger verdict, and the outer finally must
        # see RECORDED=True from that and NOT fire the fallback's own separate write again.
        self.assertEqual(len(self.verdict_calls), 1)

    def test_systemexit_now_writes_hook_error_the_real_fix(self):
        """The real, live gap this change closes: a SystemExit (timeout, or a stray sys.exit()
        inside main_fn) used to reach the ledger's 'error'/'exited-without-recording' row with
        NO matching audit-plane HOOK_ERROR -- exactly ATLASSN-31's confirmed production shape."""
        def main_fn(data):
            sys.exit(1)

        with self.assertRaises(SystemExit):
            hc.run(main_fn)
        self.assertEqual(len(self.audit_calls), 1,
                          "the real fix: SystemExit must now also write a HOOK_ERROR")
        self.assertEqual(self.audit_calls[0][0], "HOOK_ERROR")
        self.assertEqual(self.audit_calls[0][1]["session_id"], "s1")
        self.assertEqual(self.audit_calls[0][1]["cwd"], "/tmp/req-test")
        self.assertIn("exited-without-recording", self.audit_calls[0][1]["error"])
        self.assertEqual(len(self.verdict_calls), 1)
        self.assertEqual(self.verdict_calls[0][0], "error")
        self.assertEqual(self.verdict_calls[0][1].get("kind"), "exited-without-recording")

    def test_keyboardinterrupt_also_writes_hook_error(self):
        """Same fallback path, different real trigger -- must not be special-cased to only
        SystemExit."""
        def main_fn(data):
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            hc.run(main_fn)
        self.assertEqual(len(self.audit_calls), 1)
        self.assertEqual(self.audit_calls[0][0], "HOOK_ERROR")

    def test_audit_append_failure_on_fallback_path_does_not_crash_the_hook(self):
        """The instrument must never be able to take down the guard it measures -- if the
        audit-plane write itself fails, run() must still complete (exit 0-equivalent via
        SystemExit), matching this module's own fail-open discipline everywhere else."""
        self.audit_patcher.stop()
        self.audit_patcher = mock.patch.object(
            hc.audit_lib, "audit_append", side_effect=RuntimeError("disk full"))
        self.audit_patcher.start()

        def main_fn(data):
            sys.exit(1)

        with self.assertRaises(SystemExit):
            hc.run(main_fn)  # must not raise RuntimeError -- proves the try/except is real
        self.assertEqual(len(self.verdict_calls), 1)


class DenyReasonReachesLedgerTests(unittest.TestCase):
    """FORE-189: hc.deny(reason) must make that reason text land on the ledger row, not just in
    the stdout envelope the harness reads -- the whole point is a deny's remedy becoming a
    queryable ledger field instead of something recoverable only from a transcript join."""

    def setUp(self):
        # A real hook is a fresh Python process, so these module globals start None/False every
        # time. This test file shares one process with the rest of the suite under `discover`,
        # so a prior test module's hc.stolen()/mark() call can leave state behind here that no
        # real invocation would ever see -- reset the full set a real process would start with,
        # not just the two this class happens to touch.
        for name, default in (("RECORDED", False), ("EMITTED_REASON", None),
                              ("EMITTED_KIND", None), ("EMITTED_DECISION", None),
                              ("EMITTED_RULE_ID", None), ("STOLEN_TARGET", None),
                              ("INPUT_UNPARSEABLE", False)):
            setattr(hc, name, default)
            self.addCleanup(setattr, hc, name, default)
        self.stdin_patcher = mock.patch.object(sys, "stdin", io.StringIO(json.dumps(
            {"session_id": "s1", "cwd": "/tmp/req-test"})))
        self.stdin_patcher.start()
        self.addCleanup(self.stdin_patcher.stop)
        self.verdict_calls = []
        self.verdict_patcher = mock.patch.object(
            hc.verdict_ledger, "record",
            side_effect=lambda data, verdict, **kw: self.verdict_calls.append((verdict, kw)))
        self.verdict_patcher.start()
        self.addCleanup(self.verdict_patcher.stop)
        self.stdout_patcher = mock.patch.object(sys, "stdout", io.StringIO())
        self.stdout_patcher.start()
        self.addCleanup(self.stdout_patcher.stop)

    def test_real_deny_call_carries_its_reason_to_the_ledger(self):
        def main_fn(data):
            hc.deny("Named remedy: drop the --force flag and retry.")

        with self.assertRaises(SystemExit):
            hc.run(main_fn)
        self.assertEqual(len(self.verdict_calls), 1)
        verdict, kw = self.verdict_calls[0]
        self.assertEqual(verdict, "fire")
        self.assertEqual(kw.get("decision"), "deny")
        self.assertEqual(kw.get("reason"), "Named remedy: drop the --force flag and retry.")

    def test_a_silent_hook_carries_no_reason(self):
        """Negative control: a hook that never calls deny() must not somehow leak a stale
        EMITTED_REASON from a prior process -- proves this is read fresh, not defaulted true."""
        def main_fn(data):
            hc.passthrough()

        with self.assertRaises(SystemExit):
            hc.run(main_fn)
        self.assertEqual(len(self.verdict_calls), 1)
        verdict, kw = self.verdict_calls[0]
        self.assertEqual(verdict, "silent")
        self.assertIsNone(kw.get("reason"))


class RewriteEmitsAllowWithUpdatedInputTests(unittest.TestCase):
    """FORE-660 (Stage 3): hc.rewrite() is new -- no prior test existed for it (it didn't
    exist before this proposal). Proves the real stdout JSON shape the harness actually
    consumes, not just that the function runs without raising."""

    def setUp(self):
        for name, default in (("EMITTED_KIND", None), ("EMITTED_DECISION", None),
                               ("EMITTED_RULE_ID", None)):
            setattr(hc, name, default)
            self.addCleanup(setattr, hc, name, default)
        self.stdout_patcher = mock.patch.object(sys, "stdout", io.StringIO())
        self.stdout = self.stdout_patcher.start()
        self.addCleanup(self.stdout_patcher.stop)

    def test_emits_allow_with_the_full_updated_input(self):
        hc.rewrite({"subagent_type": "Explore", "prompt": "look into the auth module"})
        emitted = json.loads(self.stdout.getvalue())
        hso = emitted["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "allow")
        self.assertEqual(hso["updatedInput"],
                          {"subagent_type": "Explore", "prompt": "look into the auth module"})
        self.assertNotIn("permissionDecisionReason", hso)

    def test_reason_is_included_only_when_given(self):
        hc.rewrite({"command": "echo hi"}, reason="substituted for X")
        emitted = json.loads(self.stdout.getvalue())
        self.assertEqual(emitted["hookSpecificOutput"]["permissionDecisionReason"],
                          "substituted for X")

    def test_marks_rewrite_kind_and_allow_decision(self):
        hc.rewrite({"x": 1})
        self.assertEqual(hc.EMITTED_KIND, "rewrite")
        self.assertEqual(hc.EMITTED_DECISION, "allow")

    def test_does_not_mutate_the_caller_supplied_dict_shape_unexpectedly(self):
        """Non-regression control: rewrite() must pass updated_input through as-is (the exact
        object/content the caller built), never silently drop or rename a key."""
        original = {"subagent_type": "Explore", "prompt": "x", "description": "y"}
        hc.rewrite(dict(original))
        emitted = json.loads(self.stdout.getvalue())
        self.assertEqual(emitted["hookSpecificOutput"]["updatedInput"], original)


class OriginCwdTests(unittest.TestCase):
    """FORE-562. Fixtures mirror the REAL record-type distribution
    FINDING-FORE-562-CWD-DRIFT-MEASURED-20260913.md measured across 1,673 real transcripts
    (queue-operation, last-prompt, mode, fork-context-ref opening records with no cwd, then a
    real record that carries one) -- realistic shapes, not real transcript content (this
    machine's real transcripts carry real conversation text, which does not belong in a
    committed test fixture). A read-only empirical spot-check against 60 real transcript files
    on this machine, done separately and not committed, found origin_cwd resolving all 60
    within the 40-line bound, corroborating the finding doc's own population measurement."""

    def _write_transcript(self, lines):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False,
                                          encoding="utf-8")
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        for line in lines:
            tmp.write(json.dumps(line) + "\n")
        tmp.close()
        return tmp.name

    def test_no_transcript_path_returns_none(self):
        self.assertIsNone(hc.origin_cwd({}))
        self.assertIsNone(hc.origin_cwd({"transcript_path": None}))
        self.assertIsNone(hc.origin_cwd({"transcript_path": ""}))

    def test_non_dict_payload_returns_none(self):
        self.assertIsNone(hc.origin_cwd(["not", "a", "dict"]))
        self.assertIsNone(hc.origin_cwd(None))

    def test_nonexistent_file_returns_none(self):
        self.assertIsNone(hc.origin_cwd({"transcript_path": "/does/not/exist/anywhere.jsonl"}))

    def test_cwd_on_line_1_is_found_immediately(self):
        path = self._write_transcript([{"cwd": "/Users/m5/dev/proj-a", "type": "user"}])
        self.assertEqual(hc.origin_cwd({"transcript_path": path}), "/Users/m5/dev/proj-a")

    def test_realistic_metadata_prefix_then_cwd_is_found(self):
        """The real, common shape (69% of real transcripts per the finding doc): the opening
        records are harness metadata with no cwd at all, and the first cwd-bearing record
        shows up a few lines in."""
        path = self._write_transcript([
            {"type": "queue-operation", "op": "enqueue"},
            {"type": "last-prompt", "prompt": "..."},
            {"type": "mode", "mode": "default"},
            {"cwd": "/Users/m5/dev/proj-b", "type": "user"},
        ])
        self.assertEqual(hc.origin_cwd({"transcript_path": path}), "/Users/m5/dev/proj-b")

    def test_cwd_beyond_the_bound_is_not_found_fails_closed(self):
        lines = [{"type": "queue-operation"}] * 45 + [{"cwd": "/too/late"}]
        path = self._write_transcript(lines)
        self.assertIsNone(hc.origin_cwd({"transcript_path": path}, max_lines=40))

    def test_cwd_just_inside_the_bound_is_found(self):
        lines = [{"type": "queue-operation"}] * 39 + [{"cwd": "/just/in/time"}]
        path = self._write_transcript(lines)
        self.assertEqual(hc.origin_cwd({"transcript_path": path}, max_lines=40), "/just/in/time")

    def test_no_cwd_anywhere_within_bound_fails_closed(self):
        """The negative control: a transcript with no cwd anywhere in the scanned window must
        return None, never fall back to any other value."""
        lines = [{"type": "queue-operation"}] * 40
        path = self._write_transcript(lines)
        self.assertIsNone(hc.origin_cwd({"transcript_path": path}, max_lines=40))

    def test_malformed_json_lines_are_skipped_not_fatal(self):
        path = self._write_transcript([])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("not valid json{\n")
            fh.write("\n")  # blank line
            fh.write(json.dumps({"cwd": "/after/garbage"}) + "\n")
        self.assertEqual(hc.origin_cwd({"transcript_path": path}), "/after/garbage")

    def test_never_falls_back_to_live_payload_cwd(self):
        """The whole point: a payload's own (possibly drifted) cwd must never leak through when
        the transcript itself has none -- that would silently defeat the fix."""
        path = self._write_transcript([{"type": "queue-operation"}])
        result = hc.origin_cwd({"transcript_path": path, "cwd": "/live/drifted/cwd"}, max_lines=1)
        self.assertIsNone(result)
        self.assertNotEqual(result, "/live/drifted/cwd")

    def test_empty_string_cwd_value_does_not_count_as_found(self):
        path = self._write_transcript([{"cwd": ""}, {"cwd": "/real/value"}])
        self.assertEqual(hc.origin_cwd({"transcript_path": path}), "/real/value")

    def test_non_string_cwd_value_does_not_count_as_found(self):
        path = self._write_transcript([{"cwd": 12345}, {"cwd": "/real/value"}])
        self.assertEqual(hc.origin_cwd({"transcript_path": path}), "/real/value")


if __name__ == "__main__":
    unittest.main()
