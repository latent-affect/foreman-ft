#!/usr/bin/env python3
"""bollard/GOALS.json C5, C8 (PRD.md R24 / DEVH-2). Driven as a real subprocess over stdin, per
F5 -- a guard whose logic is correct but never wired to __main__ denies nothing in production.

    python3 -m unittest test_guard_destructive -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "guard_destructive.py"


def run_guard(tool_name, tool_input):
    payload = {"tool_name": tool_name, "tool_input": tool_input, "session_id": "test", "cwd": "/tmp"}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
    )


def bash(command):
    return run_guard("Bash", {"command": command})


class GuardDestructiveTests(unittest.TestCase):
    def test_recursive_force_delete_is_denied(self):
        result = bash("rm -rf /tmp/some/real/path")
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual(
            decision["hookSpecificOutput"]["permissionDecision"], "deny",
        )
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_device_level_overwrite_is_denied(self):
        result = bash("dd if=/dev/zero of=/dev/disk2 bs=1m")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_force_clean_is_denied_as_the_second_distinct_shape(self):
        result = bash("git clean -fdx")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_quote_obfuscated_recursive_delete_is_still_denied(self):
        # Same underlying command as test_recursive_force_delete_is_denied, split with empty
        # quote pairs so a naive literal-string match would miss it. GOALS.json C5's required
        # third shape.
        result = bash('r""m -r""f /tmp/some/real/path')
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_benign_command_produces_empty_stdout(self):
        result = bash("git status")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_another_benign_command_produces_empty_stdout(self):
        result = bash("ls -la /tmp")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_unrelated_tool_name_produces_empty_stdout(self):
        result = run_guard("Write", {"file_path": "/tmp/foo.py", "content": "rm -rf /"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_empty_payload_exits_zero_with_no_output(self):
        result = subprocess.run(
            [sys.executable, str(HOOK)], input="{}",
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
