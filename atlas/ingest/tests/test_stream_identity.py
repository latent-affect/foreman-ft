"""GOALS.json C1, C9. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_stream_identity -v
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import stream
from atlas.ingest.tests._helpers import TempDb, tail_verdicts, verdict_line


class StreamIdContentAddressedTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_byte_identical_copy_has_same_stream_id_different_inode(self):
        original = Path(self.tmpdir.name) / "original.jsonl"
        original.write_text(verdict_line("h1", "2026-01-01T00:00:00.000001Z", 1000) + "\n")
        copy = Path(self.tmpdir.name) / "copy.jsonl"
        shutil.copy(original, copy)
        self.assertNotEqual(original.stat().st_ino, copy.stat().st_ino)
        self.assertEqual(stream.stream_id_of_path(original), stream.stream_id_of_path(copy))

    def test_different_content_has_different_stream_id(self):
        a = Path(self.tmpdir.name) / "a.jsonl"
        a.write_text(verdict_line("h1", "2026-01-01T00:00:00.000001Z", 1000) + "\n")
        b = Path(self.tmpdir.name) / "b.jsonl"
        b.write_text(verdict_line("h2", "2026-01-01T00:00:01.000001Z", 1001) + "\n")
        self.assertNotEqual(stream.stream_id_of_path(a), stream.stream_id_of_path(b))


class IngestSourceBookkeepingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = TempDb()

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_first_seen_records_history_and_ingest_source(self):
        fixture = Path(self.tmpdir.name) / "verdicts.jsonl"
        fixture.write_text(
            verdict_line("h1", "2026-01-01T00:00:00.000001Z", 1000) + "\n"
            + verdict_line("h1", "2026-01-01T00:00:01.000001Z", 1001) + "\n"
        )
        result = tail_verdicts(self.db.conn, "verdicts", str(fixture), self.db.run_id)
        self.assertEqual(result.rows_inserted, 2)
        self.assertIsNone(result.error)

        source_rows = self.db.conn.execute(
            "SELECT stream_id, byte_offset, rows_ingested FROM ingest_source WHERE source_name='verdicts'"
        ).fetchall()
        self.assertEqual(len(source_rows), 1)
        stream_id, byte_offset, rows_ingested = source_rows[0]
        self.assertEqual(byte_offset, fixture.stat().st_size)
        self.assertEqual(rows_ingested, 2)

        history_rows = self.db.conn.execute(
            "SELECT reason FROM ingest_stream_history WHERE source_name='verdicts'"
        ).fetchall()
        self.assertEqual(len(history_rows), 1)
        self.assertEqual(history_rows[0][0], "first-seen")


if __name__ == "__main__":
    unittest.main()
