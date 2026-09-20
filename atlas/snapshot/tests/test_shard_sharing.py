"""GOALS.json C6. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.snapshot.tests.test_shard_sharing -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.snapshot.publisher import publish
from atlas.snapshot.tests._helpers import TempWarehouse, publish_kwargs


class ShardSharingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_root = Path(self.tmpdir.name) / "snapshot"
        self.wh = TempWarehouse()

    def tearDown(self):
        self.wh.close()
        self.tmpdir.cleanup()

    def test_shared_root_projects_get_ambiguous_rollup_scope(self):
        # default_dim_projects() has FORE and AREM sharing /proj/shared, TESS standalone.
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())

        fore_shard = json.loads((gen / "projects" / "FORE.json").read_text())
        self.assertEqual(fore_shard["rollup_scope"], "<ambiguous>")
        self.assertEqual(fore_shard["rollup_is_shared_with"], ["AREM"])

        arem_shard = json.loads((gen / "projects" / "AREM.json").read_text())
        self.assertEqual(arem_shard["rollup_scope"], "<ambiguous>")
        self.assertEqual(arem_shard["rollup_is_shared_with"], ["FORE"])

    def test_non_shared_project_gets_its_own_prefix_as_rollup_scope(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        tess_shard = json.loads((gen / "projects" / "TESS.json").read_text())
        self.assertEqual(tess_shard["rollup_scope"], "TESS")
        self.assertEqual(tess_shard["rollup_is_shared_with"], [])

    def test_index_roots_map_reflects_the_same_sharing(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        index = json.loads((gen / "index.json").read_text())
        shared_entry = index["roots"]["/proj/shared"]
        self.assertEqual(shared_entry["resolution"], "ambiguous")
        self.assertEqual(sorted(shared_entry["prefixes"]), ["AREM", "FORE"])
        unique_entry = index["roots"]["/proj/tess"]
        self.assertEqual(unique_entry["resolution"], "unique")
        self.assertEqual(unique_entry["prefixes"], ["TESS"])


if __name__ == "__main__":
    unittest.main()
