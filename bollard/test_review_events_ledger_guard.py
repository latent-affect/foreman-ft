"""Regression tests for REQ-12's PreToolUse hard-deny guard, review_events_ledger_guard.py.

    python3 -m unittest test_review_events_ledger_guard -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent / "review_events_ledger_guard.py")


def _run(payload):
    proc = subprocess.run(
        [sys.executable, SCRIPT], input=json.dumps(payload), capture_output=True, text=True,
    )
    return proc


class ReviewEventsLedgerGuardTests(unittest.TestCase):
    def test_direct_write_to_ledger_denied(self):
        proc = _run({
            "session_id": "s1", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/proj/.foreman/review-events.jsonl", "content": "x"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_multiedit_to_ledger_denied(self):
        """Dana Okafor's real finding (REQ-12 review round 1): MultiEdit was omitted,
        inherited faithfully from modeling this guard on guard_prodconfig.py's own pre-fix
        code. A real MultiEdit payload targeting the ledger passed through unblocked before
        this fix."""
        proc = _run({
            "session_id": "s2b", "cwd": "/tmp", "tool_name": "MultiEdit",
            "tool_input": {"file_path": ".foreman/review-events.jsonl",
                            "edits": [{"old_string": "x", "new_string": "forged entry"}]},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_edit_to_ledger_denied(self):
        proc = _run({
            "session_id": "s2", "cwd": "/tmp", "tool_name": "Edit",
            "tool_input": {"file_path": ".foreman/review-events.jsonl"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_bash_redirect_to_ledger_denied(self):
        proc = _run({
            "session_id": "s3", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "echo hacked >> .foreman/review-events.jsonl"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_bash_sed_i_on_ledger_denied(self):
        proc = _run({
            "session_id": "s4", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "sed -i '' 's/x/y/' .foreman/review-events.jsonl"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_real_stamp_review_event_invocation_not_blocked(self):
        """The one legitimate real-world shape: invoking stamp_review_event.py via Bash. Its
        own command line never mentions review-events.jsonl by name, so this must stay
        silent (the guard's own silence-is-allow convention)."""
        proc = _run({
            "session_id": "s5", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {
                "command": "python3 stamp_review_event.py /tmp/proj ARCHITECTURE-REVIEW.md architecture clint-eastwood"
            },
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_unrelated_write_not_blocked(self):
        proc = _run({
            "session_id": "s6", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/proj/some_other_file.py", "content": "x"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_unrelated_bash_command_not_blocked(self):
        proc = _run({
            "session_id": "s7", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "echo hello > /tmp/other-file.txt"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_reading_the_ledger_is_not_blocked(self):
        """This guard covers writes only -- a Bash command that merely reads the ledger
        (no write-shaped operator) must not be denied."""
        proc = _run({
            "session_id": "s8", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "cat .foreman/review-events.jsonl"},
        })
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
