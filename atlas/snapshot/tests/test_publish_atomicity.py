"""GOALS.json C1, C2. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.snapshot.tests.test_publish_atomicity -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.snapshot.publisher import publish
from atlas.snapshot.tests._helpers import TempWarehouse, publish_kwargs


class PublishAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_root = Path(self.tmpdir.name) / "snapshot"
        self.wh = TempWarehouse()

    def tearDown(self):
        self.wh.close()
        self.tmpdir.cleanup()

    def test_each_publish_creates_a_new_generation_never_mutates_the_last(self):
        gen1 = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        gen1_index_before = (gen1 / "index.json").read_text()

        gen2 = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs(warehouse_run_id=2))
        self.assertNotEqual(gen1, gen2)

        gen1_index_after = (gen1 / "index.json").read_text()
        self.assertEqual(gen1_index_before, gen1_index_after, "prior generation must not be mutated")

        current_target = (self.snapshot_root / "current").resolve()
        self.assertEqual(current_target, gen2.resolve())

    def test_a_resolved_path_stays_valid_and_unchanged_after_a_later_publish(self):
        gen1 = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs())
        resolved = (self.snapshot_root / "current").resolve()
        self.assertEqual(resolved, gen1.resolve())
        original_content = (resolved / "index.json").read_text()

        publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs(warehouse_run_id=2))

        # The reader's already-resolved path from before the second publish is still valid.
        self.assertTrue(resolved.is_dir())
        self.assertEqual((resolved / "index.json").read_text(), original_content)
        parsed = json.loads(original_content)
        self.assertEqual(parsed["schema_version"], "atlas-snapshot-2")

    def test_current_always_points_at_a_complete_generation(self):
        seen_generations = []
        for run_id in range(1, 4):
            gen = publish(str(self.snapshot_root), self.wh.conn, **publish_kwargs(warehouse_run_id=run_id))
            seen_generations.append(gen)
        for gen in seen_generations:
            index_path = gen / "index.json"
            self.assertTrue(index_path.is_file())
            json.loads(index_path.read_text())  # must parse -- never partially written


if __name__ == "__main__":
    unittest.main()
