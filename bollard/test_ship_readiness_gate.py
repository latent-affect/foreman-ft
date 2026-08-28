#!/usr/bin/env python3
"""Regression tests for ship_readiness_gate.py: opt-in gating, artifact existence,
all-five-checks-passing, and commit-hash staleness -- plus the git-push detection's own
quote-awareness (the same false-positive class fixed elsewhere for write-target extraction).

    python3 -m unittest test_ship_readiness_gate -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ship_readiness_gate as srg


class IsRealGitPushTests(unittest.TestCase):
    def test_plain_git_push_detected(self):
        self.assertTrue(srg.is_real_git_push("git push"))

    def test_git_push_with_args_detected(self):
        self.assertTrue(srg.is_real_git_push("git push origin main"))

    def test_git_push_after_chain_detected(self):
        self.assertTrue(srg.is_real_git_push("cd repo && git push"))

    def test_quoted_prose_not_detected(self):
        self.assertFalse(srg.is_real_git_push('git commit -m "reverts the git push regression"'))

    def test_quoted_single_prose_not_detected(self):
        self.assertFalse(srg.is_real_git_push("echo 'remember to git push later'"))

    def test_unrelated_command_not_detected(self):
        self.assertFalse(srg.is_real_git_push("git status"))


class ShipReadinessGateMainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore17-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.tmp, check=True)
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp,
                                   capture_output=True, text=True, check=True).stdout.strip()

    def _payload(self, command="git push"):
        return {"tool_name": "Bash", "cwd": str(self.tmp), "tool_input": {"command": command}}

    def _full_charter(self, commit_hash=None):
        return {
            "generated_at": "2026-08-20T00:00:00Z",
            "commit_hash": commit_hash if commit_hash is not None else self.head,
            "checks": {name: {"pass": True, "evidence": "test"} for name in srg.REQUIRED_CHECKS},
        }

    def _enable(self):
        (self.tmp / ".foreman" / srg.ENABLED_MARKER).write_text("")

    def _write_charter(self, charter):
        (self.tmp / srg.CHARTER_PATH_REL).write_text(json.dumps(charter))

    def _run(self, command="git push"):
        with mock.patch.object(srg.hc, "deny") as deny, mock.patch.object(srg.hc, "set_rule"):
            srg.main(self._payload(command))
            return deny

    def test_non_bash_tool_ignored(self):
        with mock.patch.object(srg.hc, "deny") as deny:
            srg.main({"tool_name": "Write", "cwd": str(self.tmp),
                      "tool_input": {"file_path": "x"}})
        deny.assert_not_called()

    def test_non_push_command_ignored(self):
        deny = self._run("git status")
        deny.assert_not_called()

    def test_not_opted_in_stays_silent(self):
        # No marker file written -- deliberate opt-in-by-default-off, matches ticket_status_gate.
        deny = self._run()
        deny.assert_not_called()

    def test_opted_in_no_charter_denies(self):
        self._enable()
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("no", deny.call_args[0][0])

    def test_unreadable_charter_denies(self):
        self._enable()
        (self.tmp / srg.CHARTER_PATH_REL).write_text("not json")
        deny = self._run()
        deny.assert_called_once()

    def test_failing_check_denies_and_names_it(self):
        self._enable()
        charter = self._full_charter()
        charter["checks"]["test_suite_green"] = {"pass": False, "evidence": "2 tests failed"}
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("test_suite_green", deny.call_args[0][0])

    def test_missing_check_key_denies(self):
        self._enable()
        charter = self._full_charter()
        del charter["checks"]["dogfood_pass"]
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("dogfood_pass", deny.call_args[0][0])

    def test_stale_charter_denies(self):
        self._enable()
        self._write_charter(self._full_charter(commit_hash="0" * 40))
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("0" * 40, deny.call_args[0][0])

    def test_current_charter_all_passing_allows(self):
        self._enable()
        self._write_charter(self._full_charter())
        deny = self._run()
        deny.assert_not_called()

    def test_charter_only_child_commit_allows(self):
        self._enable()
        self._write_charter(self._full_charter())
        subprocess.run(["git", "add", str(srg.CHARTER_PATH_REL)], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "charter"], cwd=self.tmp, check=True)
        deny = self._run()
        deny.assert_not_called()

    def test_charter_plus_other_file_child_denies(self):
        self._enable()
        self._write_charter(self._full_charter())
        (self.tmp / "README.md").write_text("changed")
        subprocess.run(["git", "add", str(srg.CHARTER_PATH_REL), "README.md"],
                       cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "charter and readme"], cwd=self.tmp, check=True)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("HEAD is now", deny.call_args[0][0])

    def test_second_commit_after_charter_only_denies(self):
        self._enable()
        self._write_charter(self._full_charter())
        subprocess.run(["git", "add", str(srg.CHARTER_PATH_REL)], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "charter"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("later")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "later code"], cwd=self.tmp, check=True)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("HEAD is now", deny.call_args[0][0])

    def test_git_unavailable_fails_closed(self):
        self._enable()
        self._write_charter(self._full_charter())
        with mock.patch.object(srg, "current_commit_hash", return_value=None), \
             mock.patch.object(srg.hc, "deny") as deny, \
             mock.patch.object(srg.hc, "set_rule"):
            srg.main(self._payload())
        deny.assert_called_once()
        self.assertIn("fails closed", deny.call_args[0][0])

    def test_not_a_foreman_project_stays_silent(self):
        outside = Path(tempfile.mkdtemp(prefix="fore17-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with mock.patch.object(srg.hc, "deny") as deny:
            srg.main({"tool_name": "Bash", "cwd": str(outside),
                      "tool_input": {"command": "git push"}})
        deny.assert_not_called()


if __name__ == "__main__":
    unittest.main()
