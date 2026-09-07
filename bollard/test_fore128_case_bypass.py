#!/usr/bin/env python3
"""FORE-128 regression test: goals_freeze_gate.py must not fail OPEN on a case-retyped path
on a case-insensitive, case-preserving filesystem (macOS APFS default).

Written because the original verification (ticket comment 701, 2026-08-26) ran as two
ephemeral scratchpad scripts that did not survive the session -- eleven days later there was
no durable, re-runnable proof the fix still held, only a comment asserting it once did. This
closes that gap: real subprocess, real PreToolUse JSON, against the actual on-disk hooks in
this directory, plus a negative control proving the test discriminates (it must fail against
a deliberately un-fixed copy, not just pass against the live one).

    python3 -m unittest test_fore128_case_bypass -v

(run from this directory so component_coupling.py's own sys.path bootstrapping is unnecessary)
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE / "goals_freeze_gate.py"
CC = HERE / "component_coupling.py"


def make_project():
    root = Path(tempfile.mkdtemp(prefix="fore128-test-"))
    (root / ".foreman").mkdir()
    (root / "monitoring").mkdir()
    (root / "notacomponent").mkdir()
    (root / "monitoring" / "dash.py").write_text("# real file\n")
    (root / "ARCHITECTURE.md").write_text(
        "```yaml components\n"
        'monitoring: ["monitoring/**"]\n'
        "```\n"
    )
    # Deliberately no GOALS.json: "monitoring" is declared but nothing is frozen yet, which is
    # the state goals_freeze_gate.py exists to deny writes into. FORE-128 is upstream of the
    # freeze/hash logic entirely -- is_implementation_path() returning False means _check()
    # never runs, regardless of what GOALS.json would have said.
    return root


def fire(gate_path, root, file_path):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(root),
               "tool_input": {"file_path": str(file_path)}}
    p = subprocess.run([sys.executable, str(gate_path)], input=json.dumps(payload),
                        capture_output=True, text=True)
    denied = ('"permissionDecision": "deny"' in p.stdout
              or '"permissionDecision":"deny"' in p.stdout)
    return denied, p.stdout, p.stderr


class Fore128CaseBypassTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_positive_control_exact_case_denied(self):
        denied, out, err = fire(GATE, self.root, self.root / "monitoring" / "dash.py")
        self.assertTrue(denied, f"exact-case write to a declared, unfrozen component must be "
                                 f"denied. stdout={out!r} stderr={err!r}")

    def test_case_retyped_path_still_denied(self):
        """The actual FORE-128 bug: same file, same inode, retyped case. Pre-fix this was
        silently allowed (is_implementation_path() returned False, main() returned with no
        decision) -- a frozen-or-not-yet-frozen component being bypassed by casing alone."""
        denied, out, err = fire(GATE, self.root, self.root / "MONITORING" / "dash.py")
        self.assertTrue(denied, f"case-retyped path to the same on-disk file must resolve to "
                                 f"the same component and be denied identically to the exact-"
                                 f"case path. stdout={out!r} stderr={err!r}")

    def test_negative_control_unrelated_path_not_denied(self):
        denied, out, err = fire(GATE, self.root, self.root / "notacomponent" / "x.py")
        self.assertFalse(denied, f"a path outside every declared component must not be denied "
                                  f"by this gate. stdout={out!r} stderr={err!r}")

    def test_control_discriminates_against_unfixed_copy(self):
        """Proves this test actually tests something: reverting _relative_or_none() to its
        pre-FORE-128 form (no _true_case() resolution) must reproduce the exact bypass this
        test exists to catch. If this fails, test_case_retyped_path_still_denied's PASS is
        not evidence of anything."""
        unfixed_dir = Path(tempfile.mkdtemp(prefix="fore128-unfixed-"))
        try:
            shutil.copy2(GATE, unfixed_dir / GATE.name)
            shutil.copy2(CC, unfixed_dir / CC.name)
            src = (unfixed_dir / CC.name).read_text()
            needle = (
                "def _relative_or_none(file_path, project_root):\n"
                "    try:\n"
                "        resolved = _true_case(Path(file_path).resolve())\n"
                "        root = _true_case(Path(project_root).resolve())\n"
                "        return resolved.relative_to(root)\n"
            )
            self.assertIn(needle, src,
                           "mutation target not found -- the live source has moved and this "
                           "control no longer targets the real fix; update the needle before "
                           "trusting any result from this file.")
            (unfixed_dir / CC.name).write_text(src.replace(
                needle,
                "def _relative_or_none(file_path, project_root):\n"
                "    try:\n"
                "        resolved = Path(file_path).resolve()\n"
                "        root = Path(project_root).resolve()\n"
                "        return resolved.relative_to(root)\n",
                1,
            ))
            denied, out, err = fire(unfixed_dir / GATE.name, self.root,
                                     self.root / "MONITORING" / "dash.py")
            self.assertFalse(denied, "the un-fixed copy was expected to reproduce the bypass "
                                      "(NOT deny the case-retyped path); it did not, so this "
                                      "control mutation is stale.")
        finally:
            shutil.rmtree(unfixed_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
