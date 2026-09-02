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


def run_installed_hook(installed_guard, tool_name, tool_input):
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "test", "cwd": "/tmp",
    }
    return subprocess.run(
        [sys.executable, str(installed_guard)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=10,
    )


def declared_tool_name_from_installed_template(installed_template, guard_filename):
    """Reads the INSTALLED template (not the repo source) for the matcher registered against
    guard_filename, same discipline test_guard_untrusted_web.py uses for the build-tree copy --
    driven against the installed copy here so a substitution bug in the template itself, not
    just in the guard file, would also be caught."""
    data = json.loads(installed_template.read_text())
    for entry in data.get("hooks", {}).get("PreToolUse", []):
        for h in entry.get("hooks", []):
            if h.get("command", "").endswith(guard_filename):
                return entry.get("matcher")
    raise AssertionError(
        f"{installed_template} registers no PreToolUse entry whose hooks include {guard_filename}"
    )


class InstalledGuardDeniesFromItsRealLocationTests(unittest.TestCase):
    """scripts/GOALS.json C5 (PRD.md R24 / DEVH-2), muse's fourth verification leg: proves the
    INSTALLED artifact works, not the one in the build tree -- catches token substitution that
    corrupts a guard's imports on the way in, which re-testing the repository source cannot.
    Widened 2026-09-02 (DEVH-38): the property this establishes is per file -- apply_tokens_tree
    rewrites each guard separately, so one guard surviving installation says nothing about the
    others. All three installed copies are driven here, each with a denial case and a silence
    control, so an installed copy that denies everything cannot pass by accident."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="install-guard-e2e-"))
        cls.repo_copy = cls.tmp / "repo"
        _copy_repo(cls.repo_copy)
        cls.home = cls.tmp / "home"
        cls.install_result = _run_installer(cls.repo_copy, cls.home)
        cls.installed_template = cls.home / "skills" / "foreman" / "config" / "settings.json.template"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_install_succeeds(self):
        self.assertEqual(self.install_result.returncode, 0, self.install_result.stderr)

    def test_installed_guard_destructive_denies_a_real_destructive_command(self):
        installed_guard = self.home / "hooks" / "guard_destructive.py"
        self.assertTrue(installed_guard.is_file(), installed_guard)
        proc = run_installed_hook(
            installed_guard, "Bash", {"command": "rm -rf /tmp/some/real/path"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_installed_guard_destructive_is_silent_on_a_benign_command(self):
        installed_guard = self.home / "hooks" / "guard_destructive.py"
        proc = run_installed_hook(installed_guard, "Bash", {"command": "ls -la /tmp"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")

    def test_installed_guard_prodconfig_denies_a_real_production_config_write(self):
        installed_guard = self.home / "hooks" / "guard_prodconfig.py"
        self.assertTrue(installed_guard.is_file(), installed_guard)
        proc = run_installed_hook(
            installed_guard, "Write",
            {"file_path": "/srv/app/.env.production", "content": "x"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_installed_guard_prodconfig_is_silent_on_an_ordinary_source_file(self):
        installed_guard = self.home / "hooks" / "guard_prodconfig.py"
        proc = run_installed_hook(
            installed_guard, "Edit",
            {"file_path": "/srv/app/src/foo.py", "old_string": "a", "new_string": "b"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")

    def test_installed_guard_untrusted_web_denies_an_ssrf_target(self):
        installed_guard = self.home / "hooks" / "guard_untrusted_web.py"
        self.assertTrue(installed_guard.is_file(), installed_guard)
        tool_name = declared_tool_name_from_installed_template(
            self.installed_template, "guard_untrusted_web.py",
        )
        proc = run_installed_hook(
            installed_guard, tool_name,
            {"url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_installed_guard_untrusted_web_is_silent_on_an_ordinary_public_url(self):
        installed_guard = self.home / "hooks" / "guard_untrusted_web.py"
        tool_name = declared_tool_name_from_installed_template(
            self.installed_template, "guard_untrusted_web.py",
        )
        proc = run_installed_hook(
            installed_guard, tool_name, {"url": "https://example.com/docs"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")
