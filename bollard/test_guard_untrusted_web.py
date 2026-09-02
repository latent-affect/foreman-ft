#!/usr/bin/env python3
"""bollard/GOALS.json C7, C8 (PRD.md R24 / DEVH-2). Driven as a real subprocess over stdin. The
tool name this guard's positive/benign cases use is READ from the shipped settings template's
own matcher for this hook, not assumed from the guard's filename -- a template registering it
under the wrong tool name would otherwise still show a green test that happened to assume
correctly (GOALS.json C7's own stated requirement).

    python3 -m unittest test_guard_untrusted_web -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "guard_untrusted_web.py"
TEMPLATE = Path(__file__).resolve().parents[1] / "skills" / "foreman" / "config" / "settings.json.template"


def declared_tool_name():
    """Reads settings.json.template, finds the PreToolUse entry whose hooks list this guard,
    and returns that entry's matcher. Fails loudly (not a silent fallback to 'WebFetch') if the
    template doesn't register this guard, since that is itself the bug C1/C7 exist to catch."""
    data = json.loads(TEMPLATE.read_text())
    for entry in data.get("hooks", {}).get("PreToolUse", []):
        for h in entry.get("hooks", []):
            if h.get("command", "").endswith("guard_untrusted_web.py"):
                return entry.get("matcher")
    raise AssertionError(
        f"{TEMPLATE} registers no PreToolUse entry whose hooks include guard_untrusted_web.py"
    )


TOOL_NAME = declared_tool_name()


def run_guard(tool_name, url):
    payload = {
        "tool_name": tool_name,
        "tool_input": {"url": url, "prompt": "summarize this"},
        "session_id": "test", "cwd": "/tmp",
    }
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
    )


class GuardUntrustedWebTests(unittest.TestCase):
    def test_declared_matcher_covers_this_guards_expected_tool(self):
        self.assertIn("WebFetch", TOOL_NAME)

    def test_cloud_metadata_endpoint_is_denied(self):
        result = run_guard(TOOL_NAME, "http://169.254.169.254/latest/meta-data/iam/security-credentials/")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_loopback_address_is_denied(self):
        result = run_guard(TOOL_NAME, "http://127.0.0.1:8080/admin")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_localhost_hostname_is_denied(self):
        result = run_guard(TOOL_NAME, "http://localhost:4000/internal")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_private_range_address_is_denied(self):
        result = run_guard(TOOL_NAME, "http://10.0.0.5/config")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_file_scheme_is_denied(self):
        result = run_guard(TOOL_NAME, "file:///etc/passwd")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_ordinary_public_url_within_the_declared_surface_produces_empty_stdout(self):
        result = run_guard(TOOL_NAME, "https://docs.anthropic.com/en/docs/claude-code")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_unrelated_tool_name_produces_empty_stdout(self):
        result = run_guard("WebSearch", "http://169.254.169.254/")
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
