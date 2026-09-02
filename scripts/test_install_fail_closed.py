#!/usr/bin/env python3
"""Install script fail-closes when a required hook is missing."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class InstallFailClosedTests(unittest.TestCase):
    def test_missing_hook_exits_nonzero(self):
        tmp = Path(tempfile.mkdtemp(prefix="install-fc-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        shutil.copytree(REPO / "scripts", tmp / "scripts")
        (tmp / "bollard").mkdir()
        (tmp / "skills" / "foreman" / "config").mkdir(parents=True)
        (tmp / ".githooks").mkdir()
        proc = subprocess.run(
            ["sh", str(tmp / "scripts" / "install-dev-harness.sh")],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("required hook missing", proc.stderr)

    def test_tokens_replaced_on_install(self):
        tmp = Path(tempfile.mkdtemp(prefix="install-tok-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        hooks = tmp / "hooks"
        skills = tmp / "skills"
        home = tmp / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOOKS_DST"] = str(hooks)
        env["SKILLS_DST"] = str(skills)
        env["AGENTS_DST"] = str(tmp / "agents")
        env["TESSERA_ROOT"] = "/tmp/fake-tessera"
        env["HOME"] = str(home)
        proc = subprocess.run(
            ["sh", str(REPO / "scripts" / "install-dev-harness.sh")],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        resolver = (hooks / "tessera_resolver" / "tessera_resolver.py").read_text()
        self.assertNotIn("/path/to/ticket-system", resolver)
        self.assertIn("/tmp/fake-tessera", resolver)
        routing = (hooks / "cross_project_routing" / "routing_table.py").read_text()
        self.assertNotIn("/path/to/home", routing)
        self.assertIn(str(home), routing)
        self.assertTrue((tmp / "agents" / "clint-eastwood.md").is_file())

    def test_required_agents_covers_every_real_agent_file(self):
        """REQUIRED_AGENTS is a hardcoded list, not derived from agents/*.md -- nothing forces it
        to stay in sync as new personas land. Found live 2026-08-29: nadia-osei.md and
        owen-reyes.md (landed 2026-08-27 per ARCHITECTURE.md) were both missing from the list,
        so the install script's own fail-closed guarantee didn't actually cover them -- exactly
        the 'guard credited with coverage it doesn't have' shape. This pins the fix by deriving
        the expected set from the real source directory rather than hardcoding a second list
        here that could itself drift the same way."""
        script = (REPO / "scripts" / "install-dev-harness.sh").read_text()
        real_agents = {p.name for p in (REPO / "agents").glob("*.md")}
        checked = set(script.split('REQUIRED_AGENTS="', 1)[1].split('"', 1)[0].split())
        for name in checked:
            self.assertIn(
                name, real_agents,
                f"REQUIRED_AGENTS names {name!r} but no such file exists in agents/",
            )
        missing = real_agents - checked
        self.assertEqual(
            missing, set(),
            f"agents/ has real files REQUIRED_AGENTS never checks: {missing}",
        )


def _copy_repo(dest):
    """A full, otherwise-real copy of the repo -- not the scripts/+empty-stub-dirs fixture the
    other tests here use, because C4/C5 need a tree the installer can actually run to
    completion against, with exactly one thing wrong (or nothing wrong) in it."""
    shutil.copytree(
        REPO, dest,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"),
    )


def _run_installer(repo_copy, home):
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["HOOKS_DST"] = str(home / "hooks")
    env["SKILLS_DST"] = str(home / "skills")
    env["AGENTS_DST"] = str(home / "agents")
    env["TESSERA_ROOT"] = str(home / "fake-tessera")
    return subprocess.run(
        ["sh", str(repo_copy / "scripts" / "install-dev-harness.sh")],
        capture_output=True, text=True, env=env,
    )


class GuardPreflightFailClosedTests(unittest.TestCase):
    """scripts/GOALS.json C4 (PRD.md R24 / DEVH-2). Three separate trials, one guard deleted at
    a time -- not one trial deleting all three, which a preflight special-casing a single
    filename could still pass."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-guard-preflight-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _trial(self, guard_filename):
        repo_copy = self.tmp / f"repo-{guard_filename}"
        _copy_repo(repo_copy)
        (repo_copy / "bollard" / guard_filename).unlink()
        proc = _run_installer(repo_copy, self.tmp / f"home-{guard_filename}")
        self.assertNotEqual(proc.returncode, 0, f"install succeeded with {guard_filename} missing")
        self.assertIn(guard_filename, proc.stderr, proc.stderr)

    def test_guard_destructive_missing_fails_closed(self):
        self._trial("guard_destructive.py")

    def test_guard_prodconfig_missing_fails_closed(self):
        self._trial("guard_prodconfig.py")

    def test_guard_untrusted_web_missing_fails_closed(self):
        self._trial("guard_untrusted_web.py")

    def test_unmodified_tree_installs_successfully(self):
        """The other half of C4: an installer that refuses everything would pass the three
        trials above trivially. This proves it isn't that."""
        repo_copy = self.tmp / "repo-unmodified"
        _copy_repo(repo_copy)
        proc = _run_installer(repo_copy, self.tmp / "home-unmodified")
        self.assertEqual(proc.returncode, 0, proc.stderr)


class InstalledGuardDeniesFromItsRealLocationTests(unittest.TestCase):
    """scripts/GOALS.json C5 (PRD.md R24 / DEVH-2), muse's fourth verification leg: proves the
    INSTALLED artifact works, not the one in the build tree -- catches token substitution that
    corrupts a guard's imports on the way in, which re-testing the repository source cannot."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="install-guard-e2e-"))
        cls.repo_copy = cls.tmp / "repo"
        _copy_repo(cls.repo_copy)
        cls.home = cls.tmp / "home"
        cls.install_result = _run_installer(cls.repo_copy, cls.home)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_install_succeeds(self):
        self.assertEqual(self.install_result.returncode, 0, self.install_result.stderr)

    def test_installed_guard_destructive_denies_a_real_destructive_command(self):
        installed_guard = self.home / "hooks" / "guard_destructive.py"
        self.assertTrue(installed_guard.is_file(), installed_guard)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/some/real/path"},
            "session_id": "test", "cwd": "/tmp",
        }
        proc = subprocess.run(
            [sys.executable, str(installed_guard)],
            input=json.dumps(payload), capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
