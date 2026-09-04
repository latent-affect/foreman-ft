#!/usr/bin/env python3
"""bollard/GOALS.json C10, C11 (PRD.md R13 / R14-R15, DEVH-16, DEVH-62).

    cd bollard && /Users/m5/.venv/bin/python3 -m unittest test_guard_allowlist -v

C10 tests drive guard_allowlist.py as a real subprocess, per F5 -- a guard whose logic is
correct but never wired to __main__ denies nothing in production -- with no other guard in the
same process, because PRD.md's own R13 verification text requires the deny to survive with
every detection-layer guard disabled, not merely alongside them.

C11 tests drive main() / hook_common.run_body() in-process so the classification function can be
monkeypatched to raise, and prove the guard-local fail-closed backstop DEVH-62 toy-modeled
(guard-local try/except calling hc.deny() on its own failure) against the discriminator case:
the identical monkeypatch applied to guard_destructive.py -- a detection guard that correctly
keeps hook_common.run()'s fail-open default -- must produce EMPTY stdout. Without that
discriminator a crashing guard and a correctly-silent guard look identical from outside, which is
the exact failure hook_common.py's own docstring names.
"""
import contextlib
import io
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
HOOK = HERE / "guard_allowlist.py"

sys.path.insert(0, str(HERE))
import isolate_verdict_ledger  # noqa: E402,F401 -- FORE-314: must precede hook_common so
# run_body_capture's in-process calls never write to the operator's real
# ~/.claude/telemetry/verdicts.jsonl under the test runner's own argv as handler_id.
import hook_common as hc  # noqa: E402
import guard_allowlist  # noqa: E402
import guard_destructive  # noqa: E402


def run_guard_subprocess(tool_name, tool_input):
    payload = {"tool_name": tool_name, "tool_input": tool_input, "session_id": "test", "cwd": "/tmp"}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
    )


def bash_subprocess(command):
    return run_guard_subprocess("Bash", {"command": command})


def run_body_capture(main_fn, data):
    """Invoke hook_common.run_body in-process (bypasses stdin) and capture whatever it wrote
    to stdout. run_body always calls sys.exit(0) at the end of both its try/except branches."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with mock.patch.object(sys, "exit"):
            hc.run_body(main_fn, data, time.time())
    return buf.getvalue()


class AllowlistGuardC10SubprocessTests(unittest.TestCase):
    """C10: no other guard invoked in the same test, three distinct spellings of the same
    denied capability, plus a benign control."""

    def test_plain_sudo_is_denied(self):
        result = bash_subprocess("sudo apt-get install malicious-package")
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_quote_embedding_sudo_is_denied(self):
        # The exact su""do spelling PRD.md R13 names.
        result = bash_subprocess('su""do apt-get install malicious-package')
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_shell_string_concatenation_sudo_is_denied(self):
        # Two adjacent quoted fragments, single- then double-quoted, that the shell
        # concatenates into one word -- a distinct construction mechanism from the
        # quote-embedding case above (an empty quote pair spliced into a bareword).
        result = bash_subprocess("""'su'"do" apt-get install malicious-package""")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_benign_command_produces_empty_stdout(self):
        result = bash_subprocess("ls -la /tmp")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class AllowlistGuardC11FailClosedTests(unittest.TestCase):
    """C11: field equality on the parsed envelope, never a substring search, and the
    discriminator against the repo's fail-open default."""

    def test_a_classification_raising_still_emits_deny(self):
        data = {"tool_name": "Bash", "tool_input": {"command": "sudo whatever"},
                "session_id": "test", "cwd": "/tmp"}
        with mock.patch.object(guard_allowlist, "_matched_capability",
                               side_effect=RuntimeError("boom")):
            stdout = run_body_capture(guard_allowlist.main, data)
        decision = json.loads(stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_b_unpatched_benign_payload_produces_empty_stdout(self):
        data = {"tool_name": "Bash", "tool_input": {"command": "ls -la /tmp"},
                "session_id": "test", "cwd": "/tmp"}
        stdout = run_body_capture(guard_allowlist.main, data)
        self.assertEqual(stdout.strip(), "")

    def test_c_discriminator_identical_monkeypatch_on_guard_destructive_stays_silent(self):
        # guard_destructive.py correctly relies on hook_common.run()'s fail-open default: it
        # has no guard-local try/except of its own. The identical monkeypatch-to-raise applied
        # to ITS classification function must therefore produce EMPTY stdout, not a deny --
        # proving this test discriminates a crashing guard from a correctly-silent one, rather
        # than passing for any guard regardless of whether the fail-closed backstop exists.
        data = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"},
                "session_id": "test", "cwd": "/tmp"}
        with mock.patch.object(guard_destructive, "_matched_pattern",
                               side_effect=RuntimeError("boom")):
            stdout = run_body_capture(guard_destructive.main, data)
        self.assertEqual(stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
