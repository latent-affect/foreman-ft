#!/usr/bin/env python3
"""skills/GOALS.json C1, C2, C3 (PRD.md R24 / DEVH-2).

    python3 -m unittest skills.foreman.config.test_settings_template -v

Run from the repo root (needs scripts/install-dev-harness.sh, which resolves paths relative to
its own location).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = Path(__file__).resolve().parent / "settings.json.template"
INSTALLER = REPO_ROOT / "scripts" / "install-dev-harness.sh"


def _load_source_template():
    return json.loads(TEMPLATE.read_text())


def _entry_for(data, command_suffix):
    for entry in data.get("hooks", {}).get("PreToolUse", []):
        for h in entry.get("hooks", []):
            if h.get("command", "").endswith(command_suffix):
                return entry, h.get("command", "")
    return None, None


class TemplateRegistersAllThreeGuardsTests(unittest.TestCase):
    """C1: parsed as JSON, never grepped -- a malformed or commented-out entry that a grep
    would match must not pass."""

    def setUp(self):
        self.data = _load_source_template()

    def test_guard_destructive_registered_under_bash_matcher(self):
        entry, cmd = _entry_for(self.data, "guard_destructive.py")
        self.assertIsNotNone(entry, "guard_destructive.py not registered in any PreToolUse entry")
        self.assertIn("Bash", entry["matcher"])

    def test_guard_prodconfig_registered_under_edit_write_matcher(self):
        entry, cmd = _entry_for(self.data, "guard_prodconfig.py")
        self.assertIsNotNone(entry, "guard_prodconfig.py not registered in any PreToolUse entry")
        self.assertIn("Edit", entry["matcher"])
        self.assertIn("Write", entry["matcher"])

    def test_guard_untrusted_web_registered_under_the_outbound_fetch_surface(self):
        entry, cmd = _entry_for(self.data, "guard_untrusted_web.py")
        self.assertIsNotNone(entry, "guard_untrusted_web.py not registered in any PreToolUse entry")
        self.assertIn("WebFetch", entry["matcher"])

    def test_six_original_gates_are_untouched(self):
        original_matcher_entry = next(
            e for e in self.data["hooks"]["PreToolUse"] if e["matcher"] == "Edit|Write|Bash"
        )
        names = [h["command"].split("/")[-1] for h in original_matcher_entry["hooks"]]
        self.assertEqual(
            names,
            [
                "architecture_gate.py", "concept_gate.py", "goals_freeze_gate.py",
                "preflight_blocking_gate.py", "dependency_provenance_gate.py",
                "ship_readiness_gate.py",
            ],
        )


@unittest.skipUnless(sys.platform != "win32", "install-dev-harness.sh is POSIX sh")
class InstalledTemplateAndGuardsTests(unittest.TestCase):
    """C2 and C3, against a REAL install into a fully temp environment -- not a reimplemented
    stand-in for substitute_tokens that could drift from what actually ships."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmpdir.name) / "home"
        cls.home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(cls.home)
        env.pop("HOOKS_DST", None)
        env.pop("SKILLS_DST", None)
        env.pop("AGENTS_DST", None)
        proc = subprocess.run(
            ["/bin/sh", str(INSTALLER)], env=env, capture_output=True, text=True, timeout=60,
        )
        cls.install_result = proc
        cls.installed_template_path = (
            cls.home / ".claude" / "skills" / "foreman" / "config" / "settings.json.template"
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def test_install_succeeds(self):
        self.assertEqual(
            self.install_result.returncode, 0,
            f"stdout={self.install_result.stdout}\nstderr={self.install_result.stderr}",
        )

    def test_c2_every_hook_command_in_the_installed_template_resolves_to_a_real_file(self):
        self.assertTrue(self.installed_template_path.is_file(), self.installed_template_path)
        data = json.loads(self.installed_template_path.read_text())
        missing = []
        for entry in data.get("hooks", {}).get("PreToolUse", []):
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                parts = cmd.split()
                script_path = Path(parts[-1]) if parts else None
                if not script_path or not script_path.is_file():
                    missing.append(cmd)
        self.assertEqual(missing, [], f"unresolved hook command(s): {missing}")

    def test_c3_each_guard_runs_as_a_clean_subprocess_on_empty_payload(self):
        data = json.loads(self.installed_template_path.read_text())
        for suffix in ("guard_destructive.py", "guard_prodconfig.py", "guard_untrusted_web.py"):
            entry, cmd = _entry_for(data, suffix)
            self.assertIsNotNone(entry, suffix)
            script_path = cmd.split()[-1]
            with self.subTest(guard=suffix):
                result = subprocess.run(
                    [sys.executable, script_path], input="{}",
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "", result.stdout)


if __name__ == "__main__":
    unittest.main()
