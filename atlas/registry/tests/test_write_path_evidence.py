"""C2: clause (b), evidence resolution against the real verdict ledger. Real anchors from
OBSERVED-BEHAVIOUR-WRITE-PATH-ATLASSN-131-20260912.md.

Every act referenced here is a REAL historical act. Clause (b)'s same-session rule means this
suite can never mint evidence of its own (schema doc, finding 6), so the alternative to real
anchors is a seeded ledger that proves the code agrees with the fixture -- which is the
failure mode C3's anti-fixture clause exists to name.
"""
import tempfile
import unittest
from pathlib import Path

from atlas.registry import write_path as wp
from atlas.registry.tests import write_path_fixtures as fx

REAL_SESSION = fx.REAL_TRANSCRIPT_SESSION
REAL_TOOL_USE_ID = fx.REAL_ANCHOR_TOOL_USE_ID
# ATLASSN-163: was a hand-invented edge_id ("...registry_self") declared nowhere in the real
# ARCHITECTURE.md, paired with a `reach` dict built solely to legitimize it -- exactly the
# caller-supplied-map trust hole ATLASSN-163 closed. write_assertion's new independent check
# (verify_write_target_against_frozen_architecture) correctly rejected it once that check
# existed; the fixture, not the fix, was wrong. This clause (b) suite has no stake in WHICH
# edge is asserted, so it now uses a real declared one -- write_path_fixtures.DECLARED_EDGE_
# GATE_CLIENT, the same constant test_write_dependency_failure.py's own accepted-write
# end-to-end test already exercises successfully against fx.REAL_PROJECT_ROOT.
REAL_DECLARED_EDGE_ID = fx.DECLARED_EDGE_GATE_CLIENT
REAL_DECLARED_REACH = fx.reach_for(REAL_DECLARED_EDGE_ID)


class EvidenceClauseTests(unittest.TestCase):
    def test_real_completed_act_accepted(self):
        self.assertTrue(wp.resolve_evidence(REAL_TOOL_USE_ID, REAL_SESSION, "observed-probe"))

    def test_fabricated_tool_use_id_rejected(self):
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_evidence("toolu_00000000000000000000000", "s-anyone", "observed-probe")
        self.assertEqual(ctx.exception.reason, "evidence-unresolved")

    def test_cross_session_reference_rejected(self):
        """The real act exists, but under a different session than the one claiming it."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_evidence(REAL_TOOL_USE_ID, "s-not-the-real-session", "observed-probe")
        self.assertEqual(ctx.exception.reason, "evidence-wrong-session")

    def test_atlassn_165_real_transcript_from_a_different_real_session_rejected(self):
        """The write-side companion to test_revocation.py's TranscriptSessionBindingTests: two
        real, different sessions -- fx.REAL_DENIED_SESSION claimed as verifier_session_id,
        fx.REAL_TRANSCRIPT_PATH (which actually belongs to fx.REAL_TRANSCRIPT_SESSION) supplied
        as the transcript. store_path/audit_log_path/records_dir are deliberately nonexistent
        paths: verify_transcript_belongs_to_session runs as write_assertion's first statement,
        before clause (c) or clause (a) ever touch any of that plumbing, so this probe is also a
        pin on the ordering claim in write_assertion's own docstring, not only on the rejection
        reason."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.write_assertion(
                store_path="/nonexistent/registry.db",
                audit_log_path="/nonexistent/audit.jsonl",
                dispatch_record_id="d-1", verifier_session_id=fx.REAL_DENIED_SESSION,
                edge_id=REAL_DECLARED_EDGE_ID, edge_repo="atlas-sonnet",
                edge_component="registry", project_root=fx.REAL_PROJECT_ROOT,
                required_role="verifier", assertion_class="structural",
                evidence_tool_use_id=REAL_TOOL_USE_ID, evidence_class="observed-probe",
                transcript_path=fx.REAL_TRANSCRIPT_PATH,
                components={"registry": ["atlas/registry/**"]}, reach=REAL_DECLARED_REACH)
        self.assertEqual(ctx.exception.reason, "evidence-transcript-session-mismatch")

    def test_evidence_class_other_than_observed_probe_rejected(self):
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_evidence(REAL_TOOL_USE_ID, REAL_SESSION, "something-else")
        self.assertEqual(ctx.exception.reason, "evidence-class-invalid")

    def test_real_denied_act_rejected_g2(self):
        """WRONG-KIND-ROW, real: a resolving, same-session, unspent reference whose ledger row
        is a denied call, not a completed act."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_evidence(fx.REAL_DENIED_TOOL_USE_ID, fx.REAL_DENIED_SESSION,
                                "observed-probe")
        self.assertEqual(ctx.exception.reason, "evidence-denied")

    def test_real_hook_error_act_rejected(self):
        """A real act whose hook errored. This anchor has genuine PostToolUse rows, so it
        passes the completeness check and ONLY the error check can reject it -- see the note
        in write_path_fixtures for why the PreToolUse-only alternative would have been an
        order-dependent probe rather than a discriminating one."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_evidence(fx.REAL_ERRORED_TOOL_USE_ID, fx.REAL_ERRORED_SESSION,
                                "observed-probe")
        self.assertEqual(ctx.exception.reason, "evidence-hook-error")

    def test_real_pretooluse_only_act_rejected_as_incomplete(self):
        """A real act that never completed: PreToolUse rows only, and deliberately one with
        NO error and NO deny row, so evidence-incomplete is the only reason available and the
        assertion can be exact. 46,293 such acts exist, so this negative is abundant rather
        than contrived.

        The first draft of this probe used an act that also carried error rows and asserted
        the reason was one of two values. A disjunctive assertion cannot distinguish the
        behaviour under test from a neighbouring one, which is the same non-discriminating
        shape this suite exists to rule out."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_evidence("toolu_01LefhTVGGPgaUH9vEfqHsLi",
                                "41db6fdd-3bf2-4232-80b3-39ab07fe2b45", "observed-probe")
        self.assertEqual(ctx.exception.reason, "evidence-incomplete")

    def test_replayed_spent_reference_rejected(self):
        with tempfile.TemporaryDirectory(prefix="c2-replay-") as tmp:
            root = Path(tmp)
            records_dir = root / "dispatch-records"
            records_dir.mkdir()
            sessions_path = root / "sessions.jsonl"
            fx.write_sessions_stream(
                sessions_path, [fx.session_line(REAL_SESSION, "2026-01-01T00:00:00Z")])
            fx.seed_json_file(records_dir, "d-1", record_id="d-1",
                                     author_session_id="s-author",
                                     named_session_id=REAL_SESSION, role="verifier",
                                     project="atlas-sonnet", repo_scope=["registry"],
                                     created_at="2025-12-01T00:00:00Z")
            config_path = fx.write_config_and_decision(root)
            store_path = root / "registry.db"
            fx.create_store(store_path)
            audit_path = root / "audit.jsonl"
            components = {"registry": ["atlas/registry/**"]}

            kwargs = dict(
                store_path=store_path, audit_log_path=audit_path, dispatch_record_id="d-1",
                verifier_session_id=REAL_SESSION,
                edge_id=REAL_DECLARED_EDGE_ID,
                edge_repo="atlas-sonnet", edge_component="registry",
                project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
                assertion_class="structural", evidence_tool_use_id=REAL_TOOL_USE_ID,
                evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
                components=components, reach=REAL_DECLARED_REACH, config_path=config_path,
                records_dir=records_dir, sessions_stream_path=sessions_path)

            first = wp.write_assertion(**kwargs)
            self.assertTrue(first.startswith("a-"))

            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.write_assertion(**kwargs)
            self.assertEqual(ctx.exception.reason, "evidence-already-spent")

    def test_lease_comes_from_signed_config_not_a_default(self):
        """F2, behaviourally: the accepted write carries the fixture's deliberately odd lease,
        so a hardcoded default could not have produced it. Checked on the audit line rather
        than by reading the source, because int('86400') and 60*60*24 both defeat a grep."""
        import json
        with tempfile.TemporaryDirectory(prefix="c2-lease-") as tmp:
            root = Path(tmp)
            records_dir = root / "dispatch-records"
            records_dir.mkdir()
            sessions_path = root / "sessions.jsonl"
            fx.write_sessions_stream(
                sessions_path, [fx.session_line(REAL_SESSION, "2026-01-01T00:00:00Z")])
            fx.seed_json_file(records_dir, "d-1", record_id="d-1",
                                     author_session_id="s-author",
                                     named_session_id=REAL_SESSION, role="verifier",
                                     project="atlas-sonnet", repo_scope=["registry"],
                                     created_at="2025-12-01T00:00:00Z")
            config_path = fx.write_config_and_decision(root, structural_s=86401)
            store_path = root / "registry.db"
            fx.create_store(store_path)
            audit_path = root / "audit.jsonl"

            wp.write_assertion(
                store_path=store_path, audit_log_path=audit_path, dispatch_record_id="d-1",
                verifier_session_id=REAL_SESSION,
                edge_id=REAL_DECLARED_EDGE_ID,
                edge_repo="atlas-sonnet", edge_component="registry",
                project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
                assertion_class="structural", evidence_tool_use_id=REAL_TOOL_USE_ID,
                evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
                components={"registry": ["atlas/registry/**"]}, reach=REAL_DECLARED_REACH,
                config_path=config_path,
                records_dir=records_dir, sessions_stream_path=sessions_path)

            line = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(line["lease_s"], 86401)

    def build_write_fixture(self, root):
        """Everything one accepted write needs, returned as kwargs. Shared by the two
        fault-injection probes so they differ only in WHERE the process dies."""
        records_dir = root / "dispatch-records"
        records_dir.mkdir()
        sessions_path = root / "sessions.jsonl"
        fx.write_sessions_stream(
            sessions_path, [fx.session_line(REAL_SESSION, "2026-01-01T00:00:00Z")])
        fx.seed_json_file(records_dir, "d-1", record_id="d-1",
                          author_session_id="s-author", named_session_id=REAL_SESSION,
                          role="verifier", project="atlas-sonnet", repo_scope=["registry"],
                          created_at="2025-12-01T00:00:00Z")
        config_path = fx.write_config_and_decision(root)
        store_path = root / "registry.db"
        fx.create_store(store_path)
        return dict(
            store_path=str(store_path), audit_log_path=str(root / "audit.jsonl"),
            dispatch_record_id="d-1", verifier_session_id=REAL_SESSION,
            edge_id=REAL_DECLARED_EDGE_ID,
            edge_repo="atlas-sonnet", edge_component="registry",
            project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
            assertion_class="structural", evidence_tool_use_id=REAL_TOOL_USE_ID,
            evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
            components={"registry": ["atlas/registry/**"]}, reach=REAL_DECLARED_REACH,
            config_path=str(config_path),
            records_dir=str(records_dir), sessions_stream_path=str(sessions_path))

    def crash_during_write(self, root, kill_at):
        """Run one write in a CHILD PROCESS that dies abruptly at `kill_at`, via os._exit --
        no unwinding, no context managers running, no SQLite cleanup. A try/finally or a
        raised exception inside this process would let Python tidy up and would prove
        something weaker than a crash."""
        import json as json_module
        import subprocess
        import sys
        kwargs = self.build_write_fixture(root)
        driver = root / "crash_driver.py"
        driver.write_text(
            "import json, os, sys\n"
            "sys.path.insert(0, %r)\n" % str(Path(fx.REAL_PROJECT_ROOT)) +
            "from atlas.registry import write_path as wp\n"
            "from atlas.registry import write_audit as wa\n"
            "kwargs = json.loads(sys.argv[1])\n"
            "kwargs['components'] = {'registry': ['atlas/registry/**']}\n"
            "kwargs['reach'] = %r\n" % REAL_DECLARED_REACH +
            "real_fsync = os.fsync\n"
            "def dying_fsync(fd):\n"
            "    if %r == 'before-audit':\n" % kill_at +
            "        os._exit(97)\n"
            "    real_fsync(fd)\n"
            "    os._exit(98)\n"
            "os.fsync = dying_fsync\n"
            "wa.os.fsync = dying_fsync\n"
            "wp.write_assertion(**kwargs)\n"
            "print('NO CRASH', file=sys.stderr)\n",
            encoding="utf-8")
        payload = dict(kwargs)
        payload.pop("components")
        payload.pop("reach")
        result = subprocess.run(
            [sys.executable, str(driver), json_module.dumps(payload)],
            capture_output=True, text=True)
        return kwargs, result

    def test_crash_before_commit_burns_nothing(self):
        """C2's fault-injection requirement: kill the process between the spend and the
        commit; on reopen spent_ref must lack the id AND assertion must lack the row.

        The whole point of putting both inserts in one transaction is that a crash costs the
        verifier nothing -- the observed act stays spendable. A half-applied write would burn
        a real, unrepeatable piece of evidence."""
        import sqlite3
        with tempfile.TemporaryDirectory(prefix="c2-crash-") as tmp:
            root = Path(tmp)
            kwargs, result = self.crash_during_write(root, "after-audit")
            self.assertEqual(result.returncode, 98,
                             f"expected the child to die at the injection point; got "
                             f"{result.returncode}: {result.stderr[-400:]}")

            connection = sqlite3.connect(kwargs["store_path"])
            assertions = connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
            spent = connection.execute(
                "SELECT COUNT(*) FROM spent_ref WHERE tool_use_id = ?",
                (REAL_TOOL_USE_ID,)).fetchone()[0]
            connection.close()
            self.assertEqual(assertions, 0, "a killed write left an assertion row behind")
            self.assertEqual(spent, 0, "a killed write burned the evidence reference")

    def test_crash_after_audit_append_leaves_the_documented_orphan_line(self):
        """The one crash window the schema doc names and accepts: the audit line is appended
        and fsynced, then the commit is lost. Replay must see an audit line with no store row
        and report it as a DETECTED divergence -- reporting is ATLASSN-132's to build; what
        this pins is that the window has exactly the shape 132 will have to handle."""
        with tempfile.TemporaryDirectory(prefix="c2-orphan-") as tmp:
            root = Path(tmp)
            kwargs, result = self.crash_during_write(root, "after-audit")
            self.assertEqual(result.returncode, 98)
            audit_lines = Path(kwargs["audit_log_path"]).read_text(
                encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1,
                             "the audit line should be on disk -- it is appended and fsynced "
                             "before the commit that was lost")

    def test_write_assertion_has_no_lease_parameter(self):
        """A payload-supplied lease is unreachable BY CONSTRUCTION, not by a check that a
        refactor could bypass. This pins the absence so it cannot be reintroduced quietly."""
        import inspect
        self.assertNotIn("lease_s", inspect.signature(wp.write_assertion).parameters)


if __name__ == "__main__":
    unittest.main()
