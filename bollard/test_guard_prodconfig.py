#!/usr/bin/env python3
"""bollard/GOALS.json C6, C8 (PRD.md R24 / DEVH-2). Driven as a real subprocess over stdin.

    python3 -m unittest test_guard_prodconfig -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolate_verdict_ledger  # noqa: E402,F401 -- FORE-314: redirects HOME before any guard
# subprocess spawns below, so tests never write to the operator's real
# ~/.claude/telemetry/verdicts.jsonl.

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

    def test_fourth_shape_claude_mcp_config_denied(self):
        result = run_guard("Edit", "/srv/app/.claude/mcp.json")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mcp_config", decision["hookSpecificOutput"]["permissionDecisionReason"])

    def test_fourth_shape_cursor_mcp_config_denied(self):
        result = run_guard("Write", "/srv/app/.cursor/mcp.json")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_bare_mcp_json_outside_declared_dirs_is_not_matched(self):
        # REQ-3's protected surface is .claude/mcp.json and .cursor/mcp.json specifically, not
        # every file named mcp.json -- confirms the pattern didn't overreach.
        result = run_guard("Edit", "/srv/app/config/mcp.json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

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
