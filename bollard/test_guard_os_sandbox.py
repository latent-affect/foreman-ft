#!/usr/bin/env python3
"""C15: OSSandboxGuard rewrites only inside its declared capability class, silent outside it."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

BOLLARD_DIR = Path(__file__).resolve().parent
GUARD = BOLLARD_DIR / "guard_os_sandbox.py"
DENY_CAPABILITY_SB = BOLLARD_DIR / "lib" / "deny_capability.sb"


def run_guard(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "test-guard-os-sandbox",
        "cwd": "/tmp",
    })
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload, capture_output=True, text=True, timeout=10,
    )


class TestGuardOsSandboxDeclaredClass(unittest.TestCase):
    def test_declared_class_is_a_module_level_constant(self):
        sys.path.insert(0, str(BOLLARD_DIR))
        import guard_os_sandbox  # noqa: E402
        self.assertTrue(hasattr(guard_os_sandbox, "CAPABILITY_CLASS_PATTERNS"))
        self.assertGreater(len(guard_os_sandbox.CAPABILITY_CLASS_PATTERNS), 0)


class TestGuardOsSandboxInClass(unittest.TestCase):
    def test_in_class_command_is_rewritten(self):
        original = "rm -rf /tmp/devh64-test-target"
        proc = run_guard(original)
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "allow")
        rewritten = hso["updatedInput"]["command"]
        self.assertTrue(
            rewritten.startswith(f"sandbox-exec -f {DENY_CAPABILITY_SB}") or
            rewritten.startswith(f"sandbox-exec -f '{DENY_CAPABILITY_SB}'"),
            f"rewritten command does not begin with the sandbox-exec invocation naming "
            f"lib/deny_capability.sb: {rewritten!r}",
        )
        self.assertTrue(
            rewritten.endswith(f"'{original}'") or rewritten.endswith(original),
            f"rewritten command does not contain the original command verbatim as its "
            f"wrapped suffix: {rewritten!r}",
        )

    def test_second_in_class_shape_is_also_rewritten(self):
        # A second, distinct destructive shape (git force-clean), not just the same one twice.
        original = "git clean -fd"
        proc = run_guard(original)
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "allow")
        self.assertIn(original, hso["updatedInput"]["command"])


class TestGuardOsSandboxOutOfClass(unittest.TestCase):
    """The load-bearing case: silence outside the declared class, including adversarially near
    the boundary, so a guard whose class is too broad cannot pass by excluding only one
    hand-picked example while still suppressing permission prompts on most real Bash traffic."""

    def _assert_silent(self, command):
        proc = run_guard(command)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "", f"expected silence for {command!r}, got {proc.stdout!r}")

    def test_ordinary_benign_command_is_silent(self):
        self._assert_silent("echo hello")

    def test_rm_without_force_recursive_flags_is_silent(self):
        # Same binary as the in-class "rm -rf" case, differing only in the missing -r/-f flags --
        # adversarially near the class boundary.
        self._assert_silent("rm /tmp/onefile.txt")

    def test_git_clean_dry_run_is_silent(self):
        # Same binary+verb as the in-class "git clean -fd" case, differing only in the flag that
        # puts it outside the class (dry-run, no force/directory flags).
        self._assert_silent("git clean -n")

    def test_dd_to_regular_file_is_silent(self):
        # Same binary as the in-class dd case, differing only in the argument that puts it
        # outside the class: writes to a regular file, not a device under /dev/.
        self._assert_silent("dd if=/dev/zero of=/tmp/output.img")


if __name__ == "__main__":
    unittest.main()
