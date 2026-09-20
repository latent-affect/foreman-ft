"""ATLASSN-126 -- the registry store's schema, per DESIGN-REGISTRY-SCHEMA-AI5-ATLASSN-110-
20260912.md's "Store layout", "Table assertion" and "Table spent_ref" sections, which the
ticket names as field-level authority.

WHY THIS SQL IS HAND-WRITTEN AND atlas/warehouse/ddl.py's IS NOT. The warehouse extracts its
DDL out of ARCHITECTURE.md section 16 rather than keeping a second hand-copied version, because
two copies drift silently. That is the right instinct and it is unavailable here: the AI-5
schema doc carries markdown TABLES, not a fenced ```sql block, and ARCHITECTURE.md section 34
carries no SQL fence at all (checked -- the document's last sql fence closes at 3671, section 34
opens at 3745). So the anti-drift property is bought a different way: test_migration parses the
field column out of the doc's own markdown tables and compares it both directions against
PRAGMA table_info. Adding a column here without the doc, or in the doc without here, fails.
That is criterion A6, and it is the reason this module is allowed to hold SQL at all.

EVERYTHING IN ONE FILE, DELIBERATELY. spent_ref is inside registry.db rather than a sidecar
because clause (b)'s single-use guarantee depends on the spent-marking and the assertion write
sharing one transaction (section 34.1b). A separate file cannot participate in that
transaction, so the guarantee would quietly become two writes that usually both happen.
"""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

DEFAULT_STORE_PATH = Path.home() / ".claude" / "foreman" / "registry" / "registry.db"

# Git-ignored by design (section 34.0, and .gitignore:3 `*.db` covers it -- verified with
# git check-ignore). Recoverability is the write-audit JSONL replay plus the ATLAS ingested
# copy, never a bare file copy, because a store restored without its -wal sidecar opens clean
# on stale-but-consistent data with no error (the WAL residual named in the schema doc).

ASSERTION_TABLE = """
CREATE TABLE assertion (
    id                              INTEGER PRIMARY KEY,
    assertion_uid                   TEXT    UNIQUE NOT NULL,
    edge_id                         TEXT    NOT NULL,
    component                       TEXT    NOT NULL,
    class                           TEXT    NOT NULL
                                    CHECK (class IN ('structural', 'in-flight')),
    lease_s                         INTEGER NOT NULL CHECK (lease_s > 0),
    verified_at                     TEXT    NOT NULL,
    verifier_session_id             TEXT    NOT NULL,
    dispatch_record_id              TEXT    NOT NULL,
    evidence_tool_use_id            TEXT    NOT NULL,
    evidence_class                  TEXT    NOT NULL
                                    CHECK (evidence_class = 'observed-probe'),
    state                           TEXT    NOT NULL DEFAULT 'live'
                                    CHECK (state IN ('live', 'revoked')),
    revoked_at                      TEXT    NULL,
    revoked_by_session_id           TEXT    NULL,
    revocation_evidence_tool_use_id TEXT    NULL,

    -- All three revocation fields are set together or none of them are. A half-revoked row
    -- would read as revoked (terminal DENY) while carrying no evidence reference to audit,
    -- which is the shape the whole D1 evidence discipline exists to prevent.
    CHECK (
        (revoked_at IS NULL
         AND revoked_by_session_id IS NULL
         AND revocation_evidence_tool_use_id IS NULL)
        OR
        (revoked_at IS NOT NULL
         AND revoked_by_session_id IS NOT NULL
         AND revocation_evidence_tool_use_id IS NOT NULL)
    )
)
"""

# uid-keyed, never rowid-keyed: rowids carry no cross-file meaning (schema doc finding 11), and
# the write-audit replay has to reconstruct this table from JSONL lines that never saw a rowid.
SPENT_REF_TABLE = """
CREATE TABLE spent_ref (
    tool_use_id   TEXT PRIMARY KEY,
    spent_at      TEXT NOT NULL,
    assertion_uid TEXT NOT NULL REFERENCES assertion(assertion_uid)
)
"""

SCHEMA_STATEMENTS = (ASSERTION_TABLE, SPENT_REF_TABLE)

# Declared once here so the read path, the reconciler and the tests share one vocabulary rather
# than three hand-copied string literals. Widening any of these is a migration, never a code
# change that starts accepting a new value.
CLASS_VOCABULARY = ("structural", "in-flight")
STATE_VOCABULARY = ("live", "revoked")
EVIDENCE_CLASS_VOCABULARY_V1 = ("observed-probe",)


def create_schema(connection):
    """Apply the v1 schema to an empty store and stamp its user_version.

    Does not commit: the caller owns the transaction, because store creation and the first
    migration check happen together in migrate.open_store().
    """
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def table_columns(connection, table):
    """Column names actually present in the live store, read back from the database itself
    rather than from the SQL string that was supposed to have created them."""
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def configure_connection(connection):
    """The pragmas that are part of the design, not defaults left to chance.

    WAL is decided (schema doc, finding 8): readers never block on a writer, which kills the
    BUSY-denial storm a verification burst would otherwise cause on the enforcement path.
    foreign_keys is ON because spent_ref's reference to assertion_uid is load-bearing for the
    replay reconstruction and SQLite does not enforce it by default.
    """
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")


def connect(path):
    """Open a store with the design's pragmas applied. isolation_level=None puts transaction
    control in the caller's hands, which D1's single-transaction requirement needs."""
    connection = sqlite3.connect(str(path), isolation_level=None)
    configure_connection(connection)
    return connection
