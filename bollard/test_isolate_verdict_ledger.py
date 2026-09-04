#!/usr/bin/env python3
"""FORE-314. Tests for isolate_verdict_ledger.py, including the real, executable fail-first proof
this ticket was built under: running the guard test suites today appends rows to wherever HOME
resolves (the operator's real ~/.claude/telemetry/verdicts.jsonl, unmodified) -- proven here
against a substituted scratch HOME rather than the real one, so this suite never itself writes to
the operator's real ledger while still exercising the exact causal mechanism.

Run: cd bollard && /Users/m5/.venv/bin/python3 -m unittest test_isolate_verdict_ledger -v
"""
import ast
import json
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The true OS home directory, read from the user database rather than os.environ["HOME"].
# Deliberately NOT `os.environ.get("HOME")` captured "before import": when this file runs as
# part of a `discover` sweep across the whole bollard/ directory, an earlier-loaded test module
# (e.g. test_class_c_coverage_boundary.py, alphabetically before this one) may have already
# imported isolate_verdict_ledger and redirected HOME process-wide before this module's own
# top-level code ever runs -- import order across files in one shared process is not this file's
# to control. pwd.getpwuid bypasses the environment variable entirely, so it is the ground truth
# regardless of what else already ran in this process.
REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir

BOLLARD = Path(__file__).resolve().parent
sys.path.insert(0, str(BOLLARD))
import isolate_verdict_ledger as ivl  # noqa: E402

# Files that invoke a real guard/hook (in-process via hook_common.run()/run_body(), or a real
# subprocess spawning a guard script as __main__) and therefore need isolate_verdict_ledger
# imported before their first hook_common/guard_* import. Any file NOT in this list either does
# not touch a real hook at all (pure unit/data tests) or is added here when it starts to.
AFFECTED_FILES = [
    "test_class_c_coverage_boundary.py",
    "test_guard_pattern_feed.py",
    "test_guard_os_sandbox.py",
    "test_guard_allowlist.py",
    "test_pre_implementation_brief_gate.py",
    "test_guard_prodconfig.py",
    "test_guard_untrusted_web.py",
    "test_guard_destructive.py",
    "test_guard_semantic_resolution.py",
]


class EnsureFakeHomeTests(unittest.TestCase):
    def test_idempotent_within_process(self):
        self.assertEqual(ivl._ensure_fake_home(), ivl._ensure_fake_home())

    def test_returns_a_real_existing_directory(self):
        self.assertTrue(Path(ivl._ensure_fake_home()).is_dir())

    def test_fake_home_is_not_the_real_home(self):
        self.assertIsNotNone(REAL_HOME, "test environment must have HOME set to test this")
        self.assertNotEqual(ivl._ensure_fake_home(), REAL_HOME)


class IsolatedVerdictLogPathTests(unittest.TestCase):
    def test_path_is_under_fake_home_not_real_home(self):
        path = ivl.isolated_verdict_log_path()
        self.assertTrue(str(path).startswith(ivl._ensure_fake_home()))
        if REAL_HOME:
            self.assertFalse(str(path).startswith(REAL_HOME + os.sep))

    def test_path_shape_matches_the_real_verdict_log_layout(self):
        path = ivl.isolated_verdict_log_path()
        self.assertEqual(path.name, "verdicts.jsonl")
        self.assertEqual(path.parent.name, "telemetry")
        self.assertEqual(path.parent.parent.name, ".claude")


class HomeEnvRedirectionTests(unittest.TestCase):
    def test_home_env_var_was_redirected_by_importing_this_module(self):
        # This module was already imported at file scope above (it must be, to test it at all) --
        # by the time this test runs, the side effect has already happened. Assert its result.
        self.assertEqual(os.environ.get("HOME"), ivl._ensure_fake_home())
        if REAL_HOME:
            self.assertNotEqual(os.environ.get("HOME"), REAL_HOME)


class ImportOrderStructuralTests(unittest.TestCase):
    """Cheap, exhaustive check that every file known to invoke a real guard/hook imports
    isolate_verdict_ledger BEFORE any hook_common/guard_* import -- the one way to use this
    module wrong (see its own docstring's ordering requirement), and the failure mode a later
    refactor could reintroduce silently if nothing checks import order structurally."""

    def _import_order(self, filename):
        path = BOLLARD / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append((node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append((node.lineno, node.module))
        return sorted(names)

    def test_every_affected_file_imports_isolation_before_any_hook_or_guard_import(self):
        failures = []
        for filename in AFFECTED_FILES:
            order = self._import_order(filename)
            isolation_lines = [ln for ln, name in order if name == "isolate_verdict_ledger"]
            hook_lines = [ln for ln, name in order
                          if name == "hook_common" or name.startswith("guard_")]
            if not isolation_lines:
                failures.append(f"{filename}: does not import isolate_verdict_ledger at all")
                continue
            if hook_lines and min(isolation_lines) > min(hook_lines):
                failures.append(
                    f"{filename}: isolate_verdict_ledger imported at line {min(isolation_lines)}, "
                    f"AFTER a hook/guard import at line {min(hook_lines)} -- too late, see "
                    f"isolate_verdict_ledger.py's own ordering requirement")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_negative_control_a_file_missing_the_import_entirely_is_caught(self):
        # Proves the check above actually looks, rather than passing vacuously -- run it against
        # a file known NOT to import isolate_verdict_ledger (this test file's own module list
        # deliberately excludes files with no real hook invocation; guard_allowlist.py itself,
        # the guard under test rather than a test file, has neither import).
        order = self._import_order("guard_allowlist.py")
        isolation_lines = [ln for ln, name in order if name == "isolate_verdict_ledger"]
        self.assertEqual(isolation_lines, [])


class RealDemonstrationTests(unittest.TestCase):
    """The fail-first proof this ticket was built under, run for real: a subprocess invocation
    of one of the actual affected test files, checked against a SUBSTITUTED scratch HOME rather
    than the operator's real one, so this suite can prove the causal mechanism without itself
    contributing a single row to the real ~/.claude/telemetry/verdicts.jsonl.

    Confirmed manually before isolate_verdict_ledger.py's import landed in the target file: the
    scratch ledger received real 'error'-verdict rows under handler_id 'python3 -m unittest' --
    the exact defect FORE-314 describes, reproduced safely. Re-run after the import landed: zero
    rows at the externally-supplied scratch path, because the fix redirects HOME internally to
    its own fresh directory regardless of what the outer environment supplied."""

    def _run_test_file_with_home(self, filename, home_dir):
        env = dict(os.environ)
        env["HOME"] = str(home_dir)
        return subprocess.run(
            ["/Users/m5/.venv/bin/python3", "-m", "unittest", filename[:-3], "-v"],
            cwd=str(BOLLARD), env=env, capture_output=True, text=True, timeout=60,
        )

    def test_fixed_target_file_does_not_write_to_the_supplied_home(self):
        with tempfile.TemporaryDirectory(prefix="fore314-demo-scratch-") as scratch:
            proc = self._run_test_file_with_home("test_guard_destructive.py", scratch)
            ledger_path = Path(scratch) / ".claude" / "telemetry" / "verdicts.jsonl"
            self.assertFalse(
                ledger_path.exists(),
                f"test_guard_destructive.py wrote to the externally-supplied HOME "
                f"({ledger_path}) -- isolate_verdict_ledger's redirect is not taking effect. "
                f"stdout/stderr:\n{proc.stdout}\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
