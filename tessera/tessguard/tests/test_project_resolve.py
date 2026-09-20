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
            store, _ = make_store(tmp, prefix="GLAS", source_root="/tmp/glasshouse")
            store.register_project("GLASFT", "GLASFT", source_root="/tmp/glasshouse-ft")
            # A naive str.startswith("/tmp/glasshouse") would match BOTH -- real
            # path-component matching must not.
            matched = project_resolve.resolve_projects_for_repo("/tmp/glasshouse", store)
            prefixes = [p["prefix"] for p in matched]
            self.assertEqual(prefixes, ["GLAS"])

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

    def test_archived_project_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="OLD", source_root="/tmp/old-project")
            store.archive_project("OLD", "tester")
            self.assertEqual(project_resolve.resolve_projects_for_repo("/tmp/old-project", store), [])
            included = project_resolve.resolve_projects_for_repo(
                "/tmp/old-project", store, include_archived=True)
            self.assertEqual([p["prefix"] for p in included], ["OLD"])


class WorktreeFallbackTests(unittest.TestCase):
    """FORE-282: propagated from DEVH-54's dev-harness-run2 copy -- the canonical checkout this
    module actually lives in (imported via sys.path by write_dispatch_record.py and every
    tessguard hard gate) had drifted 0-of-6 with the fix DEVH-54 decided and DEVH-61-64's
    architecture recycle ratified. A real git repo with a real second worktree -- not a mocked
    git_worktree_paths, since the whole point is proving `git worktree list` really does what
    the fallback assumes. Reproduced live against the unfixed canonical resolver before this
    fix landed, against this machine's real dev-harness/dev-harness-run2-qa-security worktree
    pair and the real tessera.db: resolve_projects_for_repo('.../dev-harness-run2-qa-security',
    store) returned [] pre-fix, ['DEVH'] post-fix, with dev-harness's own direct match and a
    genuinely unrelated /tmp both unchanged."""

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

    def test_project_registered_against_main_checkout_resolves_from_a_sibling_worktree(self):
        with tempfile.TemporaryDirectory() as store_tmp:
            store, _ = make_store(store_tmp, prefix="WTPROJ", source_root=str(self.main_root))
            matched = project_resolve.resolve_projects_for_repo(str(self.worktree_root), store)
            self.assertEqual([p["prefix"] for p in matched], ["WTPROJ"])

    def test_direct_match_still_wins_without_needing_the_fallback(self):
        """Regression: a worktree that IS itself directly registered must not go through
        the fallback path at all (and must still resolve correctly)."""
        with tempfile.TemporaryDirectory() as store_tmp:
            store, _ = make_store(store_tmp, prefix="DIRECT", source_root=str(self.worktree_root))
            matched = project_resolve.resolve_projects_for_repo(str(self.worktree_root), store)
            self.assertEqual([p["prefix"] for p in matched], ["DIRECT"])

    def test_no_project_registered_anywhere_still_resolves_to_empty(self):
        """The fallback must not manufacture a match that doesn't exist."""
        with tempfile.TemporaryDirectory() as store_tmp:
            store, _ = make_store(store_tmp, prefix="ELSEWHERE", source_root="/tmp/totally-unrelated")
            matched = project_resolve.resolve_projects_for_repo(str(self.worktree_root), store)
            self.assertEqual(matched, [])

    def test_archived_project_not_resurrected_through_the_fallback(self):
        # New combination canonical's own include_archived filter creates that
        # dev-harness-run2's simpler copy never had to consider: an archived registration on
        # the MAIN checkout must stay excluded even when a sibling worktree's own direct match
        # comes up empty and the fallback walks the worktree list.
        with tempfile.TemporaryDirectory() as store_tmp:
            store, _ = make_store(store_tmp, prefix="GONE", source_root=str(self.main_root))
            store.archive_project("GONE", "tester")
            matched = project_resolve.resolve_projects_for_repo(str(self.worktree_root), store)
            self.assertEqual(matched, [])
            included = project_resolve.resolve_projects_for_repo(
                str(self.worktree_root), store, include_archived=True)
            self.assertEqual([p["prefix"] for p in included], ["GONE"])

    def test_non_git_repo_root_degrades_to_exact_match_only(self):
        # GIF's case (module docstring) crossed with the fallback: `git worktree list` simply
        # fails for a non-git path, and this must degrade gracefully, not raise.
        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as plain:
            store, _ = make_store(store_tmp, prefix="X", source_root="/tmp/unrelated")
            matched = project_resolve.resolve_projects_for_repo(plain, store)
            self.assertEqual(matched, [])


if __name__ == "__main__":
    unittest.main()
