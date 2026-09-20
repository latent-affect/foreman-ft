"""ATLASSN-129 / GOALS.json C7 -- per-assertion fail-closed granularity. From the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_granularity -v

Criterion D5, frozen on the ticket (criteria_hash sha256:592fa809).

THE DEFECT THIS PREVENTS is ATLASSN-103's, applied to the registry: one unrelated source's
failure took down every gated view. Here that would be one expired or revoked assertion
suppressing a healthy sibling behind the same client. The pair probe is ATLASSN-119's.
"""

import tempfile
import unittest
from pathlib import Path

from atlas.registry import status
from atlas.registry.tests import helpers

NOW = helpers.BASE_NOW


class GranularityTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "registry.db"
        self.connection = helpers.make_store(self.path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.connection.close)

    def pass_over(self, *edge_ids):
        return status.read_edges(self.connection, list(edge_ids), NOW)


class TestPairProbe(GranularityTestCase):
    """D5 -- ATLASSN-119's pair probe and its revoked variant."""

    def test_healthy_and_expired_in_one_pass(self):
        helpers.insert(self.connection, "e:healthy", helpers.at_delta(1))
        helpers.insert(self.connection, "e:expired", helpers.at_delta(helpers.LEASE_S + 1))

        results = self.pass_over("e:healthy", "e:expired")

        self.assertEqual(results["e:healthy"].decision, status.ALLOW)
        self.assertEqual(results["e:healthy"].status, status.ACTIVE)
        self.assertEqual(results["e:expired"].decision, status.DENY)
        self.assertEqual(results["e:expired"].status, status.EXPIRED)

    def test_healthy_and_revoked_in_one_pass(self):
        helpers.insert(self.connection, "e:healthy", helpers.at_delta(1))
        helpers.insert(self.connection, "e:revoked", helpers.at_delta(1), state="revoked")

        results = self.pass_over("e:healthy", "e:revoked")

        self.assertEqual(results["e:healthy"].decision, status.ALLOW)
        self.assertEqual(results["e:revoked"].decision, status.DENY)
        self.assertEqual(results["e:revoked"].status, status.REVOKED)

    def test_order_does_not_matter(self):
        # A failure that suppressed later siblings would pass one ordering and fail the other,
        # so both orderings are asserted rather than whichever one happened to be written first.
        helpers.insert(self.connection, "e:healthy", helpers.at_delta(1))
        helpers.insert(self.connection, "e:expired", helpers.at_delta(helpers.LEASE_S + 1))

        forward = self.pass_over("e:healthy", "e:expired")
        backward = self.pass_over("e:expired", "e:healthy")

        self.assertEqual(forward["e:healthy"].decision, status.ALLOW)
        self.assertEqual(backward["e:healthy"].decision, status.ALLOW)
        self.assertEqual(forward["e:expired"].decision, status.DENY)
        self.assertEqual(backward["e:expired"].decision, status.DENY)

    def test_one_healthy_among_every_failing_kind(self):
        helpers.insert(self.connection, "e:healthy", helpers.at_delta(1))
        helpers.insert(self.connection, "e:expired", helpers.at_delta(helpers.LEASE_S + 1))
        helpers.insert(self.connection, "e:revoked", helpers.at_delta(1), state="revoked")
        helpers.insert(self.connection, "e:future", helpers.at_delta(-30))

        results = self.pass_over("e:expired", "e:revoked", "e:future", "e:absent", "e:healthy")

        self.assertEqual(results["e:healthy"].decision, status.ALLOW)
        for edge in ("e:expired", "e:revoked", "e:future", "e:absent"):
            self.assertEqual(results[edge].decision, status.DENY, f"{edge} should deny")


class TestPassShape(GranularityTestCase):
    """Controls on the pass itself, so granularity cannot be faked by returning less."""

    def test_one_result_per_requested_edge(self):
        # Without this, an implementation that dropped failing edges entirely would look like
        # perfect granularity: every returned result would be an ALLOW.
        helpers.insert(self.connection, "e:healthy", helpers.at_delta(1))
        helpers.insert(self.connection, "e:expired", helpers.at_delta(helpers.LEASE_S + 1))

        requested = ["e:healthy", "e:expired", "e:absent"]
        results = status.read_edges(self.connection, requested, NOW)

        self.assertEqual(set(results), set(requested))
        self.assertEqual(len(results), len(requested))

    def test_every_result_names_its_own_edge(self):
        helpers.insert(self.connection, "e:one", helpers.at_delta(1))
        helpers.insert(self.connection, "e:two", helpers.at_delta(helpers.LEASE_S + 1))
        results = self.pass_over("e:one", "e:two")
        for edge_id, result in results.items():
            self.assertEqual(result.edge_id, edge_id)

    def test_an_empty_pass_returns_nothing_rather_than_failing(self):
        self.assertEqual(status.read_edges(self.connection, [], NOW), {})

    def test_a_pass_of_only_failures_still_returns_them_all(self):
        # The mirror of the granularity claim: nothing is suppressed in either direction.
        helpers.insert(self.connection, "e:a", helpers.at_delta(helpers.LEASE_S + 1))
        helpers.insert(self.connection, "e:b", helpers.at_delta(1), state="revoked")
        results = self.pass_over("e:a", "e:b", "e:c")
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.decision == status.DENY for r in results.values()))

    def test_a_single_edge_pass_matches_the_single_edge_read(self):
        # read_edges must not drift from read_edge: if they can disagree, the granularity proof
        # here says nothing about what a real caller using the single-edge path gets.
        helpers.insert(self.connection, "e:solo", helpers.at_delta(1))
        self.assertEqual(self.pass_over("e:solo")["e:solo"],
                         status.read_edge(self.connection, "e:solo", NOW))


if __name__ == "__main__":
    unittest.main()
