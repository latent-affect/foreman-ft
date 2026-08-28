"""GOALS.json C3, C4. Run from the repo root:

    /path/to/venv/bin/python3 -m unittest atlas.ingest.tests.test_growth_and_partial_lines -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.ingest.tests._helpers import TempDb, padding_lines, tail_verdicts, verdict_line


class FileGrowthTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.fixture = Path(self.tmpdir.name) / "verdicts.jsonl"
        self.db = TempDb()

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_second_pass_ingests_only_newly_appended_lines(self):
        pad = padding_lines()
        first_lines = pad + [verdict_line(f"h{i}", f"2026-01-01T00:00:{i:02d}.000001Z", 1000 + i) for i in range(5)]
        self.fixture.write_text("\n".join(first_lines) + "\n")
        first = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(first.rows_inserted, len(pad) + 5)

        more_lines = [verdict_line(f"h{i}", f"2026-01-01T00:01:{i:02d}.000001Z", 2000 + i) for i in range(3)]
        with open(self.fixture, "a") as f:
            f.write("\n".join(more_lines) + "\n")
        second = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(second.rows_inserted, 3)

        total = self.db.conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]
        self.assertEqual(total, len(pad) + 5 + 3)


class PartialLineTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.fixture = Path(self.tmpdir.name) / "verdicts.jsonl"
        self.db = TempDb()

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_trailing_partial_line_not_consumed_then_ingested_once_completed(self):
        pad = padding_lines()
        pad_text = "\n".join(pad) + "\n"
        complete_line = verdict_line("h1", "2026-01-01T00:00:00.000001Z", 1000)
        second_line_full = verdict_line("h2", "2026-01-01T00:00:01.000001Z", 1001)
        partial_line = second_line_full[: len(second_line_full) // 2]
        self.fixture.write_text(pad_text + complete_line + "\n" + partial_line)  # no trailing newline

        result = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(result.rows_inserted, len(pad) + 1)
        rows = self.db.conn.execute(
            "SELECT handler_id FROM hook_verdict WHERE handler_id='h1'"
        ).fetchall()
        self.assertEqual(rows, [("h1",)])

        offset = self.db.conn.execute(
            "SELECT byte_offset FROM ingest_source WHERE source_name='verdicts'"
        ).fetchone()[0]
        self.assertEqual(offset, len(pad_text) + len(complete_line) + 1)  # stops right after the complete line

        with open(self.fixture, "a") as f:
            f.write(second_line_full[len(partial_line):] + "\n")

        result2 = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(result2.rows_inserted, 1)
        rows2 = self.db.conn.execute(
            "SELECT handler_id FROM hook_verdict WHERE handler_id IN ('h1','h2') ORDER BY byte_offset"
        ).fetchall()
        self.assertEqual(rows2, [("h1",), ("h2",)])


if __name__ == "__main__":
    unittest.main()
