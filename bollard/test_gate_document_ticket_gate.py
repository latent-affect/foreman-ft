"""Regression tests for REQ-30's generation-time enforcement, gate_document_ticket_gate.py.

    python3 -m unittest test_gate_document_ticket_gate -v
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gate_document_ticket_gate as gate


class CheckGenerationTimeLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="req30-gen-time-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _cfg(self, prefix="FORE"):
        foreman = self.root / ".foreman"
        foreman.mkdir(parents=True, exist_ok=True)
        (foreman / "tessera-project.json").write_text(json.dumps({
            "db_path": "/fake/db", "repo_root": "/fake/repo", "prefix": prefix,
        }))

    def test_non_write_tool_never_applicable(self):
        """Edit/MultiEdit imply the file already exists -- creation-time is a Write-only
        concept for this hook's deliberately narrow scope."""
        verdict, msg = gate.check_generation_time_link(
            "Edit", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root))
        self.assertIsNone(verdict)

    def test_non_gate_stage_document_not_applicable(self):
        verdict, msg = gate.check_generation_time_link(
            "Write", {"file_path": str(self.root / "notes.txt")}, str(self.root))
        self.assertIsNone(verdict)

    def test_existing_file_not_treated_as_creation(self):
        """A Write to an ALREADY-EXISTING gate document is an ordinary edit-shaped Write
        (the tool doesn't distinguish create-vs-overwrite), not a first-generation event --
        must not fire."""
        (self.root / "docs").mkdir()
        prd = self.root / "docs" / "PRD.md"
        prd.write_text("existing content")
        verdict, msg = gate.check_generation_time_link(
            "Write", {"file_path": str(prd)}, str(self.root))
        self.assertIsNone(verdict)

    def test_missing_config_asks_rather_than_silently_allows_or_denies(self):
        """Fail-open-to-ASK, not DENY: a config gap is unrelated to whether a ticket really
        exists, so blocking outright would be a false positive on real, legitimate work."""
        verdict, msg = gate.check_generation_time_link(
            "Write", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root))
        self.assertEqual(verdict, "ask")
        self.assertIn("tessera-project.json", msg)

    def test_new_gate_document_with_linked_ticket_allows_silently(self):
        self._cfg()
        with mock.patch.object(gate, "_run_tessera", return_value=(
            True, {"tickets": [{"summary": "Track PRD.md authorship", "description": ""}]}, "",
        )):
            verdict, msg = gate.check_generation_time_link(
                "Write", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root))
        self.assertIsNone(verdict)

    def test_new_gate_document_with_no_ticket_asks(self):
        """The real, motivating incident (REQ-30's own text): PRD.md created with zero
        tracking ticket."""
        self._cfg()
        with mock.patch.object(gate, "_run_tessera", return_value=(
            True, {"tickets": [{"summary": "unrelated ticket", "description": ""}]}, "",
        )):
            verdict, msg = gate.check_generation_time_link(
                "Write", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root))
        self.assertEqual(verdict, "ask")
        self.assertIn("PRD.md", msg)
        self.assertIn("FORE", msg)

    def test_tessera_query_failure_asks_not_silent(self):
        self._cfg()
        with mock.patch.object(gate, "_run_tessera", return_value=(False, None, "db locked")):
            verdict, msg = gate.check_generation_time_link(
                "Write", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root))
        self.assertEqual(verdict, "ask")
        self.assertIn("db locked", msg)

    def test_path_outside_project_root_not_applicable(self):
        verdict, msg = gate.check_generation_time_link(
            "Write", {"file_path": "/completely/unrelated/PRD.md"}, str(self.root))
        self.assertIsNone(verdict)

    def test_architecture_md_and_ship_charter_also_covered(self):
        """Confirms this reuses GATE_STAGE_DOCUMENTS wholesale, not a hand-copied partial
        list that could silently drift from the audit script's own set."""
        self._cfg()
        with mock.patch.object(gate, "_run_tessera", return_value=(True, {"tickets": []}, "")):
            v1, _ = gate.check_generation_time_link(
                "Write", {"file_path": str(self.root / "ARCHITECTURE.md")}, str(self.root))
            v2, _ = gate.check_generation_time_link(
                "Write", {"file_path": str(self.root / ".foreman" / "SHIP-CHARTER.json")},
                str(self.root))
        self.assertEqual(v1, "ask")
        self.assertEqual(v2, "ask")


class MainHookIntegrationTests(unittest.TestCase):
    """Confirms main() actually calls hc.ask() on the right verdict -- the pure-function
    tests above don't touch the hook_common integration at all."""

    def setUp(self):
        self.calls = []
        self.patcher = mock.patch.object(gate.hc, "ask", side_effect=lambda m: self.calls.append(m))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        rule_patcher = mock.patch.object(gate.hc, "set_rule")
        rule_patcher.start()
        self.addCleanup(rule_patcher.stop)

    def test_main_asks_on_untracked_new_document(self):
        with tempfile.TemporaryDirectory(prefix="req30-main-") as tmp:
            with mock.patch.object(gate, "_load_tessera_config", return_value={
                "db_path": "x", "repo_root": "y", "prefix": "FORE"}):
                with mock.patch.object(gate, "_run_tessera", return_value=(True, {"tickets": []}, "")):
                    gate.main({"tool_name": "Write", "cwd": tmp,
                               "tool_input": {"file_path": f"{tmp}/docs/PRD.md"}})
        self.assertEqual(len(self.calls), 1)

    def test_main_silent_on_non_applicable_call(self):
        gate.main({"tool_name": "Bash", "cwd": "/tmp", "tool_input": {"command": "ls"}})
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
