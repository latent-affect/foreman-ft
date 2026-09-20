"""GOALS.json C5. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_rotation -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.ingest.tests._helpers import TempDb, padding_lines, tail_verdicts, verdict_line


class RotationDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.fixture = Path(self.tmpdir.name) / "verdicts.jsonl"
        self.db = TempDb()

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_copy_truncate_rotation_starts_new_stream_from_zero(self):
        old_lines = padding_lines(prefix="old")
        self.fixture.write_text("\n".join(old_lines) + "\n")
        first = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(first.rows_inserted, len(old_lines))
        old_stream_id = self.db.conn.execute(
            "SELECT stream_id FROM ingest_source WHERE source_name='verdicts'"
        ).fetchone()[0]
        old_offset = self.db.conn.execute(
            "SELECT byte_offset FROM ingest_source WHERE source_name='verdicts'"
        ).fetchone()[0]

        # Simulate copy-truncate: the file is replaced by a DIFFERENT new file (a fresh weekly
        # rotation), not merely appended to -- its head bytes differ from the old stream's.
        new_lines = padding_lines(prefix="new") + [verdict_line("h_after_rotation", "2027-01-01T00:00:00.000001Z", 999)]
        self.fixture.write_text("\n".join(new_lines) + "\n")

        second = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertTrue(second.rotation_detected)
        self.assertEqual(second.rows_inserted, len(new_lines))

        history = self.db.conn.execute(
            "SELECT stream_id, reason, prior_stream_id, prior_byte_offset FROM ingest_stream_history "
            "WHERE source_name='verdicts' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(history), 2)  # first-seen, then rotation
        self.assertEqual(history[0][1], "first-seen")
        new_stream_id, reason, prior_stream_id, prior_byte_offset = history[1]
        self.assertEqual(reason, "rotation")
        self.assertEqual(prior_stream_id, old_stream_id)
        self.assertEqual(prior_byte_offset, old_offset)
        self.assertNotEqual(new_stream_id, old_stream_id)

        # New stream's rows are keyed under the NEW stream_id starting at byte 0, not appended
        # onto the old stream's byte range.
        new_stream_rows = self.db.conn.execute(
            "SELECT byte_offset FROM hook_verdict WHERE stream_id = ? ORDER BY byte_offset",
            (new_stream_id,),
        ).fetchall()
        self.assertEqual(new_stream_rows[0][0], 0)
        self.assertEqual(len(new_stream_rows), len(new_lines))

        # Old stream's rows are untouched, still there under the old stream_id.
        old_stream_row_count = self.db.conn.execute(
            "SELECT COUNT(*) FROM hook_verdict WHERE stream_id = ?", (old_stream_id,)
        ).fetchone()[0]
        self.assertEqual(old_stream_row_count, len(old_lines))


if __name__ == "__main__":
    unittest.main()
