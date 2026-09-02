#!/usr/bin/env python3
"""C14: lib/deny_capability.sb + lib/capability_scope.sh deny a named capability at the OS
level, and the denial is attributable to the deny rule rather than a path/permissions confound.

Same three-case shape DEVH-63 and DEVH-64 both ran against a scratch profile (positive / trap /
negative control), now required against the shipped artifact -- this test drives the real
bollard/lib/capability_scope.sh, not a toy profile.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

BOLLARD_DIR = Path(__file__).resolve().parent
CAPABILITY_SCOPE_SH = BOLLARD_DIR / "lib" / "capability_scope.sh"


class TestCapabilityScope(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_positive_command_not_touching_capability_exits_zero(self):
        proc = subprocess.run(
            [str(CAPABILITY_SCOPE_SH), "--", "echo positive-control-ok"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("positive-control-ok", proc.stdout)

    def test_trap_command_touching_capability_is_denied_with_kernel_errno_shape(self):
        target = self.target_dir / "trap_target.txt"
        proc = subprocess.run(
            [str(CAPABILITY_SCOPE_SH), "--", f"echo x > {target}"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not permitted", proc.stderr.lower())
        self.assertFalse(target.exists(), "trap command must not have actually written the file")

    def test_negative_control_same_command_without_deny_rule_exits_zero(self):
        # Identical trap command, but run under a profile with the deny rule removed -- this is
        # what attributes the trap's failure to the deny rule specifically, not to a broken path
        # or a missing binary that would produce the same nonzero exit either way.
        target = self.target_dir / "negctrl_target.txt"
        no_deny_profile = self.target_dir / "no_deny.sb"
        no_deny_profile.write_text("(version 1)\n(allow default)\n")
        proc = subprocess.run(
            ["sandbox-exec", "-f", str(no_deny_profile), "/bin/sh", "-c", f"echo x > {target}"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text().strip(), "x")


if __name__ == "__main__":
    unittest.main()
