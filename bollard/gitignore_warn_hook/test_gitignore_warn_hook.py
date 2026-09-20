"""Regression tests for CHV2-89's gitignore_warn_hook.py.

    python3 -m unittest test_gitignore_warn_hook -v

Real git repos (tempdir, real `git init`), real `git check-ignore` subprocess calls, real
subprocess dispatch of the hook script for the PostToolUse-shape tests, a real `git worktree
add`, and a real fake-`git`-on-PATH wrapper for the check-ignore-degraded case (same repro
method the CHV2-89 review probe used) -- nothing mocked.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = str(HERE / "gitignore_warn_hook.py")

sys.path.insert(0, str(HERE))
import gitignore_warn_hook as giw  # noqa: E402


def run_git(args, cwd):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def make_repo(root, gitignore_text=""):
    root.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-q"], root)
    run_git(["config", "--local", "user.email", "test@example.invalid"], root)
    run_git(["config", "--local", "user.name", "Test Fixture"], root)
    if gitignore_text:
        (root / ".gitignore").write_text(gitignore_text)
        run_git(["add", ".gitignore"], root)
        run_git(["commit", "-q", "-m", "gitignore"], root)
    return root


def run_hook(payload, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, SCRIPT], input=json.dumps(payload), capture_output=True, text=True,
        env=full_env,
    )
    return proc


def parsed_context(stdout_text):
    stripped = stdout_text.strip()
    if stripped == "":
        return None
    obj = json.loads(stripped)
    return obj.get("hookSpecificOutput", {}).get("additionalContext")


class GitRepoRootTests(unittest.TestCase):
    def test_real_repo_resolves_root(self):
        with tempfile.TemporaryDirectory(prefix="giw-root-") as tmp:
            root = make_repo(Path(tmp) / "repo")
            sub = root / "a" / "b"
            sub.mkdir(parents=True)
            resolved, detail = giw.git_repo_root(str(sub))
            self.assertEqual(resolved, root.resolve())
            self.assertIsNone(detail)

    def test_non_repo_returns_none_with_no_detail(self):
        with tempfile.TemporaryDirectory(prefix="giw-noroot-") as tmp:
            resolved, detail = giw.git_repo_root(tmp)
            self.assertIsNone(resolved)
            self.assertIsNone(detail, "an ordinary non-repo answer must not look like a failure")

    def test_missing_cwd_returns_none(self):
        resolved, detail = giw.git_repo_root(None)
        self.assertIsNone(resolved)
        self.assertIsNone(detail)

    def test_git_entirely_absent_reports_detail(self):
        """The regression for this fix's other half: git missing from PATH must be
        DISTINGUISHABLE from an ordinary non-repo answer, not collapse to the same (None, None)."""
        with tempfile.TemporaryDirectory(prefix="giw-nopath-") as tmp:
            empty_bin = Path(tmp) / "emptybin"
            empty_bin.mkdir()
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = str(empty_bin)
            try:
                resolved, detail = giw.git_repo_root(tmp)
            finally:
                os.environ["PATH"] = old_path
            self.assertIsNone(resolved)
            self.assertIsNotNone(detail)


class CheckIgnoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="giw-checkignore-")
        self.root = make_repo(Path(self.tempdir.name), gitignore_text="scratch/\n*.tmp\n")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_ignored_path_parsed(self):
        status, gi_path, line_no, pattern, detail = giw.check_ignore(self.root, "scratch/notes.txt")
        self.assertEqual(status, "ignored")
        self.assertTrue(gi_path.endswith(".gitignore"))
        self.assertEqual(pattern, "scratch/")
        self.assertIsNone(detail)

    def test_tracked_path_not_ignored(self):
        status, _gi, _line, _pattern, detail = giw.check_ignore(self.root, "README.md")
        self.assertEqual(status, "not-ignored")
        self.assertIsNone(detail)

    def test_non_repo_path_fails_open_with_detail(self):
        with tempfile.TemporaryDirectory(prefix="giw-notrepo-") as tmp:
            status, _gi, _line, _pattern, detail = giw.check_ignore(Path(tmp), "whatever.txt")
            self.assertEqual(status, "check-failed")
            self.assertIsNotNone(detail)


class NoisePatternTests(unittest.TestCase):
    def test_pycache_is_noise(self):
        self.assertTrue(giw.is_noise_pattern("__pycache__"))

    def test_deny_all_star_is_not_noise(self):
        """The sketch's own negative control: a deny-all `/*` must NOT be treated as noise --
        it's exactly the pattern that hides a real deliverable, and is the pattern that
        produced 8 of the 10 real confirmed incidents named in the design sketch."""
        self.assertFalse(giw.is_noise_pattern("/*"))

    def test_log_glob_is_noise(self):
        self.assertTrue(giw.is_noise_pattern("*.log"))


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="giw-dedup-")
        self.root = make_repo(Path(self.tempdir.name))
        self.common_dir = giw.git_common_dir(self.root)
        self.assertIsNotNone(self.common_dir, "test setup unsound -- git_common_dir must resolve")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_not_warned_before_recording(self):
        self.assertFalse(giw.already_warned(self.common_dir, "scratch/x.txt", "session-a"))

    def test_warned_after_recording_same_session(self):
        giw.record_warned(self.common_dir, "scratch/x.txt", "session-a")
        self.assertTrue(giw.already_warned(self.common_dir, "scratch/x.txt", "session-a"))

    def test_different_session_not_warned(self):
        """Core regression for this proposal's departure from the design sketch's repo-scoped
        default: a DIFFERENT session touching an already-flagged path must still be warned."""
        giw.record_warned(self.common_dir, "scratch/x.txt", "session-a")
        self.assertFalse(giw.already_warned(self.common_dir, "scratch/x.txt", "session-b"))

    def test_dedup_store_lives_under_git_dir_not_tracked_tree(self):
        giw.record_warned(self.common_dir, "scratch/x.txt", "session-a")
        store_path = self.common_dir / giw.DEDUP_RELPATH
        self.assertTrue(store_path.is_file())
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(self.root),
                                 capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")

    def test_aged_out_entry_pruned_on_read(self):
        """Direct regression for the review fix: an entry past the retention window must not
        keep suppressing its warning -- checked BEFORE any write-path prune has a chance to run."""
        store_path = self.common_dir / giw.DEDUP_RELPATH
        store_path.parent.mkdir(parents=True, exist_ok=True)
        stale_epoch = time.time() - giw.DEDUP_MAX_AGE_SECONDS - 3600
        store_path.write_text(json.dumps({
            giw.dedup_key("scratch/old.txt", "session-old"): {"last_warned_epoch": stale_epoch},
        }))
        self.assertFalse(giw.already_warned(self.common_dir, "scratch/old.txt", "session-old"))
        giw.record_warned(self.common_dir, "scratch/new.txt", "session-new")
        on_disk = json.loads(store_path.read_text())
        self.assertNotIn(giw.dedup_key("scratch/old.txt", "session-old"), on_disk)

    def test_fresh_entry_still_suppresses(self):
        """Negative control for the age-out fix: it must not have disabled dedup outright."""
        giw.record_warned(self.common_dir, "scratch/fresh.txt", "session-a")
        self.assertTrue(giw.already_warned(self.common_dir, "scratch/fresh.txt", "session-a"))

    def test_corrupt_store_treated_as_not_warned_not_crashed(self):
        store_path = self.common_dir / giw.DEDUP_RELPATH
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{ not valid json")
        self.assertFalse(giw.already_warned(self.common_dir, "scratch/x.txt", "session-a"))
        giw.record_warned(self.common_dir, "scratch/x.txt", "session-a")  # must not raise
        self.assertTrue(giw.already_warned(self.common_dir, "scratch/x.txt", "session-a"))

    def test_already_warned_false_when_common_dir_none(self):
        self.assertFalse(giw.already_warned(None, "scratch/x.txt", "session-a"))

    def test_record_warned_noop_when_common_dir_none(self):
        giw.record_warned(None, "scratch/x.txt", "session-a")  # must not raise


class WorktreeDedupTests(unittest.TestCase):
    """CHV2-89 review finding 2: a git worktree's `.git` is a FILE, not a directory."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="giw-worktree-")
        self.main_repo = make_repo(Path(self.tempdir.name) / "main", gitignore_text="scratch/\n")
        (self.main_repo / "README.md").write_text("x")
        run_git(["add", "README.md"], self.main_repo)
        run_git(["commit", "-q", "-m", "init"], self.main_repo)
        self.worktree = Path(self.tempdir.name) / "worktree"
        run_git(["worktree", "add", "-q", str(self.worktree), "-b", "wt-branch"], self.main_repo)
        self.assertTrue((self.worktree / ".git").is_file(),
                        "test setup unsound -- worktree .git must be a file, not a directory")

    def tearDown(self):
        run_git(["worktree", "remove", "-f", str(self.worktree)], self.main_repo)
        self.tempdir.cleanup()

    def test_git_common_dir_resolves_to_main_repo_not_worktree_file(self):
        common = giw.git_common_dir(self.worktree)
        self.assertIsNotNone(common)
        self.assertTrue(common.is_dir())
        self.assertEqual(common.resolve(), (self.main_repo / ".git").resolve())

    def test_dedup_persists_across_writes_from_worktree(self):
        target = self.worktree / "scratch" / "notes.txt"
        target.parent.mkdir(parents=True)
        target.write_text("v1")
        payload = {
            "session_id": "s-worktree", "cwd": str(self.worktree), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "v1"},
        }
        first = run_hook(payload)
        self.assertIsNotNone(parsed_context(first.stdout), first.stderr)
        target.write_text("v2")
        payload["tool_input"]["content"] = "v2"
        second = run_hook(payload)
        self.assertEqual(second.stdout.strip(), "", second.stderr)


class CheckDegradedIntegrationTests(unittest.TestCase):
    """CHV2-89 review finding 3: an unexpected `check-ignore` output must not vanish silently.
    Real fake-git-on-PATH repro, same method the review probe used -- passes rev-parse straight
    through to the real binary, returns exit 0 with garbage for check-ignore only."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="giw-degraded-")
        self.root = make_repo(Path(self.tempdir.name) / "repo", gitignore_text="scratch/\n")
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git, "real git binary not found on PATH -- test setup unsound")
        fakebin = Path(self.tempdir.name) / "fakebin"
        fakebin.mkdir()
        fake_git = fakebin / "git"
        fake_git.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-C" ] && [ "$3" = "check-ignore" ]; then\n'
            '  echo "GARBAGE NOT A REAL CHECK-IGNORE LINE"\n'
            "  exit 0\n"
            "fi\n"
            f'exec {real_git} "$@"\n'
        )
        fake_git.chmod(0o755)
        self.fake_path = f"{fakebin}:{os.environ.get('PATH', '')}"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_unparseable_check_ignore_output_is_not_silent(self):
        target = self.root / "scratch" / "notes.txt"
        target.parent.mkdir(parents=True)
        target.write_text("draft")
        payload = {
            "session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "draft"},
        }
        proc = run_hook(payload, env={"PATH": self.fake_path})
        context = parsed_context(proc.stdout)
        self.assertIsNotNone(context, "detector-degraded case must not be silent on stdout")
        self.assertIn("could not verify", context)
        self.assertIn("check-ignore itself failed", context)
        self.assertIn("check-ignore", proc.stderr)  # the stderr channel must also carry it
        emitted = json.loads(proc.stdout.strip())
        self.assertEqual(emitted.get("systemMessage"),
                         "[gitignore-warn] detector degraded -- see additionalContext")


class PostToolUseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="giw-integration-")
        self.root = make_repo(Path(self.tempdir.name), gitignore_text="scratch/\n__pycache__/\n")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_edit_write_to_ignored_path_warns(self):
        target = self.root / "scratch" / "notes.txt"
        target.parent.mkdir(parents=True)
        target.write_text("draft")
        payload = {
            "session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "draft"},
        }
        proc = run_hook(payload)
        context = parsed_context(proc.stdout)
        self.assertIsNotNone(context, proc.stderr)
        self.assertIn("scratch/notes.txt", context)
        self.assertIn("gitignore", context)

    def test_write_to_tracked_path_is_silent(self):
        target = self.root / "README.md"
        target.write_text("hello")
        payload = {
            "session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "hello"},
        }
        proc = run_hook(payload)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    def test_noise_pattern_stays_silent_even_though_ignored(self):
        target = self.root / "__pycache__" / "mod.pyc"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\x00")
        payload = {
            "session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": ""},
        }
        proc = run_hook(payload)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    def test_bash_write_to_ignored_path_warns(self):
        payload = {
            "session_id": "s1", "cwd": str(self.root), "tool_name": "Bash",
            "tool_input": {"command": f"echo hi > {self.root}/scratch/from_bash.txt"},
        }
        proc = run_hook(payload)
        context = parsed_context(proc.stdout)
        self.assertIsNotNone(context, proc.stderr)
        self.assertIn("scratch/from_bash.txt", context)

    def test_second_write_same_session_same_path_is_silent(self):
        target = self.root / "scratch" / "repeat.txt"
        target.parent.mkdir(parents=True)
        target.write_text("v1")
        payload = {
            "session_id": "s-repeat", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "v1"},
        }
        first = run_hook(payload)
        self.assertIsNotNone(parsed_context(first.stdout), first.stderr)
        target.write_text("v2")
        payload["tool_input"]["content"] = "v2"
        second = run_hook(payload)
        self.assertEqual(second.stdout.strip(), "", second.stderr)

    def test_new_session_same_path_warns_again(self):
        target = self.root / "scratch" / "shared.txt"
        target.parent.mkdir(parents=True)
        target.write_text("v1")
        payload_a = {
            "session_id": "session-a", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "v1"},
        }
        first = run_hook(payload_a)
        self.assertIsNotNone(parsed_context(first.stdout), first.stderr)
        payload_b = dict(payload_a, session_id="session-b")
        second = run_hook(payload_b)
        self.assertIsNotNone(parsed_context(second.stdout), second.stderr)

    def test_non_git_repo_cwd_is_silent(self):
        with tempfile.TemporaryDirectory(prefix="giw-notgit-") as tmp:
            target = Path(tmp) / "notes.txt"
            target.write_text("x")
            payload = {
                "session_id": "s1", "cwd": tmp, "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": "x"},
            }
            proc = run_hook(payload)
            self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    def test_unrelated_tool_name_is_silent(self):
        payload = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Read",
                   "tool_input": {"file_path": str(self.root / "README.md")}}
        proc = run_hook(payload)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)


if __name__ == "__main__":
    unittest.main()
