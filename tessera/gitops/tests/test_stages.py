import subprocess
import tempfile
import unittest
from pathlib import Path

from ..exceptions import GitCommandError, InvalidRevisionError, NonFastForwardError, SyncedFolderError
from ..gitops import GitOps
from ...store.store import Store


def run_git_internal(path, args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def make_source_repo(root):
    src = root / "source"
    src.mkdir()
    run_git_internal(src, ["init"])
    run_git_internal(src, ["config", "user.email", "a@b.c"])
    run_git_internal(src, ["config", "user.name", "test"])
    (src / "file.txt").write_text("v1\n")
    run_git_internal(src, ["add", "."])
    run_git_internal(src, ["commit", "-m", "v1"])
    c1 = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (src / "file.txt").write_text("v2\n")
    run_git_internal(src, ["add", "."])
    run_git_internal(src, ["commit", "-m", "v2"])
    c2 = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return src, c1, c2


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.store = Store(self.root / "tessera.db", codename="TESTPROJ", prefix="TP")
        self.ops = GitOps(self.store, self.root / "stages")
        self.src, self.c1, self.c2 = make_source_repo(self.root)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_init_stage_is_independent_repo(self):
        path = self.ops.init_stage("dev", source_repo=self.src)
        self.assertTrue((path / ".git").is_dir())
        # Independent repo: has its own object store, not a worktree .git FILE pointing
        # back at a shared repo (a git-worktree checkout has a FILE there, not a DIR).
        self.assertTrue((path / ".git").is_dir())

    def test_sync_root_rejected(self):
        with self.assertRaises(SyncedFolderError):
            GitOps(self.store, self.root / "Library" / "Mobile Documents" / "stages")

    def test_promote_ff_tags_and_records_event(self):
        self.ops.init_stage("dev", source_repo=self.src)
        new_head = self.ops.promote_stage("dev", self.c1, "agent")
        self.assertEqual(new_head, self.c1)
        self.assertEqual(self.store.get_stage_head("dev")["commit_sha"], self.c1)
        events = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='StagePromoted'"
        ).fetchone()[0]
        self.assertEqual(events, 1)
        tags = subprocess.run(
            ["git", "-C", str(self.ops.stage_path("dev")), "tag"],
            capture_output=True, text=True,
        ).stdout
        self.assertIn(f"cp-promote-dev-{self.c1[:12]}", tags)

    def test_promote_non_fastforward_raises(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        # c1 is an ancestor of c2 (already promoted past it) -- promoting "back" to c1
        # is not a fast-forward from current HEAD (c2).
        with self.assertRaises(NonFastForwardError):
            self.ops.promote_stage("dev", self.c1, "agent")

    def test_rollback_resets_and_records_event(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c1, "agent")
        self.ops.promote_stage("dev", self.c2, "agent")
        before_ticket_count = len(self.store.list_tickets(include_archived=True))

        self.ops.rollback_stage("dev", self.c1, "agent")
        self.assertEqual(self.ops.head("dev"), self.c1)
        self.assertEqual(self.store.get_stage_head("dev")["commit_sha"], self.c1)
        after_ticket_count = len(self.store.list_tickets(include_archived=True))
        self.assertEqual(before_ticket_count, after_ticket_count)  # no ticket data touched

        rollback_events = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='StageRolledBack'"
        ).fetchone()[0]
        self.assertEqual(rollback_events, 1)

    def test_reconcile_flags_stale_on_direct_git_reset(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.link_ticket_commit(tid, "dev", "agent", commit_sha=self.c2)
        self.ops.reconcile_stage("dev")
        row = self.store.conn_internal().execute(
            "SELECT stale FROM ticket_commit_links WHERE ticket_id=? AND stage='dev'", (tid,)
        ).fetchone()
        self.assertEqual(row[0], 0)

        # Direct git reset on the stage repo, bypassing gitops entirely.
        run_git_internal(self.ops.stage_path("dev"), ["reset", "--hard", self.c1])
        self.ops.reconcile_stage("dev")
        row2 = self.store.conn_internal().execute(
            "SELECT stale FROM ticket_commit_links WHERE ticket_id=? AND stage='dev'", (tid,)
        ).fetchone()
        self.assertEqual(row2[0], 1)

    def test_reconcile_unflags_when_ancestry_restored(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.link_ticket_commit(tid, "dev", "agent", commit_sha=self.c2)

        run_git_internal(self.ops.stage_path("dev"), ["reset", "--hard", self.c1])
        self.ops.reconcile_stage("dev")
        stale = self.store.conn_internal().execute(
            "SELECT stale FROM ticket_commit_links WHERE ticket_id=? AND stage='dev'", (tid,)
        ).fetchone()[0]
        self.assertEqual(stale, 1)

        # Restore ancestry (direct reset back, bypassing gitops again).
        run_git_internal(self.ops.stage_path("dev"), ["reset", "--hard", self.c2])
        self.ops.reconcile_stage("dev")
        stale2 = self.store.conn_internal().execute(
            "SELECT stale FROM ticket_commit_links WHERE ticket_id=? AND stage='dev'", (tid,)
        ).fetchone()[0]
        self.assertEqual(stale2, 0)

    def test_reconcile_stage_git_failure_does_not_silently_mark_stale(self):
        # TESS-68: a linked commit that was never fetched into this stage repo makes
        # `git merge-base --is-ancestor` fail with a real git error (object doesn't
        # exist), returncode != 0 and != 1 -- a different failure than a genuine
        # "not an ancestor" (returncode 1). Before the fix, reconcile_stage collapsed
        # both into should_be_stale=True; now it must raise instead of silently writing
        # a guessed value, and must leave the ticket's existing stale flag untouched.
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        # A well-formed but genuinely nonexistent SHA -- NOT a second real repo's commit:
        # make_source_repo's fixed content/author/message means two calls inside the
        # same wall-clock second can produce byte-identical, and therefore
        # identically-hashed, commits (content addressing) -- confirmed live while
        # writing this test, a real "different repo, same hash" surprise, not a flaw
        # in the reviewed code.
        fake_sha = "f" * 40
        self.store.link_ticket_commit(tid, "dev", "agent", commit_sha=fake_sha)
        before = self.store.conn_internal().execute(
            "SELECT stale FROM ticket_commit_links WHERE ticket_id=? AND stage='dev'", (tid,)
        ).fetchone()[0]
        with self.assertRaises(GitCommandError):
            self.ops.reconcile_stage("dev")
        after = self.store.conn_internal().execute(
            "SELECT stale FROM ticket_commit_links WHERE ticket_id=? AND stage='dev'", (tid,)
        ).fetchone()[0]
        self.assertEqual(before, after)

    def test_missing_stage_repo_gives_clear_error_not_raw_git_fatal(self):
        # TESS-64: reconcile_stage/diff/etc. on a stage that has never been promoted
        # used to surface a raw `fatal: cannot change to '<path>': No such file or
        # directory` from the git subprocess itself -- real (non-silent) but easy to
        # misread as "nothing happened." Now the missing-repo case is named explicitly,
        # before git is even invoked.
        with self.assertRaises(GitCommandError) as ctx:
            self.ops.diff("dev", self.c1)
        self.assertIn("has this stage ever been promoted", str(ctx.exception))

    def test_diff_returns_real_output(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        diff_text = self.ops.diff("dev", self.c2)
        self.assertIn("file.txt", diff_text)
        self.assertIn("v2", diff_text)
        files = self.ops.files_touched("dev", self.c2)
        self.assertEqual(files, ["file.txt"])

    def test_branch_link_is_informational_only(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        # Branch-only link (no commit_sha): must not raise and must not be touched by
        # reconcile_stage()'s ancestry check (which only looks at commit_sha IS NOT NULL).
        self.store.link_ticket_commit(tid, "dev", "agent", branch="feature/x")
        results = self.ops.reconcile_stage("dev")
        self.assertNotIn(tid, [r[0] for r in results])
        row = self.store.conn_internal().execute(
            "SELECT branch, commit_sha FROM ticket_commit_links WHERE ticket_id=? AND stage='dev'",
            (tid,),
        ).fetchone()
        self.assertEqual(row[0], "feature/x")
        self.assertIsNone(row[1])

    def test_files_touched_rejects_argument_injection_shaped_revision(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        with self.assertRaises(InvalidRevisionError):
            self.ops.files_touched("dev", "--output=/tmp/tess159-pwned")

    def test_diff_rejects_argument_injection_shaped_revision(self):
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        with self.assertRaises(InvalidRevisionError):
            self.ops.diff("dev", "--output=/tmp/tess159-pwned")

    def test_files_touched_arbitrary_write_payload_no_longer_writes_a_file(self):
        # TESS-159 live-reproduced payload: before the fix, this exact commit_sha caused
        # `git show --output=<path> ...` to write a real file at an attacker-chosen path
        # -- an arbitrary-write primitive reachable via ticket data. Reproduces the same
        # payload shape here and asserts the target file is never created.
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        target = self.root / "tess159-pwned.txt"
        self.assertFalse(target.exists())
        with self.assertRaises(InvalidRevisionError):
            self.ops.files_touched("dev", f"--output={target}")
        self.assertFalse(target.exists())

    def test_diff_and_files_touched_reject_bare_branch_name(self):
        # ARCHITECTURE.md:262-267 -- a bare ref/branch name must not resolve as if it
        # were a pinned commit.
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c2, "agent")
        with self.assertRaises(InvalidRevisionError):
            self.ops.files_touched("dev", "master")
        with self.assertRaises(InvalidRevisionError):
            self.ops.diff("dev", "master")

    def test_promote_nonexistent_commit_raises_git_command_error_not_nonfastforward(self):
        # code-review finding: a commit that was never fetched (merge-base exits 128,
        # "not a valid object") was previously misdiagnosed as NonFastForwardError,
        # which sends a debugger looking at the wrong cause.
        self.ops.init_stage("dev", source_repo=self.src)
        self.ops.promote_stage("dev", self.c1, "agent")
        with self.assertRaises(GitCommandError) as ctx:
            self.ops.promote_stage("dev", "0" * 40, "agent")
        self.assertNotIsInstance(ctx.exception, NonFastForwardError)
        self.assertIn("does not exist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
