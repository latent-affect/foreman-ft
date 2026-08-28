"""Applies the warehouse schema (extracted from ARCHITECTURE.md section 16 via ddl.py) to a
SQLite connection, idempotently, and records the applied migration in schema_migration.

Migration 1 is the whole section-16 schema as one unit -- ARCHITECTURE.md section 11 names
splitting a breaking migration into a real up/down sequence as deferred (D14, "migrations are
barely designed"); this component implements exactly what section 16 specifies today, not the
deferred behavior.
"""

import sqlite3

from . import ddl

MIGRATION_VERSION = 1
MIGRATION_NAME = "atlas-v1-warehouse-schema"
ATLAS_VERSION = "0.1.0"


class MigrationError(RuntimeError):
    pass


def _host_id():
    # Salted, not the raw hostname -- ARCHITECTURE.md's ingest_run.host_id comment: this
    # exists so a future cross-machine merge can tell runs apart, single-machine today (D13).
    import hashlib
    import socket

    return hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16]


def is_migrated(conn):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration'"
    ).fetchone()
    if row is None:
        return False
    applied = conn.execute(
        "SELECT 1 FROM schema_migration WHERE version=?", (MIGRATION_VERSION,)
    ).fetchone()
    return applied is not None


def apply(conn):
    """Idempotent: if migration MIGRATION_VERSION is already recorded in schema_migration,
    returns without re-applying (SQLite's CREATE TABLE/VIEW would raise "already exists"
    otherwise, so idempotency is not implicit -- it is checked explicitly here, not assumed
    (F3's failure signature)). Returns the applied ddl_sha256 either way."""
    schema_sql, seed_sql = ddl.extract_schema_and_seed_sql()
    digest = ddl.ddl_sha256()

    if is_migrated(conn):
        row = conn.execute(
            "SELECT ddl_sha256 FROM schema_migration WHERE version=?", (MIGRATION_VERSION,)
        ).fetchone()
        recorded_digest = row[0] if row else None
        if recorded_digest != digest:
            raise MigrationError(
                f"schema_migration records version {MIGRATION_VERSION} with ddl_sha256 "
                f"{recorded_digest!r}, but the current ARCHITECTURE.md-extracted DDL hashes to "
                f"{digest!r} -- the document changed after this database was migrated. "
                f"Section 16's own stated purpose for ddl_sha256 is detecting exactly this; "
                f"refusing to silently proceed on a mismatch."
            )
        return digest

    conn.executescript(schema_sql)
    conn.executescript(seed_sql)

    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO schema_migration (version, name, applied_at, applied_by_run, ddl_sha256) "
        "VALUES (?, ?, ?, NULL, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME, now, digest),
    )
    conn.commit()
    return digest


def new_ingest_run(conn, status="running"):
    """Convenience for callers (dq_runner, tests) that need a real ingest_run row to attach
    dq_check_run rows to -- dq_check_run.run_id references ingest_run(run_id), not a bare
    counter, per section 16's DDL."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO ingest_run (started_at, status, atlas_version, host_id) VALUES (?, ?, ?, ?)",
        (now, status, ATLAS_VERSION, _host_id()),
    )
    conn.commit()
    return cur.lastrowid


def connect(db_path):
    """Opens (creating if absent) a warehouse database at db_path, applies WAL mode and foreign
    keys per section 16's DDL header, and ensures the migration is applied. Returns the open
    connection."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    apply(conn)
    return conn
