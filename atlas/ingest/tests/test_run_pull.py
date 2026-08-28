"""GOALS.json C18-C20 (later amendment). Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.ingest.tests.test_run_pull -v
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import run_pull
from atlas.ingest.tests._helpers import TempDb


class DetectRootStateTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_source_root_is_no_source_root(self):
        self.assertEqual(run_pull.detect_root_state(None), "no-source-root")
        self.assertEqual(run_pull.detect_root_state(""), "no-source-root")

    def test_a_path_that_does_not_exist_is_root_missing(self):
        self.assertEqual(run_pull.detect_root_state(str(self.root / "nope")), "root-missing")

    def test_a_real_directory_with_no_git_is_not_a_git_repo(self):
        self.assertEqual(run_pull.detect_root_state(str(self.root)), "not-a-git-repo")

    def test_a_real_git_repo_with_dot_git_as_a_directory_is_git_repo(self):
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        self.assertEqual(run_pull.detect_root_state(str(self.root)), "git-repo")

    def test_a_worktree_with_dot_git_as_a_file_is_still_git_repo(self):
        # FATAL-class fix this mirrors: os.path.isdir(root/'.git') alone misreads a worktree
        # (real .git is elsewhere, this root only has a file pointing at it) as not-a-git-repo.
        (self.root / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/x\n")
        self.assertEqual(run_pull.detect_root_state(str(self.root)), "git-repo")


class SyncDimProjectTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDb()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name) / "real-repo"
        self.repo_root.mkdir()
        subprocess.run(["git", "-C", str(self.repo_root), "init", "-q"], check=True)

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def _fake_tessera(self, rows):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, prefix TEXT, codename TEXT, source_root TEXT)"
        )
        conn.executemany(
            "INSERT INTO projects (id, prefix, codename, source_root) VALUES (?, ?, ?, ?)", rows
        )
        conn.commit()
        return conn

    def test_sync_populates_dim_project_with_the_real_detected_root_state(self):
        tessera_conn = self._fake_tessera([
            (1, "REALP", "Real Project", str(self.repo_root)),
            (2, "MISSP", "Missing Project", "/definitely/not/a/real/path"),
        ])
        n = run_pull.sync_dim_project(self.db.conn, tessera_conn)
        self.assertEqual(n, 2)
        rows = dict(self.db.conn.execute(
            "SELECT project_prefix, root_state FROM dim_project"
        ).fetchall())
        self.assertEqual(rows["REALP"], "git-repo")
        self.assertEqual(rows["MISSP"], "root-missing")

    def test_sync_is_idempotent_and_updates_on_re_sync(self):
        tessera_conn = self._fake_tessera([(1, "REALP", "Real Project", str(self.repo_root))])
        run_pull.sync_dim_project(self.db.conn, tessera_conn)
        run_pull.sync_dim_project(self.db.conn, tessera_conn)
        count = self.db.conn.execute("SELECT COUNT(*) FROM dim_project").fetchone()[0]
        self.assertEqual(count, 1)


class RunOrchestrationTests(unittest.TestCase):
    """Exercises run() end to end against a real (fake) TESSERA sqlite file on disk and a real
    temporary git repo -- not the live production tessera.db (that's integration_test.py's job,
    per this component's own out_of_scope), but a real file, a real connection, real subprocess
    git calls, and a real persistent warehouse db path, not an in-memory shortcut."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        subprocess.run(["git", "-C", str(self.repo_root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.repo_root), "config", "user.email", "t@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo_root), "config", "user.name", "T"], check=True)
        (self.repo_root / "a.txt").write_text("1\n")
        subprocess.run(["git", "-C", str(self.repo_root), "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo_root), "commit", "-q", "-m", "Fix thing (REALP-1)"], check=True)

        self.tessera_db_path = self.root / "fake_tessera.db"
        import sqlite3
        conn = sqlite3.connect(str(self.tessera_db_path))
        conn.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, prefix TEXT, codename TEXT, source_root TEXT)"
        )
        conn.execute(
            "INSERT INTO projects (id, prefix, codename, source_root) VALUES (1, 'REALP', 'Real Project', ?)",
            (str(self.repo_root),),
        )
        conn.execute(
            "CREATE VIEW v_flat AS SELECT 1 AS event_id, 'TicketCreated' AS event_type, "
            "'2026-08-22T00:00:00Z' AS event_ts, 'someone' AS actor, 'human' AS actor_kind, "
            "'REALP-1' AS ticket_id, 'REALP' AS project_prefix, 'Task' AS ticket_type, "
            "'open' AS ticket_status, 0 AS ticket_is_closed, 'low' AS ticket_priority, "
            "NULL AS ticket_severity, NULL AS ticket_has_frozen_criteria, "
            "NULL AS ticket_criteria_frozen_before_work, 0 AS ticket_criteria_count, "
            "0 AS ticket_claim_count, NULL AS ticket_lead_time_hours, "
            "0 AS comment_has_code_snippet, NULL AS status_from, NULL AS status_to"
        )
        conn.commit()
        conn.close()

        self.warehouse_db_path = self.root / "atlas.db"
        # Deterministic: exercises the named-skip path (F3) rather than any real file that
        # happens to exist at the real default path on whatever machine runs this suite.
        self.missing_verdicts = self.root / "no-such-verdicts.jsonl"
        self.missing_audit = [("audit-fake", self.root / "no-such-audit.jsonl")]

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_run_against_real_files_populates_all_three_targets(self):
        summary = run_pull.run(
            self.warehouse_db_path, self.tessera_db_path,
            self.missing_verdicts, self.missing_audit,
        )
        self.assertTrue(self.warehouse_db_path.is_file(), "run() must create a real persistent warehouse db file")
        self.assertEqual(summary["projects_synced"], 1)
        self.assertEqual(summary["tessera_events_ingested"], 1)
        self.assertEqual(summary["git_repos_pulled"], 1)
        self.assertEqual(summary["git_commits_seen"], {"REALP": 1})

        from atlas.warehouse import migrate
        conn = migrate.connect(str(self.warehouse_db_path))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tessera_event").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM git_commit").fetchone()[0], 1)
        linked = conn.execute("SELECT ticket_id FROM git_commit_ticket").fetchone()
        self.assertEqual(linked[0], "REALP-1")
        conn.close()

    def test_a_second_real_run_does_not_duplicate_anything(self):
        run_pull.run(self.warehouse_db_path, self.tessera_db_path, self.missing_verdicts, self.missing_audit)
        summary2 = run_pull.run(
            self.warehouse_db_path, self.tessera_db_path, self.missing_verdicts, self.missing_audit,
        )
        self.assertEqual(summary2["tessera_events_ingested"], 0, "no new TESSERA events on the second real pass")

        from atlas.warehouse import migrate
        conn = migrate.connect(str(self.warehouse_db_path))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tessera_event").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM git_commit").fetchone()[0], 1)
        conn.close()

    def test_missing_verdict_and_audit_sources_are_a_named_skip_not_a_crash(self):
        summary = run_pull.run(
            self.warehouse_db_path, self.tessera_db_path, self.missing_verdicts, self.missing_audit,
        )
        self.assertIsNone(summary["verdict_rows_inserted"])
        self.assertIn("source file not found", summary["verdict_detail"])
        self.assertIsNone(summary["audit_rows_inserted"])
        self.assertIn("source file not found", summary["audit_detail_by_source"]["audit-fake"])
        self.assertEqual(summary["cwds_resolved"], 0, "no hook_verdict rows means nothing to resolve")

    def test_tessera_db_opened_readonly_cannot_be_written_by_this_process(self):
        conn = run_pull._open_tessera_readonly(self.tessera_db_path)
        with self.assertRaises(Exception):
            conn.execute("INSERT INTO projects (id, prefix, codename) VALUES (99, 'X', 'X')")
            conn.commit()
        conn.close()


class VerdictAuditResolvePullTests(unittest.TestCase):
    """The dogfood-found gap -- verdict ledger, audit-plane, and resolve must
    actually run against real files, not stay silently unwired while tessera/git pull alone
    made the warehouse look populated."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

        self.tessera_db_path = self.root / "fake_tessera.db"
        import sqlite3
        conn = sqlite3.connect(str(self.tessera_db_path))
        conn.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, prefix TEXT, codename TEXT, source_root TEXT)"
        )
        conn.execute(
            "CREATE VIEW v_flat AS SELECT 1 AS event_id, 'TicketCreated' AS event_type, "
            "'2026-08-22T00:00:00Z' AS event_ts, 'someone' AS actor, 'human' AS actor_kind, "
            "'X-1' AS ticket_id, 'X' AS project_prefix, 'Task' AS ticket_type, "
            "'open' AS ticket_status, 0 AS ticket_is_closed, 'low' AS ticket_priority, "
            "NULL AS ticket_severity, NULL AS ticket_has_frozen_criteria, "
            "NULL AS ticket_criteria_frozen_before_work, 0 AS ticket_criteria_count, "
            "0 AS ticket_claim_count, NULL AS ticket_lead_time_hours, "
            "0 AS comment_has_code_snippet, NULL AS status_from, NULL AS status_to WHERE 0"
        )
        conn.commit()
        conn.close()

        self.warehouse_db_path = self.root / "atlas.db"

        import json
        self.verdicts_path = self.root / "verdicts.jsonl"
        self.verdicts_path.write_text(json.dumps({
            "ts": "2026-08-22T00:00:00.000001Z", "epoch_ms": 1000, "handler_id": "some_gate.py",
            "event": "PreToolUse", "verdict": "fire", "session_id": "s1", "cwd": "/some/unregistered/cwd",
            "decision": "allow",
        }) + "\n")

        self.audit_global_path = self.root / "safety.jsonl"
        self.audit_global_path.write_text(json.dumps({
            "schema_version": "audit-1", "ts": "2026-08-22T00:00:00.000000Z",
            "event_type": "SAFETY_ASK", "severity": "medium", "session_id": None,
            "cwd": "/some/unregistered/cwd", "ledger": "global-safety",
        }) + "\n")
        # A second, real, permanently-coexisting audit-plane file that
        # self-reports a ledger value ('project') different from the file it actually lives in --
        # the mapper records both (source_path = the real path, ledger_claim = the self-report).
        self.audit_marker_path = self.root / "marker.jsonl"
        self.audit_marker_path.write_text(json.dumps({
            "schema_version": "audit-1", "ts": "2026-08-22T00:00:01.000000Z",
            "event_type": "TASK_ANCHOR_CAPTURED", "severity": "info", "session_id": None,
            "cwd": "/some/unregistered/cwd", "ledger": "project",
        }) + "\n")
        self.audit_sources = [
            ("audit-global-safety", self.audit_global_path),
            ("audit-secondary", self.audit_marker_path),
        ]

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_real_verdict_and_audit_files_are_ingested_and_resolve_runs(self):
        summary = run_pull.run(
            self.warehouse_db_path, self.tessera_db_path, self.verdicts_path, self.audit_sources,
        )
        self.assertEqual(summary["verdict_rows_inserted"], 1)
        self.assertEqual(summary["audit_rows_inserted"], 2, "both real audit-plane files must be unioned")
        self.assertEqual(summary["audit_rows_inserted_by_source"], {
            "audit-global-safety": 1, "audit-secondary": 1,
        })
        self.assertEqual(summary["cwds_resolved"], 1)
        self.assertEqual(summary["resolution_counts"], {"unregistered": 1})

        from atlas.warehouse import migrate
        conn = migrate.connect(str(self.warehouse_db_path))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0], 2)
        claims = {r[0] for r in conn.execute("SELECT ledger_claim FROM audit_event")}
        self.assertEqual(claims, {"global-safety", "project"})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cwd_project").fetchone()[0], 1)
        conn.close()

        # Regression: this fixture's one verdict row is 'fire' for a handler with no
        # recorded 'silent' verdict at all -- handler_denominator_nonzero (a structural-zero-denominator class) is
        # ADVISORY severity, per ARCHITECTURE.md, and must land in dq_advisory_failures, never
        # in dq_contract_failures (a real bug in this file's first version conflated the two).
        self.assertIn("handler_denominator_nonzero", summary["dq_advisory_failures"])
        self.assertNotIn("handler_denominator_nonzero", summary["dq_contract_failures"])

    def test_a_second_real_run_ingests_zero_new_verdict_and_audit_rows(self):
        run_pull.run(self.warehouse_db_path, self.tessera_db_path, self.verdicts_path, self.audit_sources)
        summary2 = run_pull.run(
            self.warehouse_db_path, self.tessera_db_path, self.verdicts_path, self.audit_sources,
        )
        self.assertEqual(summary2["verdict_rows_inserted"], 0)
        self.assertEqual(summary2["audit_rows_inserted"], 0)

    def test_two_permanently_coexisting_audit_files_never_register_as_a_false_rotation(self):
        # The bug this design guards against: reusing one source_name for two different files
        # would make stream.tail() see a stream_id mismatch and log a false 'rotation' every run.
        run_pull.run(self.warehouse_db_path, self.tessera_db_path, self.verdicts_path, self.audit_sources)
        from atlas.warehouse import migrate
        conn = migrate.connect(str(self.warehouse_db_path))
        rotations = conn.execute(
            "SELECT COUNT(*) FROM ingest_stream_history WHERE reason != 'first-seen'"
        ).fetchone()[0]
        self.assertEqual(rotations, 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
