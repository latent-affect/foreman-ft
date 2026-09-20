"""Applies the warehouse schema (extracted from ARCHITECTURE.md section 16 via ddl.py) to a
SQLite connection, idempotently, and records the applied migration in schema_migration.

Migration 1 is the whole section-16 schema as one unit -- ARCHITECTURE.md section 11 names
splitting a breaking migration into a real up/down sequence as deferred (D14, "migrations are
barely designed"); this component implements exactly what section 16 specifies today, not the
deferred behavior.

ATLASSN-188 (2026-09-20): migrations 23 and 24 below are now wired into connect() for real --
see ddl.py's own top docstring for the corrected architecture-reversal decision (PDP.md section
11.6) that unblocked them. Migrations 2-22 are untouched by that ticket.

ATLASSN-189 (2026-09-20): migration 25 below wires two triggers on dq_check, closing Iris Chen's
real finding that no schema-level guard stopped a severity='contract' row from targeting an
advisory-only source_table. Local-file DDL, same ATLASSN-188 mechanism.

ATLASSN-191 (2026-09-20, Iris Chen's real, executed probe): apply() and apply_additive() below
now wrap their DDL script AND the schema_migration record in one real, explicit transaction --
see apply_additive()'s own docstring for the full incident and the real, measured mechanism this
fix rests on, verified directly against this session's own sqlite3 rather than assumed from the
Python documentation's own wording.

ATLASSN-192 (2026-09-20, Iris Chen's real, executed probe): apply() and apply_additive() below
also now catch a real concurrent-connect() race (two processes/threads both starting from
"not yet migrated" for the same version) and treat "someone else already applied this exact
version while we were trying to" as a normal, successful, idempotent return -- rather than
propagating the loser's own real but benign OperationalError. See apply_additive()'s own
docstring for the full incident and how this interacts with ATLASSN-191's transaction fix."""

import re
import sqlite3
import time

from . import ddl

# ATLASSN-192 BOUNCE FIX (Bob, 2026-09-20, real 20-trial threaded race against the actual
# migrate.connect()): connect()'s own top-level `PRAGMA journal_mode = WAL` call runs BEFORE
# apply(conn) and is entirely outside the retry-protected path inside apply()/apply_additive().
# Setting `PRAGMA busy_timeout` one line earlier does NOT prevent this specific PRAGMA from
# still raising `sqlite3.OperationalError: database is locked` under real two-thread contention
# on a fresh, not-yet-existing db_path -- confirmed empirically, reproduced directly against the
# real migrate.connect() (not a copied function body): 6/20 and 8/20 real trials crashed at
# exactly this statement, busy_timeout=5000 already set beforehand in both runs. The WAL-mode
# transition on a brand-new file apparently does not retry through the ordinary busy handler the
# same way an in-transaction write does. Fix: a small, bounded manual retry loop around this one
# statement (and foreign_keys, defensively, for the same class of contention) -- verified this
# actually closes the race (not merely asserted) via repeated real multi-trial threaded runs
# against the real connect(), see ATLASSN-192-rationale.md's own matching section.
def _execute_with_busy_retry(conn, sql, max_attempts=50, sleep_s=0.05):
    """Retries a single statement on lock contention, bounded (2.5s total budget at the
    defaults). Only retries an OperationalError whose message names locking/busy contention --
    anything else propagates immediately, unretried."""
    last_exc = None
    for _ in range(max_attempts):
        try:
            conn.execute(sql)
            return
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            last_exc = exc
            time.sleep(sleep_s)
    raise last_exc

# ATLASSN-191 BOUNCE FIX (Bob, 2026-09-20), carried forward into this file since it builds on
# ATLASSN-191's own apply()/apply_additive(): a migration's own DDL can already supply its own
# leading transaction control -- migration 7's real SQL (ARCHITECTURE.md section 23.4) is
# deliberately wrapped in its own `BEGIN IMMEDIATE; ... COMMIT;` for a documented concurrent-
# reader schema-version-bump race, a DIFFERENT, already-solved concern from ATLASSN-191's own
# atomicity fix. Unconditionally prepending a second `BEGIN;` collided with it (SQLite does not
# allow a nested BEGIN on one connection), reproduced live against migration 7's real, current
# SQL text. Fix: detect a script that already starts with its own BEGIN (past any leading `--`
# comment lines, which migration 7's real SQL carries several of) and skip prepending a second
# one -- see ATLASSN-191-migrate.py.post's own matching comment for the full incident and the
# empirically-confirmed mechanism (a self-wrapping migration's own COMMIT already closes its
# transaction; the following schema_migration INSERT then runs in its own separate, implicit
# transaction, closed cleanly by the following commit() with no raise).
_SELF_WRAPPED_BEGIN_RE = re.compile(r"^(?:\s*--[^\n]*\n|\s*\n)*\s*BEGIN\b", re.IGNORECASE)


def _script_self_wraps_transaction(sql_text):
    """True when `sql_text`, after skipping any leading `--` comment lines and blank lines,
    itself opens with a BEGIN statement -- meaning it manages its own transaction and must not
    be wrapped in a second, outer BEGIN."""
    return bool(_SELF_WRAPPED_BEGIN_RE.match(sql_text))


# ATLASSN-191 BOUNCE FIX, ROUND 2 (Bob, 2026-09-20): migration 1's own real schema_sql
# (ARCHITECTURE.md section 16) opens with `PRAGMA journal_mode = WAL;` then
# `PRAGMA foreign_keys = ON;`, before any CREATE TABLE. SQLite refuses to CHANGE journal_mode
# while an explicit transaction is open (confirmed empirically: re-asserting an ALREADY-current
# value inside a transaction is a silent no-op, but a REAL mode transition -- the case on a bare
# connection that has not already had journal_mode set to WAL, exactly what a test constructing
# its own connection and calling apply() directly does -- raises
# `OperationalError: cannot change into wal mode from within a transaction`). Migration 1 does
# not self-wrap its own BEGIN (it opens with PRAGMA, not BEGIN), so round 1's fix above does not
# catch this shape either -- confirmed live by Bob running the real atlas/warehouse/tests suite,
# not a synthetic script.
#
# Fix: pull any LEADING PRAGMA statements (past comments/blanks before each one) out of the
# script and run them separately, in autocommit mode, BEFORE opening the transaction for
# whatever remains. This is strictly more correct than leaving them inside the wrapped
# transaction even where they don't crash: SQLite's own docs state `PRAGMA foreign_keys` is a
# silent no-op inside a transaction, so on a bare connection it would have appeared to succeed
# while never actually enabling foreign-key enforcement. Chosen over Bob's other option (making
# migration 1 stop carrying these PRAGMA lines, or having apply() skip wrapping entirely for it)
# because it keeps migration 1 under the SAME shared-transaction atomicity guarantee this ticket
# is about, for the CREATE TABLE/seed statements that follow the PRAGMA lines -- only the PRAGMA
# statements themselves are pulled out, and only because SQLite structurally requires that.
_LEADING_PRAGMA_RE = re.compile(
    r"^(?:\s*--[^\n]*\n|\s*\n)*(?P<pragma>\s*PRAGMA\b[^;]*;)",
    re.IGNORECASE,
)


def _pop_leading_pragma(sql_text):
    """If `sql_text`, after skipping leading comment/blank lines, opens with a PRAGMA statement,
    returns (that statement's text, the remainder after it). Otherwise returns (None, sql_text)
    unchanged."""
    m = _LEADING_PRAGMA_RE.match(sql_text)
    if not m:
        return None, sql_text
    return m.group("pragma"), sql_text[m.end():]


def _extract_leading_pragmas(sql_text):
    """Repeatedly pops leading PRAGMA statements. Returns (list_of_pragma_statement_texts,
    remainder_with_them_removed). An empty list means the script had none, and remainder is
    the input unchanged."""
    pragmas = []
    remainder = sql_text
    while True:
        stmt, remainder = _pop_leading_pragma(remainder)
        if stmt is None:
            break
        pragmas.append(stmt)
    return pragmas, remainder


def _run_script_transactionally(conn, sql_text):
    """The one place apply()/apply_additive() actually execute a migration's SQL text. Pulls
    out any leading PRAGMA statements and runs them separately in autocommit mode first (SQLite
    structurally requires this for a real journal_mode change; see the module-level comment
    above), then runs whatever remains either as-is (if it already self-wraps its own BEGIN --
    round 1's fix) or wrapped in this module's own BEGIN (every other case)."""
    pragmas, remainder = _extract_leading_pragmas(sql_text)
    for stmt in pragmas:
        conn.execute(stmt)
    if _script_self_wraps_transaction(remainder):
        conn.executescript(remainder)
    else:
        conn.executescript("BEGIN;\n" + remainder)

MIGRATION_VERSION = 1
MIGRATION_NAME = "atlas-v1-warehouse-schema"

# ATLASSN-27. Migration 2 is additive: it creates only new objects and alters nothing migration 1
# defined, so version 1's recorded ddl_sha256 stays exactly what a database already recorded.
# This is the minimal end of D14 (ARCHITECTURE.md section 11, "no decision on whether a breaking
# migration re-ingests from zero") and no more -- there is still no down-migration and no answer
# for the breaking case. See DECISION-ATLASSN-27.md.
MIGRATION2_VERSION = 2
MIGRATION2_NAME = "atlas-v2-live-plane"

# ATLASSN-32. Also additive. It ALTERs a migration-2 table to add two columns, which changes
# neither migration 2's recorded DDL text nor its hash -- verified against a byte copy of the
# live warehouse carrying 28,965 real subagent calls: both prior hashes and every row count
# survived unchanged.
MIGRATION3_VERSION = 3
MIGRATION3_NAME = "atlas-v3-deny-join"

# ATLASSN-33. Additive as well: four new tables and three new views, altering nothing migrations
# 1-3 defined. Section 20.1 records why main-session transcripts got parallel tables rather than a
# widened subagent_transcript -- SQLite cannot relax a NOT NULL or a CHECK in place, and the
# twelve-step rebuild it would need would leave section 18.6's recorded DDL text no longer
# describing the live schema.
MIGRATION4_VERSION = 4
MIGRATION4_NAME = "atlas-v4-session-transcripts"

# ATLASSN-35. Data-only: no CREATE/ALTER at all, just an UPDATE demoting
# fail_open_not_double_counted to advisory and an INSERT OR IGNORE adding
# fail_open_pairing_delta as the contract check in its place, on the resolution_rate_delta
# precedent (FATAL-1: a level check with an unfixable historical residue is a gate that fails
# on the calendar, not one that catches a regression). Alters no table any prior migration
# defined, so versions 1-4's recorded hashes stay exactly what they are.
MIGRATION5_VERSION = 5
MIGRATION5_NAME = "atlas-v5-fail-open-pairing-delta"
MIGRATION6_VERSION = 6
MIGRATION6_NAME = "atlas-v6-pip-coverage-and-handler-freshness"
# ATLASSN-51. Migration 6's v_handler_freshness read hook_verdict directly with no trust_state
# and no UNION ALL sentinel, against section 8's invariant -- a FATAL-2 recurrence. Fixed here
# rather than by editing section 22.4, because migration 6 is already recorded in live
# warehouses and apply_additive() re-verifies its hash on every call: an in-place correction
# would raise MigrationError on every connect(). DROP VIEW + CREATE VIEW on a view migration 6
# itself created alters no table and no row, so versions 1-6's recorded hashes stay as they are.
MIGRATION7_VERSION = 7
MIGRATION7_NAME = "atlas-v7-handler-freshness-trust-sentinel"

# ATLASSN-61. Additive: one new table (bash_command_shape) and two new views. Alters no table
# any prior migration defined, so versions 1-7's recorded hashes are unchanged. QA-fork phase
# only -- see ARCHITECTURE.md section 24.3; nothing here runs this against atlas-sonnet/master.
MIGRATION8_VERSION = 8
MIGRATION8_NAME = "atlas-v8-bash-command-shape"

# ATLASSN-62. Additive: two nullable TEXT columns on hook_verdict (ledger_origin, origin_signal),
# the ingest half of FORE-273/FORE-291's writer-identity field. Defines no view and alters no other
# table, so versions 1-8's recorded hashes are unchanged. Built against the upstream value
# `harness-heuristic`, never `harness` -- the rename landed before ingest existed precisely so this
# would not become a warehouse migration. QA-fork phase only, same as migration 8: nothing here
# runs against atlas-sonnet/master, which is an operator decision standing separately.
MIGRATION9_VERSION = 9
MIGRATION9_NAME = "atlas-v9-hook-verdict-ledger-origin"

MIGRATION10_VERSION = 10
MIGRATION10_NAME = "atlas-v10-ledger-join-coverage-delta"

MIGRATION11_VERSION = 11
MIGRATION11_NAME = "atlas-v11-ingest-run-plane"

# ATLASSN-80 / FORE-281 item 1. Additive: one new view (session_prior), no ALTER on any table
# any prior migration defined, so versions 1-11's recorded hashes are unchanged. CREATE VIEW
# only -- neither migration 10's hazard (a registered dq_check with no CHECKERS function) nor
# migration 11's (code reading a column that does not exist yet) applies, so this file's landing
# order relative to dq_runner.py is unconstrained; it is constrained relative to facade.py
# instead (see apply_migration12's own docstring).
MIGRATION12_VERSION = 12
MIGRATION12_NAME = "atlas-v12-session-prior"

# ATLASSN-84 and ATLASSN-85. Data-only, two INSERT OR IGNORE rows into dq_check, so migration
# 10's hazard applies and migration 12's does not: dq_runner.CHECKERS must already carry
# check_hook_coverage_per_session and check_build_process_ratio when this applies, or run_all()
# raises UnregisteredCheckError on every 60-second tick. Both functions land in the same commit.
MIGRATION13_VERSION = 13
MIGRATION13_NAME = "atlas-v13-session-contract-checks"

# ATLASSN-88. Same hazard and same data-only shape as migration 13: two INSERT OR IGNORE rows
# into dq_check, so dq_runner.CHECKERS must already carry check_turn_final_bytes_per_session_day
# and check_sendmessage_bytes_per_session_day when this applies.
MIGRATION14_VERSION = 14
MIGRATION14_NAME = "atlas-v14-turn-final-sendmessage-byte-deltas"

# ATLASSN-95. Additive: one new table (bash_command_shape_v2), no new view. Alters no table any
# prior migration defined -- bash_command_shape (migration 8) is deliberately untouched, a
# parallel table rather than an ALTER, for the same UNIQUE(transcript_kind, call_id)-can't-hold-
# two-versions reason migration 4 chose a parallel table over a 12-step rebuild. See
# ARCHITECTURE.md section 31.1.
MIGRATION15_VERSION = 15
MIGRATION15_NAME = "atlas-v15-bash-command-shape-v2"

# ATLASSN-96. Additive: one new view (v_gaming_evasion_by_session), no ALTER on any table any
# prior migration defined. Single trust_state, not session_prior's split -- see ARCHITECTURE.md
# section 32.2 for why: all three of this view's sources are batch-plane/materialized, none of
# them a live-plane read.
MIGRATION16_VERSION = 16
MIGRATION16_NAME = "atlas-v16-gaming-evasion-by-session"

# ATLASSN-102, ARCHITECTURE.md section 33. Re-points v_source_freshness (DROP + CREATE, on
# migration 7's precedent) and registers two contract dq_check rows so v_queryable_source
# can name session_transcript and subagent_transcript at all -- absence from that view has
# never meant "failed", it has meant "no contract exists", and the view cannot say which.
MIGRATION17_VERSION = 17
MIGRATION17_NAME = "atlas-v17-transcript-sources-visible"

# ATLASSN-144, ARCHITECTURE.md section 35. Two partial unique indexes deduplicating
# session_tool_call and session_assistant_text. Apply only once the ON CONFLICT-based insert
# path is live in session_pull.py -- see section 35's sequencing note; this migration does not
# itself enforce that ordering.
MIGRATION18_VERSION = 18
MIGRATION18_NAME = "atlas-v18-session-tool-call-text-dedup"

# ATLASSN-143, ARCHITECTURE.md section 36. Seeds the session_tool_call_evidence_attrs_present
# dq_check row at contract severity. The checker itself already landed in dq_runner.py's
# CHECKERS at commit dbed288 -- code-before-DDL, per section 26.2/29.4's convention.
MIGRATION19_VERSION = 19
MIGRATION19_NAME = "atlas-v19-tool-call-evidence-attrs-check"

ATLAS_VERSION = "0.1.0"


class MigrationError(RuntimeError):
    pass


def _host_id():
    # Salted, not the raw hostname -- ARCHITECTURE.md's ingest_run.host_id comment: this
    # exists so a future cross-machine merge can tell runs apart, single-machine today (D13).
    import hashlib
    import socket

    return hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16]


def hostId():
    """Public spelling of the same salted host identifier, added for ATLASSN-27's
    subagent_pull, which needs it for subagent_pull_run.host_id. An alias rather than a rename:
    _host_id() is already called from apply()/new_ingest_run() and renaming it would be an
    unrelated change riding along in this ticket's diff."""
    return _host_id()


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
    (F3's failure signature)). Returns the applied ddl_sha256 either way.

    FIX 2026-09-20 (ATLASSN-191): schema_sql and seed_sql are concatenated into ONE script,
    prefixed with a literal `BEGIN;`, so a broken statement anywhere in either block rolls back
    everything from this call -- see apply_additive()'s own docstring for the full incident and
    the measured mechanism.

    FIX 2026-09-20 (ATLASSN-192): also catches a real concurrent-connect() race -- see
    apply_additive()'s own docstring for the full incident; the same treatment applies here for
    migration 1's own bootstrap race."""
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

    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    combined_sql = schema_sql + "\n" + seed_sql
    try:
        _run_script_transactionally(conn, combined_sql)
        conn.execute(
            "INSERT INTO schema_migration (version, name, applied_at, applied_by_run, "
            "ddl_sha256) VALUES (?, ?, ?, NULL, ?)",
            (MIGRATION_VERSION, MIGRATION_NAME, now, digest),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        # ATLASSN-192: a concurrent connect() (another process or thread, same db_path) may have
        # already applied and recorded this exact version in the gap between our own
        # is_migrated() check above and this statement's own attempt -- re-check once before
        # treating this as a real failure. See apply_additive()'s own docstring for the full
        # incident this closes.
        if is_migrated(conn):
            row = conn.execute(
                "SELECT ddl_sha256 FROM schema_migration WHERE version=?",
                (MIGRATION_VERSION,),
            ).fetchone()
            recorded_digest = row[0] if row else None
            if recorded_digest == digest:
                return digest
            raise MigrationError(
                f"schema_migration records version {MIGRATION_VERSION} with ddl_sha256 "
                f"{recorded_digest!r} (applied by a concurrent connect() while this one was "
                f"racing it), but the current DDL hashes to {digest!r} -- refusing to silently "
                f"proceed on a mismatch."
            )
        raise
    return digest


def is_migrated_version(conn, version):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration'"
    ).fetchone()
    if row is None:
        return False
    applied = conn.execute(
        "SELECT 1 FROM schema_migration WHERE version=?", (version,)
    ).fetchone()
    return applied is not None


def is_migrated2(conn):
    return is_migrated_version(conn, MIGRATION2_VERSION)


def is_migrated3(conn):
    return is_migrated_version(conn, MIGRATION3_VERSION)


def apply_additive(conn, version, name, sql_source, digest_source, section):
    """Applies one additive migration idempotently and records it as its OWN schema_migration
    row. Same drift discipline as apply(): a recorded version whose hash no longer matches its
    section raises rather than silently proceeding.

    Never touches another version's row. That independence is the entire point of the additive
    scheme -- a change to one section's DDL must not invalidate a different migration's recorded
    hash and force a re-ingest of data that did not change.

    `section` is a free-text label identifying the DDL's source for the error message below --
    an ARCHITECTURE.md section number for migrations 2-22, or (ATLASSN-188) a local file path
    for migration 23+. The message text below still says "ARCHITECTURE.md section {section}"
    unconditionally; for a local-file migration this reads slightly oddly (naming a file path
    where it says "section") but still names the real, actionable source unambiguously. Not
    generalized to a source-agnostic wording here, on purpose: doing so would mean touching
    every one of migrations 2-22's own call sites' `section` arguments to add back an explicit
    "ARCHITECTURE.md section" prefix, for a purely cosmetic error-message improvement on a path
    that only fires when an already-applied migration's DDL changes after the fact (rare, and
    already a real incident whenever it happens) -- more edited call sites than this fix's actual
    scope needs, on Orchestrator's own decision to keep migrations 1-22 untouched. Disclosed
    here rather than silently accepted.

    FIX 2026-09-20 (ATLASSN-191, Iris Chen's real, executed probe): appended a syntactically
    invalid second statement to the real migration-23 local SQL file, then called
    migrate.connect() on a fresh db. RESULT (executed live): connect() raised
    sqlite3.OperationalError as expected -- but conn.executescript() had ALREADY committed the
    valid CREATE TABLE session_block_sequence statement that preceded the broken one, while the
    schema_migration INSERT (which records the version) never ran, because it happens AFTER
    executescript() returns, which the raise prevented. Net effect: the table silently exists,
    is_migrated_version() reports false forever, and every subsequent connect() re-attempts and
    re-raises against the same broken file -- but if the file is later fixed by removing the bad
    statement, `CREATE TABLE IF NOT EXISTS` on the retry silently no-ops against a table the
    FIRST, never-recorded, technically-failed attempt actually created, with no way to tell after
    the fact that this migration was ever broken even once.

    THE ROOT MECHANISM, MEASURED DIRECTLY THIS SESSION rather than assumed from Python's own
    documentation: SQLite's DDL statements auto-commit individually inside executescript() unless
    the script text ITSELF contains a literal `BEGIN` -- and executescript() unconditionally ends
    any transaction already open on the connection the moment it is called (confirmed live: a
    Python-level `conn.execute("BEGIN")` issued immediately before calling executescript() was
    silently closed out before the script's own statements ran, so wrapping the CALL in a
    transaction from the Python side does nothing). The fix has to live INSIDE the string
    `sql_source()` returns: prefixed here with a literal `BEGIN;`, so the transaction is real and
    stays open (confirmed: `conn.in_transaction` is True immediately after a script starting with
    an in-script `BEGIN;` raises partway through) until this function's own `conn.commit()` --
    which now also carries the schema_migration INSERT inside that SAME transaction, so the DDL
    and its own record land atomically together or not at all. On any exception, `conn.rollback()`
    undoes everything from the in-script BEGIN forward (confirmed: the earlier, successfully-run
    CREATE TABLE statement is gone after rollback, not left behind) -- closing exactly the gap
    Iris's repro found, for every additive migration through this one shared function, not just
    migration 23.

    FIX 2026-09-20 (ATLASSN-192, Iris Chen's real, executed probe, filed separately the same
    round): two real threads, synchronized on a threading.Barrier, both called migrate.connect()
    on the exact same not-yet-existing db_path at effectively the same instant, simulating two
    processes cold-starting against a fresh warehouse around the same time. RESULT (executed
    live, reproduced on the run that produced this ticket): one thread completed successfully;
    the other raised sqlite3.OperationalError ("table ... already exists"), unhandled,
    propagating all the way out of connect(). Ground truth after the race: the version was
    recorded exactly once and the db was left in a usable state -- NOT data corruption, but a
    real, unhandled crash for whichever caller loses the race, with no retry around the
    check-then-act sequence (is_migrated_version() check, then executescript(), then INSERT).

    INTERACTION WITH THE ATLASSN-191 FIX ABOVE, NAMED EXPLICITLY: wrapping the DDL in an explicit
    transaction (ATLASSN-191) changes WHICH error the race's loser sees -- from a logical
    "already exists" collision (each statement auto-committing individually, so both threads'
    identical CREATE TABLE statements can genuinely race against each other and one loses on the
    object already existing) toward a lock-contention error ("database is locked") once one
    thread's real transaction is actively held -- but does not remove the race itself: the loser
    STILL gets some real, unhandled OperationalError either way. This fix is what actually closes
    the race, regardless of which specific error text the loser happens to see: on ANY exception
    from the executescript()/INSERT/commit() sequence, re-check is_migrated_version() for this
    exact version ONCE before giving up. If it is now true AND the recorded hash matches what
    this call itself computed, the other caller already did the real work -- return the digest
    as a normal, successful, idempotent result, exactly as if this call had won the race itself.
    If the hash does NOT match, that is a real drift, not a race, and raises MigrationError as
    it always has. If the version is still not recorded at all, the original exception was a
    genuine failure, not a race loss, and propagates unchanged.

    REQUIRES connect()'s own `PRAGMA busy_timeout` (set once, at connection open, on every
    connection this module opens) to be effective against the tightest form of the race.
    Verified empirically, and disclosed here because it was a real gap in this fix's first
    draft: the retry-check above alone is not sufficient when the loser hits SQLite's own
    immediate "database is locked" (SQLITE_BUSY, default busy_timeout=0) BEFORE the winner
    has committed -- at that exact instant is_migrated_version() has nothing to see yet, so
    the retry-check finds the version still unrecorded and correctly re-raises the loser's
    own "database is locked" error, uncaught, same as before this fix. Setting a real
    busy_timeout makes SQLite itself block the loser's write attempt until the winner's
    transaction ends, which turns that same race into the ALREADY-HANDLED "table already
    exists"-shaped case (the winner has committed by the time the loser's statement
    proceeds), where the retry-check above does close it. The two pieces are one fix, not
    two independent ones."""
    digest = digest_source()

    if is_migrated_version(conn, version):
        row = conn.execute(
            "SELECT ddl_sha256 FROM schema_migration WHERE version=?", (version,)
        ).fetchone()
        recorded_digest = row[0] if row else None
        if recorded_digest != digest:
            raise MigrationError(
                f"schema_migration records version {version} with ddl_sha256 "
                f"{recorded_digest!r}, but ARCHITECTURE.md section {section} now hashes to "
                f"{digest!r} -- the document changed after this database was migrated. "
                f"Refusing to silently proceed on a mismatch."
            )
        return digest

    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    script_text = sql_source()
    try:
        _run_script_transactionally(conn, script_text)
        conn.execute(
            "INSERT INTO schema_migration (version, name, applied_at, applied_by_run, "
            "ddl_sha256) VALUES (?, ?, ?, NULL, ?)",
            (version, name, now, digest),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        if is_migrated_version(conn, version):
            row = conn.execute(
                "SELECT ddl_sha256 FROM schema_migration WHERE version=?", (version,)
            ).fetchone()
            recorded_digest = row[0] if row else None
            if recorded_digest == digest:
                return digest
            raise MigrationError(
                f"schema_migration records version {version} with ddl_sha256 "
                f"{recorded_digest!r} (applied by a concurrent connect() while this one was "
                f"racing it), but ARCHITECTURE.md section {section} now hashes to {digest!r} -- "
                f"refusing to silently proceed on a mismatch."
            )
        raise
    return digest


def apply_migration2(conn):
    """ARCHITECTURE.md section 18.6, the live plane (ATLASSN-27). Verified against a byte copy of
    the real live warehouse: version 1's ddl_sha256 unchanged, hook_verdict's 233,924 rows
    untouched, v_atlas_status byte-identical before and after."""
    return apply_additive(
        conn, MIGRATION2_VERSION, MIGRATION2_NAME,
        ddl.extract_migration2_sql, ddl.migration2_sha256, "18.6",
    )


def is_migrated4(conn):
    return is_migrated_version(conn, MIGRATION4_VERSION)


def apply_migration4(conn):
    """ARCHITECTURE.md section 20.4, main-session transcripts (ATLASSN-33). Verified against a
    byte copy of the live warehouse: versions 1, 2 and 3 hashes all unchanged, hook_verdict and
    subagent_tool_call counts preserved, v_atlas_status identical, dq_check 19 -> 20 rows."""
    return apply_additive(
        conn, MIGRATION4_VERSION, MIGRATION4_NAME,
        ddl.extract_migration4_sql, ddl.migration4_sha256, "20.4",
    )


def is_migrated5(conn):
    return is_migrated_version(conn, MIGRATION5_VERSION)


def apply_migration5(conn):
    """ARCHITECTURE.md section 21.3 (ATLASSN-35). Data-only -- no CREATE/ALTER -- so there is no
    row-count or hash invariant to verify against prior migrations beyond apply_additive's own
    drift check; correctness here is that dq_check ends with fail_open_not_double_counted
    advisory and fail_open_pairing_delta contract, checked by the caller after connect()."""
    return apply_additive(
        conn, MIGRATION5_VERSION, MIGRATION5_NAME,
        ddl.extract_migration5_sql, ddl.migration5_sha256, "21.3",
    )


def is_migrated6(conn):
    return is_migrated_version(conn, MIGRATION6_VERSION)


def apply_migration6(conn):
    """ARCHITECTURE.md section 22.4 (ATLASSN-36/38). Adds v_pip_coverage (hook-vs-skill coverage
    for WebSearch/WebFetch, the operator's specific PIP question) and v_handler_freshness
    (first-seen/last-seen per handler_id, so a consumer can exclude dead handlers from a
    'current state' claim). Additive CREATE VIEW only -- no ALTER on any table a prior migration
    defined, no row-count invariant to check beyond apply_additive's own drift check."""
    return apply_additive(
        conn, MIGRATION6_VERSION, MIGRATION6_NAME,
        ddl.extract_migration6_sql, ddl.migration6_sha256, "22.4",
    )


def is_migrated7(conn):
    return is_migrated_version(conn, MIGRATION7_VERSION)


def apply_migration7(conn):
    """ARCHITECTURE.md section 23.4 (ATLASSN-51). Re-creates v_handler_freshness behind the
    trust_state/UNION ALL sentinel migration 6 omitted. Verified against a copy of the live
    warehouse: with a contract check forced to fail, the pre-fix view served all 36 handlers and
    377,094 rows from a distrusted source and the post-fix view returns exactly one
    SOURCE-DISTRUSTED-DO-NOT-USE row; in the trusted state first_seen, last_seen and row_count
    are row-for-row identical to the raw aggregate over the same 36 handlers, and versions 1-6's
    recorded hashes are unchanged."""
    return apply_additive(
        conn, MIGRATION7_VERSION, MIGRATION7_NAME,
        ddl.extract_migration7_sql, ddl.migration7_sha256, "23.4",
    )


def is_migrated8(conn):
    return is_migrated_version(conn, MIGRATION8_VERSION)


def is_migrated9(conn):
    return is_migrated_version(conn, MIGRATION9_VERSION)


def is_migrated10(conn):
    return is_migrated_version(conn, MIGRATION10_VERSION)


def is_migrated11(conn):
    return is_migrated_version(conn, MIGRATION11_VERSION)


def apply_migration11(conn):
    """ARCHITECTURE.md section 27.6 (ATLASSN-72). One nullable TEXT column on ingest_run, so a
    fast-plane tick stops being indistinguishable from a batch run to the five checks that select
    their comparison points from that table.

    Migration 9's exact shape: a bare ALTER TABLE ADD COLUMN with no default, which rewrites no
    existing row, so there is no row-count invariant to check beyond apply_additive's own drift
    check over versions 1-10.

    LANDING ORDER IS THE REVERSE OF MIGRATION 10'S, and section 26.2's rule must not be
    generalised as "migrate.py last". That rule exists because migration 10 REGISTERED a dq_check
    row, and a registered check name with no function in CHECKERS makes run_all() raise
    UnregisteredCheckError every 60 seconds. Migration 11 registers nothing; it adds a column that
    dq_runner's scoped selectors will READ, so the dependency runs the other way and this file
    lands BEFORE dq_runner.py. Selectors landing first would query a column that does not exist
    yet and the next full ingest would fail on it. The durable rule is "land in dependency order,
    after working out which direction the dependency actually runs" -- which survives the next
    case, where 26.2's phrasing would not."""
    return apply_additive(
        conn, MIGRATION11_VERSION, MIGRATION11_NAME,
        ddl.extract_migration11_sql, ddl.migration11_sha256, "27.6",
    )


def apply_migration10(conn):
    """ARCHITECTURE.md section 26.6 (ATLASSN-63). Registers ledger_join_coverage_delta, the
    run-over-run drop check check_ledger_join_coverage's own docstring named as unbuilt.

    Data-only, exactly like migration 5: one INSERT OR IGNORE into dq_check, no CREATE and no
    ALTER, so there is no row count or object count to preserve beyond apply_additive's own drift
    check over versions 1-9. Correctness here is that dq_check gains exactly one row and that
    dq_runner.CHECKERS already carries a function of that name -- run_all() raises
    UnregisteredCheckError, deliberately, on a registered name it cannot dispatch, and both
    launchd jobs call connect() every 60 seconds, so landing this file before dq_runner.py would
    be a total DQ outage rather than a degraded run. Section 26.2 records that ordering."""
    return apply_additive(
        conn, MIGRATION10_VERSION, MIGRATION10_NAME,
        ddl.extract_migration10_sql, ddl.migration10_sha256, "26.6",
    )


def apply_migration9(conn):
    """ARCHITECTURE.md section 25.4 (ATLASSN-62). Two nullable TEXT columns on hook_verdict.

    Additive ALTERs only, no view, so apply_additive's own drift check is the whole invariant --
    there is no row count to preserve because no row is rewritten. NULL in either column means
    "ingested before this migration" rather than "origin unknown"; nothing backfills, because the
    origin was never present in those rows' source lines to recover.
    """
    return apply_additive(
        conn, MIGRATION9_VERSION, MIGRATION9_NAME,
        ddl.extract_migration9_sql, ddl.migration9_sha256, "25.4",
    )


def apply_migration8(conn):
    """ARCHITECTURE.md section 24.4 (ATLASSN-61). bash_command_shape table plus its two
    base-rate views. Additive CREATE TABLE/VIEW only -- no ALTER on any table a prior migration
    defined, no row-count invariant to check beyond apply_additive's own drift check."""
    return apply_additive(
        conn, MIGRATION8_VERSION, MIGRATION8_NAME,
        ddl.extract_migration8_sql, ddl.migration8_sha256, "24.4",
    )


def is_migrated12(conn):
    return is_migrated_version(conn, MIGRATION12_VERSION)


def apply_migration12(conn):
    """ARCHITECTURE.md section 28.4 (ATLASSN-80 / FORE-281 item 1). session_prior: a per-session
    deny history keyed on (session_id, as_of_ts), split batch_trust_state/live_trust_state per
    section 28.3. Additive CREATE VIEW only -- no ALTER on any table a prior migration defined,
    no row-count invariant to check beyond apply_additive's own drift check over versions 1-11.

    LANDING ORDER: this file has no dependency direction of its own to worry about (no dq_check
    row registered, no column added), but atlas/query/facade.py's ALLOWED_VIEWS addition DOES
    depend on this -- a facade.fetch('session_prior') call before this migration has applied
    would raise sqlite3.OperationalError: no such view. Land this file (and let connect() apply
    it) before the facade.py change, not the other way around."""
    return apply_additive(
        conn, MIGRATION12_VERSION, MIGRATION12_NAME,
        ddl.extract_migration12_sql, ddl.migration12_sha256, "28.4",
    )


def apply_migration13(conn):
    """ARCHITECTURE.md section 29.4 (ATLASSN-84, ATLASSN-85). Registers the two session-contract
    checks in dq_check. Data-only, exactly like migrations 5 and 10 -- no CREATE, no ALTER, no
    row-count invariant beyond apply_additive's own drift check over versions 1-12.

    LANDING ORDER: migration 10's hazard, restated because it bites the same way. A dq_check row
    naming a function CHECKERS does not carry makes run_all() raise every 60 seconds, so the
    dq_runner.py additions must land with or before this file, never after."""
    return apply_additive(
        conn, MIGRATION13_VERSION, MIGRATION13_NAME,
        ddl.extract_migration13_sql, ddl.migration13_sha256, "29.4",
    )


def apply_migration14(conn):
    """ARCHITECTURE.md section 30.4 (ATLASSN-88). Registers the two turn-final/SendMessage byte
    delta checks in dq_check. Data-only, exactly like migrations 5, 10, and 13 -- no CREATE, no
    ALTER, no row-count invariant beyond apply_additive's own drift check over versions 1-13.

    LANDING ORDER: migration 10's hazard, restated because it bites the same way every time. A
    dq_check row naming a function CHECKERS does not carry makes run_all() raise every 60
    seconds, so the dq_runner.py additions must land with or before this file, never after."""
    return apply_additive(
        conn, MIGRATION14_VERSION, MIGRATION14_NAME,
        ddl.extract_migration14_sql, ddl.migration14_sha256, "30.4",
    )


def is_migrated15(conn):
    return is_migrated_version(conn, MIGRATION15_VERSION)


def apply_migration15(conn):
    """ARCHITECTURE.md section 31.4 (ATLASSN-95). bash_command_shape_v2, a parallel table (never
    an ALTER of migration 8's bash_command_shape -- see section 31.1) carrying Feature A
    (has_cmd_pos_var_indirection) and Feature B (has_quote_splice) alongside the same columns
    bash_command_shape already has. Additive CREATE TABLE only, no view, no ALTER on any table any
    prior migration defined, no row-count invariant to check beyond apply_additive's own drift
    check over versions 1-14."""
    return apply_additive(
        conn, MIGRATION15_VERSION, MIGRATION15_NAME,
        ddl.extract_migration15_sql, ddl.migration15_sha256, "31.4",
    )


def is_migrated16(conn):
    return is_migrated_version(conn, MIGRATION16_VERSION)


def apply_migration16(conn):
    """ARCHITECTURE.md section 32.5 (ATLASSN-96). v_gaming_evasion_by_session, a per-session
    gaming/evasion rollup joining v_hook_verdict, bash_command_shape_v2, and session_prior.
    Additive CREATE VIEW only, no ALTER on any table any prior migration defined.

    LANDING ORDER: this view's own SQL references session_prior (migration 12) and
    bash_command_shape_v2 (migration 15) by name -- SQLite views are not compiled at CREATE VIEW
    time (their SELECT text is resolved at each query), so this would not fail loudly even if
    those tables/views did not exist yet at CREATE time, only on first real query. connect()
    still calls this after both, matching the order those objects actually need to be usable."""
    return apply_additive(
        conn, MIGRATION16_VERSION, MIGRATION16_NAME,
        ddl.extract_migration16_sql, ddl.migration16_sha256, "32.5",
    )


def is_migrated17(conn):
    return is_migrated_version(conn, MIGRATION17_VERSION)


def apply_migration17(conn):
    """ARCHITECTURE.md section 33.5 (ATLASSN-102). Re-points v_source_freshness to append two
    corpus-aggregate rows for session_transcript and subagent_transcript, and registers
    session_watermark_le_filesize and subagent_watermark_le_filesize at contract severity.

    Adds no table and no new view name, so test_schema's EXPECTED_TABLES/EXPECTED_VIEWS are
    unchanged -- this is the same shape as migration 7, which only DROP+CREATEs a view an
    earlier migration already added.

    Re-runnable, which apply_additive's documented crash-gap behaviour depends on: DROP VIEW IF
    EXISTS before the CREATE, and INSERT OR IGNORE on the two seed rows."""
    return apply_additive(
        conn, MIGRATION17_VERSION, MIGRATION17_NAME,
        ddl.extract_migration17_sql, ddl.migration17_sha256, "33.5",
    )


def is_migrated18(conn):
    return is_migrated_version(conn, MIGRATION18_VERSION)


def apply_migration18(conn):
    """ARCHITECTURE.md section 35.1 (ATLASSN-144). Two partial unique indexes deduplicating
    session_tool_call and session_assistant_text on (transcript_id, record_uuid, block_index)
    WHERE record_uuid IS NOT NULL. Re-runnable: CREATE UNIQUE INDEX IF NOT EXISTS.

    Deployment note this function cannot itself enforce: apply only once the ON CONFLICT-based
    insert path is live in session_pull.py, per section 35's sequencing finding -- applying the
    index first against the old plain-INSERT code is harmless (no conflict target references it
    yet) but buys nothing."""
    return apply_additive(
        conn, MIGRATION18_VERSION, MIGRATION18_NAME,
        ddl.extract_migration18_sql, ddl.migration18_sha256, "35.1",
    )


def is_migrated19(conn):
    return is_migrated_version(conn, MIGRATION19_VERSION)


def apply_migration19(conn):
    """ARCHITECTURE.md section 36.1 (ATLASSN-143). Seeds the
    session_tool_call_evidence_attrs_present dq_check row at contract severity. Safe to apply
    now because the checker already landed in dq_runner.py's CHECKERS (commit dbed288) --
    confirm that before applying against a different checkout, per migrations 13/17's own
    UnregisteredCheckError hazard. Re-runnable: INSERT OR IGNORE."""
    return apply_additive(
        conn, MIGRATION19_VERSION, MIGRATION19_NAME,
        ddl.extract_migration19_sql, ddl.migration19_sha256, "36.1",
    )


MIGRATION20_VERSION = 20
MIGRATION20_NAME = "atlas-v20-integration-interface-registry"


def is_migrated20(conn):
    return is_migrated_version(conn, MIGRATION20_VERSION)


def apply_migration20(conn):
    """ARCHITECTURE.md section 37.3 (ATLASSN-153). New table integration_interface and the view
    v_integration_progress over it -- the ATLAS-held cross-repo integration-test progress
    registry. Depends on no prior migration's tables. Re-runnable: CREATE TABLE/VIEW IF NOT
    EXISTS."""
    return apply_additive(
        conn, MIGRATION20_VERSION, MIGRATION20_NAME,
        ddl.extract_migration20_sql, ddl.migration20_sha256, "37.3",
    )


MIGRATION21_VERSION = 21
MIGRATION21_NAME = "atlas-v21-registry-assertion"


def is_migrated21(conn):
    return is_migrated_version(conn, MIGRATION21_VERSION)


def apply_migration21(conn):
    """ARCHITECTURE.md section 38.2 (ATLASSN-154). New table registry_assertion and the view
    v_registry_assertion over it -- ATLAS's tenth ingestion source, mirroring the
    enforcement-plane verification registry's own `assertion` table. Depends on no prior
    migration's tables. Re-runnable: CREATE TABLE/VIEW IF NOT EXISTS. Reviewed and ledger-bound
    2026-09-14 (clint-eastwood)."""
    return apply_additive(
        conn, MIGRATION21_VERSION, MIGRATION21_NAME,
        ddl.extract_migration21_sql, ddl.migration21_sha256, "38.2",
    )


MIGRATION22_VERSION = 22
MIGRATION22_NAME = "atlas-v22-never-proven-live-recent"


def is_migrated22(conn):
    return is_migrated_version(conn, MIGRATION22_VERSION)


def apply_migration22(conn):
    """ARCHITECTURE.md section 39.2 (ATLASSN-181) -- PLACEHOLDER section number, not yet real,
    and now known STALE (ATLASSN-98 finding: section 39 is really v_git_commit_detail as of
    2026-09-20); see ddl._MIGRATION22_HEADING's own comment. New view
    v_registered_never_proven_recent, over the existing v_gate_proven_live/v_handler_freshness
    views -- no new table, no dependency on any prior migration's tables beyond those two views
    already existing. Re-runnable: CREATE VIEW IF NOT EXISTS. NOT WIRED into connect()'s own
    migration sequence below -- this call is commented out deliberately until the real
    ARCHITECTURE.md entry lands (apply_additive would otherwise raise DdlExtractionError on every
    connect() until then, breaking every caller). Whoever lands the matching ARCHITECTURE.md
    section should uncomment the call in connect() at the same time.

    ATLASSN-188: untouched. Migration 22 stays on the ARCHITECTURE.md-extraction path -- it is
    ATLASSN-181's own ticket, not moved here unilaterally (see ddl.py's matching note on
    extract_migration22_sql). The local-file mechanism migrations 23/24 now use is available to
    it whenever its own owner adopts it."""
    return apply_additive(
        conn, MIGRATION22_VERSION, MIGRATION22_NAME,
        ddl.extract_migration22_sql, ddl.migration22_sha256, "39.2",
    )


MIGRATION23_VERSION = 23
MIGRATION23_NAME = "atlas-v23-session-block-sequence"


def is_migrated23(conn):
    return is_migrated_version(conn, MIGRATION23_VERSION)


def apply_migration23(conn):
    """ATLASSN-104/REQ-64, session_block_sequence. WIRED into connect() below as of ATLASSN-188
    (2026-09-20) -- its DDL now lives at atlas/warehouse/migrations/0023_session_block_sequence.sql
    (ddl.extract_migration23_sql), not an ARCHITECTURE.md heading, so it no longer needs an
    ARCHITECTURE.md edit to land. Depends on session_transcript (migration 4) and
    subagent_pull_run (migration 2) only as REFERENCES targets, both of which already exist.
    Re-runnable: CREATE TABLE/INDEX IF NOT EXISTS.

    LANDING ORDER: must land no later than session_pull.py/transcript_parse.py/subagent_pull.py
    (ATLASSN-104's own ingest-side changes, staged separately) -- those insert into
    session_block_sequence unconditionally once live, and would raise sqlite3.OperationalError on
    the very next pull if this table does not exist yet. This migration alone, with the ingest
    changes not yet landed, is inert and safe.

    ATLASSN-191/192: this is the migration Iris Chen's two real probes both targeted directly
    (a broken statement appended to this migration's own local SQL file, and two threads racing
    to apply it concurrently) -- both fixes landed in apply_additive() above, shared by every
    additive migration, not specific to this one."""
    return apply_additive(
        conn, MIGRATION23_VERSION, MIGRATION23_NAME,
        ddl.extract_migration23_sql, ddl.migration23_sha256,
        "atlas/warehouse/migrations/0023_session_block_sequence.sql",
    )


MIGRATION24_VERSION = 24
MIGRATION24_NAME = "atlas-v24-git-commit-detail"


def is_migrated24(conn):
    return is_migrated_version(conn, MIGRATION24_VERSION)


def apply_migration24(conn):
    """ATLASSN-98/REQ-77, v_git_commit_detail + v_source_freshness re-point. WIRED into connect()
    below as of ATLASSN-188 (2026-09-20) -- its DDL now lives at
    atlas/warehouse/migrations/0024_git_commit_detail.sql (ddl.extract_migration24_sql), not an
    ARCHITECTURE.md heading. No new table. Depends on git_commit/git_commit_file/ingest_run
    (migration 1) only as REFERENCES/join targets, all of which already exist. Re-runnable:
    CREATE VIEW IF NOT EXISTS for v_git_commit_detail; DROP VIEW IF EXISTS before the CREATE for
    v_source_freshness, same as migration 17.

    LANDING ORDER: must land no later than atlas/query/facade.py's ALLOWED_VIEWS addition
    (ATLASSN-98's own proposal) actually needing v_git_commit_detail to exist -- landing the
    facade change first is still safe (fetch('v_git_commit_detail') raises a real, honest
    sqlite3.OperationalError, 'no such view', never a silent pass-through), just not useful until
    this migration has also applied."""
    return apply_additive(
        conn, MIGRATION24_VERSION, MIGRATION24_NAME,
        ddl.extract_migration24_sql, ddl.migration24_sha256,
        "atlas/warehouse/migrations/0024_git_commit_detail.sql",
    )


MIGRATION25_VERSION = 25
MIGRATION25_NAME = "atlas-v25-dq-check-advisory-guard"


def is_migrated25(conn):
    return is_migrated_version(conn, MIGRATION25_VERSION)


def apply_migration25(conn):
    """ATLASSN-189 (Iris Chen's real, executed probe). Two triggers on dq_check
    (trg_dq_check_no_contract_on_advisory_insert/_update) preventing a severity='contract' row
    from ever targeting session_assistant_text or session_tool_call (advisory-only per
    ARCHITECTURE.md section 8 addendum A4) -- closes a real, reproduced gap: nothing at the
    schema level stopped this before, only a Python-side static-map test
    (test_view_dependency_drift.py), which this migration does not replace, only backs up. Local
    file (atlas/warehouse/migrations/0025_dq_check_advisory_guard.sql), same ATLASSN-188
    mechanism -- no ARCHITECTURE.md edit needed, since this guards an EXISTING table's write path
    rather than adding a new table/view with its own document section. Depends on dq_check
    (migration 1) already existing, no other prior migration's tables. Re-runnable: apply_
    additive()'s own idempotency check (is_migrated_version) prevents a second CREATE TRIGGER
    attempt from ever running against an already-migrated database -- the same protection every
    other additive migration here relies on."""
    return apply_additive(
        conn, MIGRATION25_VERSION, MIGRATION25_NAME,
        ddl.extract_migration25_sql, ddl.migration25_sha256,
        "atlas/warehouse/migrations/0025_dq_check_advisory_guard.sql",
    )


MIGRATION26_VERSION = 26
MIGRATION26_NAME = "atlas-v26-session-credential-ack"


def is_migrated26(conn):
    return is_migrated_version(conn, MIGRATION26_VERSION)


def apply_migration26(conn):
    """ATLASSN-197 (Nadia Osei's real STRIDE/validate finding, Build 8). session_credential_ack --
    the session-side analog of subagent_credential_ack (migration 1), needed so the real
    credential scan this ticket adds to session_pull.py (currently hardcoded credential_hits = 0,
    with no scan of any kind) has a triage path from its very first run, rather than becoming a
    gate that alarms on the first placeholder credential string and gets disabled, per section 8's
    own stated lesson. Local file (atlas/warehouse/migrations/0026_session_credential_ack.sql),
    same ATLASSN-188 mechanism -- no ARCHITECTURE.md edit needed, since this adds one new table
    with no document section of its own. Depends on session_tool_call (migration 4) only as a
    REFERENCES target, which already exists. Re-runnable: apply_additive()'s own idempotency
    check."""
    return apply_additive(
        conn, MIGRATION26_VERSION, MIGRATION26_NAME,
        ddl.extract_migration26_sql, ddl.migration26_sha256,
        "atlas/warehouse/migrations/0026_session_credential_ack.sql",
    )


def apply_migration3(conn):
    """ARCHITECTURE.md section 19.4, the deny-join (ATLASSN-32). Verified the same way, against a
    copy that already carried migration 2 and 28,965 real subagent calls: both prior hashes and
    every row count survived the ALTERs unchanged."""
    return apply_additive(
        conn, MIGRATION3_VERSION, MIGRATION3_NAME,
        ddl.extract_migration3_sql, ddl.migration3_sha256, "19.4",
    )


def set_ingest_run_status(conn, run_id, status, finished=True):
    """Write a run's status, and by default stamp finished_at with it. The other half of
    new_ingest_run, and it lives here for the same reason: this module owns ingest_run's
    lifecycle, so a caller cannot invent a fourth status or forget the timestamp.

    Added for the run-75 trap (ATLASSN-82). Measured against the production warehouse rather
    than asserted: ingest_run holds 94 rows, all 94 status='ok', 92 with finished_at NULL -- so
    `finished_at IS NULL AND status='ok'` is not merely the normal shape on that table, it is
    the ONLY shape the batch plane has ever produced. The two rows that do carry a finished_at
    are run_id 66 and 67, both 2026-09-06, and nothing in atlas/ writes that column today.

    THE INVARIANT THIS FUNCTION EXISTS TO CREATE, stated because the whole design turns on it:

        finished_at IS NOT NULL  <=>  the run reached a terminal state.

    The live plane already holds it. subagent_pull.py closes a failed run with finished_at SET
    and says why in its own comment -- "leaving finished_at NULL would make a hard failure"
    invisible to v_subagent_live_status -- and subagent_pull_run bears it out: 39,285 rows, 5
    NULL, all of them genuinely in flight. Two patterns for the same problem live in this
    codebase and the correct one was already written down.

    WHY `finished` EXISTS AND IS NOT A SECOND FUNCTION. run() promotes the run to 'ok' at the
    boundary between its two planes, because five dq_runner checks select their comparison
    points WHERE status='ok' and a run left 'running' through run_slow is hidden from its own
    checks. That promotion must write the STATUS without claiming the run has FINISHED. A
    separate set-status-only function would hand every future caller a way to write a terminal
    status and forget the timestamp, which is the exact failure this module centralises the
    write to prevent. So there is still one writer, the safe behaviour is the default, and the
    single call site that must not stamp passes finished=False with its reason beside it.

    Renamed from finish_ingest_run for the obvious reason: a function called finish that is
    sometimes told not to finish the run reads as a lie at the call site.
    """
    import datetime

    if status not in ("ok", "failed", "running"):
        raise ValueError(f"a run is 'running', 'ok' or 'failed', not {status!r}")
    if finished and status == "running":
        raise ValueError("a run cannot finish as 'running' -- pass finished=False to promote "
                         "the status of a run that is still going")
    if not finished:
        conn.execute("UPDATE ingest_run SET status = ? WHERE run_id = ?", (status, run_id))
        conn.commit()
        return status
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute("UPDATE ingest_run SET status = ?, finished_at = ? WHERE run_id = ?",
                 (status, now, run_id))
    conn.commit()
    return status


def new_ingest_run(conn, status="running", plane=None):
    """Convenience for callers (dq_runner, tests) that need a real ingest_run row to attach
    dq_check_run rows to -- dq_check_run.run_id references ingest_run(run_id), not a bare
    counter, per section 16's DDL.

    plane (FORE-276, column added by migration 11) records which ingest plane produced the run.
    It defaults to None, which writes SQL NULL, which is what every run before migration 11
    already carries and which dq_runner.SLOW_PLANE_RUNS reads as the slow plane -- so the default
    is the pre-split behaviour exactly, and no caller that does not care about planes changes
    meaning by upgrading past this.

    Only the fast plane needs to pass a value. It must pass 'fast', because that is the one value
    SLOW_PLANE_RUNS excludes; a fast tick left unstamped is precisely the defect ATLASSN-72
    existed to prevent, and it would be invisible rather than loud -- the run-over-run checks
    would keep passing while comparing across fifteen seconds instead of across a real batch.

    Deliberately NOT validated against a vocabulary here. ATLASSN-75 tracks registering a real
    dq_check for that, and the reason it is a follow-on rather than done inline is mechanical:
    registering a check needs DDL, the DDL is ARCHITECTURE.md section 27.6, and editing that
    section changes the hash the architecture review is currently bound to."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO ingest_run (started_at, status, atlas_version, host_id, plane) "
        "VALUES (?, ?, ?, ?, ?)",
        (now, status, ATLAS_VERSION, _host_id(), plane),
    )
    conn.commit()
    return cur.lastrowid


def connect(db_path):
    """Opens (creating if absent) a warehouse database at db_path, applies WAL mode and foreign
    keys per section 16's DDL header, and ensures the migration is applied. Returns the open
    connection."""
    conn = sqlite3.connect(db_path)
    # ATLASSN-192: without this, a concurrent connect() racing on the SAME fresh db_path can hit
    # SQLite's default busy_timeout of 0 -- an immediate "database is locked" the instant the
    # loser's own in-script BEGIN tries to acquire a write lock the winner already holds, which
    # is EARLIER in the sequence than the loser's own retry-check below can help with (that check
    # only re-examines is_migrated_version() AFTER the exception, and the winner may not have
    # committed yet at that exact instant). Confirmed empirically: the retry-check alone left a
    # real gap -- setting a real busy_timeout here makes SQLite itself wait for the winner's
    # commit before the loser's statement proceeds, converting the race into the "table already
    # exists"-shaped case the retry-check already closes. See apply_additive()'s own docstring.
    conn.execute("PRAGMA busy_timeout = 5000")
    _execute_with_busy_retry(conn, "PRAGMA journal_mode = WAL")
    _execute_with_busy_retry(conn, "PRAGMA foreign_keys = ON")
    apply(conn)
    apply_migration2(conn)
    apply_migration3(conn)
    apply_migration4(conn)
    apply_migration5(conn)
    apply_migration6(conn)
    apply_migration7(conn)
    apply_migration8(conn)
    apply_migration9(conn)
    apply_migration10(conn)
    apply_migration11(conn)
    apply_migration12(conn)
    apply_migration13(conn)
    apply_migration14(conn)
    apply_migration15(conn)
    apply_migration16(conn)
    apply_migration17(conn)
    # apply_migration18 was missing from this chain before this line -- a pre-existing gap, not
    # introduced here: the function existed (section 35, ATLASSN-144) but a fresh warehouse built
    # via connect() alone never got the dedup indexes. Safe to add now that the ON CONFLICT-era
    # insert path has been replaced with session_pull._is_replay(), which is unconditionally live
    # in this checkout -- migration 18's own sequencing requirement (code before index) no longer
    # depends on when connect() happens to run.
    apply_migration18(conn)
    apply_migration19(conn)
    apply_migration20(conn)
    apply_migration21(conn)
    # ATLASSN-181: apply_migration22 exists above but is NOT called here yet -- its DDL
    # extraction depends on an ARCHITECTURE.md section (currently a stale "39.2" placeholder,
    # now known to need a different number -- see apply_migration22's own docstring) that does
    # not exist yet. Calling it here today would raise DdlExtractionError on every connect() in
    # this codebase. Uncomment the line below in the SAME change that lands the real
    # ARCHITECTURE.md migration-22 entry, not before. UNTOUCHED by ATLASSN-188 -- not this
    # ticket's own ticket/placeholder to unblock.
    # stub-ok: intentionally commented out until its real ARCHITECTURE.md section is real
    # apply_migration22(conn)
    # ATLASSN-188 (2026-09-20): apply_migration23/24 are now WIRED for real. Both source their
    # DDL from local files under atlas/warehouse/migrations/ (see ddl.py's top docstring for the
    # architecture-reversal decision, PDP.md section 11.6, that made this possible without an
    # ARCHITECTURE.md edit) -- migration 22 above stays commented out and untouched, since it is
    # a different ticket's own placeholder, still genuinely pending a real ARCHITECTURE.md
    # section number.
    apply_migration23(conn)
    apply_migration24(conn)
    # ATLASSN-189 (2026-09-20): migration 25 wires two triggers on dq_check -- no dependency on
    # migrations 23/24, safe to apply after them in this chain purely by convention (newest last),
    # not because of any real ordering requirement.
    apply_migration25(conn)
    # ATLASSN-197 (2026-09-20): migration 26 adds session_credential_ack -- no dependency on
    # migrations 23/24/25, safe to apply after them purely by convention (newest last).
    apply_migration26(conn)
    return conn
