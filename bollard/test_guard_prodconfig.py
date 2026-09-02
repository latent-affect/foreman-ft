#!/usr/bin/env python3
"""bollard/GOALS.json C6, C8 (PRD.md R24 / DEVH-2). Driven as a real subprocess over stdin.

    python3 -m unittest test_guard_prodconfig -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "guard_prodconfig.py"


def run_guard(tool_name, file_path):
    payload = {
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path, "content": "x", "old_string": "a", "new_string": "b"},
        "session_id": "test", "cwd": "/tmp",
    }
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
    )


class GuardProdconfigTests(unittest.TestCase):
    def test_dotenv_production_denied_on_edit(self):
        result = run_guard("Edit", "/srv/app/.env.production")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_dotenv_production_denied_on_write(self):
        result = run_guard("Write", "/srv/app/.env.production")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_second_distinct_shape_production_path_segment_denied(self):
        result = run_guard("Write", "/srv/app/config/production/database.yml")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_third_shape_docker_compose_prod_denied(self):
        result = run_guard("Edit", "/srv/app/docker-compose.prod.yml")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_ordinary_source_file_edit_produces_empty_stdout(self):
        result = run_guard("Edit", "/srv/app/src/foo.py")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_ordinary_source_file_write_produces_empty_stdout(self):
        result = run_guard("Write", "/srv/app/README.md")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_unrelated_tool_name_produces_empty_stdout(self):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "cat /srv/app/.env.production"},
            "session_id": "test", "cwd": "/tmp",
        }
        result = subprocess.run(
            [sys.executable, str(HOOK)], input=json.dumps(payload),
            capture_output=True, text=True, timeout=10,
        )
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
