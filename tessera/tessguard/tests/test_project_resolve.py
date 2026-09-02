import subprocess
import tempfile
import unittest
from pathlib import Path

from tessera.tessguard import project_resolve

from .fixtures import make_store


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                    env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.t",
                         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.t",
                         "PATH": "/usr/bin:/bin"})


class ProjectResolveTests(unittest.TestCase):
    def test_multiple_projects_share_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="AAAA", source_root="/tmp/shared-repo")
            store.register_project("BBBB", "BBBB", source_root="/tmp/shared-repo")
            matched = project_resolve.resolve_projects_for_repo("/tmp/shared-repo", store)
            prefixes = sorted(p["prefix"] for p in matched)
            self.assertEqual(prefixes, ["AAAA", "BBBB"])

    def test_naive_startswith_would_collide_but_this_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="EXPJ", source_root="/tmp/example-project")
            store.register_project("EXPJFT", "EXPJFT", source_root="/tmp/example-project-ft")
            # A naive str.startswith("/tmp/example-project") would match BOTH -- real
            # path-component matching must not.
            matched = project_resolve.resolve_projects_for_repo("/tmp/example-project", store)
            prefixes = [p["prefix"] for p in matched]
            self.assertEqual(prefixes, ["EXPJ"])

    def test_nested_source_root_matches_outer_root(self):
        # The GIF case: repo_root is a plain outer directory, source_root is nested inside it.
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="GIF", source_root="/tmp/gif-smith/gifsmith")
            matched = project_resolve.resolve_projects_for_repo("/tmp/gif-smith", store)
            self.assertEqual([p["prefix"] for p in matched], ["GIF"])

    def test_unrelated_repo_resolves_to_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="X", source_root="/tmp/somewhere-else")
            matched = project_resolve.resolve_projects_for_repo("/tmp/unrelated-repo", store)
            self.assertEqual(matched, [])


class WorktreeFallbackTests(unittest.TestCase):
    """DEVH-54/GOALS.json C9. A real git repo with a real second worktree -- not a mocked
    git_worktree_paths, since the whole point is proving `git worktree list` really does
    what the fallback assumes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.main_root = Path(self.tmp.name) / "main-checkout"
        self.main_root.mkdir()
        _git(self.main_root, "init", "-q")
        (self.main_root / "README.md").write_text("x\n")
        _git(self.main_root, "add", "-A")
        _git(self.main_root, "commit", "-q", "-m", "initial")
        self.worktree_root = Path(self.tmp.name) / "second-worktree"
        _git(self.main_root, "worktree", "add", "-q", str(self.worktree_root), "-b", "wt-branch")

    def tearDown(self):
        self.tmp.cleanup()

    def test_c9_project_registered_against_main_checkout_resolves_from_a_sibling_worktree(self):
        with tempfile.TemporaryDirectory() as store_tmp:
            store, _ = make_store(store_tmp, prefix="WTPROJ", source_root=str(self.main_root))
            matched = project_resolve.resolve_projects_for_repo(str(self.worktree_root), store)
            self.assertEqual([p["prefix"] for p in matched], ["WTPROJ"])

    def test_c9_direct_match_still_wins_without_needing_the_fallback(self):
        """Regression: a worktree that IS itself directly registered must not go through
        the fallback path at all (and must still resolve correctly)."""
        with tempfile.TemporaryDirectory() as store_tmp:
            store, _ = make_store(store_tmp, prefix="DIRECT", source_root=str(self.worktree_root))
            matched = project_resolve.resolve_projects_for_repo(str(self.worktree_root), store)
            self.assertEqual([p["prefix"] for p in matched], ["DIRECT"])

    def test_c9_no_project_registered_anywhere_still_resolves_to_empty(self):
        """F12: the fallback must not manufacture a match that doesn't exist."""
        with tempfile.TemporaryDirectory() as store_tmp:
            store, _ = make_store(store_tmp, prefix="ELSEWHERE", source_root="/tmp/totally-unrelated")
            matched = project_resolve.resolve_projects_for_repo(str(self.worktree_root), store)
            self.assertEqual(matched, [])


if __name__ == "__main__":
    unittest.main()
