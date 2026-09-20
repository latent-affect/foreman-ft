"""ATLASSN-130 -- the availability matrix (GOALS.json C6, failure signature F1).

THIS IS THE FAIL-CLOSED BATTERY'S SUBJECT. F1 -- a gate that looks alive and does nothing -- is
this project's signature failure, and every branch below exists so that a registry which cannot
answer says DENY with a reason, rather than shrugging and letting the caller proceed.

Every store-level fault denies EVERY requested edge, because nothing could be read at all. That
is the opposite of C7's per-assertion granularity, and the two must not be confused: a malformed
row denies its own edge while its siblings serve; an unopenable store denies the whole pass.
Both are tested, in the same suite, precisely so neither can quietly acquire the other's
behaviour.

`registry-unavailable` is a STATUS, not a flavour of absence. An auditor has to be able to tell
"nobody verified this edge" (never-registered) from "nobody could ask" (registry-unavailable).
Collapsing them would make a broken store indistinguishable from an unverified edge, which is
the most comfortable possible way to fail open.

CLASSIFICATION IS BY MEASURED BEHAVIOUR, NOT BY THE DOC'S ERRNO. The schema doc names
SQLITE_READONLY_RECOVERY for the hot-WAL read-only case. Measured on this machine (sqlite
3.53.4, probe run 2026-09-12): a store copied with a live -wal into a read-only location and
opened mode=ro raises SQLITE_CANTOPEN -- "unable to open database file" -- because SQLite cannot
create the -shm file it needs to replay the log, and it fails before it ever reports a
readonly-recovery condition. So SQLITE_CANTOPEN with a -wal sidecar present is classified
recovery-needed, and the heuristic is written down here rather than left for a reader to
rediscover. SQLITE_READONLY* is still mapped, because a differently-shaped read-only store can
raise it; both routes reach the same named sub-reason.

THE READ PATH NEVER WRITES. The store is opened mode=ro through a URI, so a read cannot create a
missing store, repair a corrupt one, or replay a WAL. A read that healed the thing it was
inspecting would destroy the evidence an availability fault is supposed to produce.
"""

import sqlite3
from pathlib import Path

from atlas.registry import ddl, migrate, status

SUB_ABSENT = "absent"
SUB_LOCKED = "locked"
SUB_CORRUPT = "corrupt"
SUB_RECOVERY_NEEDED = "recovery-needed"
SUB_SCHEMA_VERSION = "schema-version"
SUB_READ_ERROR = "read-error"

STORE_LEVEL_SUB_REASONS = (
    SUB_ABSENT, SUB_LOCKED, SUB_CORRUPT, SUB_RECOVERY_NEEDED, SUB_SCHEMA_VERSION, SUB_READ_ERROR,
)

# Milliseconds a read will wait on a lock before giving up and denying. Kept small on purpose:
# these reads sit off the per-tool-call blocking path and a gate that waits is a gate that
# stalls the thing it guards. Exceeding it is `locked`, which is a real answer, not a failure to
# get one.
DEFAULT_BUSY_BUDGET_MS = 250


def unavailable(edge_ids, sub_reason):
    """Deny every requested edge. A store-level fault is not per-assertion."""
    return {
        edge_id: status.StatusResult(
            edge_id, status.REGISTRY_UNAVAILABLE, status.DENY, sub_reason, None)
        for edge_id in edge_ids
    }


def classify(exception, store_path):
    """Map a real sqlite exception onto one of the matrix's named sub-reasons.

    Uses sqlite_errorname where the runtime provides it (3.11+), because the message text is not
    a stable contract. Falls back to message matching only when the attribute is absent.
    """
    name = getattr(exception, "sqlite_errorname", "") or ""
    message = str(exception).lower()

    if name.startswith("SQLITE_BUSY") or "database is locked" in message:
        return SUB_LOCKED
    if name.startswith("SQLITE_READONLY"):
        return SUB_RECOVERY_NEEDED
    if name in ("SQLITE_NOTADB", "SQLITE_CORRUPT") or name.startswith("SQLITE_CORRUPT"):
        return SUB_CORRUPT
    if "file is not a database" in message or "malformed" in message:
        return SUB_CORRUPT
    if name == "SQLITE_CANTOPEN":
        # See the module docstring: measured, this is how a hot WAL under a read-only opener
        # actually surfaces. Without a -wal sidecar there is nothing to replay, so it is an
        # ordinary read failure instead.
        if Path(str(store_path) + "-wal").exists():
            return SUB_RECOVERY_NEEDED
        return SUB_READ_ERROR
    return SUB_READ_ERROR


def read_guarded(store_path, edge_ids, now, busy_budget_ms=DEFAULT_BUSY_BUDGET_MS,
                 known_versions=migrate.KNOWN_VERSIONS, connect=None):
    """Status for each edge, with every store-level fault converted to a named DENY.

    `connect` is an injection point used by the suite to reach the catch-all read-error branch,
    which by construction cannot be produced by any specific fault -- it exists for the errors
    nobody enumerated. It defaults to the real opener and is never overridden in production.
    """
    edge_ids = list(edge_ids)
    path = Path(store_path)

    if not path.is_file():
        return unavailable(edge_ids, SUB_ABSENT)

    opener = connect if connect is not None else open_readonly
    connection = None
    try:
        connection = opener(path, busy_budget_ms)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in known_versions:
            return unavailable(edge_ids, SUB_SCHEMA_VERSION)
        return status.read_edges(connection, edge_ids, now)
    except sqlite3.DatabaseError as exc:
        return unavailable(edge_ids, classify(exc, path))
    except Exception:
        # The catch-all C6 requires. Anything unenumerated is still a DENY, never a pass-through
        # exception that a caller might handle by proceeding.
        return unavailable(edge_ids, SUB_READ_ERROR)
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def open_readonly(path, busy_budget_ms):
    """Open the store strictly read-only, with a bounded wait for a lock.

    The first statement is executed here rather than left to the caller because sqlite3.connect
    is lazy: it can return a connection object for a file it has not actually opened, so a fault
    would surface at an unpredictable later point instead of inside this function's own try.
    """
    connection = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, timeout=busy_budget_ms / 1000.0)
    connection.execute("SELECT 1")
    return connection


def read_one(store_path, edge_id, now, **kwargs):
    """Single-edge convenience. Same guarantees; returns one StatusResult."""
    return read_guarded(store_path, [edge_id], now, **kwargs)[edge_id]


__all__ = [
    "SUB_ABSENT", "SUB_LOCKED", "SUB_CORRUPT", "SUB_RECOVERY_NEEDED", "SUB_SCHEMA_VERSION",
    "SUB_READ_ERROR", "STORE_LEVEL_SUB_REASONS", "DEFAULT_BUSY_BUDGET_MS",
    "read_guarded", "read_one", "classify", "unavailable", "open_readonly", "ddl",
]
