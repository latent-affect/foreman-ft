"""ATLASSN-126 -- schema, DDL and additive-migration harness. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_migration -v

Criteria A1-A6 frozen on the ticket (criteria_hash sha256:6f69b0ff), A1 owning GOALS.json C15.

Every store here is a REAL file on disk, never :memory:. WAL is a design decision the criteria
assert, and journal_mode cannot be WAL on an in-memory database -- a suite that tested the
in-memory path would report a passing WAL assertion that says nothing about the real store.
"""

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.registry import ddl, migrate

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN_DOC = REPO_ROOT / "DESIGN-REGISTRY-SCHEMA-AI5-ATLASSN-110-20260912.md"

# One valid row, used as the baseline every constraint probe deviates from by exactly one
# field. Without this shape a probe can pass because the row was malformed for an unrelated
# reason, which would make the constraint look enforced when it is not.
VALID_ASSERTION = {
    "assertion_uid": "a-01JBXR8Z9QK2M4N6P8R0T2V4W6",
    "edge_id": "claude-hooks-v2:hooks/:registers",
    "component": "registry",
    "class": "structural",
    # Deliberately NOT one of AI-3's proposed 1800/3600/86400 (failure signature F2:
    # no proposed number reachable in code, test fixtures included). An arbitrary
    # positive integer is all this row needs.
    "lease_s": 9973,
    "verified_at": "2026-09-12T15:00:00Z",
    "verifier_session_id": "244e3d16-f16c-4d03-8583-c7fae6e19d9c",
    "dispatch_record_id": "d-01JBXR8Z9QK2M4N6P8R0T2V4W7",
    "evidence_tool_use_id": "toolu_01ABCDEFGHIJKLMNOPQRSTUV",
    "evidence_class": "observed-probe",
    "state": "live",
    "revoked_at": None,
    "revoked_by_session_id": None,
    "revocation_evidence_tool_use_id": None,
}


def insert_assertion(connection, **overrides):
    row = dict(VALID_ASSERTION)
    row.update(overrides)
    columns = ", ".join(f'"{name}"' for name in row)
    placeholders = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO assertion ({columns}) VALUES ({placeholders})", tuple(row.values()))
    return row


def design_doc_fields(table_name):
    """The field column of the AI-5 schema doc's markdown table for `table_name`.

    One cell legitimately names three fields at once (the revocation triple), so cells are split
    on commas. Located by heading text rather than line number, so an unrelated edit to the
    document does not silently point this at the wrong table.
    """
    text = DESIGN_DOC.read_text(encoding="utf-8")
    heading = f"### Table `{table_name}`"
    start = text.index(heading) + len(heading)
    fields = []
    for line in text[start:].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if fields:
                break
            continue
        cell = stripped.strip("|").split("|")[0].strip()
        if not cell or cell.startswith("---") or cell == "field":
            continue
        for name in cell.split(","):
            cleaned = name.strip().strip("`").strip()
            if cleaned:
                fields.append(cleaned)
    return fields


class StoreTestCase(unittest.TestCase):
    """Gives each test a real store file in its own temporary directory."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "registry.db"
        self.connection = migrate.create_store(self.path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.connection.close)


class TestFreshStore(StoreTestCase):
    """A2 -- version, journal mode and declared columns on a newly created store."""

    def test_fresh_store_is_at_schema_version_one(self):
        self.assertEqual(migrate.current_version(self.connection), 1)
        self.assertEqual(ddl.SCHEMA_VERSION, 1)

    def test_fresh_store_uses_write_ahead_logging(self):
        self.assertEqual(migrate.journal_mode(self.connection).lower(), "wal")

    def test_assertion_table_carries_every_declared_column(self):
        columns = ddl.table_columns(self.connection, "assertion")
        for name in ("id", "assertion_uid", "edge_id", "component", "class", "lease_s",
                     "verified_at", "verifier_session_id", "dispatch_record_id",
                     "evidence_tool_use_id", "evidence_class", "state", "revoked_at",
                     "revoked_by_session_id", "revocation_evidence_tool_use_id"):
            self.assertIn(name, columns)

    def test_spent_ref_table_carries_every_declared_column(self):
        self.assertEqual(ddl.table_columns(self.connection, "spent_ref"),
                         ["tool_use_id", "spent_at", "assertion_uid"])

    def test_a_valid_row_inserts(self):
        # The companion positive for every constraint probe below: if this ever fails, those
        # probes stop proving anything about the constraint they name.
        insert_assertion(self.connection)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0], 1)


class TestConstraints(StoreTestCase):
    """A3 -- the CHECKs are enforced by the store, not by calling code."""

    def assertRejects(self, **overrides):
        with self.assertRaises(sqlite3.IntegrityError):
            insert_assertion(self.connection, **overrides)

    def test_class_vocabulary_is_closed(self):
        self.assertRejects(**{"class": "structural-ish"})
        insert_assertion(self.connection, **{"class": "in-flight"})

    def test_state_vocabulary_is_closed(self):
        self.assertRejects(state="stale")

    def test_evidence_class_is_the_single_v1_value(self):
        # Widening this is a migration, never code that starts accepting another value.
        self.assertRejects(evidence_class="self-reported")
        self.assertRejects(evidence_class="observed-probe-v2")

    def test_lease_must_be_positive(self):
        self.assertRejects(lease_s=0)
        self.assertRejects(lease_s=-1)

    def test_assertion_uid_is_unique(self):
        insert_assertion(self.connection)
        self.assertRejects(edge_id="a-different-edge")

    def test_required_fields_reject_null(self):
        self.assertRejects(edge_id=None)
        self.assertRejects(verifier_session_id=None)
        self.assertRejects(dispatch_record_id=None)

    def test_revocation_triple_is_all_or_nothing(self):
        self.assertRejects(state="revoked", revoked_at="2026-09-12T16:00:00Z")
        self.assertRejects(state="revoked", revoked_at="2026-09-12T16:00:00Z",
                           revoked_by_session_id="s-1")
        insert_assertion(
            self.connection,
            state="revoked",
            revoked_at="2026-09-12T16:00:00Z",
            revoked_by_session_id="s-1",
            revocation_evidence_tool_use_id="toolu_01REVOCATIONEVIDENCE00000",
        )

    def test_spent_ref_requires_a_real_assertion(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO spent_ref (tool_use_id, spent_at, assertion_uid) VALUES (?, ?, ?)",
                ("toolu_01ORPHAN0000000000000000", "2026-09-12T15:00:00Z", "a-nonexistent"))


class TestSharedTransaction(StoreTestCase):
    """A4 -- spent_ref is in the same file, so it is in the same transaction."""

    def write_both(self):
        row = insert_assertion(self.connection)
        self.connection.execute(
            "INSERT INTO spent_ref (tool_use_id, spent_at, assertion_uid) VALUES (?, ?, ?)",
            (row["evidence_tool_use_id"], "2026-09-12T15:00:00Z", row["assertion_uid"]))

    def counts(self):
        return (
            self.connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0],
            self.connection.execute("SELECT COUNT(*) FROM spent_ref").fetchone()[0],
        )

    def test_rollback_leaves_neither_row(self):
        self.connection.execute("BEGIN")
        self.write_both()
        self.assertEqual(self.counts(), (1, 1))  # visible inside the transaction
        self.connection.execute("ROLLBACK")
        self.assertEqual(self.counts(), (0, 0))

    def test_commit_leaves_both_rows(self):
        self.connection.execute("BEGIN")
        self.write_both()
        self.connection.execute("COMMIT")
        self.assertEqual(self.counts(), (1, 1))

    def test_both_tables_live_in_the_same_file(self):
        # The structural fact the shared transaction rests on. A sidecar spent_ref would put
        # these in two files, and no rollback could cover both.
        tables = {row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("assertion", tables)
        self.assertIn("spent_ref", tables)


class TestAdditiveMigration(StoreTestCase):
    """A1 / GOALS.json C15 -- rows survive a migration field-for-field."""

    MIGRATION2 = migrate.Migration(
        version=2,
        name="test-additive-column",
        statements=("ALTER TABLE assertion ADD COLUMN note TEXT NULL",),
    )

    def seed(self):
        rows = []
        for index in range(3):
            rows.append(insert_assertion(
                self.connection,
                assertion_uid=f"a-01JBXR8Z9QK2M4N6P8R0T2V4W{index}",
                edge_id=f"repo{index}:path/:reads",
                lease_s=9973 + index,
                **{"class": "structural" if index % 2 == 0 else "in-flight"},
            ))
        return rows

    def test_migration_preserves_every_row_field_for_field(self):
        self.seed()
        before = migrate.integrity_snapshot(self.connection, "assertion")
        original_columns = list(before[0])

        migrate.apply_additive(self.connection, self.MIGRATION2)

        after = migrate.integrity_snapshot(self.connection, "assertion")
        self.assertEqual(len(after), len(before))
        # Compare on the columns that existed before the migration: the new one is expected to
        # be there and is not part of "preserved". Every original value must be identical.
        for original, migrated in zip(before, after):
            for column in original_columns:
                self.assertEqual(original[column], migrated[column],
                                 f"column {column} changed across the migration")
        self.assertIn("note", ddl.table_columns(self.connection, "assertion"))

    def test_migration_bumps_user_version(self):
        self.assertEqual(migrate.current_version(self.connection), 1)
        self.assertEqual(migrate.apply_additive(self.connection, self.MIGRATION2), 2)

    def test_migration_out_of_sequence_is_refused(self):
        skipping = migrate.Migration(version=3, name="skips-two", statements=())
        with self.assertRaises(migrate.VersionSequenceError):
            migrate.apply_additive(self.connection, skipping)
        self.assertEqual(migrate.current_version(self.connection), 1)

    def test_non_additive_statements_are_refused(self):
        for statement in ("DROP TABLE spent_ref",
                          "DELETE FROM assertion",
                          "ALTER TABLE assertion RENAME COLUMN component TO comp",
                          "ALTER TABLE assertion DROP COLUMN component"):
            with self.subTest(statement=statement):
                destructive = migrate.Migration(version=2, name="destructive",
                                                statements=(statement,))
                with self.assertRaises(migrate.NonAdditiveMigration):
                    migrate.apply_additive(self.connection, destructive)
        self.assertEqual(migrate.current_version(self.connection), 1)

    def test_formatting_cannot_hide_a_destructive_statement(self):
        sneaky = migrate.Migration(
            version=2, name="sneaky",
            statements=("drop    table\n   spent_ref",))
        with self.assertRaises(migrate.NonAdditiveMigration):
            migrate.apply_additive(self.connection, sneaky)

    def test_a_failing_migration_leaves_the_store_untouched(self):
        self.seed()
        before = migrate.integrity_snapshot(self.connection, "assertion")
        broken = migrate.Migration(
            version=2, name="broken",
            statements=("ALTER TABLE assertion ADD COLUMN note TEXT NULL",
                        "ALTER TABLE nonexistent_table ADD COLUMN x TEXT"))
        with self.assertRaises(sqlite3.OperationalError):
            migrate.apply_additive(self.connection, broken)
        self.assertEqual(migrate.current_version(self.connection), 1)
        self.assertEqual(migrate.integrity_snapshot(self.connection, "assertion"), before)
        self.assertNotIn("note", ddl.table_columns(self.connection, "assertion"))


class TestVersionRefusal(unittest.TestCase):
    """A5 -- an unexpected user_version is refused, not interpreted."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "registry.db"
        self.addCleanup(self.directory.cleanup)

    def stamp_version(self, version):
        connection = migrate.create_store(self.path)
        connection.execute(f"PRAGMA user_version = {version}")
        connection.close()

    def test_a_future_version_is_refused(self):
        self.stamp_version(99)
        with self.assertRaises(migrate.UnknownSchemaVersion) as caught:
            migrate.open_store(self.path)
        self.assertIn("99", str(caught.exception))

    def test_a_zero_version_is_refused(self):
        self.stamp_version(0)
        with self.assertRaises(migrate.UnknownSchemaVersion):
            migrate.open_store(self.path)

    def test_a_known_version_opens(self):
        self.stamp_version(1)
        connection = migrate.open_store(self.path)
        self.addCleanup(connection.close)
        self.assertEqual(migrate.current_version(connection), 1)

    def test_a_missing_store_is_refused_rather_than_created(self):
        # "The store is gone" and "nothing is registered" must never look the same to a caller
        # on the enforcement path.
        with self.assertRaises(migrate.MigrationError):
            migrate.open_store(self.path)
        self.assertFalse(self.path.exists())

    def test_creating_over_an_existing_store_is_refused(self):
        migrate.create_store(self.path).close()
        with self.assertRaises(migrate.MigrationError):
            migrate.create_store(self.path)


class TestSchemaDocFidelity(StoreTestCase):
    """A6 -- the DDL and the AI-5 schema doc do not silently diverge."""

    def test_assertion_columns_match_the_design_doc_both_ways(self):
        documented = set(design_doc_fields("assertion"))
        created = set(ddl.table_columns(self.connection, "assertion"))
        self.assertEqual(documented, created,
                         f"documented-not-created: {sorted(documented - created)}; "
                         f"created-not-documented: {sorted(created - documented)}")

    def test_spent_ref_columns_match_the_design_doc_both_ways(self):
        self.assertEqual(set(design_doc_fields("spent_ref")),
                         set(ddl.table_columns(self.connection, "spent_ref")))

    def test_the_doc_parser_actually_finds_fields(self):
        # Negative control on the parser itself: a parser that silently returned an empty set
        # would make both comparisons above pass against an empty created set and prove nothing.
        self.assertIn("assertion_uid", design_doc_fields("assertion"))
        self.assertEqual(len(design_doc_fields("assertion")), 15)
        self.assertEqual(len(design_doc_fields("spent_ref")), 3)

    def test_declared_vocabularies_match_the_ddl_checks(self):
        for value in ddl.CLASS_VOCABULARY:
            self.assertIn(f"'{value}'", ddl.ASSERTION_TABLE)
        for value in ddl.STATE_VOCABULARY:
            self.assertIn(f"'{value}'", ddl.ASSERTION_TABLE)
        for value in ddl.EVIDENCE_CLASS_VOCABULARY_V1:
            self.assertIn(f"'{value}'", ddl.ASSERTION_TABLE)


if __name__ == "__main__":
    unittest.main()
