#!/usr/bin/env python3
"""Regression tests for CHV2-91's untracked_write_warn_hook.py. No test file existed before
this change (Alice's own message: "not tested by me -- no exec tool this session"). Real git
repos, real subprocess spawns of the real hook script -- same convention
gitignore_warn_hook's own test_gitignore_warn_hook.py already uses.

    python3 -m unittest test_untracked_write_warn_hook -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = str(HERE / "untracked_write_warn_hook.py")

sys.path.insert(0, str(HERE))
import untracked_write_warn_hook as uww  # noqa: E402


def run_git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def make_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-q"], root)
    run_git(["config", "--local", "user.email", "test@example.invalid"], root)
    run_git(["config", "--local", "user.name", "Test Fixture"], root)
    (root / "README.md").write_text("hello")
    run_git(["add", "README.md"], root)
    run_git(["commit", "-q", "-m", "init"], root)
    return root


def run_hook(payload, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, SCRIPT], input=json.dumps(payload),
                          capture_output=True, text=True, env=full_env)


def parsed_context(stdout_text):
    stripped = stdout_text.strip()
    if stripped == "":
        return None
    obj = json.loads(stripped)
    return obj.get("hookSpecificOutput", {}).get("additionalContext")


class DedupUnitTests(unittest.TestCase):
    """Direct unit coverage of the dedup store, independent of any subprocess/git overhead --
    the same split gitignore_warn_hook's own DedupTests class uses."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="uww-dedup-")
        self.common_dir = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_not_warned_yet_returns_false(self):
        self.assertFalse(uww.already_warned(self.common_dir, "a.py", "s1"))

    def test_recorded_then_already_warned_is_true(self):
        uww.record_warned(self.common_dir, "a.py", "s1")
        self.assertTrue(uww.already_warned(self.common_dir, "a.py", "s1"))

    def test_dedup_is_scoped_per_session_not_per_repo(self):
        """The design's own stated reasoning (CHV2-89's review): a later session's genuine
        need for the warning must not be silenced by an earlier session's judgment call."""
        uww.record_warned(self.common_dir, "a.py", "s1")
        self.assertFalse(uww.already_warned(self.common_dir, "a.py", "s2"))

    def test_dedup_is_scoped_per_path(self):
        uww.record_warned(self.common_dir, "a.py", "s1")
        self.assertFalse(uww.already_warned(self.common_dir, "b.py", "s1"))

    def test_none_common_dir_never_warned_and_never_raises(self):
        self.assertFalse(uww.already_warned(None, "a.py", "s1"))
        uww.record_warned(None, "a.py", "s1")  # must not raise

    def test_stale_entries_are_pruned(self):
        store = {uww.dedup_key("old.py", "s1"): {"last_warned_epoch": 0}}
        pruned = uww.prune_dedup_store(store, now=uww.DEDUP_MAX_AGE_SECONDS + 1)
        self.assertEqual(pruned, {})

    def test_cap_keeps_the_most_recent_entries(self):
        store = {f"k{i}\x00s1": {"last_warned_epoch": i} for i in range(uww.DEDUP_MAX_ENTRIES + 5)}
        pruned = uww.prune_dedup_store(store, now=uww.DEDUP_MAX_ENTRIES + 5)
        self.assertEqual(len(pruned), uww.DEDUP_MAX_ENTRIES)
        self.assertIn(f"k{uww.DEDUP_MAX_ENTRIES + 4}\x00s1", pruned)
        self.assertNotIn("k0\x00s1", pruned)

    def test_malformed_store_file_is_treated_as_empty_not_fatal(self):
        path = self.common_dir / "malformed.json"
        path.write_text("not json{")
        self.assertEqual(uww.load_dedup_store(path), {})


class CheckUntrackedTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="uww-checkuntracked-")
        self.root = make_repo(Path(self.tempdir.name))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_tracked_file_is_tracked_or_ignored(self):
        status, _ = uww.check_untracked(self.root, "README.md")
        self.assertEqual(status, "tracked-or-ignored")

    def test_new_untracked_file_is_untracked(self):
        (self.root / "new.py").write_text("x")
        status, _ = uww.check_untracked(self.root, "new.py")
        self.assertEqual(status, "untracked")

    def test_gitignored_file_is_tracked_or_ignored(self):
        (self.root / ".gitignore").write_text("scratch/\n")
        run_git(["add", ".gitignore"], self.root)
        run_git(["commit", "-q", "-m", "ignore"], self.root)
        (self.root / "scratch").mkdir()
        (self.root / "scratch" / "x.py").write_text("x")
        status, _ = uww.check_untracked(self.root, "scratch/x.py")
        self.assertEqual(status, "tracked-or-ignored")


class PostToolUseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="uww-integration-")
        self.root = make_repo(Path(self.tempdir.name))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_write_to_new_untracked_file_warns(self):
        target = self.root / "new_module.py"
        target.write_text("x = 1")
        payload = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
                   "tool_input": {"file_path": str(target), "content": "x = 1"}}
        proc = run_hook(payload)
        context = parsed_context(proc.stdout)
        self.assertIsNotNone(context, proc.stderr)
        self.assertIn("new_module.py", context)
        self.assertIn("untracked", context)

    def test_write_to_tracked_path_is_silent(self):
        target = self.root / "README.md"
        target.write_text("updated")
        payload = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
                   "tool_input": {"file_path": str(target), "content": "updated"}}
        proc = run_hook(payload)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    def test_gitignored_write_is_silent(self):
        """The complement to gitignore_warn_hook's own scope -- an ignored path is this hook's
        negative control, not something it should also warn about."""
        (self.root / ".gitignore").write_text("scratch/\n")
        run_git(["add", ".gitignore"], self.root)
        run_git(["commit", "-q", "-m", "ignore"], self.root)
        target = self.root / "scratch" / "notes.txt"
        target.parent.mkdir()
        target.write_text("draft")
        payload = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
                   "tool_input": {"file_path": str(target), "content": "draft"}}
        proc = run_hook(payload)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    def test_bash_write_to_new_untracked_file_warns(self):
        """Unlike gitignore_warn_hook's own check_ignore (a pure path-pattern test that works
        on a path regardless of whether the file exists), this hook's check_untracked() calls
        `git ls-files --others`, which only reports files that actually EXIST on disk -- so
        this test must really execute the command first, matching real PostToolUse timing
        (the hook fires AFTER the tool call already ran), not just reference the target path."""
        subprocess.run(["bash", "-c", f"echo hi > {self.root}/from_bash.txt"], check=True)
        payload = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Bash",
                   "tool_input": {"command": f"echo hi > {self.root}/from_bash.txt"}}
        proc = run_hook(payload)
        context = parsed_context(proc.stdout)
        self.assertIsNotNone(context, proc.stderr)
        self.assertIn("from_bash.txt", context)

    def test_second_write_to_the_same_new_file_in_the_same_session_is_silent(self):
        """Session-scoped dedup: one warning per new file per session, not per edit -- the
        design's own stated tradeoff, verified as real behavior."""
        target = self.root / "new_module.py"
        target.write_text("x = 1")
        payload = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
                   "tool_input": {"file_path": str(target), "content": "x = 1"}}
        first = run_hook(payload)
        self.assertIsNotNone(parsed_context(first.stdout), first.stderr)
        target.write_text("x = 2")
        payload["tool_input"]["content"] = "x = 2"
        second = run_hook(payload)
        self.assertEqual(second.stdout.strip(), "", second.stderr)

    def test_same_new_file_different_session_warns_again(self):
        """The design's own named reasoning: a later session's genuine need for the warning
        must not be silenced by an earlier session's judgment call."""
        target = self.root / "new_module.py"
        target.write_text("x = 1")
        payload_s1 = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Write",
                     "tool_input": {"file_path": str(target), "content": "x = 1"}}
        first = run_hook(payload_s1)
        self.assertIsNotNone(parsed_context(first.stdout), first.stderr)
        payload_s2 = {"session_id": "s2", "cwd": str(self.root), "tool_name": "Write",
                     "tool_input": {"file_path": str(target), "content": "x = 1"}}
        second = run_hook(payload_s2)
        self.assertIsNotNone(parsed_context(second.stdout), second.stderr)

    def test_after_git_add_the_same_file_no_longer_warns(self):
        target = self.root / "new_module.py"
        target.write_text("x = 1")
        run_git(["add", "new_module.py"], self.root)
        payload = {"session_id": "s3", "cwd": str(self.root), "tool_name": "Write",
                   "tool_input": {"file_path": str(target), "content": "x = 1"}}
        proc = run_hook(payload)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    def test_non_write_tool_is_silent_no_op(self):
        payload = {"session_id": "s1", "cwd": str(self.root), "tool_name": "Read",
                   "tool_input": {"file_path": str(self.root / "README.md")}}
        proc = run_hook(payload)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    def test_outside_any_git_repo_is_silent(self):
        with tempfile.TemporaryDirectory(prefix="uww-not-a-repo-") as tmp:
            target = Path(tmp) / "new.py"
            target.write_text("x")
            payload = {"session_id": "s1", "cwd": tmp, "tool_name": "Write",
                       "tool_input": {"file_path": str(target), "content": "x"}}
            proc = run_hook(payload)
            self.assertEqual(proc.stdout.strip(), "", proc.stderr)


if __name__ == "__main__":
    unittest.main()
