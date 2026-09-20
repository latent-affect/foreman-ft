"""Regression tests for REQ-30 clause 2's gate_document_ticket_staleness_check.py.

    python3 -m unittest test_gate_document_ticket_staleness_check -v
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import gate_document_ticket_staleness_check as gdsc


class CheckStalenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="req30-staleness-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Isolate the dedup state dir per test so real ~/.claude state never leaks in/out.
        self.state_tmp = tempfile.TemporaryDirectory(prefix="req30-staleness-state-")
        self.addCleanup(self.state_tmp.cleanup)
        self.state_patcher = mock.patch.object(gdsc, "STATE_DIR", Path(self.state_tmp.name))
        self.state_patcher.start()
        self.addCleanup(self.state_patcher.stop)

    def _cfg(self):
        d = self.root / ".foreman"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tessera-project.json").write_text(json.dumps({
            "db_path": "/fake/db", "repo_root": "/fake/repo", "prefix": "FORE",
        }))

    def _iso(self, minutes_ago):
        return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()

    def test_non_write_shaped_tool_not_applicable(self):
        msg = gdsc.check_staleness("Bash", {"command": "ls"}, str(self.root), "s1")
        self.assertIsNone(msg)

    def test_non_gate_stage_document_not_applicable(self):
        msg = gdsc.check_staleness("Write", {"file_path": str(self.root / "notes.txt")}, str(self.root), "s1")
        self.assertIsNone(msg)

    def test_no_config_not_applicable(self):
        msg = gdsc.check_staleness("Write", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root), "s1")
        self.assertIsNone(msg)

    def test_no_linked_ticket_not_applicable(self):
        """gate_document_ticket_gate.py's job (no ticket at all), not this one's."""
        self._cfg()
        with mock.patch.object(gdsc, "_find_linked_ticket", return_value=None):
            msg = gdsc.check_staleness("Write", {"file_path": str(self.root / "docs" / "PRD.md")},
                                        str(self.root), "s1")
        self.assertIsNone(msg)

    def test_freshly_updated_ticket_not_applicable(self):
        self._cfg()
        with mock.patch.object(gdsc, "_find_linked_ticket",
                                return_value={"ticket_id": "FORE-1", "updated_at": self._iso(5)}):
            msg = gdsc.check_staleness("Write", {"file_path": str(self.root / "docs" / "PRD.md")},
                                        str(self.root), "s1")
        self.assertIsNone(msg)

    def test_real_stale_ticket_nudges(self):
        """The real, motivating scenario: a gate document edited, its linked ticket untouched
        well past the threshold."""
        self._cfg()
        with mock.patch.object(gdsc, "_find_linked_ticket",
                                return_value={"ticket_id": "FORE-124", "updated_at": self._iso(90)}):
            msg = gdsc.check_staleness("Write", {"file_path": str(self.root / "docs" / "PRD.md")},
                                        str(self.root), "s1")
        self.assertIsNotNone(msg)
        self.assertIn("FORE-124", msg)
        self.assertIn("PRD.md", msg)

    def test_second_edit_same_session_same_document_deduped(self):
        self._cfg()
        with mock.patch.object(gdsc, "_find_linked_ticket",
                                return_value={"ticket_id": "FORE-124", "updated_at": self._iso(90)}):
            first = gdsc.check_staleness("Write", {"file_path": str(self.root / "docs" / "PRD.md")},
                                          str(self.root), "s1")
            second = gdsc.check_staleness("Edit", {"file_path": str(self.root / "docs" / "PRD.md")},
                                           str(self.root), "s1")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_different_session_not_deduped(self):
        """The dedup key is (session, document) -- a different session editing the same
        document must still get its own nudge."""
        self._cfg()
        with mock.patch.object(gdsc, "_find_linked_ticket",
                                return_value={"ticket_id": "FORE-124", "updated_at": self._iso(90)}):
            gdsc.check_staleness("Write", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root), "s1")
            second_session = gdsc.check_staleness(
                "Write", {"file_path": str(self.root / "docs" / "PRD.md")}, str(self.root), "s2")
        self.assertIsNotNone(second_session)

    def test_unparseable_updated_at_fails_silent_not_crash(self):
        self._cfg()
        with mock.patch.object(gdsc, "_find_linked_ticket",
                                return_value={"ticket_id": "FORE-1", "updated_at": "not-a-date"}):
            msg = gdsc.check_staleness("Write", {"file_path": str(self.root / "docs" / "PRD.md")},
                                        str(self.root), "s1")
        self.assertIsNone(msg)


class MainHookIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.patcher = mock.patch.object(gdsc.hc, "warn", side_effect=lambda m: self.calls.append(m))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        rule_patcher = mock.patch.object(gdsc.hc, "set_rule")
        rule_patcher.start()
        self.addCleanup(rule_patcher.stop)

    def test_main_warns_on_real_staleness(self):
        with mock.patch.object(gdsc, "check_staleness", return_value="a real nudge"):
            gdsc.main({"tool_name": "Write", "cwd": "/tmp", "session_id": "s1",
                       "tool_input": {"file_path": "/tmp/docs/PRD.md"}})
        self.assertEqual(self.calls, ["a real nudge"])

    def test_main_silent_when_nothing_to_say(self):
        with mock.patch.object(gdsc, "check_staleness", return_value=None):
            gdsc.main({"tool_name": "Bash", "cwd": "/tmp", "tool_input": {"command": "ls"}})
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
