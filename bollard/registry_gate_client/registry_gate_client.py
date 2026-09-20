#!/usr/bin/env python3
"""registry_gate_client.py -- ATLASSN-187 (PDP.md section 5's "the piece that would wire a real
gate to a real assertion"; atlas-sonnet ARCHITECTURE.md section 34.6's `registry_to_foremanhook`
reach edge / `registry_gate_client_wired`'s "not-yet-created" pin).

LIBRARY, NOT A HOOK. PDP.md section 5, verbatim: "registry_gate_client (the piece that would
wire a real gate to a real assertion) is deliberately unbuilt, an open design call (no concrete
gate/edge was named to bind it to) rather than an oversight." No concrete gate/edge exists to
bind this to yet, so this module is a plain, importable read client -- no `hook_common` import,
no `main()`, no PreToolUse registration of its own. A future gate hook imports `read_one()` /
`read_many()` and decides what to do with the result; wiring a hook around a consumer nobody
named would be inventing scope this ticket doesn't have.

READ TARGET -- registry.db directly, never ATLAS's `v_registry_assertion` view or query facade.
Decided by the orchestrating session, PDP.md section 11.6 (comment 3791/3792 on ATLASSN-187,
this repo's own `.foreman/alice-proposals/ATLASSN-187-FINDING-read-target-and-drift-risk.md`),
on evidence read directly from atlas-sonnet's `ARCHITECTURE.md`: section 34.0, "the registry's
read path is gate-synchronous and fail-closed, which places it on the enforcement plane... AI-2
gates only the analytics copy and never the enforcement path... the enforcement path must not
inherit warehouse contract-failure states"; section 38.2's own comment on `v_registry_assertion`,
"this view answers 'what did the analytics-copy last see,' not 'is this edge active right now'
(... the authoritative answer is always the live store)." A gate read is exactly the "right now"
question, so the live store is the only correct target -- not a preference, the document's own
explicit disclaimer. No cross-repo Python import of `atlas.registry` either way (the reach
block's own uniform convention, independently confirmed by direct read of ARCHITECTURE.md's
`yaml reach` fence at lines 4306-4317: every edge there is a pinned {repo, path, direction}, none
a Python import).

STATUS-COMPUTATION PARITY, THE REAL COST OF THAT DECISION. Reading registry.db directly means
this module cannot call `atlas.registry.status.compute_status()` or
`atlas.registry.availability.read_guarded()` -- doing so would be the forbidden cross-repo
import. Both are genuinely non-trivial and are reimplemented here FAITHFULLY, not approximated,
from a direct read of both files this same session:

  status.py's `compute_status()`: revoked is checked FIRST and wins over any time arithmetic (a
  revoked row with a future `verified_at` still reads revoked, never a clock anomaly); a
  negative delta (clock stepped backward) denies with its own `clock-anomaly` sub-reason rather
  than clamping to zero; a malformed `lease_s` or unparseable `verified_at` denies with its own
  `malformed` sub-reason, scoped to that one edge (a sibling edge in the same pass still serves,
  C7); the lease interval is closed at BOTH ends (`delta_seconds <= lease_s`), so a read at the
  exact lease boundary is still active; "latest assertion for an edge" is
  `ORDER BY verified_at DESC, id DESC LIMIT 1` -- the `id` tiebreak is deliberate, not
  decoration, for deterministic ordering on a same-second double write.

  availability.py's `read_guarded()`/`classify()`: a store-level fault (absent, locked,
  corrupt, needs-recovery, wrong schema version, or an unenumerated read error) denies EVERY
  requested edge at once, never per-edge -- the opposite of the per-assertion granularity above,
  and the two must not be confused. `classify()` uses `sqlite_errorname` (3.11+) over
  message-text matching where available, and the measured, non-obvious
  SQLITE_CANTOPEN-plus-live-WAL-sidecar -> `recovery-needed` (rather than plain `read-error`)
  heuristic, because that combination is how a hot-WAL read-only open actually surfaces on this
  machine's sqlite (measured 2026-09-12, atlas-sonnet's own probe run, cited in that file's own
  module docstring) -- not the schema doc's named errno, which never actually appears.

THE DRIFT RISK THIS CREATES, DISCLOSED RATHER THAN HIDDEN. Both source files' own docstrings
warn against exactly this kind of second implementation ("a second implementation of that
selection... is how a read and a reconciliation start disagreeing" -- status.py); reimplementing
them here, across a repo boundary, carries the same risk with less visibility (no shared PR ever
reviews both sides together). Mitigation, per the decision this module was built against:
`ARCHITECTURE.md` (claude-hooks-v2) needs a new pinned reach edge --
`registry_status_logic: {repo: "atlas-sonnet", path: "atlas/registry/status.py" and
"atlas/registry/availability.py", direction: "reads", pinned_at: <content-hash of both>}` -- so
a future change to either source file that isn't mirrored here is a detectable, named drift
rather than a silent one. THAT EDGE IS NOT YET ADDED: it is held for Clint, same held-write
pattern as ATLASSN-104/98's ARCHITECTURE.md sections -- this module's code lands regardless;
only the reach-block addition waits. Whoever adds it should also re-diff this file's
REVOKED/ACTIVE/EXPIRED/UNAVAILABLE logic against the pinned sources at that time, once, to
confirm this initial port is actually faithful -- never executed by me (see rationale.md).

STDLIB ONLY, matching atlas/registry/ddl.py's own constraint and registry_write_guard's
GOALS.json constraint for its cross-repo sibling.
"""

import sqlite3
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

# Same derivation registry_write_guard.py already uses for the one authoritative store --
# ARCHITECTURE.md 34.0 (atlas-sonnet), a fixed machine-global path, never project-relative.
DEFAULT_REGISTRY_DB_PATH = Path.home() / ".claude" / "foreman" / "registry" / "registry.db"

# atlas/registry/ddl.py: SCHEMA_VERSION = 1. Mirrored, not imported (see module docstring).
KNOWN_SCHEMA_VERSIONS = (1,)

ACTIVE = "active"
EXPIRED = "expired"
REVOKED = "revoked"
NEVER_REGISTERED = "never-registered"
REGISTRY_UNAVAILABLE = "registry-unavailable"

ALLOW = "allow"
DENY = "deny"

SUB_CLOCK_ANOMALY = "clock-anomaly"
SUB_MALFORMED = "malformed"
SUB_ABSENT = "absent"
SUB_LOCKED = "locked"
SUB_CORRUPT = "corrupt"
SUB_RECOVERY_NEEDED = "recovery-needed"
SUB_SCHEMA_VERSION = "schema-version"
SUB_READ_ERROR = "read-error"

STORE_LEVEL_SUB_REASONS = (
    SUB_ABSENT, SUB_LOCKED, SUB_CORRUPT, SUB_RECOVERY_NEEDED, SUB_SCHEMA_VERSION, SUB_READ_ERROR,
)

# Milliseconds a read will wait on a lock before giving up and denying (mirrors
# availability.py's own DEFAULT_BUSY_BUDGET_MS and its reasoning: these reads sit off the
# per-tool-call blocking path, and a gate that waits is a gate that stalls the thing it guards).
DEFAULT_BUSY_BUDGET_MS = 250

GateResult = namedtuple("GateResult",
                        "edge_id status decision sub_reason assertion_uid verified_at",
                        defaults=(None,))

SELECT_LATEST_FOR_EDGE = """
SELECT assertion_uid, state, verified_at, lease_s
FROM assertion
WHERE edge_id = ?
ORDER BY verified_at DESC, id DESC
LIMIT 1
"""


def parse_timestamp(text):
    """Mirrors atlas/registry/status.py:parse_timestamp exactly (see module docstring's parity
    note). None on anything unparseable -- a malformed timestamp is a per-assertion DENY, not an
    exception that takes the whole read down."""
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_status(row, now, edge_id=None):
    """Mirrors atlas/registry/status.py:compute_status exactly. `row` is None when the edge has
    no assertion at all (NEVER_REGISTERED) -- distinct from REGISTRY_UNAVAILABLE, which is a
    store-level fault (see unavailable() below) and must never be conflated with "nobody
    verified this."""
    if row is None:
        return GateResult(edge_id, NEVER_REGISTERED, DENY, None, None)

    assertion_uid, state, verified_at_text, lease_s = row

    if state == REVOKED:
        return GateResult(edge_id, REVOKED, DENY, None, assertion_uid)

    verified_at = parse_timestamp(verified_at_text)
    if verified_at is None:
        return GateResult(edge_id, EXPIRED, DENY, SUB_MALFORMED, assertion_uid)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta_seconds = (now - verified_at).total_seconds()

    if delta_seconds < 0:
        # Never active. An NTP step backwards must not silently re-activate a stale assertion.
        return GateResult(edge_id, EXPIRED, DENY, SUB_CLOCK_ANOMALY, assertion_uid, verified_at)

    if not isinstance(lease_s, int) or lease_s <= 0:
        return GateResult(edge_id, EXPIRED, DENY, SUB_MALFORMED, assertion_uid, verified_at)

    # Closed interval at BOTH ends, per ARCHITECTURE.md section 34.0's own `<=`: a delta of
    # exactly lease_s is still active.
    if delta_seconds <= lease_s:
        return GateResult(edge_id, ACTIVE, ALLOW, None, assertion_uid, verified_at)

    return GateResult(edge_id, EXPIRED, DENY, None, assertion_uid, verified_at)


def read_edges(connection, edge_ids, now):
    """Mirrors atlas/registry/status.py:read_edges -- one read pass, each edge resolved
    independently (C7's per-assertion granularity): an expired or revoked edge denies on its own
    without touching a healthy sibling read in the same pass."""
    results = {}
    for edge_id in edge_ids:
        row = connection.execute(SELECT_LATEST_FOR_EDGE, (edge_id,)).fetchone()
        results[edge_id] = compute_status(row, now, edge_id=edge_id)
    return results


def unavailable(edge_ids, sub_reason):
    """Mirrors atlas/registry/availability.py:unavailable -- a store-level fault denies EVERY
    requested edge at once. This is the opposite of read_edges' per-assertion granularity above,
    and the two must never be confused: a malformed row denies its own edge while siblings
    serve; an unopenable store denies the whole pass."""
    return {edge_id: GateResult(edge_id, REGISTRY_UNAVAILABLE, DENY, sub_reason, None)
            for edge_id in edge_ids}


def classify(exception, store_path):
    """Mirrors atlas/registry/availability.py:classify exactly, including its measured,
    non-obvious SQLITE_CANTOPEN-plus-live-WAL heuristic (that file's own docstring: measured on
    sqlite 3.53.4, a store copied with a live -wal into a read-only location and opened mode=ro
    raises SQLITE_CANTOPEN rather than a readonly-recovery condition, because SQLite cannot
    create the -shm file it needs to replay the log)."""
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
        if Path(str(store_path) + "-wal").exists():
            return SUB_RECOVERY_NEEDED
        return SUB_READ_ERROR
    return SUB_READ_ERROR


def open_readonly(path, busy_budget_ms):
    """Mirrors atlas/registry/availability.py:open_readonly -- strictly read-only via a URI, a
    bounded lock wait, and one real statement executed here (sqlite3.connect is lazy and can
    return a connection object for a file it never actually opened, so a fault must be forced to
    surface inside this function's own try, not at some unpredictable later point)."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                 timeout=busy_budget_ms / 1000.0)
    connection.execute("SELECT 1")
    return connection


def read_many(edge_ids, now=None, store_path=DEFAULT_REGISTRY_DB_PATH,
             busy_budget_ms=DEFAULT_BUSY_BUDGET_MS, known_versions=KNOWN_SCHEMA_VERSIONS,
             connect=None):
    """Status for each requested edge, with every store-level fault converted to a named DENY
    covering the whole pass (mirrors atlas/registry/availability.py:read_guarded). D2 (PDP.md
    section 5): zero code paths here default to ALLOW on missing or stale data -- absence,
    corruption, a locked store and a malformed row are all DENY, each with a named reason.

    `connect` is a test-only injection point for the catch-all read-error branch, which by
    construction cannot be produced by any specific fault. It defaults to the real opener and is
    never overridden in production, mirroring availability.py's own convention exactly.
    """
    edge_ids = list(edge_ids)
    if now is None:
        now = datetime.now(timezone.utc)
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
        return read_edges(connection, edge_ids, now)
    except sqlite3.DatabaseError as exc:
        return unavailable(edge_ids, classify(exc, path))
    except Exception:
        # The catch-all: anything unenumerated is still a DENY, never a pass-through exception a
        # caller might handle by proceeding.
        return unavailable(edge_ids, SUB_READ_ERROR)
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def read_one(edge_id, now=None, **kwargs):
    """Single-edge convenience. Same guarantees as read_many; returns one GateResult."""
    return read_many([edge_id], now=now, **kwargs)[edge_id]


__all__ = [
    "DEFAULT_REGISTRY_DB_PATH", "KNOWN_SCHEMA_VERSIONS", "ACTIVE", "EXPIRED", "REVOKED",
    "NEVER_REGISTERED", "REGISTRY_UNAVAILABLE", "ALLOW", "DENY", "SUB_CLOCK_ANOMALY",
    "SUB_MALFORMED", "SUB_ABSENT", "SUB_LOCKED", "SUB_CORRUPT", "SUB_RECOVERY_NEEDED",
    "SUB_SCHEMA_VERSION", "SUB_READ_ERROR", "STORE_LEVEL_SUB_REASONS", "DEFAULT_BUSY_BUDGET_MS",
    "GateResult", "parse_timestamp", "compute_status", "read_edges", "unavailable", "classify",
    "open_readonly", "read_many", "read_one",
]
