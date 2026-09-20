"""GOALS.json C5, C6. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.hookclient.tests.test_project_resolution -v

Note: this test module imports atlas.resolve.matcher to CROSS-CHECK hookclient's vendored
matching against the original -- test code only, per this component's own out_of_scope note.
The shipped atlas/hookclient/*.py modules never import it.
"""

import os
import unittest

from atlas.hookclient.reader import read_index, read_project, resolve_project
from atlas.hookclient.tests._helpers import DEFAULT_INDEX, FixtureSnapshot
from atlas.resolve import matcher as real_matcher


class ProjectResolutionCrossCheckTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureSnapshot()
        self.fixture.write_generation("gen-1", shards={
            "TESS": {"schema_version": "atlas-snapshot-2", "project_prefix": "TESS", "rollup_scope": "TESS",
                     "rollup_is_shared_with": [], "verdict_rollup": {}, "recent_fail_open": []},
        })
        self.index_result = read_index(str(self.fixture.root))

    def tearDown(self):
        self.fixture.close()

    def _cross_check(self, cwd):
        hookclient_resolution, hookclient_candidates = resolve_project(cwd, self.index_result)
        projects = [
            (p, root) for root, entry in DEFAULT_INDEX["roots"].items() for p in entry["prefixes"]
        ]
        real_resolution, _, real_candidates = real_matcher.resolve_cwd(cwd, projects)
        self.assertEqual(hookclient_resolution, real_resolution)
        self.assertEqual(sorted(hookclient_candidates), sorted(real_candidates))
        return hookclient_resolution, hookclient_candidates

    def test_unique_match(self):
        resolution, candidates = self._cross_check("/proj/tess/src")
        self.assertEqual(resolution, "unique")
        self.assertEqual(candidates, ["TESS"])

    def test_ambiguous_shared_root_matches_the_real_fore_arem_shape(self):
        resolution, candidates = self._cross_check("/proj/shared")
        self.assertEqual(resolution, "ambiguous")
        self.assertEqual(candidates, ["AREM", "FORE"])

    def test_unregistered(self):
        resolution, candidates = self._cross_check("/proj/nowhere")
        self.assertEqual(resolution, "unregistered")
        self.assertEqual(candidates, [])


class ReadProjectSameGenerationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureSnapshot()

    def tearDown(self):
        self.fixture.close()

    def test_read_project_uses_the_originally_resolved_generation(self):
        self.fixture.write_generation("gen-1", shards={
            "TESS": {"project_prefix": "TESS", "marker": "gen-1-shard"},
        })
        first_read = read_index(str(self.fixture.root))
        self.assertTrue(first_read.ok)

        # Simulate a concurrent republish: current now points elsewhere, with a DIFFERENT
        # shard for the same prefix.
        self.fixture.write_generation("gen-2", shards={
            "TESS": {"project_prefix": "TESS", "marker": "gen-2-shard"},
        })
        current_target = os.path.realpath(self.fixture.root / "current")
        self.assertNotEqual(current_target, first_read.generation_dir)

        result = read_project("TESS", first_read)
        self.assertTrue(result.ok)
        self.assertEqual(result.shard["marker"], "gen-1-shard")


if __name__ == "__main__":
    unittest.main()
