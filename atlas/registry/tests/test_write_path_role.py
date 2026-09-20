"""C1: clause (a), role only from a valid, re-read dispatch record. Every probe from
GOALS.json's own C1 verification text, using observed reason strings from
OBSERVED-BEHAVIOUR-WRITE-PATH-ATLASSN-131-20260912.md."""
import tempfile
import unittest
from pathlib import Path

from atlas.registry import write_path as wp
from atlas.registry.tests import write_path_fixtures as fx

EDGE_REPO = "atlas-sonnet"
EDGE_COMPONENT = "registry"
ROLE = "verifier"


class RoleClauseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="c1-role-")
        self.root = Path(self._tmp.name)
        self.records_dir = self.root / "dispatch-records"
        # Must exist even when no record is written into it, or every probe below rejects
        # dispatch-record-store-unreachable instead of the reason it means to exercise.
        self.records_dir.mkdir()
        self.sessions_path = self.root / "sessions.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def resolve(self, record_id):
        return wp.resolve_role(record_id, EDGE_REPO, EDGE_COMPONENT, ROLE,
                               records_dir=self.records_dir,
                               sessions_stream_path=self.sessions_path)

    def test_payload_only_role_claim_rejected_no_bypass_exists(self):
        """There is no code path that accepts a bare role string without a real record --
        proven by pointing at a record_id that was never written.

        Same code path as test_missing_record_rejected below -- kept as two named tests
        because C1's own probe list names them separately (a payload-only claim vs. a
        genuinely missing record read as two distinct requirements in the frozen text), not
        because they exercise different code."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-never-written")
        self.assertEqual(ctx.exception.reason, "no-dispatch-record")
        self.assertFalse(ctx.exception.retryable)

    def test_self_authored_record_rejected(self):
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-x", "2026-09-12T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-x", named_session_id="s-x", role=ROLE,
                                 project=EDGE_REPO, repo_scope=[EDGE_COMPONENT],
                                 created_at="2026-09-01T00:00:00Z")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-1")
        self.assertEqual(ctx.exception.reason, "self-authored-dispatch-record")

    def test_record_postdating_session_start_rejected(self):
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-named", "2026-09-01T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-author", named_session_id="s-named",
                                 role=ROLE, project=EDGE_REPO, repo_scope=[EDGE_COMPONENT],
                                 created_at="2026-09-10T00:00:00Z")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-1")
        self.assertEqual(ctx.exception.reason, "dispatch-record-postdates-session-start")

    def test_missing_record_rejected(self):
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-does-not-exist")
        self.assertEqual(ctx.exception.reason, "no-dispatch-record")

    def test_absent_session_rejected_g1(self):
        """Named session nowhere in sessions.jsonl -- fail-closed, not a skip."""
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-someone-else", "2026-09-01T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-author", named_session_id="s-named",
                                 role=ROLE, project=EDGE_REPO, repo_scope=[EDGE_COMPONENT],
                                 created_at="2026-08-01T00:00:00Z")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-1")
        self.assertEqual(ctx.exception.reason, "named-session-absent-from-stream")
        self.assertFalse(ctx.exception.retryable)

    def test_scope_mismatch_disjoint_project_rejected(self):
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-named", "2026-09-12T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-author", named_session_id="s-named",
                                 role=ROLE, project="unrelated-project",
                                 repo_scope=[EDGE_COMPONENT], created_at="2026-08-01T00:00:00Z")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-1")
        self.assertEqual(ctx.exception.reason, "scope-mismatch:project")

    def test_prefix_collision_rejected_g5(self):
        """G5: a record scoped to repo 'atlas' must not authorize an edge in 'atlas-sonnet'.

        Same reason string and same code line as the disjoint-project probe above, and NOT
        redundant with it: this one's input is a strict PREFIX of the real repo name, so an
        implementation using startswith() rather than equality would accept this while still
        rejecting 'unrelated-project'. The discriminating power is in the input, not the
        assertion -- verified by mutation, where a startswith() variant fails this test and
        passes the other.

        (C1's text calls the field 'repo_scope'; in the landed schema the repo lives in
        `project` and `repo_scope` is the component list. The semantic the probe needs is the
        repo-name one, so it goes in `project`.)"""
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-named", "2026-09-12T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-author", named_session_id="s-named",
                                 role=ROLE, project="atlas", repo_scope=[EDGE_COMPONENT],
                                 created_at="2026-08-01T00:00:00Z")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-1")
        self.assertEqual(ctx.exception.reason, "scope-mismatch:project")

    def test_component_not_in_scope_rejected(self):
        """The other half of the containment rule: the repo matches, the component does not."""
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-named", "2026-09-12T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-author", named_session_id="s-named",
                                 role=ROLE, project=EDGE_REPO, repo_scope=["warehouse"],
                                 created_at="2026-08-01T00:00:00Z")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-1")
        self.assertEqual(ctx.exception.reason, "scope-mismatch:component")

    def test_role_mismatch_rejected(self):
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-named", "2026-09-12T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-author", named_session_id="s-named",
                                 role="reviewer", project=EDGE_REPO,
                                 repo_scope=[EDGE_COMPONENT], created_at="2026-08-01T00:00:00Z")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("d-1")
        self.assertEqual(ctx.exception.reason, "role-mismatch")

    def test_valid_in_scope_record_accepted(self):
        fx.write_sessions_stream(self.sessions_path,
                                 [fx.session_line("s-named", "2026-09-12T00:00:00Z")])
        fx.seed_json_file(self.records_dir, "d-1", record_id="d-1",
                                 author_session_id="s-author", named_session_id="s-named",
                                 role=ROLE, project=EDGE_REPO, repo_scope=[EDGE_COMPONENT],
                                 created_at="2026-08-01T00:00:00Z")
        record = self.resolve("d-1")
        self.assertEqual(record["record_id"], "d-1")


if __name__ == "__main__":
    unittest.main()
