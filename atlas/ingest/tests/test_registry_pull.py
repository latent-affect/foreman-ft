"""ATLASSN-154: registry_pull and v_registry_assertion.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_registry_pull -v
"""

import datetime
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import registry_pull as rp
from atlas.warehouse import migrate

REGISTRY_ASSERTION_DDL = """
CREATE TABLE assertion (
    id                              INTEGER PRIMARY KEY,
    assertion_uid                   TEXT    UNIQUE NOT NULL,
    edge_id                         TEXT    NOT NULL,
    component                       TEXT    NOT NULL,
    class                           TEXT    NOT NULL,
    lease_s                         INTEGER NOT NULL,
    verified_at                     TEXT    NOT NULL,
    verifier_session_id             TEXT    NOT NULL,
    dispatch_record_id              TEXT    NOT NULL,
    evidence_tool_use_id            TEXT    NOT NULL,
    evidence_class                  TEXT    NOT NULL,
    state                           TEXT    NOT NULL DEFAULT 'live',
    revoked_at                      TEXT    NULL,
    revoked_by_session_id           TEXT    NULL,
    revocation_evidence_tool_use_id TEXT    NULL
)
"""


def make_assertion_row(uid, component="registry", state="live", lease_s=300,
                        verified_at=None, revoked_at=None, revoked_by_session_id=None,
                        revocation_evidence_tool_use_id=None):
    return {
        "assertion_uid": uid, "edge_id": f"edge:{uid}", "component": component,
        "class": "in-flight", "lease_s": lease_s,
        "verified_at": verified_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "verifier_session_id": "s-verifier", "dispatch_record_id": "d-1",
        "evidence_tool_use_id": f"tu-{uid}", "evidence_class": "observed-probe",
        "state": state, "revoked_at": revoked_at,
        "revoked_by_session_id": revoked_by_session_id,
        "revocation_evidence_tool_use_id": revocation_evidence_tool_use_id,
    }


def write_registry_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(REGISTRY_ASSERTION_DDL)
    for row in rows:
        conn.execute(
            "INSERT INTO assertion (" + ", ".join(row.keys()) + ") VALUES ("
            + ", ".join("?" for _ in row) + ")",
            list(row.values()),
        )
    conn.commit()
    conn.close()


# ATLASSN-166. write_registry_db's own REGISTRY_ASSERTION_DDL enforces NOT NULL on every column
# production's real source schema does (ddl.py) -- which means a NULL row can never be SEEDED
# through it: SQLite itself refuses the insert. That is exactly right for what write_registry_db
# is for (simulating the governed write path's own output), and exactly why a SEPARATE helper is
# needed for THIS ticket's fixture -- row_is_well_formed()'s whole premise is that a NULL in a
# NOT NULL column can only exist via an OUT-OF-BAND write that recreated the table under a laxer
# schema in the first place (the real source table's own NOT NULL constraints would refuse it
# exactly like the test DDL just did). This helper recreates the table that way, matching the
# realistic mechanism rather than asserting the DB can hold a state its own real schema forbids.
TAMPERED_ASSERTION_DDL = """
CREATE TABLE assertion (
    id                               INTEGER PRIMARY KEY,
    assertion_uid                    TEXT,
    edge_id                          TEXT,
    component                        TEXT,
    class                            TEXT,
    lease_s                          INTEGER,
    verified_at                      TEXT,
    verifier_session_id              TEXT,
    dispatch_record_id               TEXT,
    evidence_tool_use_id             TEXT,
    evidence_class                   TEXT,
    state                            TEXT DEFAULT 'live',
    revoked_at                       TEXT,
    revoked_by_session_id            TEXT,
    revocation_evidence_tool_use_id  TEXT
)
"""


def write_registry_db_with_tampered_schema(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(TAMPERED_ASSERTION_DDL)
    for row in rows:
        conn.execute(
            "INSERT INTO assertion (" + ", ".join(row.keys()) + ") VALUES ("
            + ", ".join("?" for _ in row) + ")",
            list(row.values()),
        )
    conn.commit()
    conn.close()


class RegistryPullTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.warehouse_db = self.root / "atlas.db"
        self.registry_db = self.root / "registry.db"
        # migrate.connect() applies every migration, including 21 -- creates
        # registry_assertion/v_registry_assertion the same way it creates every other
        # migration's tables for a fresh warehouse.
        migrate.connect(str(self.warehouse_db)).close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run(self):
        return rp.run(warehouse_db_path=self.warehouse_db, registry_db_path=self.registry_db)

    def _rows(self, sql, params=()):
        conn = sqlite3.connect(str(self.warehouse_db))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_a_real_assertion_lands(self):
        write_registry_db(self.registry_db, [make_assertion_row("u1")])
        result = self._run()
        self.assertEqual(result, {"ingested": 1, "error": None})
        rows = self._rows("SELECT assertion_uid, component FROM registry_assertion")
        self.assertEqual(rows, [("u1", "registry")])

    def test_no_registry_file_ingests_zero_not_an_error_state(self):
        # Bootstrap day one (section 34.7): a registry that has never been written yet.
        result = self._run()
        self.assertEqual(result["ingested"], 0)
        self.assertIsNotNone(result["error"])  # named, but distinguishable from a real failure
        self.assertIn("bootstrap day one", result["error"])

    def test_revocation_update_is_reflected_not_missed(self):
        """The whole reason this source is reap-and-refresh, not a watermark: a revocation is
        an UPDATE on an existing row, and a naive append-only ingest would never see it."""
        write_registry_db(self.registry_db, [make_assertion_row("u1", state="live")])
        self._run()
        conn = sqlite3.connect(str(self.registry_db))
        conn.execute(
            "UPDATE assertion SET state='revoked', revoked_at=?, revoked_by_session_id=?, "
            "revocation_evidence_tool_use_id=? WHERE assertion_uid='u1'",
            (datetime.datetime.now(datetime.timezone.utc).isoformat(), "s-revoker", "tu-revoke"),
        )
        conn.commit()
        conn.close()
        self._run()
        rows = self._rows(
            "SELECT state, revoked_by_session_id FROM registry_assertion WHERE assertion_uid='u1'")
        self.assertEqual(rows, [("revoked", "s-revoker")])

    def test_reingest_is_idempotent_no_duplicate_rows(self):
        write_registry_db(self.registry_db, [make_assertion_row("u1")])
        self._run()
        self._run()
        self._run()
        n = self._rows(
            "SELECT COUNT(*) FROM registry_assertion WHERE assertion_uid='u1'")[0][0]
        self.assertEqual(n, 1)

    def test_multiple_assertions_all_land(self):
        write_registry_db(self.registry_db, [make_assertion_row("u1"), make_assertion_row("u2"),
                                              make_assertion_row("u3")])
        result = self._run()
        self.assertEqual(result["ingested"], 3)

    def test_a_read_failure_leaves_the_prior_row_set_untouched(self):
        write_registry_db(self.registry_db, [make_assertion_row("u1")])
        self._run()
        # Corrupt the registry file in place -- a real open/read failure, not a missing file.
        self.registry_db.write_bytes(b"not a sqlite file")
        result = self._run()
        self.assertIsNotNone(result["error"])
        n = self._rows("SELECT COUNT(*) FROM registry_assertion")[0][0]
        self.assertEqual(n, 1)  # the prior row survives; ingest did not reap to zero on failure

    def test_view_computes_revoked_status(self):
        write_registry_db(self.registry_db, [make_assertion_row("u1", state="revoked",
                           revoked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                           revoked_by_session_id="s-revoker",
                           revocation_evidence_tool_use_id="tu-revoke")])
        self._run()
        rows = self._rows(
            "SELECT status_as_of_ingest FROM v_registry_assertion WHERE assertion_uid='u1'")
        self.assertEqual(rows, [("revoked",)])

    def test_view_computes_expired_status(self):
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(seconds=3600)).isoformat()
        write_registry_db(self.registry_db, [make_assertion_row("u1", lease_s=60, verified_at=old)])
        self._run()
        rows = self._rows(
            "SELECT status_as_of_ingest FROM v_registry_assertion WHERE assertion_uid='u1'")
        self.assertEqual(rows, [("expired-as-of-ingest",)])

    def test_view_computes_active_status(self):
        write_registry_db(self.registry_db, [make_assertion_row("u1", lease_s=3600)])
        self._run()
        rows = self._rows(
            "SELECT status_as_of_ingest FROM v_registry_assertion WHERE assertion_uid='u1'")
        self.assertEqual(rows, [("active-as-of-ingest",)])

    def test_ingest_never_writes_the_source_registry(self):
        """registry_pull opens the source read-only -- confirms it structurally cannot be the
        write path registry_write_guard.py exists to protect against, by trying to write through
        the SAME connection the ingest uses and confirming SQLite itself refuses it."""
        write_registry_db(self.registry_db, [make_assertion_row("u1")])
        conn = rp._open_registry_readonly(self.registry_db)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("UPDATE assertion SET state='revoked' WHERE assertion_uid='u1'")
        conn.close()

    def _quarantine_lines(self):
        path = self.root / "quarantine" / "ingest-quarantine.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def test_atlassn_166_null_in_a_previously_unchecked_column_does_not_crash_the_cycle(self):
        """The actual bug: dispatch_record_id is NOT NULL in the real schema but was one of the
        8 row_is_well_formed() never checked. Before this fix, this row reached the INSERT,
        raised an uncaught sqlite3.IntegrityError, and killed the whole cycle -- taking the
        healthy row down with it via run()'s rollback-and-reraise."""
        good = make_assertion_row("u-good")
        bad = make_assertion_row("u-bad")
        bad["dispatch_record_id"] = None
        write_registry_db_with_tampered_schema(self.registry_db, [good, bad])
        result = self._run()
        self.assertIsNone(result["error"], f"the cycle must not fail: {result}")
        rows = self._rows("SELECT assertion_uid FROM registry_assertion ORDER BY assertion_uid")
        self.assertEqual(rows, [("u-good",)], "the healthy row must land even though its "
                                              "neighbour in the same batch was malformed")
        self.assertEqual(result["ingested"], 1)

    def test_atlassn_166_the_skipped_row_is_quarantined_not_silently_dropped(self):
        bad = make_assertion_row("u-bad")
        bad["dispatch_record_id"] = None
        write_registry_db_with_tampered_schema(self.registry_db, [bad])
        self._run()
        lines = self._quarantine_lines()
        self.assertTrue(any(line["stream_id"] == "u-bad" for line in lines),
                        f"expected a quarantine record for u-bad, got: {lines}")

    def test_atlassn_166_class_lease_s_state_malformed_row_still_lands_per_atlassn_164(self):
        """The property ATLASSN-164 established and this fix must not regress, now actually
        under test for the first time: a row malformed only in class/lease_s/state (never a NOT
        NULL violation -- registry_assertion's warehouse schema has no CHECK constraint on these
        columns, only NOT NULL) still inserts successfully AND is quarantined, exactly as
        row_is_well_formed()'s own docstring claims but no prior test in this file confirmed."""
        bad = make_assertion_row("u-badclass")
        bad["class"] = "not-a-real-class"
        write_registry_db_with_tampered_schema(self.registry_db, [bad])
        result = self._run()
        self.assertEqual(result["ingested"], 1)
        rows = self._rows("SELECT assertion_uid, class FROM registry_assertion")
        self.assertEqual(rows, [("u-badclass", "not-a-real-class")])
        lines = self._quarantine_lines()
        self.assertTrue(any(line["stream_id"] == "u-badclass" for line in lines))


if __name__ == "__main__":
    unittest.main()
