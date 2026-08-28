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
