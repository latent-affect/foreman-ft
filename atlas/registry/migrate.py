"""ATLASSN-126 -- the additive-migration harness for the registry store (GOALS.json C15).

C15: "Schema change is an additive migration bumping user_version: applying migration N+1 to a
populated version-N store preserves every existing row, and a store at an unexpected version
denies reads per C6 rather than being interpreted loosely."

Two halves, and they fail in opposite directions on purpose:

  - apply_additive() REFUSES a migration that is not additive, rather than trusting the author
    to have written an additive one. "Additive" is the criterion's own word; leaving it as a
    convention the next implementer is expected to honour is how a criterion becomes prose.
  - open_store() REFUSES a store whose user_version this code does not know, rather than
    reading it on the assumption that an unknown version is probably close enough. An
    unexpected version means the file was written by code this process has never seen; there is
    no safe way to interpret its rows, and guessing produces a confident wrong answer.

The read-path DENY that an unexpected version ultimately produces belongs to C6's availability
matrix (ATLASSN-130), not here. What lives here is the refusal that makes that DENY reachable.
"""

from collections import namedtuple
from pathlib import Path

from atlas.registry import ddl

# A migration is data, not code: version is the version it produces, statements run in order,
# all inside one transaction with the user_version bump.
Migration = namedtuple("Migration", "version name statements")

KNOWN_VERSIONS = (ddl.SCHEMA_VERSION,)

# Keyword guard, not a SQL parser, and stated as such. It catches the destructive forms a
# hand-written migration actually reaches for; it cannot catch a destructive statement written
# to evade it, and it is not a security boundary -- migrations are authored in-repo and land
# through the same review as everything else. Its job is to make a mistake loud, not to stop an
# adversary.
NON_ADDITIVE_MARKERS = (
    "DROP TABLE", "DROP COLUMN", "DROP INDEX", "RENAME TO", "RENAME COLUMN",
    "DELETE FROM", "TRUNCATE", "UPDATE ",
)


class MigrationError(Exception):
    """Base for every refusal this module raises."""


class UnknownSchemaVersion(MigrationError):
    """The store's user_version is not one this code knows how to read."""


class NonAdditiveMigration(MigrationError):
    """A migration statement would destroy or rewrite existing data."""


class VersionSequenceError(MigrationError):
    """A migration was applied to a store that is not at its immediate predecessor version."""


def current_version(connection):
    return connection.execute("PRAGMA user_version").fetchone()[0]


def assert_additive(migration):
    """Raise unless every statement in `migration` only adds.

    Checked against an upper-cased, whitespace-collapsed form so that formatting cannot hide a
    marker from the comparison.
    """
    for statement in migration.statements:
        flattened = " ".join(statement.upper().split())
        for marker in NON_ADDITIVE_MARKERS:
            if marker in flattened:
                raise NonAdditiveMigration(
                    f"migration {migration.version} ({migration.name}) contains {marker!r}, "
                    f"which is not additive; C15 requires existing rows to survive a migration "
                    f"field-for-field"
                )


def apply_additive(connection, migration):
    """Apply one additive migration and bump user_version, in a single transaction.

    Either the statements and the version bump both land, or neither does. A migration that
    half-applied would leave a store whose version lies about its own shape, which is worse
    than a migration that failed.
    """
    assert_additive(migration)
    present = current_version(connection)
    if present != migration.version - 1:
        raise VersionSequenceError(
            f"migration {migration.version} ({migration.name}) expects a store at version "
            f"{migration.version - 1}, found {present}"
        )
    connection.execute("BEGIN")
    try:
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {migration.version}")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return current_version(connection)


def create_store(path):
    """Create a new store at `path` with the v1 schema. Fails if one is already there."""
    path = Path(path)
    if path.exists():
        raise MigrationError(f"refusing to create a store over an existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = ddl.connect(path)
    connection.execute("BEGIN")
    try:
        ddl.create_schema(connection)
    except Exception:
        connection.execute("ROLLBACK")
        connection.close()
        raise
    connection.execute("COMMIT")
    return connection


def open_store(path, known_versions=KNOWN_VERSIONS):
    """Open an existing store, refusing any user_version this code does not know.

    Deliberately does NOT create a missing store: a caller on the enforcement path that
    silently creates an empty registry would turn "the store is gone" into "nothing is
    registered", and those two must not look the same. Creation is create_store()'s explicit job.
    """
    path = Path(path)
    if not path.is_file():
        raise MigrationError(f"no registry store at {path}")
    connection = ddl.connect(path)
    version = current_version(connection)
    if version not in known_versions:
        connection.close()
        raise UnknownSchemaVersion(
            f"registry store at {path} is at schema version {version}, which this code does "
            f"not know (known: {sorted(known_versions)}); refusing to interpret it"
        )
    return connection


def journal_mode(connection):
    return connection.execute("PRAGMA journal_mode").fetchone()[0]


def integrity_snapshot(connection, table):
    """Every row of `table` as a list of dicts, for field-for-field comparison across a
    migration. Ordered by rowid so the comparison is stable, and built from the live column
    list rather than a hardcoded one so a migration that adds a column is still comparable on
    the columns that existed before it."""
    columns = ddl.table_columns(connection, table)
    quoted = ", ".join(f'"{name}"' for name in columns)
    rows = connection.execute(f"SELECT {quoted} FROM {table} ORDER BY rowid").fetchall()
    return [dict(zip(columns, row)) for row in rows]


__all__ = [
    "Migration", "MigrationError", "UnknownSchemaVersion", "NonAdditiveMigration",
    "VersionSequenceError", "KNOWN_VERSIONS", "current_version", "assert_additive",
    "apply_additive", "create_store", "open_store", "journal_mode", "integrity_snapshot",
]
