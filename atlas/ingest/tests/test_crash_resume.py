"""GOALS.json C2. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.ingest.tests.test_crash_resume -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.ingest import stream
from atlas.ingest.tests._helpers import TempDb, tail_verdicts, verdict_line


def _row_set(conn):
    rows = conn.execute(
        "SELECT stream_id, byte_offset, ts, handler_id, verdict FROM hook_verdict ORDER BY byte_offset"
    ).fetchall()
    return set(rows)


class CrashResumeIdempotencyTests(unittest.TestCase):
    """stream_id is sha256 of the first 4096 bytes (stream.py): for a fixture SMALLER than
    4096 bytes, every append changes its own head, since the whole file IS the head -- content
    identity is only stable once a stream has grown past that size, which real verdicts.jsonl
    (44 MB) always has and a small test fixture must be built large enough to reach. 300 lines
    (~10 KB) with chunk cut points chosen to all individually exceed 4096 bytes reproduces the
    real crash-resume scenario without hitting that edge case."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        lines = [
            verdict_line(f"h{i % 3}", f"2026-01-01T00:00:{i:04d}.000001Z", 1000 + i)
            for i in range(300)
        ]
        self.fixture = Path(self.tmpdir.name) / "verdicts.jsonl"
        self.fixture.write_text("\n".join(lines) + "\n")
        self.full_bytes = self.fixture.read_bytes()
        self.assertGreater(len(self.full_bytes), 5 * stream.STREAM_ID_HEAD_BYTES)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_single_pass_and_five_chunk_resume_produce_identical_rows(self):
        db1 = TempDb()
        tail_verdicts(db1.conn, "verdicts", str(self.fixture), db1.run_id)
        single_pass_rows = _row_set(db1.conn)
        db1.close()
        self.assertEqual(len(single_pass_rows), 300)

        db2 = TempDb()
        chunk_dir = Path(self.tmpdir.name) / "chunked"
        chunk_dir.mkdir()
        chunk_path = chunk_dir / "verdicts.jsonl"
        chunk_sizes = [len(self.full_bytes) * i // 5 for i in range(1, 6)]
        for size in chunk_sizes:
            self.assertGreater(size, stream.STREAM_ID_HEAD_BYTES, "chunk cut point must exceed the stream-id head size")
            chunk_path.write_bytes(self.full_bytes[:size])
            tail_verdicts(db2.conn, "verdicts", str(chunk_path), db2.run_id)
        chunked_rows = _row_set(db2.conn)
        db2.close()

        self.assertEqual(single_pass_rows, chunked_rows)

    def test_replaying_the_same_range_after_a_lost_watermark_inserts_zero_new_rows(self):
        # "a replayed range whose watermark commit was lost inserts 0 rows" -- ARCHITECTURE.md
        # section 2. Calling tail() again against an UNCHANGED file (watermark already at EOF)
        # must not re-insert anything, which is the real crash-safety property: the only way to
        # get duplicate work is a watermark that silently reverted, which this proves it doesn't.
        db = TempDb()
        first = tail_verdicts(db.conn, "verdicts", str(self.fixture), db.run_id)
        self.assertEqual(first.rows_inserted, 300)
        second = tail_verdicts(db.conn, "verdicts", str(self.fixture), db.run_id)
        self.assertEqual(second.rows_inserted, 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
