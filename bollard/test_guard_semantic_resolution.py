#!/usr/bin/env python3
"""bollard/GOALS.json C12, C13 (PRD.md R15, DEVH-16).

C12: driven as a real subprocess over stdin, per F5 -- a guard whose logic is correct but never
wired to __main__ denies nothing in production. No other guard is invoked in this file, per F11
-- the criterion is that SemanticResolutionGuard alone, with the other three guards absent,
catches a character-code-constructed destructive command.

C13: an ast.parse walk over guard_semantic_resolution.py's own source proves it contains no
execution path, and the SAME walk (find_forbidden_calls, imported from the guard module rather
than reimplemented here) is proven to fire against two real fixture files: one that calls eval
directly, one that reaches eval by dynamic resolution through getattr/__builtins__. Per F12: a
resolver that runs attacker-controlled input to see what it produces is a worse hole than the
evasion it closes, so a scan never shown to fire on both positives is not evidence of absence for
either.

    cd bollard && /Users/m5/.venv/bin/python3 -m unittest test_guard_semantic_resolution -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolate_verdict_ledger  # noqa: E402,F401 -- FORE-314: must precede the hook_common import
# guard_semantic_resolution pulls in transitively, so tests never write to the operator's real
# ~/.claude/telemetry/verdicts.jsonl.
from guard_semantic_resolution import find_forbidden_calls  # noqa: E402

HOOK = Path(__file__).resolve().parent / "guard_semantic_resolution.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures_semantic_resolution"


def run_guard(tool_name, tool_input):
    payload = {"tool_name": tool_name, "tool_input": tool_input, "session_id": "test", "cwd": "/tmp"}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
    )


def bash(command):
    return run_guard("Bash", {"command": command})


class GuardSemanticResolutionC12Tests(unittest.TestCase):
    """C12: SemanticResolutionGuard alone catches a character-code-constructed destructive
    command, with the other three guards absent. This test file never imports or drives
    guard_allowlist, guard_pattern_feed or guard_os_sandbox -- satisfying "absent" by
    construction, not by mocking them off."""

    def test_chr_concatenation_constructed_destructive_command_is_denied(self):
        # chr(114)+chr(109)+chr(32)+chr(45)+chr(114)+chr(102)+chr(32)+chr(47)+chr(116)+chr(109)
        # +chr(112)+chr(47)+chr(120) spells "rm -rf /tmp/x" one character at a time -- PRD.md
        # R15's named chr(a)+chr(b) shape.
        command = (
            "python3 -c \"import os; os.system("
            "chr(114)+chr(109)+chr(32)+chr(45)+chr(114)+chr(102)+chr(32)+"
            "chr(47)+chr(116)+chr(109)+chr(112)+chr(47)+chr(120))\""
        )
        result = bash(command)
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_hex_escape_constructed_destructive_command_is_denied_as_the_second_construction(self):
        # \x72\x6d\x20\x2d\x72\x66\x20\x2f\x74\x6d\x70\x2f\x78 spells the same "rm -rf /tmp/x"
        # via hex-byte escapes -- a distinct programmatic construction from chr-concatenation,
        # as C12 requires ("two distinct constructions of the same destructive token").
        command = "eval $'\\x72\\x6d\\x20\\x2d\\x72\\x66\\x20\\x2f\\x74\\x6d\\x70\\x2f\\x78'"
        result = bash(command)
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_chr_concatenation_resolving_to_a_harmless_token_produces_empty_stdout(self):
        # THE CONTROL C12 NAMES: chr(108)+chr(115) spells "ls", not a destructive token. A guard
        # that denies anything containing chr(...) rather than resolving what it spells would
        # fail this case and block ordinary work.
        command = "python3 -c \"print(chr(108)+chr(115))\""
        result = bash(command)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_benign_command_with_no_character_code_construction_produces_empty_stdout(self):
        result = bash("git status")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_unrelated_tool_name_produces_empty_stdout(self):
        result = run_guard("Write", {"file_path": "/tmp/foo.py", "content": "chr(114)+chr(109)"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_empty_payload_exits_zero_with_no_output(self):
        result = subprocess.run(
            [sys.executable, str(HOOK)], input="{}",
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class GuardSemanticResolutionC13Tests(unittest.TestCase):
    def test_own_source_contains_no_forbidden_execution_call(self):
        source = HOOK.read_text()
        findings = find_forbidden_calls(source)
        self.assertEqual(
            findings, [],
            f"guard_semantic_resolution.py must contain no eval/exec/compile/__import__/"
            f"os.system/os.popen/subprocess call and no dynamic-resolution path, found: {findings}",
        )

    def test_scan_fires_on_direct_eval_fixture(self):
        # NAME-MATCHING ALONE IS NOT SUFFICIENT is the criterion's own wording; this fixture is
        # the case a name-only OR a dynamic-only scan would both still catch, establishing the
        # baseline before the dynamic-resolution fixture below.
        fixture_path = FIXTURES / "direct_eval_fixture.py"
        findings = find_forbidden_calls(fixture_path.read_text())
        self.assertIn("eval", findings)

    def test_scan_fires_on_dynamically_resolved_eval_fixture(self):
        # THE DISCRIMINATOR: getattr(__builtins__, 'ev'+'al')(token) produces no Call node
        # literally named eval. A name-only scan passes this fixture silently, which is F12 --
        # a resolver that evaluates a constructed command and calls that "closing the evasion."
        fixture_path = FIXTURES / "dynamic_eval_fixture.py"
        findings = find_forbidden_calls(fixture_path.read_text())
        self.assertIn("dynamic-resolution", findings)

    def test_fixtures_are_real_files_not_inline_strings(self):
        # Guards against a future edit quietly replacing the fixture files with inline string
        # literals in this test, which would still pass the two tests above but stop being
        # "fixture files" as the criterion specifies.
        self.assertTrue((FIXTURES / "direct_eval_fixture.py").is_file())
        self.assertTrue((FIXTURES / "dynamic_eval_fixture.py").is_file())


if __name__ == "__main__":
    unittest.main()
