"""Regression tests for FORE-485's ledger_write_guard.py.

    python3 -m unittest test_ledger_write_guard -v

Same real-subprocess dispatch convention as this suite's sibling ledger guard test,
test_review_events_ledger_guard.py -- a hook-shaped stdin payload in, stdout out, nothing
mocked.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent / "ledger_write_guard.py")


def run(payload):
    proc = subprocess.run(
        [sys.executable, SCRIPT], input=json.dumps(payload), capture_output=True, text=True,
    )
    return proc


class LedgerWriteGuardTests(unittest.TestCase):
    def test_direct_write_to_ledger_denied(self):
        proc = run({
            "session_id": "s1", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/proj/.foreman/ledger.jsonl", "content": "x"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_multiedit_to_ledger_denied(self):
        proc = run({
            "session_id": "s2b", "cwd": "/tmp", "tool_name": "MultiEdit",
            "tool_input": {"file_path": ".foreman/ledger.jsonl",
                            "edits": [{"old_string": "x", "new_string": "forged entry"}]},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_edit_to_ledger_denied(self):
        proc = run({
            "session_id": "s2", "cwd": "/tmp", "tool_name": "Edit",
            "tool_input": {"file_path": ".foreman/ledger.jsonl"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_notebookedit_to_ledger_denied(self):
        proc = run({
            "session_id": "s2c", "cwd": "/tmp", "tool_name": "NotebookEdit",
            "tool_input": {"file_path": ".foreman/ledger.jsonl", "new_source": "x"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_bash_redirect_to_ledger_denied(self):
        proc = run({
            "session_id": "s3", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "echo hacked >> .foreman/ledger.jsonl"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_bash_sed_i_on_ledger_denied(self):
        proc = run({
            "session_id": "s4", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "sed -i '' 's/x/y/' .foreman/ledger.jsonl"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_unrelated_write_not_blocked(self):
        proc = run({
            "session_id": "s6", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/proj/some_other_file.py", "content": "x"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_unrelated_bash_command_not_blocked(self):
        proc = run({
            "session_id": "s7", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "echo hello > /tmp/other-file.txt"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_reading_the_ledger_is_not_blocked(self):
        proc = run({
            "session_id": "s8", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "cat .foreman/ledger.jsonl"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_disclosed_residual_obfuscated_bash_write_not_caught(self):
        """Documents, does not fix, the same residual this project's own measured A2 finding
        already names (ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md section 3.2): a Bash command whose
        OWN visible text never spells "ledger.jsonl" -- built here from concatenated string
        parts inside a python3 -c one-liner -- is invisible to this guard's text-match check,
        exactly as it already is for review_events_ledger_guard.py's identical mechanism. This
        assertion documents the gap so nobody mistakes silence here for coverage; it is not a
        claim that this attack is blocked."""
        obfuscated = (
            "python3 -c \"import pathlib; "
            "p = pathlib.Path('.foreman') / ('led' + 'ger.json' + 'l'); "
            "p.open('a').write('forged\\n')\""
        )
        self.assertNotIn("ledger.jsonl", obfuscated)  # confirms the command text itself is clean
        proc = run({
            "session_id": "s9", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": obfuscated},
        })
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
