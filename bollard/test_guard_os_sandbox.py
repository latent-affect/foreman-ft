#!/usr/bin/env python3
"""C15: OSSandboxGuard rewrites only inside its declared capability class, silent outside it.
C18: the command string it actually emits, executed as-is, removes the capability."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BOLLARD_DIR = Path(__file__).resolve().parent
GUARD = BOLLARD_DIR / "guard_os_sandbox.py"
DENY_CAPABILITY_SB = BOLLARD_DIR / "lib" / "deny_capability.sb"


def run_guard(command: str, env_override=None) -> subprocess.CompletedProcess:
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "test-guard-os-sandbox",
        "cwd": "/tmp",
    })
    env = dict(os.environ)
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload, capture_output=True, text=True, timeout=10, env=env,
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


class TestGuardOsSandboxEmittedComposition(unittest.TestCase):
    """C18: verify the guard's OWN composition, not capability_scope.sh in isolation (C14) and
    not just the emitted string's shape (C15). Takes updatedInput.command VERBATIM from the
    guard's real stdout and executes that exact string -- a flag divergence between
    _wrapped_command and capability_scope.sh would pass C14 and C15 and still ship a wrapper
    that denies nothing, which is exactly what this criterion exists to catch."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _emitted_command(self, original_command, env_override=None):
        # original_command's own write attempt travels INSIDE the wrapped string (it's the
        # verbatim suffix of what guard_os_sandbox emits), so the sandbox profile is what
        # decides whether it lands -- appending a write AFTER the emitted string would run
        # outside sandbox-exec entirely and prove nothing about the guard's composition.
        proc = run_guard(original_command, env_override=env_override)
        out = json.loads(proc.stdout)
        return out["hookSpecificOutput"]["updatedInput"]["command"]

    def test_emitted_command_executed_verbatim_denies_at_the_kernel(self):
        target = self.target_dir / "c18_trap_target.txt"
        original = f"rm -rf /tmp/devh64-c18-test-target && echo x > {target}"
        emitted = self._emitted_command(original)
        proc = subprocess.run(
            ["/bin/sh", "-c", emitted],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("not permitted", proc.stderr.lower())
        self.assertFalse(target.exists())

    def test_negative_control_no_deny_profile_lets_the_write_through(self):
        no_deny_profile = self.target_dir / "no_deny.sb"
        no_deny_profile.write_text("(version 1)\n(allow default)\n")
        target = self.target_dir / "c18_negctrl_target.txt"
        original = f"rm -rf /tmp/devh64-c18-test-target && echo x > {target}"
        emitted = self._emitted_command(
            original, env_override={"BOLLARD_DENY_CAPABILITY_SB_OVERRIDE": str(no_deny_profile)}
        )
        self.assertIn(str(no_deny_profile), emitted)
        proc = subprocess.run(
            ["/bin/sh", "-c", emitted],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text().strip(), "x")


if __name__ == "__main__":
    unittest.main()
