import subprocess
import tempfile
import unittest
from pathlib import Path

from .. import discrepancy
from ...gitops.gitops import GitOps
from ...store.store import Store


def run_git_internal(path, args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


class DiscrepancyTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.store = Store(self.root / "test.db", codename="TESTPROJ", prefix="TP")
        self.ops = GitOps(self.store, self.root / "stages")

        src = self.root / "source"
        src.mkdir()
        run_git_internal(src, ["init"])
        run_git_internal(src, ["config", "user.email", "a@b.c"])
        run_git_internal(src, ["config", "user.name", "test"])
        (src / "a.py").write_text("a\n")
        (src / "b.py").write_text("b\n")
        run_git_internal(src, ["add", "."])
        run_git_internal(src, ["commit", "-m", "touch a.py and b.py"])
        self.commit_sha = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()

        self.ops.init_stage("dev", source_repo=src)
        self.ops.promote_stage("dev", self.commit_sha, "agent")
        self.tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_mismatched_claim_produces_discrepancy(self):
        self.store.record_claim(
            self.tid, "agent", "fixed a.py", ["a.py"], self.commit_sha,
        )
        result = discrepancy.discrepancy_for_ticket(self.store, self.ops, self.tid, "dev")
        self.assertFalse(result["matches"])
        self.assertEqual(result["touched_but_not_claimed"], ["b.py"])
        self.assertEqual(result["claimed_but_not_touched"], [])

    def test_matching_claim_produces_no_discrepancy(self):
        self.store.record_claim(
            self.tid, "agent", "touched both files", ["a.py", "b.py"], self.commit_sha,
        )
        result = discrepancy.discrepancy_for_ticket(self.store, self.ops, self.tid, "dev")
        self.assertTrue(result["matches"])
        self.assertEqual(result["touched_but_not_claimed"], [])
        self.assertEqual(result["claimed_but_not_touched"], [])


class SingleRepoDiscrepancyTests(unittest.TestCase):
    """Fix: discrepancy_for_ticket() only ever worked against
    TESSERA's own staged dev/integration/FT/prod deployment model. Every real Foreman pilot
    project is a plain single repo with no staging concept -- these tests exercise the real
    single-repo path directly, with no GitOps/staging involved at all."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.store = Store(self.root / "test.db")

        self.repo = self.root / "repo"
        self.repo.mkdir()
        run_git_internal(self.repo, ["init"])
        run_git_internal(self.repo, ["config", "user.email", "a@b.c"])
        run_git_internal(self.repo, ["config", "user.name", "test"])
        (self.repo / "a.py").write_text("a\n")
        (self.repo / "b.py").write_text("b\n")
        run_git_internal(self.repo, ["add", "."])
        run_git_internal(self.repo, ["commit", "-m", "touch a.py and b.py"])
        self.commit_sha = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()

        self.store.register_project("SINGLEREPO", "SR", source_root=str(self.repo))
        self.tid = self.store.create_ticket(
            ticket_type="Task", reporter="me", actor="agent", project="SR",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_files_touched_singlerepo_real_commit(self):
        files = discrepancy.files_touched_singlerepo(self.repo, self.commit_sha)
        self.assertEqual(sorted(files), ["a.py", "b.py"])

    def test_files_touched_singlerepo_unknown_sha_returns_none(self):
        files = discrepancy.files_touched_singlerepo(self.repo, "0" * 40)
        self.assertIsNone(files)

    def test_files_touched_singlerepo_option_sha_returns_none(self):
        files = discrepancy.files_touched_singlerepo(self.repo, "--output=/tmp/pwned")
        self.assertIsNone(files)
        self.assertFalse(Path("/tmp/pwned").exists())

    def test_files_touched_singlerepo_not_a_repo_returns_none(self):
        files = discrepancy.files_touched_singlerepo(self.root, self.commit_sha)
        self.assertIsNone(files)

    def test_discrepancy_singlerepo_no_claim_returns_none(self):
        result = discrepancy.discrepancy_for_ticket_singlerepo(self.store, self.tid)
        self.assertIsNone(result)

    def test_discrepancy_singlerepo_matching_claim(self):
        self.store.record_claim(
            self.tid, "agent", "touched both", ["a.py", "b.py"], self.commit_sha,
        )
        result = discrepancy.discrepancy_for_ticket_singlerepo(self.store, self.tid)
        self.assertTrue(result["matches"])

    def test_discrepancy_singlerepo_mismatched_claim(self):
        self.store.record_claim(
            self.tid, "agent", "only fixed a.py", ["a.py"], self.commit_sha,
        )
        result = discrepancy.discrepancy_for_ticket_singlerepo(self.store, self.tid)
        self.assertFalse(result["matches"])
        self.assertEqual(result["touched_but_not_claimed"], ["b.py"])

    def test_discrepancy_singlerepo_no_source_root_returns_none(self):
        self.store.register_project("NOROOT", "NR", source_root=None)
        tid2 = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent", project="NR")
        self.store.record_claim(tid2, "agent", "claim with no real project root", ["x.py"], self.commit_sha)
        result = discrepancy.discrepancy_for_ticket_singlerepo(self.store, tid2)
        self.assertIsNone(result)

    def test_check_and_record_closure_no_claim_records_event(self):
        result = discrepancy.check_and_record_closure(self.store, self.tid, "agent")
        self.assertEqual(result["checked"], "no_claim")
        conn = self.store.conn_internal()
        rows = conn.execute(
            "SELECT event_type FROM events WHERE ticket_id=? ORDER BY id", (self.tid,)
        ).fetchall()
        self.assertIn(("ClosedWithNoClaim",), rows)

    def test_rebuild_projection_after_diff_check_does_not_raise(self):
        self.store.record_claim(
            self.tid, "agent", "touched both", ["a.py", "b.py"], self.commit_sha,
        )
        discrepancy.check_and_record_closure(self.store, self.tid, "agent")
        rebuilt = self.store.rebuild_projection()
        self.assertIn("tickets", rebuilt)

    def test_check_and_record_closure_matching_claim_records_event(self):
        self.store.record_claim(
            self.tid, "agent", "touched both", ["a.py", "b.py"], self.commit_sha,
        )
        result = discrepancy.check_and_record_closure(self.store, self.tid, "agent")
        self.assertEqual(result["checked"], "diff")
        self.assertTrue(result["matches"])
        conn = self.store.conn_internal()
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE ticket_id=? AND event_type=?",
            (self.tid, "ClaimDiscrepancyChecked"),
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_check_and_record_closure_mismatched_claim_still_records_event(self):
        # Advisory only -- a real mismatch does NOT raise or block, it's recorded as a real,
        # queryable finding, same as a match.
        self.store.record_claim(
            self.tid, "agent", "only fixed a.py", ["a.py"], self.commit_sha,
        )
        result = discrepancy.check_and_record_closure(self.store, self.tid, "agent")
        self.assertEqual(result["checked"], "diff")
        self.assertFalse(result["matches"])
        conn = self.store.conn_internal()
        rows = conn.execute(
            "SELECT payload FROM events WHERE ticket_id=? AND event_type=?",
            (self.tid, "ClaimDiscrepancyChecked"),
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_check_and_record_closure_never_raises_on_internal_failure(self):
        # A claim whose commit_sha doesn't exist in the repo at all -- files_touched_singlerepo
        # returns None, check_and_record_closure must degrade to "skipped", not raise.
        self.store.record_claim(self.tid, "agent", "claims a fake commit", ["a.py"], "f" * 40)
        result = discrepancy.check_and_record_closure(self.store, self.tid, "agent")
        self.assertEqual(result["checked"], "skipped")

    def test_e2e_real_cli_transition_records_diff_check(self):
        """No mocks: a real subprocess invocation of the actual CLI's transition command,
        the same way a real user/agent would run it -- confirms the wiring in cli.py itself,
        not just the underlying function."""
        self.store.record_claim(
            self.tid, "agent", "touched both", ["a.py", "b.py"], self.commit_sha,
        )
        ticket_system_root = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            ["python3", "-m", "tessera.api.cli", "--db", str(self.root / "test.db"),
             "transition", self.tid, "--actor", "agent", "--status", "closed"],
            cwd=ticket_system_root, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        conn = self.store.conn_internal()
        rows = conn.execute(
            "SELECT event_type FROM events WHERE ticket_id=? AND event_type=?",
            (self.tid, "ClaimDiscrepancyChecked"),
        ).fetchall()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
