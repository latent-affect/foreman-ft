import tempfile
import unittest

from tessera.tessguard import project_resolve

from .fixtures import make_store


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


if __name__ == "__main__":
    unittest.main()
