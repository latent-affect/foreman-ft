#!/usr/bin/env python3
"""Install script fail-closes when a required hook is missing."""
import os
import shutil
import subprocess
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
