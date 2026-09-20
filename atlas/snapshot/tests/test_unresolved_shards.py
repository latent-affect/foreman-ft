"""Regression test for the Check 6 finding (foreman:integration-test): the three unresolved-
scope bucket shards ARCHITECTURE.md sections 3/17 document as real, published shards were never
actually written. Not a new frozen GOALS.json criterion -- a bug fix in already-covered
behavior (build_index/publish's own documented contract), verified here permanently.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.snapshot.tests.test_unresolved_shards -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.snapshot.publisher import publish
from atlas.snapshot.tests._helpers import TempWarehouse, publish_kwargs

EXPECTED_BUCKET_FILES = {
    "ambiguous-UNRESOLVED.json", "unregistered-UNRESOLVED.json", "no-cwd-UNRESOLVED.json",
}


class UnresolvedShardTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_root = Path(self.tmpdir.name) / "snapshot"
        self.wh = TempWarehouse()

    def tearDown(self):
        self.wh.close()
        self.tmpdir.cleanup()

    def test_all_three_bucket_shards_are_written(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        shard_files = {p.name for p in (gen / "projects").iterdir()}
        self.assertTrue(EXPECTED_BUCKET_FILES.issubset(shard_files))

    def test_bucket_shard_shape_matches_documented_contract(self):
        gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        ambiguous_shard = json.loads((gen / "projects" / "ambiguous-UNRESOLVED.json").read_text())
        self.assertEqual(ambiguous_shard["project_prefix"], None)
        self.assertEqual(ambiguous_shard["rollup_scope"], "<ambiguous>")
        self.assertIn("verdict_rollup", ambiguous_shard)
        self.assertNotIn("rollup_is_shared_with", ambiguous_shard)  # only project shards carry this

    def test_supplied_unresolved_rollup_content_is_written(self):
        rollup = {"<ambiguous>": {"guard_destructive.py": {"": {"silent": 100}}}}
        gen = publish(
            str(self.snapshot_root), self.wh.conn,
            **publish_kwargs(),
        )
        # Re-publish with real bucket content this time (separate call to exercise the param).
        gen2 = publish(
            str(self.snapshot_root), self.wh.conn, unresolved_verdict_rollups=rollup,
            **publish_kwargs(warehouse_run_id=2),
        )
        shard = json.loads((gen2 / "projects" / "ambiguous-UNRESOLVED.json").read_text())
        self.assertEqual(shard["verdict_rollup"], rollup["<ambiguous>"])


if __name__ == "__main__":
    unittest.main()
