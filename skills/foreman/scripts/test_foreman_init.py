#!/usr/bin/env python3
"""skills/GOALS.json C4 (PRD.md R24 / DEVH-2, fixing DEVH-29). Regression + preserved-invariant
test for verify_settings_shape()'s collection bug: the original code only ever gathered hook
commands from the PreToolUse entry whose matcher was EXACTLY "Edit|Write|Bash", so R24's three
guards -- registered under "Bash", "Edit|Write" and the outbound-fetch surface, exactly as
ARCHITECTURE.md specifies -- were never checked at all. A guard pointing at a missing file
passed cleanly.

    python3 -m unittest test_foreman_init -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "bollard" / "tessera_resolver"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import foreman_init  # noqa: E402


def _write_settings(claude_dir, entries):
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": entries}}, indent=2)
    )


def _real_file(tmp, name):
    p = Path(tmp) / name
    p.write_text("# a real file\n")
    return str(p)


def _four_entry_fixture(tmp, *, guard_untrusted_web_path):
    """The shape ARCHITECTURE.md specifies: the six-gate 'Edit|Write|Bash' entry, plus three
    separate entries for R24's guards -- 'Bash', 'Edit|Write' and the outbound-fetch surface."""
    return [
        {
            "matcher": "Edit|Write|Bash",
            "hooks": [{"type": "command", "command": f"python3 {_real_file(tmp, 'gate_one.py')}"}],
        },
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": f"python3 {_real_file(tmp, 'guard_destructive.py')}"}],
        },
        {
            "matcher": "Edit|Write",
            "hooks": [{"type": "command", "command": f"python3 {_real_file(tmp, 'guard_prodconfig.py')}"}],
        },
        {
            "matcher": "WebFetch",
            "hooks": [{"type": "command", "command": f"python3 {guard_untrusted_web_path}"}],
        },
    ]


class VerifySettingsShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.project_root = Path(self.tmpdir.name)

    def test_regression_broken_guard_under_a_non_edit_write_bash_matcher_is_caught(self):
        """The bug, reproduced: guard_untrusted_web's target file does not exist. Against the
        code as it stood before this fix, verify_settings_shape() returned cleanly, because that
        guard's entry (matcher 'WebFetch') was never even inspected. It must now raise, and the
        raised message must name the broken command specifically -- not merely raise on
        something else, which a weaker assertion could pass against a different regression."""
        missing_path = str(self.project_root / "guard_untrusted_web.py")  # deliberately never created
        entries = _four_entry_fixture(self.tmpdir.name, guard_untrusted_web_path=missing_path)
        _write_settings(self.project_root / ".claude", entries)

        with self.assertRaises(foreman_init.InitError) as ctx:
            foreman_init.verify_settings_shape(self.project_root)
        self.assertIn(missing_path, str(ctx.exception))

    def test_broken_guard_under_bash_only_matcher_is_also_caught(self):
        entries = _four_entry_fixture(self.tmpdir.name, guard_untrusted_web_path=_real_file(self.tmpdir.name, "guard_untrusted_web.py"))
        # A path _four_entry_fixture never creates -- deliberately distinct from
        # "guard_destructive.py", which the fixture already wrote as a real file.
        missing_path = str(self.project_root / "guard_destructive_MISSING.py")
        entries[1]["hooks"][0]["command"] = f"python3 {missing_path}"
        _write_settings(self.project_root / ".claude", entries)

        with self.assertRaises(foreman_init.InitError) as ctx:
            foreman_init.verify_settings_shape(self.project_root)
        self.assertIn(missing_path, str(ctx.exception))

    def test_all_real_files_across_all_four_entries_passes(self):
        entries = _four_entry_fixture(
            self.tmpdir.name,
            guard_untrusted_web_path=_real_file(self.tmpdir.name, "guard_untrusted_web.py"),
        )
        _write_settings(self.project_root / ".claude", entries)
        commands = foreman_init.verify_settings_shape(self.project_root)
        self.assertEqual(len(commands), 4)

    def test_control_broken_reference_inside_the_six_gate_entry_still_caught(self):
        """dev-harness-33's own control: the pre-fix function correctly raised when the broken
        reference was INSIDE the 'Edit|Write|Bash' entry, proving the blindness was specific to
        other entries, not general breakage. Must still raise after this fix."""
        entries = _four_entry_fixture(
            self.tmpdir.name,
            guard_untrusted_web_path=_real_file(self.tmpdir.name, "guard_untrusted_web.py"),
        )
        # "gate_one.py" is a real file the fixture already wrote -- deliberately distinct.
        missing_path = str(self.project_root / "gate_one_MISSING.py")
        entries[0]["hooks"][0]["command"] = f"python3 {missing_path}"
        _write_settings(self.project_root / ".claude", entries)

        with self.assertRaises(foreman_init.InitError) as ctx:
            foreman_init.verify_settings_shape(self.project_root)
        self.assertIn(missing_path, str(ctx.exception))

    # ------------------------------------------------- preserved Bash-coverage invariant

    def test_no_bash_covering_entry_still_raises(self):
        """DEVH-29 F5: the fix must not trade the Bash-coverage requirement away while widening
        collection. A settings.json whose gates never see Bash traffic must still fail closed."""
        entries = [
            {
                "matcher": "Edit|Write",
                "hooks": [{"type": "command", "command": f"python3 {_real_file(self.tmpdir.name, 'gate_one.py')}"}],
            },
        ]
        _write_settings(self.project_root / ".claude", entries)
        with self.assertRaises(foreman_init.InitError) as ctx:
            foreman_init.verify_settings_shape(self.project_root)
        self.assertIn("Bash", str(ctx.exception))

    def test_bash_only_matcher_alone_satisfies_the_bash_coverage_invariant(self):
        """A matcher of exactly 'Bash' (guard_destructive.py's own registration) must satisfy
        the invariant on its own, without needing the six-gate 'Edit|Write|Bash' entry present."""
        entries = [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": f"python3 {_real_file(self.tmpdir.name, 'guard_destructive.py')}"}],
            },
        ]
        _write_settings(self.project_root / ".claude", entries)
        commands = foreman_init.verify_settings_shape(self.project_root)
        self.assertEqual(len(commands), 1)

    def test_anti_fix_substring_matcher_would_not_be_exploitable_here(self):
        """DEVH-29 F6: the Bash-coverage check must not be satisfiable by an unrelated matcher
        that merely CONTAINS the substring 'Bash' without naming it as a real pipe-separated
        alternative -- e.g. a hypothetical future matcher 'BashDisabled' must NOT satisfy the
        invariant. This pins the exact-token-after-split behavior, not a substring test."""
        entries = [
            {
                "matcher": "BashDisabled",
                "hooks": [{"type": "command", "command": f"python3 {_real_file(self.tmpdir.name, 'unrelated.py')}"}],
            },
        ]
        _write_settings(self.project_root / ".claude", entries)
        with self.assertRaises(foreman_init.InitError) as ctx:
            foreman_init.verify_settings_shape(self.project_root)
        self.assertIn("Bash", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
