"""ATLASSN-154: reads the enforcement-plane verification registry's authoritative store
(~/.claude/foreman/registry/registry.db, ARCHITECTURE.md section 34.0) and refreshes
`registry_assertion`, the source table `v_registry_assertion` (ARCHITECTURE.md section 38) is
built over.

Not an append-only log ingest like session_pull/subagent_pull: the source `assertion` table's
revocation (section 34.1's D1-governed write) is an UPDATE on an existing row, never a new one,
so a watermark keyed on rowid would ingest an assertion once and never see its later revocation
-- the wrong direction to be wrong in for a security-relevant store. Shaped instead like
integration_interface_pull.py's reap-and-refresh (section 37.2/38.1's shared reasoning): every
pass re-reads the whole source table fresh and treats it as the CURRENT declared state. There is
exactly one registry, machine-global, so the reap step here is table-wide, not per-project.

Runnable standalone:

    /Users/m5/.venv/bin/python3 -m atlas.ingest.registry_pull
"""

import argparse
import datetime
import sqlite3
from pathlib import Path

from atlas.ingest import stream
from atlas.warehouse import migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAREHOUSE_DB = REPO_ROOT / "atlas" / "warehouse" / "atlas.db"
DEFAULT_REGISTRY_DB = Path.home() / ".claude" / "foreman" / "registry" / "registry.db"

ASSERTION_COLUMNS = [
    "assertion_uid", "edge_id", "component", "class", "lease_s", "verified_at",
    "verifier_session_id", "dispatch_record_id", "evidence_tool_use_id", "evidence_class",
    "state", "revoked_at", "revoked_by_session_id", "revocation_evidence_tool_use_id",
]

# ATLASSN-164. The exact domain atlas.registry.ddl's own CHECK constraints on the source
# `assertion` table declare (class IN ('structural','in-flight'); state IN ('live','revoked')),
# mirrored here rather than imported: this ingest already treats the source as untrusted input
# to be reaped and copied, never as a dependency it links against, and a value-domain constant
# is not the kind of parsing logic C9's "no shared derivation" rule is about -- there is only
# one place either enum could disagree with the other, and importing across the registry/ingest
# boundary here would be the wrong direction (ingest depending on the component it audits).
VALID_CLASSES = frozenset({"structural", "in-flight"})
VALID_STATES = frozenset({"live", "revoked"})


class RegistryPullError(RuntimeError):
    pass


def nowIso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _open_registry_readonly(registry_db_path):
    """A read-only URI connection -- this ingest never writes the source, only reads it (the
    same posture registry_write_guard.py's own docstring names as the one legitimate access
    pattern for anything that isn't atlas.registry.write_path itself)."""
    uri = f"file:{registry_db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def fetch_all_assertions(registry_conn):
    """Every row currently in the source `assertion` table, as sqlite3.Row (row_factory must be
    sqlite3.Row). Ordered by assertion_uid so a partial read (should one ever occur) is at least
    deterministic run to run."""
    registry_conn.row_factory = sqlite3.Row
    columns = ", ".join(ASSERTION_COLUMNS)
    return registry_conn.execute(
        f"SELECT {columns} FROM assertion ORDER BY assertion_uid"
    ).fetchall()


# ATLASSN-166. The other 8 of registry_assertion's 11 NOT NULL columns (ARCHITECTURE.md section
# 38.2's DDL) -- everything row_is_well_formed's original 3 checks (class, lease_s, state) did
# not already cover. write_assertion is the only governed producer, and it has no code path that
# supplies None for any of these (every one is a required parameter or a value it derives itself
# before the INSERT it performs) -- so, exactly like class/lease_s/state, a NULL here cannot have
# come from the governed write path and is the same free tamper signature.
REQUIRED_NOT_NULL_COLUMNS = (
    "assertion_uid", "edge_id", "component", "verified_at", "verifier_session_id",
    "dispatch_record_id", "evidence_tool_use_id", "evidence_class",
)


def row_is_well_formed(row):
    """ATLASSN-164, widened by ATLASSN-166. atlas.registry.write_path.write_assertion is the
    ONLY governed producer of rows in the source `assertion` table, and it only ever supplies a
    class/state from this same domain, a lease_s that is a positive int (no lease_s parameter
    reaching a caller exists on that function at all -- see write_path.py's own write_assertion
    docstring), and a real, non-NULL value for every other NOT NULL column (REQUIRED_NOT_NULL_
    COLUMNS) -- each one is either a required parameter or a value write_assertion derives
    itself before its own INSERT. A row falling outside any of those domains cannot have come
    from the governed write path: the shape itself is a free tamper signature (Iris Chen,
    ATLASSN-164), evidence of an out-of-band write against the source store.

    ATLASSN-166: this function widened checks class/lease_s/state ALREADY had (all three are
    also NOT NULL in the source schema, so a NULL there was already caught -- this closes the
    other 8). Originally checked only 3 of the 11; a NULL in any of the unchecked 8 sailed
    through here as "well-formed" and was only ever discovered later, the hard way, as an
    uncaught sqlite3.IntegrityError at the INSERT into registry_assertion that killed the whole
    ingest cycle (refresh_registry's own docstring now covers the fix on that side). Widening
    this function does NOT by itself prevent that crash -- see refresh_registry's per-row
    try/except, which is the actual safety net and does not depend on this function being
    exhaustive. What widening this function buys is QUARANTINE VISIBILITY: a row that fails one
    of these 8 checks is now flagged and durably recorded BEFORE the insert is even attempted,
    the same as class/lease_s/state already were, instead of surfacing only as an unexplained
    crash with no quarantine trail at all.

    Still returns False on ANY failing check rather than SKIPPING the row here -- ATLASSN-164's
    point stands for all 11 columns, not just the original 3: a malformed row is quarantined for
    visibility, never silently dropped from consideration by this function's own verdict. Only
    an actual insert-time constraint violation (refresh_registry's try/except) decides whether a
    row is skipped; this function's job stays limited to flagging, exactly as before.

    `type(x) is int` rather than `isinstance` deliberately excludes bool (a bool is an int
    subclass in Python; sqlite3 never returns one for an INTEGER column in real use, but this
    stays exact rather than accepting a coincidentally-int-shaped value by isinstance's wider
    rule).
    """
    if row["class"] not in VALID_CLASSES:
        return False
    if type(row["lease_s"]) is not int or row["lease_s"] <= 0:
        return False
    if row["state"] not in VALID_STATES:
        return False
    for column in REQUIRED_NOT_NULL_COLUMNS:
        if row[column] is None:
            return False
    return True


def refresh_registry(warehouse_conn, registry_db_path):
    """One reap-and-refresh pass, in the caller's transaction. Returns (ingested, error_detail)
    -- ingested is the count of assertions now on record in registry_assertion for the source
    uids just read (0 for an empty/never-written registry, which is the honest bootstrap-day-one
    picture per section 34.7, not an error); error_detail is None on a clean pass or a string
    naming what went wrong reaching the source (the row set is left untouched on a read/open
    failure -- reaping to zero on a transient outage would be worse than serving one ingest
    cycle's stale copy, same discipline integration_interface_pull.refresh_project uses).
    Counted by re-querying registry_assertion after the insert loop rather than by hand-tracked
    arithmetic, so a row skipped THIS cycle (ATLASSN-166) that is still sitting there with its
    last-good values from a PRIOR cycle is correctly counted as on record, and a row that never
    inserted at all is correctly not.

    ATLASSN-164: a row failing row_is_well_formed() is still ATTEMPTED here -- this function's
    job is to mirror the source, not to arbitrate it, and withholding the row from
    registry_assertion on this function's OWN say-so would make a tampered assertion simply
    vanish from the warehouse rather than surface as the anomaly it is. It is ADDITIONALLY
    flagged to the same ingest-quarantine sidecar every other atlas.ingest source uses
    (atlas.ingest.stream.record_quarantine), so the tamper signal is durable and visible instead
    of being silently absorbed as an ordinary row, which is what this ingest did before that fix.

    ATLASSN-166: "attempted" is now the operative word -- row_is_well_formed()'s verdict never
    gates the insert (that principle does not change), but the database's own NOT NULL
    constraint is a real backstop the row_is_well_formed() pre-scan cannot promise to be
    exhaustive against, and before this fix a row that tripped it took the whole ingest cycle
    down with it (an uncaught sqlite3.IntegrityError, propagated through run()'s rollback-and-
    reraise). The insert loop below now catches a constraint violation PER ROW, classifies it via
    atlas.ingest.stream.is_bad_data_constraint (the same classifier every other atlas.ingest
    source already uses: NOT NULL/CHECK are the row's fault and are contained; PRIMARYKEY/UNIQUE/
    FOREIGNKEY are this function's own fault and are re-raised, never silently absorbed), and
    quarantines-and-skips only the row that actually violated it -- every other row in the same
    pass still lands. A row already caught by the row_is_well_formed() pre-scan will almost
    always be caught here too (that is expected, not a bug: the pre-scan's job is visibility
    before the attempt, this catch's job is that the attempt can never crash the cycle even when
    the pre-scan does not already know why it will fail)."""
    registry_path = Path(registry_db_path)
    if not registry_path.is_file():
        return 0, (f"no registry store at {registry_path} (bootstrap day one, section 34.7 -- "
                    f"not necessarily an error)")

    try:
        registry_conn = _open_registry_readonly(registry_path)
    except sqlite3.Error as exc:
        return 0, f"could not open {registry_path} read-only: {exc}"

    try:
        rows = fetch_all_assertions(registry_conn)
    except sqlite3.Error as exc:
        return 0, f"could not read assertion table from {registry_path}: {exc}"
    finally:
        registry_conn.close()

    now = nowIso()
    current_uids = [row["assertion_uid"] for row in rows]

    quarantine_path = stream.default_quarantine_path(warehouse_conn)
    for row in rows:
        if row_is_well_formed(row):
            continue
        stream.record_quarantine(
            warehouse_conn, quarantine_path, "registry_assertion", row["assertion_uid"], None,
            f"impossible class/lease_s/state or a NULL required column, out-of-band write "
            f"against the source store suspected: class={row['class']!r} "
            f"lease_s={row['lease_s']!r} ({type(row['lease_s']).__name__}) "
            f"state={row['state']!r}, "
            + ", ".join(f"{col}={row[col]!r}" for col in REQUIRED_NOT_NULL_COLUMNS))

    if current_uids:
        placeholders = ",".join("?" for _ in current_uids)
        warehouse_conn.execute(
            f"DELETE FROM registry_assertion WHERE assertion_uid NOT IN ({placeholders})",
            current_uids,
        )
    else:
        warehouse_conn.execute("DELETE FROM registry_assertion")

    for row in rows:
        values = [row[col] for col in ASSERTION_COLUMNS] + [now]
        try:
            warehouse_conn.execute(
                "INSERT INTO registry_assertion (" + ", ".join(ASSERTION_COLUMNS)
                + ", ingested_at) VALUES (" + ", ".join("?" for _ in ASSERTION_COLUMNS)
                + ", ?) ON CONFLICT (assertion_uid) DO UPDATE SET "
                + ", ".join(f"{col} = excluded.{col}" for col in ASSERTION_COLUMNS
                            if col != "assertion_uid")
                + ", ingested_at = excluded.ingested_at",
                values,
            )
        except sqlite3.IntegrityError as exc:
            # ATLASSN-166. NOT NULL/CHECK (stream.BAD_DATA_CONSTRAINTS) is the ROW's fault --
            # contain it, exactly as record_quarantine already contains every other source's bad
            # rows, and move on to the next one. PRIMARYKEY/UNIQUE/FOREIGNKEY
            # (stream.PIPELINE_FAULT_CONSTRAINTS) is THIS function's fault -- a real bug (e.g. a
            # duplicate assertion_uid the ON CONFLICT clause should have absorbed but somehow
            # didn't) and must stay loud, per stream.is_bad_data_constraint's own contract: False
            # (re-raise) both for a genuine pipeline fault and for an unclassifiable error, so an
            # exception this ingest has never seen before keeps its loud behaviour by default
            # rather than being silently absorbed on a guess.
            if not stream.is_bad_data_constraint(exc):
                raise
            stream.record_quarantine(
                warehouse_conn, quarantine_path, "registry_assertion", row["assertion_uid"],
                None,
                f"insert into registry_assertion rejected by a NOT NULL/CHECK constraint: {exc}")
            continue

    ingested = warehouse_conn.execute(
        "SELECT COUNT(*) FROM registry_assertion").fetchone()[0]
    return ingested, None


def run(warehouse_db_path=DEFAULT_WAREHOUSE_DB, registry_db_path=DEFAULT_REGISTRY_DB):
    """One pass. Returns {"ingested": n, "error": str-or-None}. The whole refresh runs in one
    transaction -- a read/open failure raises before BEGIN does any harm, and a mid-refresh
    exception rolls back rather than leaving a half-reaped table."""
    conn = migrate.connect(str(warehouse_db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            ingested, error_detail = refresh_registry(conn, registry_db_path)
        except Exception:
            conn.rollback()
            raise
        conn.commit()
    finally:
        conn.close()
    return {"ingested": ingested, "error": error_detail}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_WAREHOUSE_DB))
    parser.add_argument("--registry-db", default=str(DEFAULT_REGISTRY_DB))
    args = parser.parse_args()
    result = run(Path(args.db), Path(args.registry_db))
    status = f"error: {result['error']}" if result["error"] else f"{result['ingested']} ingested"
    print(f"registry: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
