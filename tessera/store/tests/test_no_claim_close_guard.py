"""TESS-192. The no-claim close guard, its vocabulary, its rate limit, and the replay of
the real incident that motivated it.

The defect this closes was not a missing record. `ClosedWithNoClaim` had been emitted
correctly 200 times in the live database as of 2026-09-04 -- every one of them with an
empty payload, because no reason was ever asked for -- and nothing anywhere read one. So a
session could close 20 tickets without a single claim behind any of them and the only
trace was a row nobody queried. These tests exercise the consumer, not the record.

Criteria frozen on TESS-192 as sha256:3c28406fd3c9c3975d42a92c1c1da141050eb14552d2ea79c47
ae9b4cb750866; the test names below map onto C1-C6 of that freeze.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ...common import timestamps
from .. import schema
from ..exceptions import ClaimRequiredError, NoClaimRateLimitError
from ..store import Store

# The real burst, read from the live database on 2026-09-04 and pinned here so the test
# does not depend on that database still existing or still containing it. Verified at
# capture time: 12 ClosedWithNoClaim events, actor ticket-system-bd, span 1.252897s, and
# no claim row or ClaimRecorded event for any of the twelve, ever.
INCIDENT_ACTOR = "ticket-system-bd"
INCIDENT_TIMESTAMPS = [
    "2026-09-03T13:18:26.603574Z", "2026-09-03T13:18:26.715515Z",
    "2026-09-03T13:18:26.828337Z", "2026-09-03T13:18:26.940724Z",
    "2026-09-03T13:18:27.052738Z", "2026-09-03T13:18:27.163403Z",
    "2026-09-03T13:18:27.276789Z", "2026-09-03T13:18:27.389831Z",
    "2026-09-03T13:18:27.502260Z", "2026-09-03T13:18:27.630437Z",
    "2026-09-03T13:18:27.743015Z", "2026-09-03T13:18:27.856471Z",
]


class NoClaimCloseGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def make_ticket(self):
        return self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")

    def claimed_ticket(self):
        tid = self.make_ticket()
        self.store.record_claim(tid, "agent", "did the thing", ["a.py"],
                                "0123456789abcdef0123456789abcdef01234567")
        return tid

    def events_of_type(self, event_type):
        conn = sqlite3.connect(self.db_path)
        try:
            return [
                {"ticket_id": r[0], "actor": r[1], "payload": json.loads(r[2] or "{}")}
                for r in conn.execute(
                    "SELECT ticket_id, actor, payload FROM events WHERE event_type=?"
                    " ORDER BY id", (event_type,))
            ]
        finally:
            conn.close()

    # ---- C1: a close with no claim and no reason is refused -------------------------

    def test_close_without_claim_and_without_reason_is_refused(self):
        tid = self.make_ticket()
        with self.assertRaises(ClaimRequiredError):
            self.store.transition_status(tid, "agent", "closed")
        # The refusal must leave the ticket open. A guard that closes the ticket and then
        # complains is the advisory behaviour this replaces.
        self.assertEqual(self.store.get_ticket(tid)["status"], "open")
        self.assertEqual(self.events_of_type("ClosedWithNoClaim"), [])

    def test_close_with_a_real_claim_needs_no_reason(self):
        tid = self.claimed_ticket()
        self.store.transition_status(tid, "agent", "closed")
        self.assertEqual(self.store.get_ticket(tid)["status"], "closed")
        self.assertEqual(self.events_of_type("ClosedWithNoClaim"), [])

    def test_reason_on_a_claimed_ticket_is_refused(self):
        # Otherwise a caller could pass disposition-pass on every close reflexively and
        # the reason field would stop meaning anything.
        tid = self.claimed_ticket()
        with self.assertRaises(ClaimRequiredError):
            self.store.transition_status(tid, "agent", "closed",
                                         no_claim_reason="disposition-pass")

    def test_reason_on_a_non_close_transition_is_refused(self):
        tid = self.make_ticket()
        self.store.freeze_ticket_criteria(
            tid, "agent",
            [{"id": "c1", "statement": "s", "verification": "v", "verifiable": True}])
        with self.assertRaises(ClaimRequiredError):
            self.store.transition_status(tid, "agent", "in_progress",
                                         no_claim_reason="duplicate")

    # ---- C2: the vocabulary is closed -----------------------------------------------

    def test_every_listed_reason_is_accepted(self):
        for reason in schema.NO_CLAIM_REASONS:
            with self.subTest(reason=reason):
                tid = self.make_ticket()
                # Rate limiting applies to disposition-pass only; give each subtest its
                # own actor so this test measures the vocabulary and nothing else.
                self.store.transition_status(tid, f"agent-{reason}", "closed",
                                             no_claim_reason=reason)
                self.assertEqual(self.store.get_ticket(tid)["status"], "closed")

    def test_unlisted_reason_is_refused(self):
        for bad in ("stale", "", "DUPLICATE", "disposition pass", "wont_fix"):
            with self.subTest(bad=bad):
                tid = self.make_ticket()
                with self.assertRaises(ClaimRequiredError):
                    self.store.transition_status(tid, "agent", "closed",
                                                 no_claim_reason=bad)
                self.assertEqual(self.store.get_ticket(tid)["status"], "open")

    # ---- C3: disposition-pass is rate limited, the others are not -------------------

    def test_disposition_pass_is_rate_limited_per_actor(self):
        limit = schema.DISPOSITION_PASS_PER_MINUTE
        for _ in range(limit):
            tid = self.make_ticket()
            self.store.transition_status(tid, "burst-actor", "closed",
                                         no_claim_reason="disposition-pass")
        over = self.make_ticket()
        with self.assertRaises(NoClaimRateLimitError):
            self.store.transition_status(over, "burst-actor", "closed",
                                         no_claim_reason="disposition-pass")
        self.assertEqual(self.store.get_ticket(over)["status"], "open")

    def test_rate_limit_is_scoped_to_one_actor(self):
        for _ in range(schema.DISPOSITION_PASS_PER_MINUTE):
            self.store.transition_status(self.make_ticket(), "actor-a", "closed",
                                         no_claim_reason="disposition-pass")
        # A different actor in the same minute is unaffected. If this fails the limit is
        # global, which would make one busy session block every other session.
        self.store.transition_status(self.make_ticket(), "actor-b", "closed",
                                     no_claim_reason="disposition-pass")

    def test_other_reasons_are_never_rate_limited(self):
        for reason in ("duplicate", "wont-fix", "superseded-by"):
            with self.subTest(reason=reason):
                for _ in range(schema.DISPOSITION_PASS_PER_MINUTE + 5):
                    self.store.transition_status(self.make_ticket(), "bulk-actor",
                                                 "closed", no_claim_reason=reason)

    def test_rate_limit_window_expires(self):
        for _ in range(schema.DISPOSITION_PASS_PER_MINUTE):
            self.store.transition_status(self.make_ticket(), "slow-actor", "closed",
                                         no_claim_reason="disposition-pass")
        later = timestamps.iso_minus_seconds(timestamps.utc_now_iso(), -3600)
        with mock.patch("tessera.store.store.utc_now_iso", return_value=later):
            self.store.transition_status(self.make_ticket(), "slow-actor", "closed",
                                         no_claim_reason="disposition-pass")

    # ---- C4: the reason reaches the event -------------------------------------------

    def test_reason_is_recorded_on_the_event(self):
        tid = self.make_ticket()
        self.store.transition_status(tid, "agent", "closed", no_claim_reason="wont-fix")
        events = self.events_of_type("ClosedWithNoClaim")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ticket_id"], tid)
        self.assertEqual(events[0]["payload"], {"reason": "wont-fix"})

    def test_no_claim_close_emits_exactly_one_event(self):
        # check_and_record_closure used to emit this event itself. If both paths fire,
        # every no-claim close is double counted and the rate limiter's effective limit
        # is halved -- a silent behaviour change, not a visible one.
        from ...api import discrepancy
        tid = self.make_ticket()
        self.store.transition_status(tid, "agent", "closed", no_claim_reason="duplicate")
        discrepancy.check_and_record_closure(self.store, tid, "agent")
        self.assertEqual(len(self.events_of_type("ClosedWithNoClaim")), 1)

    # ---- C5: the view's threshold agrees with the guard's ---------------------------

    def apply_views(self):
        sql = (Path(__file__).resolve().parents[3] / "tools" / "tess_analytics_views.sql").read_text()
        conn = sqlite3.connect(self.db_path)
        conn.executescript(sql)
        conn.commit()
        conn.row_factory = sqlite3.Row
        return conn

    def test_view_agrees_with_the_guard_it_reports_on(self):
        """The view duplicates the guard's limit as a SQL literal, because a view cannot
        import a Python constant. This is what stops the two drifting apart.

        It deliberately does NOT match strings against the .sql file. An earlier version of
        this test asserted the literal "COUNT(*) > 1" appeared in the file, which pinned the
        number and not the predicate -- it stayed green while over_threshold was firing on
        five rapid duplicate closes that the guard permits by design. A string match cannot
        see that. So this drives real closes through the real guard and then reads the view
        over the same database, comparing behaviour to behaviour.
        """
        allowed_duplicates = 5
        for _ in range(allowed_duplicates):
            self.store.transition_status(self.make_ticket(), "dupe-actor", "closed",
                                         no_claim_reason="duplicate")
        # Two disposition-pass closes by one actor: the guard lets the first through and
        # refuses the second, so this minute contains exactly one landed close.
        self.store.transition_status(self.make_ticket(), "dp-actor", "closed",
                                     no_claim_reason="disposition-pass")
        with self.assertRaises(NoClaimRateLimitError):
            self.store.transition_status(self.make_ticket(), "dp-actor", "closed",
                                         no_claim_reason="disposition-pass")
        # A pre-guard shaped event: closed with no reason recorded at all. Under the guard
        # this close would be refused outright, whatever the rate.
        self.store.record_closed_with_no_claim(self.make_ticket(), "legacy-actor")

        conn = self.apply_views()
        try:
            rows = {r["actor"]: r for r in conn.execute(
                "SELECT * FROM v_no_claim_close_bursts")}

            dupes = rows["dupe-actor"]
            self.assertEqual(dupes["closes_in_minute"], allowed_duplicates)
            self.assertEqual(dupes["guard_would_refuse_count"], 0)
            self.assertEqual(
                dupes["over_threshold"], 0,
                "five rapid duplicate closes are legal by design and must not alert; an "
                "alert that fires on permitted behaviour is one people learn to ignore",
            )

            dp = rows["dp-actor"]
            # Only the close that actually landed produced an event; the refused one did
            # not, so the view sees one row and the refusal is not double counted.
            self.assertEqual(dp["disposition_pass_closes"], 1)
            self.assertEqual(dp["disposition_pass_over_limit"], 0)
            self.assertEqual(dp["over_threshold"], 0)

            legacy = rows["legacy-actor"]
            self.assertEqual(legacy["closes_with_no_reason_recorded"], 1)
            self.assertEqual(
                legacy["guard_would_refuse_count"], 1,
                "a close naming no reason is refused by the guard regardless of rate, so a "
                "single one is already over threshold",
            )
            self.assertEqual(legacy["over_threshold"], 1)
        finally:
            conn.close()

    def test_view_limit_literal_still_matches_the_python_constant(self):
        """Narrow companion to the behavioural test above: the SQL hardcodes the limit, so
        if someone raises DISPOSITION_PASS_PER_MINUTE the view silently keeps grading
        against 1. This fails loudly at that moment and names the file to edit."""
        self.assertEqual(
            schema.DISPOSITION_PASS_PER_MINUTE, 1,
            "DISPOSITION_PASS_PER_MINUTE changed; tools/tess_analytics_views.sql hardcodes "
            "this limit in v_no_claim_close_bursts (guard_would_refuse_count and "
            "disposition_pass_over_limit) and must be updated to match",
        )

    # ---- C6: the acceptance test, stated by TESS-192 --------------------------------

    def test_replaying_the_real_incident_refuses_after_the_first_close(self):
        """TESS-192's acceptance criterion, literally.

        Replays ticket-system-bd's real 2026-09-03T13:18Z burst: twelve closes, no claim
        on any ticket, the original actor, the original inter-close spacing driven by the
        original timestamps. Exactly one must land.
        """
        tickets = [self.make_ticket() for _ in INCIDENT_TIMESTAMPS]
        succeeded, refused = [], []
        for tid, ts in zip(tickets, INCIDENT_TIMESTAMPS):
            with mock.patch("tessera.store.store.utc_now_iso", return_value=ts):
                try:
                    self.store.transition_status(tid, INCIDENT_ACTOR, "closed",
                                                 no_claim_reason="disposition-pass")
                    succeeded.append(tid)
                except NoClaimRateLimitError:
                    refused.append(tid)

        self.assertEqual(len(succeeded), 1, "the guard must refuse after the first close")
        self.assertEqual(len(refused), 11)
        # And the eleven refusals must have left their tickets open, not merely logged.
        for tid in refused:
            self.assertEqual(self.store.get_ticket(tid)["status"], "open")

    def test_replay_control_the_same_burst_lands_fully_without_the_guard(self):
        """Negative control for the test above.

        Without this, a replay that refused for some unrelated reason -- a broken fixture,
        a ticket that could not be created, an exception on the first close -- would also
        produce "one succeeded, eleven refused" and read as the guard working. Here the
        same twelve closes are driven through the same code path with the rate limit
        raised out of the way; all twelve must land. If this fails, the acceptance test
        above proves nothing.
        """
        tickets = [self.make_ticket() for _ in INCIDENT_TIMESTAMPS]
        landed = 0
        with mock.patch.object(schema, "DISPOSITION_PASS_PER_MINUTE", 10_000):
            for tid, ts in zip(tickets, INCIDENT_TIMESTAMPS):
                with mock.patch("tessera.store.store.utc_now_iso", return_value=ts):
                    self.store.transition_status(tid, INCIDENT_ACTOR, "closed",
                                                 no_claim_reason="disposition-pass")
                    landed += 1
        self.assertEqual(landed, len(INCIDENT_TIMESTAMPS))


if __name__ == "__main__":
    unittest.main()
