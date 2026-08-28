#!/usr/bin/env python3
"""Regression tests for concept_gate.py: package-existence, decision-field gating
(go opens, kill/hold/recycle all deny), Bash-write-target extraction, scope isolation from
architecture_gate.py's own implementation-path coverage, and fail-closed behavior on a malformed
package.

    python3 -m unittest test_concept_gate -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import concept_gate as cg


class ConceptGateMainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore71-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write_package(self, decision="go", **extra):
        pkg = {"stage": "concept", "decision": decision,
               "gate_owner_persona": "Priya Desai", "tessera_ticket_ids": ["DEMO-1"],
               "summary": "test package", **extra}
        pkg_path = self.tmp / cg.PACKAGE_RELPATH
        pkg_path.parent.mkdir(parents=True, exist_ok=True)
        pkg_path.write_text(json.dumps(pkg))

    def _payload_edit(self, file_path="ARCHITECTURE.md"):
        return {"tool_name": "Edit", "cwd": str(self.tmp),
                "tool_input": {"file_path": file_path}}

    def _payload_bash(self, command):
        return {"tool_name": "Bash", "cwd": str(self.tmp), "tool_input": {"command": command}}

    def _run(self, payload):
        with mock.patch.object(cg.hc, "deny") as deny, mock.patch.object(cg.hc, "set_rule"):
            cg.main(payload)
            return deny

    def _run_with_rule(self, payload):
        with mock.patch.object(cg.hc, "deny") as deny, \
             mock.patch.object(cg.hc, "set_rule") as set_rule:
            cg.main(payload)
            return deny, set_rule

    # C1
    def test_denies_when_no_package(self):
        deny = self._run(self._payload_edit())
        deny.assert_called_once()
        self.assertIn("no TPM concept-stage gate-review package", deny.call_args[0][0])

    # C2
    def test_allows_on_go_decision(self):
        self._write_package(decision="go")
        deny = self._run(self._payload_edit())
        deny.assert_not_called()

    # C3 (parametrized over kill/hold/recycle)
    def test_denies_on_non_go_decisions(self):
        for decision in ("kill", "hold", "recycle"):
            with self.subTest(decision=decision):
                self._write_package(decision=decision)
                deny = self._run(self._payload_edit())
                deny.assert_called_once()
                self.assertIn(decision, deny.call_args[0][0])

    def test_denies_on_missing_decision_field(self):
        pkg_path = self.tmp / cg.PACKAGE_RELPATH
        pkg_path.parent.mkdir(parents=True, exist_ok=True)
        pkg_path.write_text(json.dumps({"stage": "concept"}))
        deny = self._run(self._payload_edit())
        deny.assert_called_once()

    # C4
    def test_bash_write_target_extraction(self):
        deny = self._run(self._payload_bash("echo x > ARCHITECTURE.md"))
        deny.assert_called_once()

    def test_bash_write_target_extraction_allows_on_go(self):
        self._write_package(decision="go")
        deny = self._run(self._payload_bash("echo x > ARCHITECTURE.md"))
        deny.assert_not_called()

    def test_bash_unrelated_target_not_gated(self):
        deny = self._run(self._payload_bash("echo x > other-file.md"))
        deny.assert_not_called()

    # C5 -- m1 fix: assert the actual rule_id, not just deny.assert_not_called()
    def test_silent_on_non_architecture_targets(self):
        deny, set_rule = self._run_with_rule(
            self._payload_edit(file_path=str(self.tmp / "atlas" / "warehouse" / "x.py")))
        deny.assert_not_called()
        set_rule.assert_called_once_with(f"{cg.RULE_ID}:not-in-scope")

    def test_silent_on_review_file(self):
        # A denied ARCHITECTURE.md write must not accidentally also gate the (separately-owned)
        # ARCHITECTURE-REVIEW.md file, which is architecture_gate.py's own concern, not this one.
        deny = self._run(self._payload_edit(file_path="ARCHITECTURE-REVIEW.md"))
        deny.assert_not_called()

    # FATAL regression (adversarial review, 2026-08-22): case-insensitive filesystem bypass.
    def test_case_insensitive_lowercase_edit_denied(self):
        deny = self._run(self._payload_edit(file_path="architecture.md"))
        deny.assert_called_once()

    def test_case_insensitive_mixed_case_edit_denied(self):
        deny = self._run(self._payload_edit(file_path="Architecture.MD"))
        deny.assert_called_once()

    def test_case_insensitive_bash_redirect_denied(self):
        deny = self._run(self._payload_bash("echo x > architecture.md"))
        deny.assert_called_once()

    def test_case_insensitive_lowercase_allows_on_go(self):
        self._write_package(decision="go")
        deny = self._run(self._payload_edit(file_path="architecture.md"))
        deny.assert_not_called()

    # Stage-field check (adversarial review, 2026-08-22): a package from a different stage
    # (once sibling gates exist) must not silently open this one.
    def test_denies_on_wrong_stage(self):
        self._write_package(decision="go", stage="architecture")
        deny = self._run(self._payload_edit())
        deny.assert_called_once()
        self.assertIn("stage", deny.call_args[0][0])

    def test_denies_on_missing_stage_field(self):
        pkg_path = self.tmp / cg.PACKAGE_RELPATH
        pkg_path.parent.mkdir(parents=True, exist_ok=True)
        pkg_path.write_text(json.dumps({"decision": "go"}))
        deny = self._run(self._payload_edit())
        deny.assert_called_once()

    # m2 fix: C4's claimed coverage of tee/sed -i, evidenced for real, not just `>`.
    def test_bash_tee_target_extraction(self):
        deny = self._run(self._payload_bash("echo x | tee ARCHITECTURE.md"))
        deny.assert_called_once()

    def test_bash_sed_inplace_target_extraction(self):
        deny = self._run(self._payload_bash("sed -i '' 's/x/y/' ARCHITECTURE.md"))
        deny.assert_called_once()

    # C6
    def test_fails_closed_on_malformed_package(self):
        pkg_path = self.tmp / cg.PACKAGE_RELPATH
        pkg_path.parent.mkdir(parents=True, exist_ok=True)
        pkg_path.write_text("{not valid json")
        deny = self._run(self._payload_edit())
        deny.assert_called_once()
        self.assertIn("could not be read as JSON", deny.call_args[0][0])

    # C7
    def test_noop_outside_foreman_project(self):
        outside = Path(tempfile.mkdtemp(prefix="fore71-nonforeman-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        deny = self._run({"tool_name": "Edit", "cwd": str(outside),
                           "tool_input": {"file_path": "ARCHITECTURE.md"}})
        deny.assert_not_called()

    def test_non_write_tool_ignored(self):
        deny = self._run({"tool_name": "Read", "cwd": str(self.tmp),
                           "tool_input": {"file_path": "ARCHITECTURE.md"}})
        deny.assert_not_called()

    def test_edit_with_no_file_path_ignored(self):
        deny = self._run({"tool_name": "Edit", "cwd": str(self.tmp), "tool_input": {}})
        deny.assert_not_called()


if __name__ == "__main__":
    unittest.main()
