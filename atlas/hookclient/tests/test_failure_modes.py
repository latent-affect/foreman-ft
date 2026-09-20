"""GOALS.json C1, C4. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.hookclient.tests.test_failure_modes -v
"""

import json
import unittest

from atlas.hookclient.reader import read_index
from atlas.hookclient.tests._helpers import DEFAULT_INDEX, FixtureSnapshot


class FailureModeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureSnapshot()

    def tearDown(self):
        self.fixture.close()

    def test_whole_root_removed(self):
        import shutil
        shutil.rmtree(self.fixture.root)
        result = read_index(str(self.fixture.root))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "root-removed")

    def test_pointer_missing(self):
        # root exists, but no 'current' symlink was ever created.
        result = read_index(str(self.fixture.root))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "pointer-missing")

    def test_pointer_dangling(self):
        import os
        os.symlink("gen-does-not-exist", self.fixture.root / "current")
        result = read_index(str(self.fixture.root))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "pointer-dangling")

    def test_index_corrupt(self):
        gen_dir = self.fixture.write_generation("gen-1")
        (gen_dir / "index.json").write_text("{not valid json")
        result = read_index(str(self.fixture.root))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "index-corrupt")

    def test_shard_corrupt_does_not_block_the_index_read(self):
        # shard corruption is a read_project()-time failure, not an index-read-time one --
        # the index itself is valid and must still be readable.
        gen_dir = self.fixture.write_generation("gen-1")
        (gen_dir / "projects" / "TESS.json").write_text("{broken")
        result = read_index(str(self.fixture.root))
        self.assertTrue(result.ok)

        from atlas.hookclient.reader import read_project
        proj_result = read_project("TESS", result)
        self.assertFalse(proj_result.ok)
        self.assertEqual(proj_result.reason, "shard-corrupt")

    def test_schema_version_mismatch(self):
        bad_index = dict(DEFAULT_INDEX)
        bad_index["schema_version"] = "atlas-snapshot-1"
        self.fixture.write_generation("gen-1", index=bad_index)
        result = read_index(str(self.fixture.root))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "schema-version-mismatch")


if __name__ == "__main__":
    unittest.main()
