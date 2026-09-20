#!/usr/bin/env python3
"""Regression tests for FORE-208's ask_user_question_gate.py: the fresh/session-matched/
non-stale category-declaration requirement, the four sanctioned categories, the peer-coordination
-> declared-orchestrator redirect (REQ-43), declaration consumption, and the fail-closed
entrypoint. Landed live 2026-08-27 (ticket comment 834) but never left a re-runnable test
artifact -- this is that artifact, same gap shape as FORE-228 that motivated test_bob_write_gate.py.

    python3 -m unittest test_ask_user_question_gate -v
"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ask_user_question_gate as aqg


class AskUserQuestionGateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore208-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def decl_path(self):
        return self.tmp / aqg.DECLARATION_RELPATH

    def write_decl(self, session_id="s1", category="mechanical-default", written_at=None,
                   why="test"):
        if written_at is None:
            written_at = time.time()
        self.decl_path().write_text(json.dumps({
            "session_id": session_id, "written_at": written_at, "category": category, "why": why,
        }))

    def write_orchestrator(self, held_by):
        (self.tmp / ".foreman" / "pdp-orchestrator.json").write_text(
            json.dumps({"session_id": held_by, "claimed_at": time.time()}))

    def write_transcript(self, cwd):
        """FORE-562: jurisdiction now reads hc.origin_cwd() (a transcript scan), not the live
        payload cwd -- a real one-line transcript with the intended cwd keeps every existing
        test's fixture meaning unchanged (the payload's cwd and the transcript's origin agree,
        matching the ordinary non-drifted case every one of these tests is actually about)."""
        path = self.tmp / "transcript.jsonl"
        path.write_text(json.dumps({"cwd": str(cwd)}) + "\n")
        return path

    def payload(self, session_id="s1", cwd=None):
        resolved_cwd = cwd if cwd is not None else self.tmp
        return {"tool_name": "AskUserQuestion", "session_id": session_id,
                "cwd": str(resolved_cwd),
                "transcript_path": str(self.write_transcript(resolved_cwd))}

    def run_main(self, payload):
        with mock.patch.object(aqg.hc, "deny") as deny, \
             mock.patch.object(aqg.hc, "set_rule") as set_rule, \
             mock.patch.object(aqg.hc, "audit") as audit:
            aqg.main(payload)
            return deny, set_rule, audit


class NonMatchingToolTests(AskUserQuestionGateTestBase):
    def test_non_ask_user_question_tool_is_silent_no_op(self):
        deny, set_rule, audit = self.run_main(
            {"tool_name": "Write", "session_id": "s1", "cwd": str(self.tmp)})
        deny.assert_not_called()
        set_rule.assert_not_called()
        audit.assert_not_called()


class ProjectRootDiscoveryTests(AskUserQuestionGateTestBase):
    def test_no_foreman_dir_anywhere_is_allowed_unscoped(self):
        outside = Path(tempfile.mkdtemp(prefix="fore208-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        deny, set_rule, audit = self.run_main(self.payload(cwd=outside))
        deny.assert_not_called()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:not-a-foreman-project")
        audit.assert_not_called()

    def test_foreman_dir_found_by_walking_up_from_a_subdirectory(self):
        nested = self.tmp / "src" / "deep"
        nested.mkdir(parents=True)
        self.write_decl()
        deny, set_rule, audit = self.run_main(self.payload(cwd=nested))
        deny.assert_not_called()

    def test_no_cwd_at_all_is_allowed_unscoped(self):
        deny, set_rule, audit = self.run_main(
            {"tool_name": "AskUserQuestion", "session_id": "s1", "cwd": None})
        deny.assert_not_called()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:not-a-foreman-project")


class DeclarationValidityTests(AskUserQuestionGateTestBase):
    def test_no_declaration_file_denies_undeclared(self):
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_called_once()
        self.assertIn("FORE-208", deny.call_args[0][0])
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:undeclared")

    def test_malformed_json_denies_undeclared(self):
        self.decl_path().write_text("{not valid json")
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_called_once()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:undeclared")

    def test_declaration_not_a_dict_denies_undeclared(self):
        self.decl_path().write_text(json.dumps(["not", "a", "dict"]))
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_called_once()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:undeclared")

    def test_declaration_from_a_different_session_denies_undeclared(self):
        self.write_decl(session_id="some-other-session")
        deny, set_rule, _ = self.run_main(self.payload(session_id="s1"))
        deny.assert_called_once()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:undeclared")

    def test_stale_declaration_denies_stale(self):
        self.write_decl(written_at=time.time() - aqg.STALE_AFTER_SECONDS - 1)
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_called_once()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:stale")

    def test_declaration_just_under_the_staleness_boundary_is_fresh(self):
        self.write_decl(written_at=time.time() - aqg.STALE_AFTER_SECONDS + 5)
        deny, _, _ = self.run_main(self.payload())
        deny.assert_not_called()

    def test_non_numeric_written_at_denies_stale(self):
        self.decl_path().write_text(json.dumps({
            "session_id": "s1", "written_at": "not-a-number", "category": "mechanical-default"}))
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_called_once()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:stale")

    def test_invalid_category_denies_invalid_category(self):
        self.write_decl(category="not-a-real-category")
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_called_once()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:invalid-category")

    def test_all_four_sanctioned_categories_are_accepted(self):
        for category in aqg.CATEGORIES:
            with self.subTest(category=category):
                self.write_decl(category=category)
                deny, _, audit = self.run_main(self.payload())
                deny.assert_not_called()
                self.assertEqual(audit.call_args[0][1]["category"], category)


class ValidDeclarationConsumptionTests(AskUserQuestionGateTestBase):
    def test_valid_declaration_allows_and_is_consumed(self):
        self.write_decl(category="mechanical-default")
        deny, set_rule, audit = self.run_main(self.payload())
        deny.assert_not_called()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:declared-mechanical-default")
        audit.assert_called_once()
        self.assertEqual(audit.call_args[0][0], "ASK_USER_QUESTION_DECLARED")
        self.assertFalse(self.decl_path().exists(), "declaration must be consumed (unlinked)")

    def test_repeat_question_without_redeclaring_denies_again(self):
        self.write_decl()
        self.run_main(self.payload())
        self.assertFalse(self.decl_path().exists())
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_called_once()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:undeclared")

    def test_why_field_is_truncated_to_500_chars_in_the_audit_record(self):
        self.write_decl(why="x" * 1000)
        _, _, audit = self.run_main(self.payload())
        self.assertEqual(len(audit.call_args[0][1]["why"]), 500)


class PeerCoordinationOrchestratorRedirectTests(AskUserQuestionGateTestBase):
    def test_peer_coordination_with_no_orchestrator_on_record_allows_unchanged(self):
        self.write_decl(category="peer-coordination")
        deny, set_rule, _ = self.run_main(self.payload())
        deny.assert_not_called()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:declared-peer-coordination")

    def test_peer_coordination_with_a_different_orchestrator_on_record_is_redirected(self):
        self.write_orchestrator(held_by="orchestrator-session")
        self.write_decl(session_id="s1", category="peer-coordination")
        deny, set_rule, audit = self.run_main(self.payload(session_id="s1"))
        deny.assert_called_once()
        reason = deny.call_args[0][0]
        self.assertIn("orchestrator-session", reason)
        self.assertIn("SendMessage", reason)
        set_rule.assert_called_once_with(
            "ASK-USER-QUESTION-GATE:peer-coordination-has-orchestrator")
        audit.assert_called_once()
        self.assertEqual(audit.call_args[0][1]["reason"], "peer-coordination-has-orchestrator")

    def test_redirect_also_consumes_the_declaration(self):
        self.write_orchestrator(held_by="orchestrator-session")
        self.write_decl(session_id="s1", category="peer-coordination")
        self.run_main(self.payload(session_id="s1"))
        self.assertFalse(self.decl_path().exists(),
                          "declaration must be consumed even on the redirect-deny path")

    def test_peer_coordination_when_the_asking_session_is_itself_the_orchestrator_allows(self):
        self.write_orchestrator(held_by="s1")
        self.write_decl(session_id="s1", category="peer-coordination")
        deny, _, _ = self.run_main(self.payload(session_id="s1"))
        deny.assert_not_called()

    def test_malformed_orchestrator_file_is_treated_as_no_orchestrator(self):
        (self.tmp / ".foreman" / "pdp-orchestrator.json").write_text("{not valid json")
        self.write_decl(category="peer-coordination")
        deny, _, _ = self.run_main(self.payload())
        deny.assert_not_called()

    def test_non_peer_coordination_category_ignores_orchestrator_file_entirely(self):
        self.write_orchestrator(held_by="orchestrator-session")
        self.write_decl(session_id="s1", category="mechanical-default")
        deny, set_rule, _ = self.run_main(self.payload(session_id="s1"))
        deny.assert_not_called()
        set_rule.assert_called_once_with("ASK-USER-QUESTION-GATE:declared-mechanical-default")


class FindProjectRootTests(unittest.TestCase):
    def test_none_cwd_returns_none(self):
        self.assertIsNone(aqg.find_project_root(None))

    def test_empty_string_cwd_returns_none(self):
        self.assertIsNone(aqg.find_project_root(""))


class EntrypointTests(unittest.TestCase):
    """Fail-closed entrypoint (module docstring section 4.5-style reasoning): an internal bug
    must deny, never silently allow an undeclared question through."""

    def test_non_dict_payload_denies_internal_error(self):
        with mock.patch.object(aqg.hc, "read_input", return_value=["not", "a", "dict"]), \
             mock.patch.object(aqg.hc, "deny") as mock_deny, \
             mock.patch.object(aqg.hc, "audit"), \
             mock.patch.object(aqg.hc, "set_rule"), \
             mock.patch.object(aqg, "verdict_ledger"), \
             mock.patch.object(aqg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            aqg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_called_once()
        self.assertIn("internal error", mock_deny.call_args[0][0])

    def test_internal_exception_in_main_denies_fail_closed(self):
        data = {"tool_name": "AskUserQuestion", "session_id": "s1", "cwd": "/tmp"}
        with mock.patch.object(aqg.hc, "read_input", return_value=data), \
             mock.patch.object(aqg, "main", side_effect=RuntimeError("simulated bug")), \
             mock.patch.object(aqg.hc, "deny") as mock_deny, \
             mock.patch.object(aqg.hc, "audit"), \
             mock.patch.object(aqg.hc, "set_rule"), \
             mock.patch.object(aqg, "verdict_ledger"), \
             mock.patch.object(aqg, "audit_lib") as mock_audit_lib, \
             self.assertRaises(SystemExit) as ctx:
            aqg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_called_once()
        self.assertIn("internal error", mock_deny.call_args[0][0])
        mock_audit_lib.audit_append.assert_called_once()

    def test_normal_allow_flow_records_verdict_and_exits_zero(self):
        data = {"tool_name": "Write", "session_id": "s1", "cwd": "/tmp"}
        with mock.patch.object(aqg.hc, "read_input", return_value=data), \
             mock.patch.object(aqg.hc, "deny") as mock_deny, \
             mock.patch.object(aqg, "verdict_ledger") as mock_ledger, \
             mock.patch.object(aqg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            aqg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_not_called()
        mock_ledger.record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
