"""ATLASSN-129 / GOALS.json C5 -- status computed at read, stored nowhere. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_status_computation -v

Criteria D1-D4 and D6 frozen on the ticket (criteria_hash sha256:592fa809).
"""

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from atlas.registry import status
from atlas.registry.tests import helpers

NOW = helpers.BASE_NOW


class StatusTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "registry.db"
        self.connection = helpers.make_store(self.path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.connection.close)

    def read(self, edge_id, now=NOW):
        return status.read_edge(self.connection, edge_id, now)


class TestBoundarySemantics(StatusTestCase):
    """D2 -- the interval is closed at BOTH ends."""

    def test_delta_zero_is_active(self):
        helpers.insert(self.connection, "e:zero", helpers.at_delta(0))
        result = self.read("e:zero")
        self.assertEqual(result.status, status.ACTIVE)
        self.assertEqual(result.decision, status.ALLOW)

    def test_delta_exactly_the_lease_is_still_active(self):
        # C5's boundary equality: section 34.0 writes `<=`, so the last second counts.
        helpers.insert(self.connection, "e:edge", helpers.at_delta(helpers.LEASE_S))
        self.assertEqual(self.read("e:edge").status, status.ACTIVE)

    def test_one_second_past_the_lease_is_expired(self):
        helpers.insert(self.connection, "e:past", helpers.at_delta(helpers.LEASE_S + 1))
        result = self.read("e:past")
        self.assertEqual(result.status, status.EXPIRED)
        self.assertEqual(result.decision, status.DENY)
        # No sub-reason: an ordinary expiry is not an anomaly, and conflating the two would make
        # the clock-anomaly signal meaningless.
        self.assertIsNone(result.sub_reason)

    def test_mid_window_is_active(self):
        helpers.insert(self.connection, "e:mid", helpers.at_delta(helpers.LEASE_S // 2))
        self.assertEqual(self.read("e:mid").status, status.ACTIVE)

    def test_the_boundary_pair_differs_by_exactly_one_second(self):
        # Control on the two probes above: they must straddle the boundary, not merely both
        # happen to land on their expected side for unrelated reasons.
        helpers.insert(self.connection, "e:on", helpers.at_delta(helpers.LEASE_S))
        helpers.insert(self.connection, "e:off", helpers.at_delta(helpers.LEASE_S + 1))
        self.assertEqual(self.read("e:on").status, status.ACTIVE)
        self.assertEqual(self.read("e:off").status, status.EXPIRED)


class TestClockAnomaly(StatusTestCase):
    """D3 -- a verified_at in the future is never active."""

    def test_negative_delta_denies_with_clock_anomaly(self):
        helpers.insert(self.connection, "e:future", helpers.at_delta(-60))
        result = self.read("e:future")
        self.assertEqual(result.sub_reason, status.SUB_CLOCK_ANOMALY)
        self.assertEqual(result.decision, status.DENY)
        # C5 says "never active", and asserting the deny alone would pass against an
        # implementation that quietly clamped the delta to zero and called it active.
        self.assertNotEqual(result.status, status.ACTIVE)

    def test_a_future_assertion_inside_the_lease_window_is_still_not_active(self):
        # The dangerous case: only slightly ahead, so a clamp-to-zero bug would look right.
        helpers.insert(self.connection, "e:slightly", helpers.at_delta(-1))
        result = self.read("e:slightly")
        self.assertNotEqual(result.status, status.ACTIVE)
        self.assertEqual(result.sub_reason, status.SUB_CLOCK_ANOMALY)


class TestRevocation(StatusTestCase):
    """D4 -- revoked is terminal, and a later write on the same edge supersedes it."""

    def test_revoked_denies(self):
        helpers.insert(self.connection, "e:rev", helpers.at_delta(1), state="revoked")
        result = self.read("e:rev")
        self.assertEqual(result.status, status.REVOKED)
        self.assertEqual(result.decision, status.DENY)

    def test_revoked_denies_even_well_inside_its_lease(self):
        helpers.insert(self.connection, "e:rev2", helpers.at_delta(0), state="revoked")
        self.assertEqual(self.read("e:rev2").status, status.REVOKED)

    def test_a_later_write_on_the_same_edge_resumes_service(self):
        revoked_uid = helpers.insert(self.connection, "e:resume", helpers.at_delta(300),
                                     state="revoked")
        self.assertEqual(self.read("e:resume").status, status.REVOKED)

        fresh_uid = helpers.insert(self.connection, "e:resume", helpers.at_delta(10))
        result = self.read("e:resume")
        self.assertEqual(result.status, status.ACTIVE)
        self.assertEqual(result.assertion_uid, fresh_uid)

        # The revoked row is retained as history, not deleted or rewritten.
        row = self.connection.execute(
            "SELECT state, revoked_at FROM assertion WHERE assertion_uid = ?",
            (revoked_uid,)).fetchone()
        self.assertEqual(row[0], "revoked")
        self.assertIsNotNone(row[1])

    def test_revocation_of_the_newer_row_denies_again(self):
        helpers.insert(self.connection, "e:again", helpers.at_delta(300), state="revoked")
        helpers.insert(self.connection, "e:again", helpers.at_delta(10))
        self.assertEqual(self.read("e:again").status, status.ACTIVE)
        helpers.insert(self.connection, "e:again", helpers.at_delta(5), state="revoked")
        self.assertEqual(self.read("e:again").status, status.REVOKED)

    def test_selection_is_deterministic_when_two_rows_share_a_timestamp(self):
        # A same-second double write must not leave the answer to SQLite's row order. The id
        # tiebreak makes the later INSERT win, and this asserts it rather than assuming it.
        moment = helpers.at_delta(10)
        helpers.insert(self.connection, "e:tie", moment, assertion_uid="a-tie-first")
        helpers.insert(self.connection, "e:tie", moment, assertion_uid="a-tie-second")
        for _ in range(5):
            self.assertEqual(self.read("e:tie").assertion_uid, "a-tie-second")


class TestNeverRegistered(StatusTestCase):
    """D6 -- no row is its own outcome."""

    def test_unknown_edge_is_never_registered(self):
        result = self.read("e:unknown")
        self.assertEqual(result.status, status.NEVER_REGISTERED)
        self.assertEqual(result.decision, status.DENY)
        self.assertIsNone(result.assertion_uid)

    def test_never_registered_is_distinct_from_every_other_outcome(self):
        self.assertNotIn(status.NEVER_REGISTERED,
                         (status.ACTIVE, status.EXPIRED, status.REVOKED,
                          status.REGISTRY_UNAVAILABLE))

    def test_registry_unavailable_is_not_produced_by_this_module(self):
        # The store-level family belongs to C6's matrix in ATLASSN-130. If this module ever
        # started emitting it, the two would be conflated and a caller could not tell "the store
        # is broken" from "this edge has no assertion".
        helpers.insert(self.connection, "e:ok", helpers.at_delta(1))
        for edge in ("e:ok", "e:missing"):
            self.assertIn(self.read(edge).status, status.COMPUTED_STATUSES)


class TestNothingIsStored(StatusTestCase):
    """D1 -- a read pass writes nothing."""

    def populate(self):
        helpers.insert(self.connection, "e:live", helpers.at_delta(1))
        helpers.insert(self.connection, "e:old", helpers.at_delta(helpers.LEASE_S + 99))
        helpers.insert(self.connection, "e:gone", helpers.at_delta(5), state="revoked")

    def test_the_store_is_byte_identical_after_a_full_read_pass(self):
        self.populate()
        self.connection.execute("PRAGMA wal_checkpoint(FULL)")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()

        results = status.read_edges(self.connection,
                                    ["e:live", "e:old", "e:gone", "e:absent"], NOW)
        self.assertEqual(len(results), 4)

        self.connection.execute("PRAGMA wal_checkpoint(FULL)")
        after = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(before, after, "a read pass modified the store")

    def test_the_read_path_works_against_a_read_only_connection(self):
        # Strongest form of "computes, never stores": if any code on this path attempted a
        # write, SQLite itself would refuse it here.
        self.populate()
        self.connection.execute("PRAGMA wal_checkpoint(FULL)")
        readonly = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.addCleanup(readonly.close)
        result = status.read_edge(readonly, "e:live", NOW)
        self.assertEqual(result.status, status.ACTIVE)

    def test_status_is_not_a_column(self):
        # A stored status would be a second source of truth that goes stale between a lease
        # expiring and anything noticing.
        columns = [row[1] for row in
                   self.connection.execute("PRAGMA table_info(assertion)").fetchall()]
        self.assertNotIn("status", columns)

    def test_the_same_row_reads_differently_as_time_passes(self):
        # The behavioural proof that status is derived, not stored: one unchanged row, three
        # instants, three answers.
        helpers.insert(self.connection, "e:aging", helpers.at_delta(0))
        self.assertEqual(self.read("e:aging", NOW).status, status.ACTIVE)
        self.assertEqual(
            self.read("e:aging", NOW + timedelta(seconds=helpers.LEASE_S)).status, status.ACTIVE)
        self.assertEqual(
            self.read("e:aging", NOW + timedelta(seconds=helpers.LEASE_S + 1)).status,
            status.EXPIRED)


class TestMalformedStoredValues(StatusTestCase):
    """Per-assertion malformed denial. C6/ATLASSN-130 owns the full matrix; what matters here is
    that a malformed row denies its own edge without taking the pass down."""

    def test_unparseable_verified_at_denies_that_assertion(self):
        helpers.insert(self.connection, "e:bad", helpers.at_delta(1))
        self.connection.execute(
            "UPDATE assertion SET verified_at = 'not-a-timestamp' WHERE edge_id = 'e:bad'")
        result = self.read("e:bad")
        self.assertEqual(result.decision, status.DENY)
        self.assertEqual(result.sub_reason, status.SUB_MALFORMED)

    def test_a_malformed_row_does_not_raise_and_does_not_break_the_pass(self):
        helpers.insert(self.connection, "e:bad", helpers.at_delta(1))
        helpers.insert(self.connection, "e:good", helpers.at_delta(1))
        self.connection.execute(
            "UPDATE assertion SET verified_at = '' WHERE edge_id = 'e:bad'")
        results = status.read_edges(self.connection, ["e:bad", "e:good"], NOW)
        self.assertEqual(results["e:bad"].decision, status.DENY)
        self.assertEqual(results["e:good"].status, status.ACTIVE)


if __name__ == "__main__":
    unittest.main()
