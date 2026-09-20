"""ATLASSN-187. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest hooks.registry_gate_client.test_registry_gate_client -v

FIX 2026-09-20 (this session's own real-execution self-test): ASSERTION_TABLE_SQL below was
missing the real schema's table-level CHECK (the three revocation fields must be all-NULL or
all-set together, atlas/registry/ddl.py's own comment: "A half-revoked row would read as revoked
... while carrying no evidence reference to audit"). Found by diffing this constant against the
real file directly, not by a failing test -- no fixture here originally set any revocation field
to a non-NULL value, so the gap was silent rather than test-breaking. Copied verbatim now,
including the constraint.

CORRECTION 2026-09-20 (Bob's finding, post-land): the round that added the constraint above
claimed to also add two new tests proving it -- true of a scratch re-verification copy, never
true of THIS file, which landed with the constraint but not the two tests. Added for real now:
test_revoked_row_with_all_revocation_fields_null_is_still_insertable (the control -- every
existing fixture's usage pattern still inserts fine) and
test_a_genuinely_half_revoked_row_is_rejected_by_the_new_check (the constraint actually firing
against a real sqlite3.IntegrityError, not assumed from the SQL text alone).

Every fixture below builds a real sqlite file with the `assertion` table copied verbatim from
atlas-sonnet's atlas/registry/ddl.py (read directly, not paraphrased), so a schema mismatch
between this test's fixture and the real store would be a real, catchable defect rather than an
assumption baked into both sides. Re-run and confirmed GREEN this session (24/24) under the
sandboxed self-test Bash grant.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import registry_gate_client as rgc

# Copied verbatim from atlas/registry/ddl.py's ASSERTION_TABLE (re-read directly this session,
# diffed line for line against the real file -- see this file's own top docstring for the one
# gap that diff found and fixed). Still a hand-copy, disclosed as such, not extracted
# mechanically the way atlas's own warehouse DDL is (that anti-drift machinery is
# atlas-sonnet-internal and not reachable cross-repo, per this ticket's own read-target
# decision). Whoever adds the registry_status_logic reach-edge pin should diff this constant
# against the real file too, not just the two source modules.
ASSERTION_TABLE_SQL = """
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


def make_store(path, schema_version=1):
    connection = sqlite3.connect(str(path))
    connection.execute(ASSERTION_TABLE_SQL)
    connection.execute(f"PRAGMA user_version = {schema_version}")
    connection.commit()
    connection.close()


def insert_assertion(path, edge_id, verified_at, lease_s=3600, state="live",
                     assertion_uid=None, component="c1", class_="structural"):
    assertion_uid = assertion_uid or f"uid-{edge_id}-{verified_at}"
    connection = sqlite3.connect(str(path))
    connection.execute(
        "INSERT INTO assertion (assertion_uid, edge_id, component, class, lease_s, verified_at, "
        "verifier_session_id, dispatch_record_id, evidence_tool_use_id, evidence_class, state) "
        "VALUES (?, ?, ?, ?, ?, ?, 'sess-verifier', 'd-1', 'toolu-1', 'observed-probe', ?)",
        (assertion_uid, edge_id, component, class_, lease_s, verified_at, state),
    )
    connection.commit()
    connection.close()
    return assertion_uid


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ComputeStatusTests(unittest.TestCase):
    """The state machine itself, independent of any real file I/O."""

    def test_no_row_is_never_registered(self):
        result = rgc.compute_status(None, datetime.now(timezone.utc), edge_id="e1")
        self.assertEqual(result.status, rgc.NEVER_REGISTERED)
        self.assertEqual(result.decision, rgc.DENY)

    def test_revoked_denies_regardless_of_time(self):
        now = datetime.now(timezone.utc)
        row = ("uid-1", "revoked", iso(now), 3600)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.REVOKED)
        self.assertEqual(result.decision, rgc.DENY)

    def test_revoked_wins_over_a_future_verified_at_not_read_as_clock_anomaly(self):
        """Revocation is a terminal act-based fact and must be decided BEFORE any time
        arithmetic -- a revoked row whose verified_at is in the future must still read as
        revoked, not as a clock-anomaly denial (which would misreport the reason)."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=1)
        row = ("uid-1", "revoked", iso(future), 3600)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.REVOKED)
        self.assertIsNone(result.sub_reason)

    def test_active_within_lease(self):
        now = datetime.now(timezone.utc)
        verified_at = now - timedelta(seconds=100)
        row = ("uid-1", "live", iso(verified_at), 3600)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.ACTIVE)
        self.assertEqual(result.decision, rgc.ALLOW)

    def test_active_at_exact_lease_boundary_closed_interval(self):
        """delta_seconds == lease_s must still be ACTIVE -- the interval is closed at both ends
        (ARCHITECTURE.md section 34.0's own `<=`). A boundary test rather than only interior
        cases, since an off-by-one here (`<` instead of `<=`) is exactly the kind of defect an
        interior-only test suite would never catch."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        verified_at = now - timedelta(seconds=3600)
        row = ("uid-1", "live", iso(verified_at), 3600)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.ACTIVE)

    def test_expired_one_second_past_lease(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        verified_at = now - timedelta(seconds=3601)
        row = ("uid-1", "live", iso(verified_at), 3600)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.EXPIRED)
        self.assertIsNone(result.sub_reason)

    def test_clock_anomaly_future_verified_at_denies_not_activates(self):
        now = datetime.now(timezone.utc)
        future = now + timedelta(seconds=10)
        row = ("uid-1", "live", iso(future), 3600)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.EXPIRED)
        self.assertEqual(result.decision, rgc.DENY)
        self.assertEqual(result.sub_reason, rgc.SUB_CLOCK_ANOMALY)

    def test_malformed_verified_at_denies_this_edge_only(self):
        now = datetime.now(timezone.utc)
        row = ("uid-1", "live", "not-a-timestamp", 3600)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.EXPIRED)
        self.assertEqual(result.sub_reason, rgc.SUB_MALFORMED)

    def test_malformed_lease_zero_denies(self):
        now = datetime.now(timezone.utc)
        row = ("uid-1", "live", iso(now), 0)
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.EXPIRED)
        self.assertEqual(result.sub_reason, rgc.SUB_MALFORMED)

    def test_malformed_lease_non_int_denies(self):
        now = datetime.now(timezone.utc)
        row = ("uid-1", "live", iso(now), "not-a-number")
        result = rgc.compute_status(row, now, edge_id="e1")
        self.assertEqual(result.status, rgc.EXPIRED)
        self.assertEqual(result.sub_reason, rgc.SUB_MALFORMED)


class ReadManyRealStoreTests(unittest.TestCase):
    """Real sqlite files, real reads -- not mocked. C7 (per-edge independence) and D2
    (fail-closed on every store-level fault) both need a real store to mean anything."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "registry.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_never_registered_edge_on_a_real_empty_store(self):
        make_store(self.path)
        result = rgc.read_one("no-such-edge", store_path=self.path)
        self.assertEqual(result.status, rgc.NEVER_REGISTERED)

    def test_active_edge_on_a_real_store(self):
        make_store(self.path)
        now = datetime.now(timezone.utc)
        insert_assertion(self.path, "edge-1", iso(now - timedelta(seconds=10)), lease_s=3600)
        result = rgc.read_one("edge-1", now=now, store_path=self.path)
        self.assertEqual(result.status, rgc.ACTIVE)
        self.assertEqual(result.decision, rgc.ALLOW)

    def test_latest_by_verified_at_then_id_tiebreak(self):
        """SELECT_LATEST_FOR_EDGE orders by verified_at DESC, id DESC. Two assertions with the
        SAME verified_at must resolve deterministically to the later-inserted (higher id) row,
        not to whichever SQLite happens to return first."""
        make_store(self.path)
        now = datetime.now(timezone.utc)
        same_ts = iso(now - timedelta(seconds=10))
        insert_assertion(self.path, "edge-1", same_ts, lease_s=3600,
                         assertion_uid="uid-first", state="live")
        insert_assertion(self.path, "edge-1", same_ts, lease_s=3600,
                         assertion_uid="uid-second", state="revoked")
        result = rgc.read_one("edge-1", now=now, store_path=self.path)
        # The second (higher-id) row is revoked -- if the tiebreak were wrong, this would read
        # ACTIVE from the first row instead.
        self.assertEqual(result.status, rgc.REVOKED)
        self.assertEqual(result.assertion_uid, "uid-second")

    def test_one_expired_edge_does_not_block_a_healthy_sibling_c7(self):
        make_store(self.path)
        now = datetime.now(timezone.utc)
        insert_assertion(self.path, "edge-healthy", iso(now - timedelta(seconds=10)), lease_s=3600)
        insert_assertion(self.path, "edge-expired", iso(now - timedelta(seconds=999999)), lease_s=3600)
        results = rgc.read_many(["edge-healthy", "edge-expired"], now=now, store_path=self.path)
        self.assertEqual(results["edge-healthy"].status, rgc.ACTIVE)
        self.assertEqual(results["edge-expired"].status, rgc.EXPIRED)

    def test_missing_store_is_registry_unavailable_absent_not_never_registered(self):
        """D2/C6: an absent store must not be conflated with 'nobody verified this' -- an
        auditor has to be able to tell 'nobody verified this edge' from 'nobody could ask.'"""
        missing_path = Path(self.tmpdir.name) / "does-not-exist.db"
        result = rgc.read_one("edge-1", store_path=missing_path)
        self.assertEqual(result.status, rgc.REGISTRY_UNAVAILABLE)
        self.assertEqual(result.sub_reason, rgc.SUB_ABSENT)
        self.assertNotEqual(result.status, rgc.NEVER_REGISTERED)

    def test_wrong_schema_version_is_registry_unavailable(self):
        make_store(self.path, schema_version=999)
        result = rgc.read_one("edge-1", store_path=self.path)
        self.assertEqual(result.status, rgc.REGISTRY_UNAVAILABLE)
        self.assertEqual(result.sub_reason, rgc.SUB_SCHEMA_VERSION)

    def test_corrupt_store_is_registry_unavailable_not_a_raised_exception(self):
        """A file that exists but is not a real sqlite database -- the whole point of D2's
        fail-closed contract is that this must come back as a named DENY, never propagate as an
        uncaught exception a caller might mishandle by proceeding."""
        self.path.write_bytes(b"this is not a sqlite database")
        result = rgc.read_one("edge-1", store_path=self.path)
        self.assertEqual(result.status, rgc.REGISTRY_UNAVAILABLE)
        self.assertIn(result.sub_reason, (rgc.SUB_CORRUPT, rgc.SUB_READ_ERROR))

    def test_store_level_fault_denies_every_requested_edge_not_just_one(self):
        """The converse of C7: a store-level fault is NOT per-assertion -- it must deny the
        WHOLE requested pass, unlike a single malformed row."""
        missing_path = Path(self.tmpdir.name) / "does-not-exist.db"
        results = rgc.read_many(["edge-a", "edge-b", "edge-c"], store_path=missing_path)
        self.assertEqual(len(results), 3)
        for edge_id in ("edge-a", "edge-b", "edge-c"):
            self.assertEqual(results[edge_id].status, rgc.REGISTRY_UNAVAILABLE)

    def test_unenumerated_read_error_still_denies_via_the_catchall(self):
        """The injected `connect` callable raising an exception sqlite3.DatabaseError does not
        cover confirms the catch-all Exception branch itself denies, rather than propagating."""
        def broken_connect(path, busy_budget_ms):
            raise RuntimeError("simulated unenumerated failure")

        make_store(self.path)
        result = rgc.read_one("edge-1", store_path=self.path, connect=broken_connect)
        self.assertEqual(result.status, rgc.REGISTRY_UNAVAILABLE)
        self.assertEqual(result.sub_reason, rgc.SUB_READ_ERROR)

    def test_revoked_row_with_all_revocation_fields_null_is_still_insertable(self):
        """The control for the test below: this module never WRITES revoked_at/
        revoked_by_session_id/revocation_evidence_tool_use_id (it is read-only), so every
        fixture here only ever inserts a 'revoked' row with all three columns NULL. Confirms the
        added CHECK constraint (below) does not reject the one shape this whole test file
        actually uses."""
        make_store(self.path)
        insert_assertion(self.path, "edge-1", iso(datetime.now(timezone.utc)), state="revoked")
        result = rgc.read_one("edge-1", store_path=self.path)
        self.assertEqual(result.status, rgc.REVOKED)

    def test_a_genuinely_half_revoked_row_is_rejected_by_the_new_check(self):
        """The constraint ASSERTION_TABLE_SQL was missing until this session's diff against the
        real atlas/registry/ddl.py, now proven to actually fire rather than assumed correct from
        the SQL text alone: revoked_at set without its two siblings must be rejected by SQLite
        itself, matching the real schema's own D1 evidence discipline (a half-revoked row would
        read as REVOKED/DENY while carrying no evidence reference to audit)."""
        make_store(self.path)
        connection = sqlite3.connect(str(self.path))
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO assertion (assertion_uid, edge_id, component, class, lease_s, "
                "verified_at, verifier_session_id, dispatch_record_id, evidence_tool_use_id, "
                "evidence_class, state, revoked_at) VALUES ('u1', 'e1', 'c1', 'structural', "
                "3600, '2026-01-01T00:00:00Z', 's1', 'd1', 't1', 'observed-probe', 'revoked', "
                "'2026-01-01T00:00:00Z')"
            )
        connection.close()


class ClassifyTests(unittest.TestCase):
    """classify() against real sqlite exceptions, not hand-typed strings -- the fallback
    message-matching path is exercised the same way availability.py's own test presumably does,
    by triggering the real condition rather than constructing a fake exception object."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "registry.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_a_database_classifies_corrupt(self):
        self.path.write_bytes(b"garbage, not sqlite")
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            connection.execute("SELECT 1 FROM assertion")
        connection.close()
        self.assertEqual(rgc.classify(ctx.exception, self.path), rgc.SUB_CORRUPT)

    def test_cantopen_without_wal_sidecar_classifies_read_error(self):
        """SQLITE_CANTOPEN with no -wal sidecar present is an ordinary read failure, per
        availability.py's own measured heuristic -- distinct from the WAL-present case below."""
        missing = Path(self.tmpdir.name) / "does-not-exist.db"
        try:
            sqlite3.connect(f"file:{missing}?mode=ro", uri=True).execute("SELECT 1")
            self.fail("expected sqlite3.OperationalError opening a nonexistent mode=ro store")
        except sqlite3.OperationalError as exc:
            self.assertEqual(rgc.classify(exc, missing), rgc.SUB_READ_ERROR)

    def test_cantopen_with_live_wal_sidecar_classifies_recovery_needed(self):
        """The measured, non-obvious case this whole module exists to get right: a store copied
        with a live -wal into a location opened mode=ro raises SQLITE_CANTOPEN, and the presence
        of the -wal sidecar is what distinguishes this from an ordinary read-error."""
        missing = Path(self.tmpdir.name) / "does-not-exist.db"
        wal_sidecar = Path(str(missing) + "-wal")
        wal_sidecar.write_bytes(b"fake wal contents")
        try:
            sqlite3.connect(f"file:{missing}?mode=ro", uri=True).execute("SELECT 1")
            self.fail("expected sqlite3.OperationalError opening a nonexistent mode=ro store")
        except sqlite3.OperationalError as exc:
            self.assertEqual(rgc.classify(exc, missing), rgc.SUB_RECOVERY_NEEDED)


if __name__ == "__main__":
    unittest.main()
