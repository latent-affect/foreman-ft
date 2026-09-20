"""ATLASSN-132 / C14: the ordering half. The audit line is appended and fsynced BEFORE the
commit, an append failure ABORTS the transaction with reason `audit-append-failed`, and the one
window that ordering deliberately leaves open -- appended, then the commit lost -- is reported by
replay as a DETECTED divergence rather than quietly reconciled.

C14 is the criterion that only a REAL crash can discharge. A raised exception inside this process
would let Python unwind and close the connection tidily, which proves something weaker than a
process that dies holding an open transaction. So the crash probe runs a child and kills it with
os._exit at the injection point, exactly as ATLASSN-131's C2 battery does.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from atlas.registry import architecture_parse, ddl, write_audit as wa, write_path as wp
from atlas.registry.tests import write_path_fixtures as fx

COMPONENTS = architecture_parse.parse_components(
    architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT))
EDGE_ONE = "claude-hooks-v2:hooks/:registers:registry_gate_client_wired"


class AppendFailureHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="c14-")
        self.root = Path(self.tmp.name)
        self.records_dir = self.root / "dispatch-records"
        self.records_dir.mkdir()
        self.sessions_path = self.root / "sessions.jsonl"
        fx.write_sessions_stream(
            self.sessions_path,
            [fx.session_line(fx.REAL_TRANSCRIPT_SESSION, "2026-01-01T00:00:00Z")])
        fx.seed_json_file(
            self.records_dir, "d-1", record_id="d-1", author_session_id="s-author",
            named_session_id=fx.REAL_TRANSCRIPT_SESSION, role="verifier",
            project="atlas-sonnet", repo_scope=["registry"],
            created_at="2025-12-01T00:00:00Z")
        self.config_path = fx.write_config_and_decision(self.root)
        self.store_path = self.root / "registry.db"
        fx.create_store(self.store_path)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, audit_log_path):
        return wp.write_assertion(
            store_path=self.store_path, audit_log_path=audit_log_path,
            dispatch_record_id="d-1", verifier_session_id=fx.REAL_TRANSCRIPT_SESSION,
            edge_id=EDGE_ONE, edge_repo="atlas-sonnet", edge_component="registry",
            project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
            assertion_class="structural", evidence_tool_use_id=fx.REAL_ANCHOR_TOOL_USE_ID,
            evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
            components=COMPONENTS, reach=fx.reach_for(EDGE_ONE), config_path=self.config_path,
            records_dir=self.records_dir, sessions_stream_path=self.sessions_path)

    def store_counts(self):
        connection = sqlite3.connect(self.store_path)
        try:
            return (connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0],
                    connection.execute("SELECT COUNT(*) FROM spent_ref").fetchone()[0])
        finally:
            connection.close()


class UnwritableTrailAbortsTheTransactionTests(AppendFailureHarness):
    def test_trail_path_is_a_directory(self):
        """A directory where the trail should be: open(..., 'a') raises IsADirectoryError, an
        OSError. Chosen alongside the permission case below because the two reach the same
        refusal through different errno, and a handler catching only one of them would pass a
        single-probe battery."""
        audit_path = self.root / "audit-as-a-directory"
        audit_path.mkdir()
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.write(audit_path)
        self.assertEqual(ctx.exception.reason, "audit-append-failed")
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(self.store_counts(), (0, 0),
                         "the store advanced past its tamper-evidence trail")

    def test_trail_file_is_read_only(self):
        audit_path = self.root / "audit.jsonl"
        audit_path.write_text("", encoding="utf-8")
        os.chmod(audit_path, 0o444)
        try:
            with self.assertRaises(wp.AssertionRejected) as ctx:
                self.write(audit_path)
        finally:
            os.chmod(audit_path, 0o644)
        self.assertEqual(ctx.exception.reason, "audit-append-failed")
        self.assertEqual(self.store_counts(), (0, 0))

    def test_the_evidence_reference_is_not_burned(self):
        """An append failure must cost the verifier nothing: the observed act stays spendable, so
        the same write succeeds once the trail is writable again. Without this the refusal would
        be fail-closed and lossy at the same time."""
        audit_path = self.root / "audit-as-a-directory"
        audit_path.mkdir()
        with self.assertRaises(wp.AssertionRejected):
            self.write(audit_path)
        good = self.root / "audit.jsonl"
        uid = self.write(good)
        self.assertTrue(uid.startswith("a-"))
        self.assertEqual(self.store_counts(), (1, 1))

    def test_a_writer_that_ignored_the_append_failure_would_commit(self):
        """The control, stated as the mutant it is: with the append result discarded, the same
        input commits a row. If this ever stops committing, the probes above are passing for a
        reason other than the abort they claim to test."""
        connection = ddl.connect(self.store_path)
        try:
            connection.execute("BEGIN")
            connection.execute(
                "INSERT INTO assertion (assertion_uid, edge_id, component, class, lease_s, "
                "verified_at, verifier_session_id, dispatch_record_id, evidence_tool_use_id, "
                "evidence_class) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("a-MUTANT", EDGE_ONE, "registry", "structural", 86401,
                 "2026-09-12T19:00:00Z", fx.REAL_TRANSCRIPT_SESSION, "d-1",
                 fx.REAL_ANCHOR_TOOL_USE_ID, "observed-probe"))
            try:
                wa.append_line(self.root / "audit-as-a-directory-2", {"op": "assert"})
            except wa.AuditAppendFailed:
                pass  # the mutation: swallow it and commit anyway
            connection.execute("COMMIT")
        finally:
            connection.close()
        self.assertEqual(self.store_counts()[0], 1,
                         "the fail-open shape must be reachable, or the abort proves nothing")


class CrashBetweenAppendAndCommitTests(AppendFailureHarness):
    """C14's second probe: the window the ordering accepts, and the report that makes it safe."""

    def crash_after_append(self):
        audit_path = self.root / "audit.jsonl"
        driver = self.root / "append_crash_driver.py"
        driver.write_text(
            "import json, os, sys\n"
            "sys.path.insert(0, %r)\n" % str(Path(fx.REAL_PROJECT_ROOT)) +
            "from atlas.registry import write_path as wp\n"
            "from atlas.registry import write_audit as wa\n"
            "kwargs = json.loads(sys.argv[1])\n"
            "kwargs['components'] = json.loads(sys.argv[2])\n"
            "kwargs['reach'] = json.loads(sys.argv[3])\n"
            "real_append = wa.append_line\n"
            "def dying_append(path, payload):\n"
            "    real_append(path, payload)\n"
            "    os._exit(98)\n"
            "wa.append_line = dying_append\n"
            "wp.write_audit.append_line = dying_append\n"
            "wp.write_assertion(**kwargs)\n"
            "print('NO CRASH', file=sys.stderr)\n",
            encoding="utf-8")
        kwargs = {
            "store_path": str(self.store_path), "audit_log_path": str(audit_path),
            "dispatch_record_id": "d-1", "verifier_session_id": fx.REAL_TRANSCRIPT_SESSION,
            "edge_id": EDGE_ONE, "edge_repo": "atlas-sonnet", "edge_component": "registry",
            "project_root": fx.REAL_PROJECT_ROOT, "required_role": "verifier",
            "assertion_class": "structural",
            "evidence_tool_use_id": fx.REAL_ANCHOR_TOOL_USE_ID,
            "evidence_class": "observed-probe",
            "transcript_path": fx.REAL_TRANSCRIPT_PATH,
            "config_path": str(self.config_path), "records_dir": str(self.records_dir),
            "sessions_stream_path": str(self.sessions_path),
        }
        result = subprocess.run(
            [sys.executable, str(driver), json.dumps(kwargs), json.dumps(COMPONENTS),
             json.dumps(fx.reach_for(EDGE_ONE))],
            capture_output=True, text=True)
        return audit_path, result

    def test_the_line_survives_and_the_row_does_not(self):
        audit_path, result = self.crash_after_append()
        self.assertEqual(result.returncode, 98,
                         f"expected death at the injection point; got {result.returncode}: "
                         f"{result.stderr[-400:]}")
        self.assertEqual(len(audit_path.read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(self.store_counts(), (0, 0))

    def test_replay_reports_the_orphan_as_a_divergence(self):
        """C14's own sentence: never silently reconciled. Replay rebuilds a store from the trail,
        the comparison finds a replayed assertion the live store does not have, and it comes back
        NAMED -- an empty divergence list here would be the F4 failure the trail exists to
        prevent."""
        audit_path, result = self.crash_after_append()
        self.assertEqual(result.returncode, 98)

        replayed, state = wa.replay_into_store(audit_path, self.root / "replayed.db")
        original = ddl.connect(self.store_path)
        try:
            divergences = wa.compare_store_to_replay(original, replayed)
        finally:
            original.close()
            replayed.close()

        kinds = [d["kind"] for d in divergences]
        self.assertIn(wa.ORPHAN_AUDIT_LINE, kinds, divergences)
        self.assertIn(wa.SPENT_REF_DIVERGENCE, kinds,
                      "the orphaned line also implies a spent_ref the store never took; both "
                      "halves of the lost commit are reported, not just the assertion")
        orphan_uid = next(d["detail"] for d in divergences
                          if d["kind"] == wa.ORPHAN_AUDIT_LINE)
        self.assertIn(orphan_uid, state["assertions"])
        self.assertTrue(orphan_uid.startswith("a-"))

    def test_a_healthy_write_produces_no_divergence(self):
        """The discriminating control for the probe above. If replay reported an orphan for a
        committed write too, the orphan assertion would be meaningless."""
        audit_path = self.root / "clean-audit.jsonl"
        self.write(audit_path)
        replayed, _ = wa.replay_into_store(audit_path, self.root / "clean-replayed.db")
        original = ddl.connect(self.store_path)
        try:
            self.assertEqual(wa.compare_store_to_replay(original, replayed), [])
        finally:
            original.close()
            replayed.close()

    def test_a_store_row_the_trail_cannot_explain_is_its_own_named_divergence(self):
        """The opposite direction, and the one C14's guarantee is actually about: the store must
        never be ahead of its trail. Reported under its own kind rather than folded into the
        orphan case, because the two mean opposite things to whoever reads the report."""
        audit_path = self.root / "audit.jsonl"
        self.write(audit_path)
        audit_path.write_text("", encoding="utf-8")
        replayed, _ = wa.replay_into_store(audit_path, self.root / "replayed-empty.db")
        original = ddl.connect(self.store_path)
        try:
            divergences = wa.compare_store_to_replay(original, replayed)
        finally:
            original.close()
            replayed.close()
        self.assertTrue(any(d["kind"] == wa.STORE_ROW_WITHOUT_AUDIT_LINE for d in divergences),
                        divergences)


if __name__ == "__main__":
    unittest.main()
