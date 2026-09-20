"""GOALS.json C13-C17 (gitrepo_to_ingest amendment). Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_gitrepo_pull -v

Uses a real temporary git repo and real `git` subprocess calls, per this project's own
'executed means a real call ran, not that a mock returned a plausible value' convention
(foreman:integration-test's guardrail) -- a mocked git log would not catch a real parsing bug
in run_git_log's actual output shape.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import gitrepo_pull
from atlas.ingest.tests._helpers import TempDb


class RealGitRepo:
    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name)
        self._run("init", "-q")
        self._run("config", "user.email", "test-fixture@example.invalid")
        self._run("config", "user.name", "Test Fixture")

    def _run(self, *args):
        subprocess.run(["git", "-C", str(self.path), *args], check=True, capture_output=True)

    def commit(self, filename, content, subject):
        (self.path / filename).write_text(content)
        self._run("add", filename)
        self._run("commit", "-q", "-m", subject)

    def cleanup(self):
        self._tmpdir.cleanup()


class GitLogParsingTests(unittest.TestCase):
    def setUp(self):
        self.repo = RealGitRepo()

    def tearDown(self):
        self.repo.cleanup()

    def test_a_repo_with_no_commits_parses_to_an_empty_list(self):
        output = gitrepo_pull.run_git_log(self.repo.path)
        self.assertEqual(gitrepo_pull.parse_git_log(output), [])

    def test_single_commit_single_file_parses_correctly(self):
        self.repo.commit("a.txt", "line1\nline2\n", "First commit")
        commits = gitrepo_pull.parse_git_log(gitrepo_pull.run_git_log(self.repo.path))
        self.assertEqual(len(commits), 1)
        c = commits[0]
        self.assertEqual(c["subject"], "First commit")
        self.assertEqual(c["files"], [("a.txt", 2, 0)])
        self.assertEqual(len(c["sha"]), 40)
        self.assertTrue(c["committed_ts"].endswith("+00:00"))

    def test_multiple_commits_multiple_files_parse_in_reverse_chronological_order(self):
        self.repo.commit("a.txt", "one\n", "First")
        self.repo.commit("b.txt", "two\nthree\n", "Second")
        self.repo.commit("a.txt", "one\nmodified\n", "Third")
        commits = gitrepo_pull.parse_git_log(gitrepo_pull.run_git_log(self.repo.path))
        self.assertEqual([c["subject"] for c in commits], ["Third", "Second", "First"])
        self.assertEqual(commits[0]["files"], [("a.txt", 1, 0)])
        self.assertEqual(commits[1]["files"], [("b.txt", 2, 0)])

    def test_a_nonexistent_repo_path_raises_gitlogerror_not_silently_empty(self):
        with self.assertRaises(gitrepo_pull.GitLogError):
            gitrepo_pull.run_git_log("/nonexistent/not/a/repo")


class TicketCandidateRegexTests(unittest.TestCase):
    def test_regex_matches_every_cited_real_corpus_example(self):
        # ARCHITECTURE.md section 8's real-corpus dropped-candidate list -- these must all be
        # CANDIDATES (matched by the regex), even though every one of them is later dropped by
        # the registered-prefix check, not by the regex itself.
        cited = ["P0-2", "P0-3", "P0-4", "P0-6", "F5-001", "F5-002", "F5-003", "F5-004",
                 "MR-0", "ISO-8601"]
        for example in cited:
            with self.subTest(example=example):
                self.assertIn(example, gitrepo_pull.extract_ticket_candidates(f"see {example} for detail"))

    def test_a_real_registered_style_prefix_is_extracted(self):
        self.assertEqual(
            gitrepo_pull.extract_ticket_candidates("Fix override widening (ATLASSN-9)"),
            ["ATLASSN-9"],
        )

    def test_lowercase_is_not_a_candidate(self):
        self.assertEqual(gitrepo_pull.extract_ticket_candidates("fixes atlassn-9"), [])


class InsertAndValidationTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDb()
        self.db.conn.execute(
            "INSERT INTO dim_project (project_prefix, project_codename, source_root, "
            "root_state, refreshed_at) VALUES ('ATLASSN', 'ATLASSN', '/x', 'git-repo', "
            "'2026-01-01T00:00:00Z')"
        )
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()

    def test_a_registered_prefix_candidate_becomes_a_real_ticket_link(self):
        commit = {
            "sha": "a" * 40, "committed_ts": "2026-08-22T00:00:00+00:00",
            "author_raw": "someone@example.invalid", "subject": "Fix thing (ATLASSN-9)",
            "files": [("x.py", 3, 1)],
        }
        gitrepo_pull.insert_commit(self.db.conn, "ATLASSN", commit, self.db.run_id, {"ATLASSN"})
        self.db.conn.commit()
        row = self.db.conn.execute(
            "SELECT ticket_id, origin FROM git_commit_ticket WHERE sha = ?", (commit["sha"],)
        ).fetchone()
        self.assertEqual(row, ("ATLASSN-9", "commit-message-regex"))
        dropped = self.db.conn.execute("SELECT COUNT(*) FROM git_ticket_candidate_dropped").fetchone()[0]
        self.assertEqual(dropped, 0)

    def test_an_unregistered_prefix_candidate_is_dropped_not_linked(self):
        commit = {
            "sha": "b" * 40, "committed_ts": "2026-08-22T00:00:00+00:00",
            "author_raw": "someone@example.invalid", "subject": "See P0-3 for the rubric",
            "files": [],
        }
        gitrepo_pull.insert_commit(self.db.conn, "ATLASSN", commit, self.db.run_id, {"ATLASSN"})
        self.db.conn.commit()
        linked = self.db.conn.execute("SELECT COUNT(*) FROM git_commit_ticket").fetchone()[0]
        self.assertEqual(linked, 0)
        dropped = self.db.conn.execute(
            "SELECT candidate, reason FROM git_ticket_candidate_dropped WHERE sha = ?", (commit["sha"],)
        ).fetchone()
        self.assertEqual(dropped, ("P0-3", "unregistered-prefix"))

    def test_author_is_stored_hashed_not_in_plaintext(self):
        commit = {
            "sha": "c" * 40, "committed_ts": "2026-08-22T00:00:00+00:00",
            "author_raw": "real.person@example.invalid", "subject": "No ticket here",
            "files": [],
        }
        gitrepo_pull.insert_commit(self.db.conn, "ATLASSN", commit, self.db.run_id, {"ATLASSN"})
        self.db.conn.commit()
        stored = self.db.conn.execute(
            "SELECT author_hash FROM git_commit WHERE sha = ?", (commit["sha"],)
        ).fetchone()[0]
        self.assertNotIn("real.person", stored)
        self.assertEqual(stored, gitrepo_pull.hash_author("real.person@example.invalid"))

    def test_reinserting_the_same_commit_is_a_no_op_not_a_duplicate(self):
        commit = {
            "sha": "d" * 40, "committed_ts": "2026-08-22T00:00:00+00:00",
            "author_raw": "someone@example.invalid", "subject": "Idempotency check (ATLASSN-9)",
            "files": [("x.py", 1, 0)],
        }
        gitrepo_pull.insert_commit(self.db.conn, "ATLASSN", commit, self.db.run_id, {"ATLASSN"})
        gitrepo_pull.insert_commit(self.db.conn, "ATLASSN", commit, self.db.run_id, {"ATLASSN"})
        self.db.conn.commit()
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM git_commit").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM git_commit_file").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM git_commit_ticket").fetchone()[0], 1)


class PullOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDb()
        self.db.conn.execute(
            "INSERT INTO dim_project (project_prefix, project_codename, source_root, "
            "root_state, refreshed_at) VALUES ('ATLASSN', 'ATLASSN', '/x', 'git-repo', "
            "'2026-01-01T00:00:00Z')"
        )
        self.db.conn.commit()
        self.repo = RealGitRepo()

    def tearDown(self):
        self.db.close()
        self.repo.cleanup()

    def test_pull_against_a_real_repo_ingests_every_commit_exactly_once_across_two_passes(self):
        self.repo.commit("a.txt", "1\n", "First (ATLASSN-1)")
        self.repo.commit("b.txt", "2\n", "Second")
        n1 = gitrepo_pull.pull_git_commits(self.db.conn, self.repo.path, "ATLASSN", {"ATLASSN"}, self.db.run_id)
        self.assertEqual(n1, 2)
        self.repo.commit("c.txt", "3\n", "Third (ATLASSN-2)")
        n2 = gitrepo_pull.pull_git_commits(self.db.conn, self.repo.path, "ATLASSN", {"ATLASSN"}, self.db.run_id)
        self.assertEqual(n2, 3, "full re-walk sees all 3 commits; INSERT OR IGNORE makes re-seeing the first 2 free")
        total = self.db.conn.execute("SELECT COUNT(*) FROM git_commit").fetchone()[0]
        self.assertEqual(total, 3)
        linked = self.db.conn.execute("SELECT ticket_id FROM git_commit_ticket ORDER BY ticket_id").fetchall()
        self.assertEqual([r[0] for r in linked], ["ATLASSN-1", "ATLASSN-2"])


if __name__ == "__main__":
    unittest.main()
