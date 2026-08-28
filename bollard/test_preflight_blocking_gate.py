#!/usr/bin/env python3
"""Ambiguous/unreachable deny must not block the named remedy write.

    python3 -m unittest test_preflight_blocking_gate -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import preflight_blocking_gate as pbg
import tessera_resolver


STATUSES = (
    ("ambiguous", {"status": "ambiguous", "candidates": ["AREM", "FORE"]}),
    ("unreachable", {"status": "unreachable", "reason": "cli down"}),
)


class PreflightRemedyCarveOutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore80-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.assertFalse(
            (self.tmp / ".foreman" / tessera_resolver.OVERRIDE_FILENAME).exists())

    def payload_edit(self, file_path):
        return {
            "tool_name": "Edit",
            "cwd": str(self.tmp),
            "tool_input": {"file_path": str(file_path)},
        }

    def payload_write(self, file_path):
        return {
            "tool_name": "Write",
            "cwd": str(self.tmp),
            "tool_input": {"file_path": str(file_path)},
        }

    def payload_bash(self, command):
        return {
            "tool_name": "Bash",
            "cwd": str(self.tmp),
            "tool_input": {"command": command},
        }

    def run_gate(self, payload, resolve_result):
        with mock.patch.object(pbg.tr, "resolve", return_value=resolve_result), \
             mock.patch.object(pbg.hc, "deny") as deny, \
             mock.patch.object(pbg.hc, "set_rule") as set_rule:
            pbg.main(payload)
            return deny, set_rule

    def test_remedy_path_uses_override_filename(self):
        self.assertEqual(
            pbg.remedy_path(self.tmp).name, tessera_resolver.OVERRIDE_FILENAME)

    def test_allows_sole_remedy_edit_or_write(self):
        relative = f".foreman/{tessera_resolver.OVERRIDE_FILENAME}"
        absolute = str(self.tmp / ".foreman" / tessera_resolver.OVERRIDE_FILENAME)
        for tool, payload_fn in (("Edit", self.payload_edit),
                                 ("Write", self.payload_write)):
            for file_path in (relative, absolute):
                for status, result in STATUSES:
                    with self.subTest(tool=tool, file_path=file_path, status=status):
                        deny, set_rule = self.run_gate(payload_fn(file_path), result)
                        deny.assert_not_called()
                        set_rule.assert_called_once_with(
                            f"{pbg.RULE_ID}:{status}-remedy-write")

    def test_allows_sole_remedy_bash_redirect(self):
        relative = f"echo FORE > .foreman/{tessera_resolver.OVERRIDE_FILENAME}"
        absolute = (
            f"echo FORE > {self.tmp}/.foreman/{tessera_resolver.OVERRIDE_FILENAME}")
        for command in (relative, absolute):
            for status, result in STATUSES:
                with self.subTest(command=command, status=status):
                    deny, set_rule = self.run_gate(self.payload_bash(command), result)
                    deny.assert_not_called()
                    set_rule.assert_called_once_with(
                        f"{pbg.RULE_ID}:{status}-remedy-write")

    def test_denies_non_remedy_writes(self):
        name = tessera_resolver.OVERRIDE_FILENAME
        cases = [
            self.payload_write(self.tmp / ".claude" / "settings.json"),
            self.payload_write(self.tmp / ".foreman" / "other"),
            self.payload_write(self.tmp / "vendor" / ".foreman" / name),
            self.payload_write(self.tmp / ".foreman" / f"{name}.bak"),
            self.payload_edit(self.tmp / "src" / "foo.py"),
            self.payload_bash("echo x > README.md"),
            self.payload_bash("true"),
            self.payload_bash(f"echo FORE > .foreman/{name} && echo x > README.md"),
        ]
        for payload in cases:
            for status, result in STATUSES:
                with self.subTest(payload=payload, status=status):
                    deny, set_rule = self.run_gate(payload, result)
                    deny.assert_called_once()
                    set_rule.assert_called_once_with(f"{pbg.RULE_ID}:{status}")


if __name__ == "__main__":
    unittest.main()
