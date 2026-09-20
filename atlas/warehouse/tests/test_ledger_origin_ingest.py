"""ATLASSN-62: hook_verdict.ledger_origin / .origin_signal, migration 9 and its ingest half.

Both halves are asserted separately, because either alone leaves the field useless: the schema can
hold the columns while the mapper silently drops them, and the mapper can carry them while the
schema has nowhere to put them. That was the actual pre-change state -- the writers emitted both
fields and the warehouse could neither map nor store them.

WHAT THIS DELIBERATELY DOES NOT ASSERT: that ledger_origin means what it says. It cannot. The value
is a noise filter, not attribution -- `harness-heuristic` is forgeable by any local process that
sets CLAUDE_PID before spawning a descendant (FORE-291), which is why it is named for the strength
of its own claim. A test here that treated it as authentication would be encoding a guarantee the
upstream writer explicitly does not make.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import verdicts
from atlas.warehouse import ddl, migrate

# Shaped like a real post-FORE-291 ledger line, including the renamed value. If the upstream
# vocabulary changes, this is one of the places that should notice.
LEDGER_LINE = {
    "ts": "2026-09-03T19:30:10.582000Z",
    "epoch_ms": 1788000010582,
    "handler_id": "guard_destructive.py",
    "event": "PreToolUse",
    "verdict": "silent",
    "session_id": "5ed4502f-0000-0000-0000-000000000000",
    "cwd": "/Users/m5/dev/dev-harness-run2",
    "tool_name": "Bash",
    "tool_use_id": "toolu_atlassn62",
    "ledger_origin": "harness-heuristic",
    "origin_signal": "ppid-is-claude",
}


class LedgerOriginIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "atlas.db"
        self.addCleanup(self.tmpdir.cleanup)

    def test_mapper_carries_both_fields(self):
        row = verdicts.map_verdict_row(LEDGER_LINE)
        self.assertEqual(row["ledger_origin"], "harness-heuristic")
        self.assertEqual(row["origin_signal"], "ppid-is-claude")

    def test_both_fields_are_in_the_column_allowlist(self):
        # The allowlist is what makes an unrecognised key a no-op by design, so a field missing
        # from it is dropped silently rather than erroring -- exactly how these two were lost.
        self.assertIn("ledger_origin", verdicts.HOOK_VERDICT_COLUMNS)
        self.assertIn("origin_signal", verdicts.HOOK_VERDICT_COLUMNS)

    def test_a_row_without_the_fields_still_maps(self):
        # Every row written before the upstream writers carried these fields, which is most of the
        # ledger. Absent must map to None (SQL NULL), not raise and not invent a value.
        legacy = {k: v for k, v in LEDGER_LINE.items()
                  if k not in ("ledger_origin", "origin_signal")}
        row = verdicts.map_verdict_row(legacy)
        self.assertIsNone(row["ledger_origin"])
        self.assertIsNone(row["origin_signal"])

    def test_migration_adds_the_columns_and_records_itself(self):
        conn = migrate.connect(str(self.path))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(hook_verdict)")]
        self.assertIn("ledger_origin", cols)
        self.assertIn("origin_signal", cols)
        rows = dict(conn.execute("SELECT version, ddl_sha256 FROM schema_migration"))
        self.assertIn(9, rows)
        self.assertEqual(rows[9], ddl.migration9_sha256())
        conn.close()

    def test_round_trip_through_the_real_insert(self):
        conn = migrate.connect(str(self.path))
        run_id = migrate.new_ingest_run(conn)
        verdicts.insert_hook_verdict_row(
            conn, "atlassn62-test", 0, verdicts.map_verdict_row(LEDGER_LINE), run_id)
        conn.commit()
        stored = conn.execute(
            "SELECT ledger_origin, origin_signal FROM hook_verdict WHERE stream_id=?",
            ("atlassn62-test",)).fetchone()
        self.assertEqual(stored, ("harness-heuristic", "ppid-is-claude"))
        conn.close()

    def test_null_is_a_distinct_category_from_the_writers_explicit_unknown(self):
        """`NULL` means "ingested before migration 9". `'unknown'` means the writer ran and could
        not resolve an origin. A consumer that folds them together is reading a decade of
        pre-migration rows as a classification, so the two must stay distinguishable in SQL."""
        conn = migrate.connect(str(self.path))
        run_id = migrate.new_ingest_run(conn)
        legacy = {k: v for k, v in LEDGER_LINE.items()
                  if k not in ("ledger_origin", "origin_signal")}
        unknown = dict(LEDGER_LINE, ledger_origin="unknown", origin_signal="resolve-failed")
        verdicts.insert_hook_verdict_row(conn, "s-legacy", 0, verdicts.map_verdict_row(legacy),
                                         run_id)
        verdicts.insert_hook_verdict_row(conn, "s-unknown", 0, verdicts.map_verdict_row(unknown),
                                         run_id)
        conn.commit()
        nulls = conn.execute(
            "SELECT COUNT(*) FROM hook_verdict WHERE ledger_origin IS NULL").fetchone()[0]
        unknowns = conn.execute(
            "SELECT COUNT(*) FROM hook_verdict WHERE ledger_origin = 'unknown'").fetchone()[0]
        self.assertEqual((nulls, unknowns), (1, 1))
        conn.close()

    def test_migration_nine_does_not_disturb_any_prior_hash(self):
        # The additive scheme's entire point: one section's DDL must not invalidate another
        # migration's recorded hash. Asserted against the extractors rather than a frozen literal,
        # so this stays true when an earlier section is legitimately edited.
        conn = migrate.connect(str(self.path))
        rows = dict(conn.execute("SELECT version, ddl_sha256 FROM schema_migration"))
        self.assertEqual(rows[1], ddl.ddl_sha256())
        self.assertEqual(rows[8], ddl.migration8_sha256())
        self.assertEqual(len(set(rows.values())), len(rows),
                         "each migration must still hash independently")
        conn.close()


if __name__ == "__main__":
    unittest.main()
