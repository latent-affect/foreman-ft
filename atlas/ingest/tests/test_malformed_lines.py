"""GOALS.json C6, C8. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_malformed_lines -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.ingest.tests._helpers import TempDb, tail_verdicts, verdict_line


class MalformedLineTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.fixture = Path(self.tmpdir.name) / "verdicts.jsonl"
        self.db = TempDb()

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_malformed_json_line_is_counted_and_skipped_not_fatal(self):
        good1 = verdict_line("h1", "2026-01-01T00:00:00.000001Z", 1000)
        bad = '{"ts": "2026-01-01T00:00:01.000001Z", "handler_id": broken not json'
        good2 = verdict_line("h2", "2026-01-01T00:00:02.000001Z", 1002)
        self.fixture.write_text(good1 + "\n" + bad + "\n" + good2 + "\n")

        result = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(result.rows_inserted, 2)
        self.assertEqual(result.rows_skipped, 1)
        self.assertEqual(len(result.skip_details), 1)
        self.assertEqual(result.skip_details[0].byte_offset, len(good1) + 1)

        handlers = {r[0] for r in self.db.conn.execute("SELECT handler_id FROM hook_verdict")}
        self.assertEqual(handlers, {"h1", "h2"})

    def test_line_missing_a_required_field_is_skipped_not_inserted(self):
        # ts, handler_id, verdict are NOT NULL per ARCHITECTURE.md section 16 -- a row missing
        # one is real malformed input (map_verdict_row returns None), distinct from a JSON
        # parse failure, but the pass must not treat it as fatal either.
        missing_verdict = json.dumps({"ts": "2026-01-01T00:00:00.000001Z", "handler_id": "h1"})
        good = verdict_line("h2", "2026-01-01T00:00:01.000001Z", 1001)
        self.fixture.write_text(missing_verdict + "\n" + good + "\n")

        result = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(result.rows_inserted, 1)
        # Check 2 (adversarial-code-review, MEDIUM) regression: a mapper-rejected row (real
        # JSON, fails the mapper's own required-field rule) is counted separately from a
        # JSON-decode failure -- it must not vanish, counted in neither total.
        self.assertEqual(result.rows_rejected_by_mapper, 1)
        rows = self.db.conn.execute("SELECT handler_id FROM hook_verdict").fetchall()
        self.assertEqual(rows, [("h2",)])

    def test_unknown_extra_field_does_not_reject_the_row(self):
        obj = json.loads(verdict_line("h1", "2026-01-01T00:00:00.000001Z", 1000))
        obj["some_future_field_not_yet_a_column"] = {"nested": "value"}
        self.fixture.write_text(json.dumps(obj) + "\n")

        result = tail_verdicts(self.db.conn, "verdicts", str(self.fixture), self.db.run_id)
        self.assertEqual(result.rows_inserted, 1)
        self.assertEqual(result.rows_skipped, 0)
        row = self.db.conn.execute(
            "SELECT handler_id, verdict FROM hook_verdict"
        ).fetchone()
        self.assertEqual(row, ("h1", "fire"))


if __name__ == "__main__":
    unittest.main()
