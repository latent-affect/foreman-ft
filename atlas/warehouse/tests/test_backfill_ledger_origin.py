"""ATLASSN-64. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_backfill_ledger_origin -v

The acceptance criteria this ticket names directly: a negative control proven red first (a row
whose origin was deliberately cleared must come back), a row that already carries an origin must
NOT be touched, and hook_verdict's row count must be identical before and after -- an UPDATE that
changes the count is an INSERT in disguise.
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import backfill_ledger_origin, migrate

from atlas.warehouse.tests.test_dq_runner import _make_verdict

STREAM_ID = "fixture-stream"


def _write_verdicts_jsonl(path, lines):
    """Writes lines and returns their byte offsets (matching stream.tail()'s own bookkeeping:
    the offset of a line is the byte position of its first character)."""
    offsets = []
    offset = 0
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            text = json.dumps(line)
            offsets.append(offset)
            fh.write(text + "\n")
            offset += len(text.encode("utf-8")) + 1
    return offsets


class BackfillLedgerOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.conn = migrate.connect(str(root / "test.db"))
        self.verdicts_path = root / "verdicts.jsonl"
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _seed(self, source_lines, warehouse_ledger_origin):
        """source_lines: one dict per verdicts.jsonl line. warehouse_ledger_origin: parallel list
        of the value ALREADY stored in hook_verdict for that row (None to simulate the frozen,
        not-yet-healed state). Returns the offsets, so a test can target a specific line."""
        offsets = _write_verdicts_jsonl(self.verdicts_path, source_lines)
        for line, offset, origin in zip(source_lines, offsets, warehouse_ledger_origin):
            _make_verdict(
                self.conn, self.run_id, STREAM_ID, offset, line["ts"], line["handler_id"],
                line["verdict"], ledger_origin=origin, origin_signal=None,
            )
        self.conn.commit()
        return offsets

    def _origin_of(self, offset):
        return self.conn.execute(
            "SELECT ledger_origin, origin_signal FROM hook_verdict "
            "WHERE stream_id = ? AND byte_offset = ?", (STREAM_ID, offset),
        ).fetchone()

    def test_a_frozen_stamped_row_is_recovered(self):
        """THE DISCRIMINATING CASE. A row whose source line carries ledger_origin but whose
        warehouse row was frozen NULL by the watermark, per this ticket's whole premise."""
        lines = [{
            "ts": "2026-01-01T00:00:00Z", "handler_id": "guard_destructive.py", "verdict": "fire",
            "ledger_origin": "verdict-ledger", "origin_signal": "pid-chain",
        }]
        offsets = self._seed(lines, warehouse_ledger_origin=[None])
        self.assertIsNone(self._origin_of(offsets[0])[0], "control: must start NULL")

        result = backfill_ledger_origin.backfill_range(
            self.conn, str(self.verdicts_path), 0, offsets[0] + len(json.dumps(lines[0])) + 1,
            STREAM_ID,
        )
        self.assertEqual(result["rows_updated"], 1)
        self.assertEqual(self._origin_of(offsets[0]), ("verdict-ledger", "pid-chain"))

    def test_a_row_already_carrying_an_origin_is_not_touched(self):
        """The other half of the control. An already-healthy row must not be rewritten, and the
        UPDATE's own IS NULL guard is what this test is actually exercising."""
        lines = [{
            "ts": "2026-01-01T00:00:00Z", "handler_id": "guard_destructive.py", "verdict": "fire",
            "ledger_origin": "verdict-ledger", "origin_signal": "pid-chain",
        }]
        offsets = self._seed(lines, warehouse_ledger_origin=["already-set"])

        result = backfill_ledger_origin.backfill_range(
            self.conn, str(self.verdicts_path), 0, offsets[0] + len(json.dumps(lines[0])) + 1,
            STREAM_ID,
        )
        self.assertEqual(result["rows_updated"], 0, "already-populated row must not count as healed")
        self.assertEqual(self._origin_of(offsets[0])[0], "already-set")

    def test_a_source_line_with_no_origin_field_is_left_null(self):
        """Not every unhealed row is recoverable -- a line that never carried ledger_origin at
        all (a shell writer, or a guard_* writer from before FORE-311 lands) must stay NULL, not
        be coerced to some placeholder."""
        lines = [{
            "ts": "2026-01-01T00:00:00Z", "handler_id": "guard_allowlist.py", "verdict": "fire",
        }]
        offsets = self._seed(lines, warehouse_ledger_origin=[None])

        result = backfill_ledger_origin.backfill_range(
            self.conn, str(self.verdicts_path), 0, offsets[0] + len(json.dumps(lines[0])) + 1,
            STREAM_ID,
        )
        self.assertEqual(result["lines_stamped"], 0)
        self.assertEqual(result["rows_updated"], 0)
        self.assertIsNone(self._origin_of(offsets[0])[0])

    def test_row_count_is_unchanged_an_update_that_changes_it_is_an_insert_in_disguise(self):
        lines = [
            {"ts": "2026-01-01T00:00:00Z", "handler_id": "guard_destructive.py",
             "verdict": "fire", "ledger_origin": "verdict-ledger", "origin_signal": "pid-chain"},
            {"ts": "2026-01-01T00:00:01Z", "handler_id": "guard_allowlist.py", "verdict": "fire"},
        ]
        self._seed(lines, warehouse_ledger_origin=[None, None])
        before = self.conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]

        end = sum(len(json.dumps(line)) + 1 for line in lines)
        backfill_ledger_origin.backfill_range(self.conn, str(self.verdicts_path), 0, end, STREAM_ID)

        after = self.conn.execute("SELECT COUNT(*) FROM hook_verdict").fetchone()[0]
        self.assertEqual(before, after)

    def test_a_stamped_row_outside_hook_verdict_entirely_is_counted_stamped_but_not_updated(self):
        """A source line can be stamped while its (stream_id, byte_offset) pair never landed in
        hook_verdict at all -- e.g. the real mapper rejected it. rows_updated must reflect only
        rows that actually exist to be healed, not every stamped line read."""
        lines = [{
            "ts": "2026-01-01T00:00:00Z", "handler_id": "guard_destructive.py", "verdict": "fire",
            "ledger_origin": "verdict-ledger", "origin_signal": "pid-chain",
        }]
        offsets = _write_verdicts_jsonl(self.verdicts_path, lines)
        # Deliberately do NOT insert a hook_verdict row for this offset.

        end = offsets[0] + len(json.dumps(lines[0])) + 1
        result = backfill_ledger_origin.backfill_range(
            self.conn, str(self.verdicts_path), 0, end, STREAM_ID)
        self.assertEqual(result["lines_stamped"], 1)
        self.assertEqual(result["rows_updated"], 0)

    def test_a_range_not_ending_on_a_line_boundary_raises(self):
        lines = [{
            "ts": "2026-01-01T00:00:00Z", "handler_id": "guard_destructive.py", "verdict": "fire",
            "ledger_origin": "verdict-ledger", "origin_signal": "pid-chain",
        }]
        self._seed(lines, warehouse_ledger_origin=[None])
        with self.assertRaises(ValueError):
            backfill_ledger_origin.backfill_range(self.conn, str(self.verdicts_path), 0, 5, STREAM_ID)


if __name__ == "__main__":
    unittest.main()
