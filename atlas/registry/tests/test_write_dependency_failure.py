"""C4: each of the four dependencies (dispatch-record store, sessions stream, verdict ledger,
session-transcript store) fails toward a named RETRYABLE rejection when unreachable, never
toward a genuine clause failure or a crash.

Four, not three: the session-transcript store joined the list with the 2026-09-12 amendment
that moved clause (c)'s target source off the ledger.
"""
import tempfile
import unittest
from pathlib import Path

from atlas.registry import architecture_parse, write_path as wp
from atlas.registry.tests import write_path_fixtures as fx

EDGE_REPO = "atlas-sonnet"
EDGE_COMPONENT = "registry"
ROLE = "verifier"

COMPONENTS = architecture_parse.parse_components(
    architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT))


class DispatchRecordStoreDependencyTests(unittest.TestCase):
    def test_dir_absent_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.resolve_role("d-1", EDGE_REPO, EDGE_COMPONENT, ROLE, records_dir=missing,
                                sessions_stream_path=Path(tmp) / "sessions.jsonl")
            self.assertEqual(ctx.exception.reason, "dispatch-record-store-unreachable")
            self.assertTrue(ctx.exception.retryable)

    def test_restored_then_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records_dir = root / "dispatch-records"
            records_dir.mkdir()
            sessions_path = root / "sessions.jsonl"
            fx.write_sessions_stream(
                sessions_path, [fx.session_line("s-named", "2026-09-12T00:00:00Z")])
            fx.seed_json_file(records_dir, "d-1", record_id="d-1",
                                     author_session_id="s-author", named_session_id="s-named",
                                     role=ROLE, project=EDGE_REPO,
                                     repo_scope=[EDGE_COMPONENT],
                                     created_at="2026-08-01T00:00:00Z")
            record = wp.resolve_role("d-1", EDGE_REPO, EDGE_COMPONENT, ROLE,
                                     records_dir=records_dir,
                                     sessions_stream_path=sessions_path)
            self.assertEqual(record["record_id"], "d-1")


class SessionsStreamDependencyTests(unittest.TestCase):
    def test_file_absent_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            records_dir = Path(tmp) / "dispatch-records"
            fx.seed_json_file(records_dir, "d-1", record_id="d-1",
                                     author_session_id="s-author", named_session_id="s-named",
                                     role=ROLE, project=EDGE_REPO,
                                     repo_scope=[EDGE_COMPONENT],
                                     created_at="2026-08-01T00:00:00Z")
            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.resolve_role("d-1", EDGE_REPO, EDGE_COMPONENT, ROLE,
                                records_dir=records_dir,
                                sessions_stream_path=Path(tmp) / "no-such-sessions.jsonl")
            self.assertEqual(ctx.exception.reason, "sessions-stream-unreachable")
            self.assertTrue(ctx.exception.retryable)


class VerdictLedgerDependencyTests(unittest.TestCase):
    def test_file_absent_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.resolve_evidence("toolu_anything", "s-anyone", "observed-probe",
                                    verdict_ledger_path=Path(tmp) / "no-such-verdicts.jsonl")
            self.assertEqual(ctx.exception.reason, "verdict-ledger-unreachable")
            self.assertTrue(ctx.exception.retryable)

    def test_restored_then_accepted(self):
        self.assertTrue(wp.resolve_evidence(
            fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_TRANSCRIPT_SESSION, "observed-probe"))


class SessionTranscriptStoreDependencyTests(unittest.TestCase):
    def test_file_absent_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.resolve_component_for_write_edit(
                    Path(tmp) / "no-such-transcript.jsonl", fx.REAL_ANCHOR_TOOL_USE_ID,
                    fx.REAL_PROJECT_ROOT, COMPONENTS, "registry")
            self.assertEqual(ctx.exception.reason, "session-transcript-store-unreachable")
            self.assertTrue(ctx.exception.retryable)

    def test_restored_then_accepted(self):
        component = wp.resolve_component_for_write_edit(
            fx.REAL_TRANSCRIPT_PATH, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT,
            COMPONENTS, "registry")
        self.assertEqual(component, "registry")


class AllFourRestoredEndToEndTests(unittest.TestCase):
    def test_same_write_accepted_once_every_dependency_is_healthy(self):
        with tempfile.TemporaryDirectory(prefix="c4-e2e-") as tmp:
            root = Path(tmp)
            records_dir = root / "dispatch-records"
            records_dir.mkdir()
            sessions_path = root / "sessions.jsonl"
            fx.write_sessions_stream(
                sessions_path,
                [fx.session_line(fx.REAL_TRANSCRIPT_SESSION, "2026-01-01T00:00:00Z")])
            fx.seed_json_file(
                records_dir, "d-1", record_id="d-1", author_session_id="s-author",
                named_session_id=fx.REAL_TRANSCRIPT_SESSION, role="verifier",
                project="atlas-sonnet", repo_scope=["registry"],
                created_at="2025-12-01T00:00:00Z")
            config_path = fx.write_config_and_decision(root)
            store_path = root / "registry.db"
            fx.create_store(store_path)
            audit_path = root / "audit.jsonl"

            assertion_uid = wp.write_assertion(
                store_path=store_path, audit_log_path=audit_path, dispatch_record_id="d-1",
                verifier_session_id=fx.REAL_TRANSCRIPT_SESSION,
                edge_id="claude-hooks-v2:hooks/:registers:registry_gate_client_wired",
                edge_repo="atlas-sonnet", edge_component="registry",
                project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
                assertion_class="structural",
                evidence_tool_use_id=fx.REAL_ANCHOR_SECOND_TOOL_USE_ID,
                evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
                components=COMPONENTS, reach=fx.reach_for(fx.DECLARED_EDGE_GATE_CLIENT),
                config_path=config_path,
                records_dir=records_dir, sessions_stream_path=sessions_path)
            self.assertTrue(assertion_uid.startswith("a-"))


if __name__ == "__main__":
    unittest.main()
