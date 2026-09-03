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
    """C18 (tightened, cf round 2): verify the guard's OWN composition against THREE in-class
    shapes -- plain, an embedded quote, and a shell metacharacter -- all through the same
    verbatim-execute path. A wrapper assembled by naive interpolation is correct for a plain
    command (exactly what a test author reaches for first) and escapable by an embedded quote,
    running the remainder OUTSIDE the sandbox -- this project's own canonical evasion class
    (C10's quote-embedding form, C17's quote-obfuscated variant) turned against the wrapper
    instead of against a pattern list. C14 proves capability_scope.sh denies and C15 proves the
    emitted string's shape, but neither executes the exact string this guard's own
    _wrapped_command builds, so neither would catch a wrapper that is safe for plain input and
    escapable by quoted input."""

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

    def _assert_denies_at_the_kernel(self, original_command, target):
        emitted = self._emitted_command(original_command)
        proc = subprocess.run(
            ["/bin/sh", "-c", emitted],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("not permitted", proc.stderr.lower(),
                      f"expected kernel denial for {original_command!r}, got stderr={proc.stderr!r}")
        self.assertFalse(target.exists())

    def test_plain_command_denies_at_the_kernel(self):
        target = self.target_dir / "c18_plain_target.txt"
        original = f"rm -rf /tmp/devh64-c18-test-target-plain && echo x > {target}"
        self._assert_denies_at_the_kernel(original, target)

    def test_embedded_quote_command_denies_at_the_kernel(self):
        # A literal single quote inside the original command -- the exact character shlex.quote
        # exists to escape when wrapping the whole string in single quotes. A wrapper built by
        # naive interpolation instead of shlex.quote would let this quote close the -c argument
        # early and run the remainder OUTSIDE the sandbox -- this is the case that catches that.
        target = self.target_dir / "c18_quote_target.txt"
        original = f"rm -rf \"/tmp/devh64-c18-test-target's-dir\" && echo x > {target}"
        self._assert_denies_at_the_kernel(original, target)

    def test_shell_metacharacter_command_substitution_denies_at_the_kernel(self):
        target = self.target_dir / "c18_subst_target.txt"
        original = f"rm -rf /tmp/devh64-c18-test-target-$(echo sub) && echo x > {target}"
        self._assert_denies_at_the_kernel(original, target)

    def test_negative_control_post_emission_profile_substitution_lets_the_write_through(self):
        # Per C18's amendment: the substitution happens AFTER emission, in the test, NOT via the
        # guard's own environment override -- C19 requires the guard itself to refuse an
        # unusable profile, so a control routed through the override would be testing a path
        # C19 now forbids. This takes the real emitted string (real profile, C19 lets it
        # through) and swaps the profile PATH in that string for a no-deny profile.
        no_deny_profile = self.target_dir / "no_deny.sb"
        no_deny_profile.write_text("(version 1)\n(allow default)\n")
        target = self.target_dir / "c18_negctrl_target.txt"
        original = f"rm -rf /tmp/devh64-c18-test-target-negctrl && echo x > {target}"
        emitted = self._emitted_command(original)
        real_profile = str(DENY_CAPABILITY_SB)
        self.assertIn(real_profile, emitted, "expected the real profile path in the emitted string")
        substituted = emitted.replace(real_profile, str(no_deny_profile))
        proc = subprocess.run(
            ["/bin/sh", "-c", substituted],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text().strip(), "x")


class TestGuardOsSandboxRefusesUnusableProfile(unittest.TestCase):
    """C19: guard_os_sandbox refuses to substitute the control (emits deny, not allow+rewrite)
    when the resolved profile cannot be shown to deny anything -- the fail-closed fix for F17
    (BOLLARD_DENY_CAPABILITY_SB_OVERRIDE, added so C16/C18 could vary the profile, had no
    validation; combined with the rewrite bypassing Claude Code's own ambient safety layer, a
    permissive override made this guard strictly worse than not registering it at all)."""

    IN_CLASS_COMMAND = "rm -rf /tmp/devh64-c19-test-target"

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.scratch = Path(self._tmpdir.name)

    def tearDown(self):
        # An unreadable fixture must be restored to a removable mode before TemporaryDirectory
        # cleans up, or cleanup itself fails.
        unreadable = self.scratch / "unreadable.sb"
        if unreadable.exists():
            unreadable.chmod(0o600)
        self._tmpdir.cleanup()

    def _run_with_override(self, profile_path):
        return run_guard(
            self.IN_CLASS_COMMAND,
            env_override={"BOLLARD_DENY_CAPABILITY_SB_OVERRIDE": str(profile_path)},
        )

    def test_profile_with_no_deny_rule_denies_rather_than_rewrites(self):
        no_deny = self.scratch / "no_deny.sb"
        no_deny.write_text("(version 1)\n(allow default)\n")
        proc = self._run_with_override(no_deny)
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", hso)

    def test_profile_with_commented_out_deny_rule_denies_rather_than_rewrites(self):
        # Case (d), Priya's own finding against the shipped code: a Seatbelt ';' comment still
        # contains the literal substring "(deny" and would pass a raw-text search that doesn't
        # know what a comment is -- reachable through the exact door C19 exists to close.
        commented = self.scratch / "commented_deny.sb"
        commented.write_text("(version 1)\n(allow default)\n\n; (deny file-write*)\n")
        proc = self._run_with_override(commented)
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", hso)

    def test_profile_with_deny_text_inside_a_string_literal_denies_rather_than_rewrites(self):
        # DEVH-80, cf's own finding, empirically confirmed against real sandbox-exec: the
        # literal text "(deny" inside a STRING ARGUMENT of a harmless (allow ...) rule still
        # passes a raw substring/regex search, even though the rule itself denies nothing --
        # (allow default) grants everything and the second rule is a no-op allow for a bogus
        # path. Running this profile for real: sandbox-exec exits 0, marker written, zero
        # protection.
        string_literal_deny = self.scratch / "string_literal_deny.sb"
        string_literal_deny.write_text(
            '(version 1)\n(allow default)\n(allow file-read* (literal "(deny file-write*)"))\n'
        )
        proc = self._run_with_override(string_literal_deny)
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", hso)

    def test_nonexistent_profile_path_denies_rather_than_rewrites(self):
        proc = self._run_with_override(self.scratch / "does-not-exist.sb")
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", hso)

    def test_unreadable_profile_denies_rather_than_rewrites(self):
        unreadable = self.scratch / "unreadable.sb"
        unreadable.write_text("(version 1)\n(deny file-write*)\n")
        unreadable.chmod(0o000)
        proc = self._run_with_override(unreadable)
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", hso)

    def test_positive_control_real_profile_still_rewrites(self):
        # Without this, a guard that denies unconditionally (regardless of the profile) would
        # pass every case above for the wrong reason.
        proc = run_guard(self.IN_CLASS_COMMAND)
        out = json.loads(proc.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "allow")
        self.assertIn("updatedInput", hso)


if __name__ == "__main__":
    unittest.main()
