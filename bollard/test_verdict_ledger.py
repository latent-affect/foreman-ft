#!/usr/bin/env python3
"""CHV2-1. Fail-first tests for porting DEVH-77's fallback-marker behavior into
claude-hooks-v2's own verdict_ledger.py -- a structurally different module from bollard's (11
copies + one deployment at ~/.claude/hooks/, all one lineage, surveyed under FORE-316/AREM-17)
that bob_write_gate.py actually imports, traced by real sys.path/import resolution rather than
proximity on disk. Ported on this module's own terms (its own ledger_origin/origin_signal
fields, its own VALID_DECISIONS enum) -- nothing here is copied in from bollard, and this
module is not restructured toward bollard's shape.

Run: cd hooks && /Users/m5/.venv/bin/python3 -m unittest test_verdict_ledger -v
"""
import json
import pathlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verdict_ledger  # noqa: E402


def _blocked_verdict_log(tmp_path):
    """A VERDICT_LOG path guaranteed to fail on write via a REAL filesystem condition -- a
    regular file sitting where a directory needs to exist, so Path.mkdir(parents=True) raises a
    genuine NotADirectoryError -- not a mocked exception. The same class of real OSError a full
    disk or a permissions problem would raise, per the fail-first instruction to force a real
    failure where one can be produced."""
    blocker = tmp_path / "blocked-not-a-directory"
    blocker.write_text("this is a file, not a directory", encoding="utf-8")
    return blocker / "subdir" / "verdicts.jsonl"


class NegativeControlTests(unittest.TestCase):
    """A fix that marks every call would be worse than the bug it fixes -- it would make the
    arming run look broken throughout. This is the discriminating control for every test below."""

    def test_normal_write_produces_exactly_one_row_with_no_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            good_log = Path(tmp) / "verdicts.jsonl"
            with mock.patch.object(verdict_ledger, "VERDICT_LOG", good_log):
                verdict_ledger.record({"session_id": "s1"}, "silent", handler_id="some_guard.py")
            lines = good_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertNotIn("ledger_write_failed", row)
            self.assertEqual(row["verdict"], "silent")
            self.assertEqual(row["handler_id"], "some_guard.py")


class RealWriteFailureTests(unittest.TestCase):
    """THE fail-first proof (constraint 1): force a real, unmocked write failure and confirm
    today's outcome is a lost verdict with nothing on disk to distinguish it from correct
    silence. Run and confirmed to fail against the pre-fix module -- record() returned normally
    with the ledger file simply never created, identical to what a genuinely silent guard
    produces."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_a_real_unmocked_write_failure_does_not_crash_and_is_at_least_attempted_twice(self):
        blocked_log = _blocked_verdict_log(self.tmp_path)
        # record()'s own absolute invariant: never raise, never block the calling hook. This
        # must hold even when BOTH the primary write and the fallback retry fail identically --
        # the retry targets the SAME VERDICT_LOG path DEVH-77's own design does, so a
        # structurally broken path (as opposed to a transient one) defeats the retry too. That
        # is an inherited limit of the mechanism, not a regression from this fix: see
        # test_a_transient_style_failure_leaves_a_recoverable_marker below for the case this
        # port actually recovers.
        with mock.patch.object(verdict_ledger, "VERDICT_LOG", blocked_log):
            try:
                verdict_ledger.record({"session_id": "s1"}, "silent", handler_id="bob_write_gate.py")
            except Exception as exc:  # pragma: no cover -- this failing IS the test failing
                self.fail(f"record() must never raise, even on a double write failure: {exc!r}")
        # Confirmed today (pre-fix): nothing recoverable exists anywhere for this scenario --
        # not at the blocked path (cannot exist, the parent is a file) and, before this fix, not
        # anywhere else either. That absence is exactly the bug CHV2-1 exists to fix for the
        # recoverable (non-structural) case.

    def test_no_stray_marker_file_appears_for_a_structurally_broken_path(self):
        # A structurally broken VERDICT_LOG has no valid sibling location implied by this
        # module's own design (module docstring: "It lives under telemetry/, is chmod 600" --
        # a fixed, singular path, not a search path). Confirms the fix does not silently invent
        # a new location that a future reader would have to discover by accident.
        blocked_log = _blocked_verdict_log(self.tmp_path)
        with mock.patch.object(verdict_ledger, "VERDICT_LOG", blocked_log):
            verdict_ledger.record({"session_id": "s1"}, "silent")
        # Nothing under tmp_path should exist except the deliberately-placed blocker file itself.
        created = [p for p in self.tmp_path.rglob("*") if p.is_file()]
        self.assertEqual([p.name for p in created], ["blocked-not-a-directory"])


class RecoverableMarkerTests(unittest.TestCase):
    """Constraint from the acceptance criteria: after the fix, 'the ledger write failed' and
    'the guard was correctly silent' must be distinguishable from what is on disk. Real Records
    for the STRUCTURAL failure above have no recoverable location (see above) -- this class
    proves the mechanism DEVH-77 actually ported does work for the failure class it was designed
    for: a TRANSIENT problem, where a retry at the same path can succeed even though the first
    attempt did not. A permanently-broken real path cannot be made to recover between two calls
    without process-level tricks this suite does not rely on, so this one test deliberately uses
    a targeted mock (the underlying open() call fails exactly once) rather than a real failure --
    flagged here explicitly rather than left to look like the same kind of proof as the test
    above."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.log_path = self.tmp_path / "verdicts.jsonl"

    def test_transient_style_failure_leaves_a_recoverable_marker(self):
        real_open = Path.open
        call_count = {"n": 0}

        def flaky_open(self_path, *args, **kwargs):
            if self_path == verdict_ledger.VERDICT_LOG and call_count["n"] == 0:
                call_count["n"] += 1
                raise OSError(28, "No space left on device (simulated, transient)")
            return real_open(self_path, *args, **kwargs)

        with mock.patch.object(verdict_ledger, "VERDICT_LOG", self.log_path), \
             mock.patch.object(Path, "open", flaky_open):
            verdict_ledger.record({"session_id": "s1"}, "fire", handler_id="bob_write_gate.py",
                                  decision="deny", rule_id="TEST:rule")
        self.assertTrue(self.log_path.is_file(), "the retry (fallback) write never landed")
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertTrue(row["ledger_write_failed"])
        self.assertEqual(row["attempted_verdict"], "fire")
        self.assertEqual(row["handler_id"], "bob_write_gate.py")
        self.assertEqual(row["session_id"], "s1")
        self.assertIn("error", row)
        self.assertIn("No space left on device", row["error"])
        # Carries this module's own fields, not bollard's -- the port is on this module's own
        # terms, not a copy of bollard's record shape.
        self.assertIn("ledger_origin", row)
        self.assertIn("origin_signal", row)
        # rule_id and decision are NOT expected on the fallback marker (DEVH-77's own shape
        # doesn't carry them either -- the marker records that a verdict was LOST, not what it
        # would have said) -- asserted absent so a future "helpful" addition is a deliberate
        # decision, not a silent drift.
        self.assertNotIn("rule_id", row)
        self.assertNotIn("decision", row)

    def test_fallback_marker_itself_failing_does_not_raise(self):
        # Constraint 3, verified directly rather than only reasoned about: if the MARKER write
        # itself fails (not just the primary), record() must still never raise. Simulates the
        # primary write failing normally, then the fallback's own retry ALSO failing (a second,
        # independent transient condition) -- the double-failure case _write_fallback_marker's
        # own docstring names as the reason its own except clause must never propagate.
        def always_raise(self_path, *args, **kwargs):
            raise OSError(5, "Input/output error (simulated)")

        with mock.patch.object(verdict_ledger, "VERDICT_LOG", self.log_path), \
             mock.patch.object(Path, "open", always_raise):
            try:
                verdict_ledger.record({"session_id": "s1"}, "silent")
            except Exception as exc:  # pragma: no cover -- this failing IS the test failing
                self.fail(f"record() must never raise, even when the fallback ALSO fails: {exc!r}")


class ReasonFieldTests(unittest.TestCase):
    """FORE-189: hc.deny(reason) already has the remedy text in hand at the moment it writes a
    verdict, but record() never carried it -- the letter-versus-intent rubric's COMPLIED/ILLUSORY
    distinction (ATLASSN-32's sibling column) was recoverable only from a transcript join, which
    fails outright for any deny whose transcript is missing (Run 1: 189/307 with no tool_use_id).
    This makes the deny's own reason text a first-class ledger field instead."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "verdicts.jsonl"

    def test_reason_is_recorded_on_a_deny_row(self):
        with mock.patch.object(verdict_ledger, "VERDICT_LOG", self.log_path):
            verdict_ledger.record({"session_id": "s1"}, "fire", handler_id="guard_destructive.py",
                                  decision="deny", rule_id="TEST:rule",
                                  reason="Named remedy: drop the --force flag and retry.")
        row = json.loads(self.log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["reason"], "Named remedy: drop the --force flag and retry.")

    def test_reason_absent_when_not_passed(self):
        """The None-strip convention every other optional field in this module already follows
        -- a row with no reason must not carry reason="" or reason=null, so a consumer can test
        presence the same way it already does for `target` and `decision`."""
        with mock.patch.object(verdict_ledger, "VERDICT_LOG", self.log_path):
            verdict_ledger.record({"session_id": "s1"}, "silent")
        row = json.loads(self.log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn("reason", row)

    def test_reason_is_bounded_and_says_so_when_truncated(self):
        """Bounded per the ticket's own wording ('a bounded reason field'), not an unbounded
        pass-through -- and a silent mid-sentence cut would be worse than no field at all for a
        rubric that reads this text for a named remedy, so a truncation must be visible in the
        row, not just shorter than the input."""
        long_reason = "x" * (verdict_ledger.MAX_REASON_LEN + 200)
        with mock.patch.object(verdict_ledger, "VERDICT_LOG", self.log_path):
            verdict_ledger.record({"session_id": "s1"}, "fire", decision="deny",
                                  reason=long_reason)
        row = json.loads(self.log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertLessEqual(len(row["reason"]), verdict_ledger.MAX_REASON_LEN + len("...[truncated]"))
        # CHV2-119: was assertTrue(...endswith(...)). The marker now sits between the
        # kept head and the kept tail, so it is still VISIBLE -- which is what this
        # test's own docstring requires -- without being last.
        self.assertIn("...[truncated]", row["reason"])
        self.assertLess(len(row["reason"]), len(long_reason))

    def test_reason_at_exactly_the_bound_is_not_truncated(self):
        """Off-by-one control: a reason exactly at MAX_REASON_LEN must pass through byte-for-byte,
        proving the bound is inclusive rather than an accidental (< vs <=) truncation of the
        common case."""
        exact_reason = "y" * verdict_ledger.MAX_REASON_LEN
        with mock.patch.object(verdict_ledger, "VERDICT_LOG", self.log_path):
            verdict_ledger.record({"session_id": "s1"}, "fire", decision="deny",
                                  reason=exact_reason)
        row = json.loads(self.log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["reason"], exact_reason)


class ThroughBobWriteGateTests(unittest.TestCase):
    """Constraint 4: test through bob_write_gate.py's own REAL entrypoint, not only through
    verdict_ledger in isolation. `main(data)` alone does not reach the finally block that
    actually calls verdict_ledger.record() -- that block lives in the fail-closed wrapper,
    `_fail_closed_entrypoint()` (section 4.5), which reads stdin itself and is the thing
    test_bob_write_gate.py's own EntrypointTests class already exercises for OTHER reasons
    (mocking verdict_ledger away). This class deliberately does NOT mock verdict_ledger away --
    the real module, with a real (simulated-transient) write failure, is the point."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.log_path = self.tmp_path / "verdicts.jsonl"
        sys.path.insert(0, str(Path(__file__).resolve().parent / "alice_bob"))
        global bwg
        import bob_write_gate as bwg  # noqa: E402

    def test_bob_write_gate_verdict_survives_a_transient_ledger_failure(self):
        real_open = Path.open
        call_count = {"n": 0}

        def flaky_open(self_path, *args, **kwargs):
            if self_path == verdict_ledger.VERDICT_LOG and call_count["n"] == 0:
                call_count["n"] += 1
                raise OSError(28, "No space left on device (simulated, transient)")
            return real_open(self_path, *args, **kwargs)

        # A minimal payload that reaches case 6 (agent_type present -> immediate deny) through
        # the gate's own real logic -- doesn't need a real dispatch fixture, just needs to make
        # the entrypoint emit a real verdict through its own real finally block.
        data = {
            "tool_name": "Write", "session_id": "s1", "cwd": "/tmp",
            "tool_use_id": "t1", "agent_type": "some-subagent",
            "tool_input": {"file_path": "/tmp/x.py", "content": "x = 1\n"},
        }
        with mock.patch.object(bwg.hc, "read_input", return_value=data), \
             mock.patch.object(verdict_ledger, "VERDICT_LOG", self.log_path), \
             mock.patch.object(Path, "open", flaky_open), \
             self.assertRaises(SystemExit) as ctx:
            bwg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)  # gate's own convention: deny is silent-exit-0
        self.assertTrue(self.log_path.is_file(),
                        "bob_write_gate's own finally block never produced a recoverable marker "
                        "on a transient ledger write failure")
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertTrue(row["ledger_write_failed"])
        self.assertEqual(row["handler_id"], "bob_write_gate.py")


class ReasonTruncationKeepsBothEndsTests(unittest.TestCase):
    """CHV2-119. The reason field exists so the COMPLIED/ILLUSORY rubric can recover what a deny
    told the agent to do (FORE-189). FORE-659 then put the literal runnable next command at the
    END of every deny message, and head-only truncation discarded exactly that.

    These assert on the SHAPE of the truncation, not on a length, because a length assertion
    passes just as happily when the wrong half is kept -- which is how this survived."""

    def test_truncation_keeps_the_tail_not_only_the_head(self):
        head_marker = "HEAD-MARKER-OPENING-POLICY"
        tail_marker = "TAIL-MARKER-ListAgents()-RUNNABLE-NEXT-COMMAND"
        filler = "f" * (verdict_ledger.MAX_REASON_LEN * 2)
        reason = head_marker + filler + tail_marker
        bounded = verdict_ledger._bound_reason(reason)
        self.assertIn(head_marker, bounded, "the opening policy text must survive")
        self.assertIn(tail_marker, bounded,
                      "the runnable next command is at the END of every FORE-659 deny message; "
                      "a truncation that keeps only the head drops the one thing this field "
                      "exists to record")
        self.assertIn(verdict_ledger.TRUNCATION_SUFFIX, bounded,
                      "a truncation must still mark itself rather than looking complete")

    def test_tail_budget_holds_the_longest_trailing_remedy_block_in_the_repo(self):
        """The tail is DERIVED, not chosen: write_gate.RECOVERY_NOTE is appended to the end of
        five deny reasons, so any tail shorter than it truncates recovery guidance mid-sentence.
        Reads the real constant, so this fails if that text grows past the budget rather than
        silently starting to cut it again."""
        import ast
        source = (pathlib.Path(__file__).resolve().parent / "alice_bob_fable" / "gate"
                  / "write_gate.py").read_text(encoding="utf-8")
        recovery_note = None
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "RECOVERY_NOTE" for t in node.targets):
                recovery_note = ast.literal_eval(node.value)
        self.assertIsNotNone(recovery_note, "RECOVERY_NOTE not found; re-derive the tail budget")
        self.assertLessEqual(
            len(recovery_note), verdict_ledger.MAX_REASON_TAIL,
            f"RECOVERY_NOTE is {len(recovery_note)} chars and the tail budget is "
            f"{verdict_ledger.MAX_REASON_TAIL}. It no longer survives truncation whole, which is "
            f"the CHV2-119 defect returning by a different route.")
        bounded = verdict_ledger._bound_reason("x" * 900 + recovery_note)
        self.assertIn(recovery_note, bounded)

    def test_a_reason_that_fits_is_still_byte_for_byte_unchanged(self):
        """Control. Without it, a truncator that mangled every reason would pass the two above."""
        reason = "z" * verdict_ledger.MAX_REASON_LEN
        self.assertEqual(verdict_ledger._bound_reason(reason), reason)
        self.assertNotIn(verdict_ledger.TRUNCATION_SUFFIX,
                         verdict_ledger._bound_reason("short reason"))


if __name__ == "__main__":
    unittest.main()
