"""TESS-178: event types that are deliberately part of the hash chain but project nothing.

rebuild_projection() raised on any event type it did not recognise, and two real types in
the live database were never given a branch -- ClaimDiscrepancyChecked (19 events) and
ClosedWithNoClaim (137). The consequence was not a wrong result, it was no result: this
project's own correctness check, the thing event sourcing is supposed to buy, could not run
against data/tessera.db at all, while passing everywhere on the small synthetic stores the
tests build. Green in every test, inoperable on the one database that matters.

The fix is an explicit, documented exclusion rather than invented replay clauses, because
both types are pure audit records -- they log a finding ABOUT the system, not a state change
within it. Writing replay clauses would have manufactured projection state that the live
write path never creates, making rebuild diverge from live in the opposite direction.

The two tests that carry the weight here are not the happy path:

  - test_unknown_event_type_still_raises -- the exclusion must be an explicit enumeration,
    never a broadened except. If this ever stops raising, the fix has converted a loud gap
    into a silent one and is strictly worse than the defect it replaced. Proven to
    discriminate by replacing the enumeration with a blanket pass and watching it go red.
  - test_every_neutral_event_actually_projects_nothing -- being on the list is a CLAIM that
    the event writes nothing. This enforces the claim against each type's real store method
    instead of trusting the comment next to it.
"""

import tempfile
import unittest
from pathlib import Path

from .. import schema
from ..store import Store


class ProjectionNeutralEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")
        self.ticket_id = self.store.create_ticket(
            ticket_type="Task", reporter="jon", actor="agent", summary="a real ticket",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def emitters_internal(self):
        """Each neutral event type mapped to a callable that emits it through the REAL
        store method that produces it in production -- not through append_event_internal
        directly, which would test the constant against itself rather than against the
        behaviour of the code that writes these events."""
        return {
            "ClaimDiscrepancyChecked": lambda: self.store.record_diff_check(
                self.ticket_id, "agent",
                {"matches": ["a.py"], "touched_but_not_claimed": ["b.py"],
                 "claimed_but_not_touched": []},
            ),
            "ClosedWithNoClaim": lambda: self.store.record_closed_with_no_claim(
                self.ticket_id, "agent",
            ),
        }

    def test_every_neutral_event_has_an_emitter_in_this_test(self):
        """Coverage guard. Adding a type to PROJECTION_NEUTRAL_EVENTS without adding it
        here would let it inherit the neutrality claim below without ever being checked
        against it -- the exclusion list would start growing on trust again, which is the
        habit this ticket exists to break."""
        self.assertEqual(
            set(self.emitters_internal()), set(schema.PROJECTION_NEUTRAL_EVENTS),
            "every type on PROJECTION_NEUTRAL_EVENTS needs a real emitter here",
        )

    def test_every_neutral_event_actually_projects_nothing(self):
        """The claim being on the list is a claim about behaviour. Enforce it."""
        for event_type, emit in self.emitters_internal().items():
            with self.subTest(event_type=event_type):
                before = self.store.live_projection()
                events_before = self.event_count_internal()

                emit()

                after = self.store.live_projection()
                self.assertEqual(
                    self.event_count_internal(), events_before + 1,
                    f"{event_type} must still be appended to the chain -- neutral means "
                    f"'projects nothing', not 'is not recorded'",
                )
                for table in before:
                    self.assertEqual(
                        sorted(before[table]), sorted(after[table]),
                        f"{event_type} is on PROJECTION_NEUTRAL_EVENTS but changed "
                        f"{table!r}. Either it is not neutral and needs a real replay "
                        f"clause, or it should not be on that list.",
                    )

    def test_rebuild_matches_live_after_neutral_events(self):
        """The whole point: these events used to make rebuild_projection() raise."""
        for emit in self.emitters_internal().values():
            emit()

        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(set(live.keys()), set(rebuilt.keys()))
        for table in live:
            self.assertEqual(
                sorted(live[table]), sorted(rebuilt[table]),
                f"table {table!r} diverged between live and rebuilt projections",
            )

    def test_neutral_events_stay_in_the_verified_hash_chain(self):
        """Excluded from PROJECTION, not from the chain. A pure audit event is still
        tamper-evident history and must still hash-verify."""
        for emit in self.emitters_internal().values():
            emit()
        self.assertEqual(
            self.store.verify_chain(),
            {"roots": 1, "tips": 1, "orphans": 0, "hash_mismatches": 0},
        )

    def test_unknown_event_type_still_raises(self):
        """The negative control, and the line this fix must not cross.

        replay_event_internal() raising on an unrecognised type is what forces anyone
        adding an event that DOES mutate projected state to decide how it replays. The
        exclusion added for TESS-178 must be an explicit enumeration; if it were ever
        widened into a blanket except or a bare fall-through pass, a real unhandled event
        would be silently skipped and the projection check would go quietly blind -- worse
        than the loud failure this ticket started from.

        Proven to discriminate, not assumed to: replacing the
        `elif event_type in schema.PROJECTION_NEUTRAL_EVENTS` branch with an unconditional
        pass turns this test red and leaves the rest of the module green.
        """
        import sqlite3

        from ..store import replay_event_internal

        shadow = sqlite3.connect(":memory:")
        shadow.execute("PRAGMA foreign_keys=OFF")
        schema.init_schema(shadow)
        with self.assertRaises(ValueError) as caught:
            replay_event_internal(
                shadow, "SomeEventNobodyHasWrittenAHandlerFor", self.ticket_id, "agent",
                {}, "2026-01-01T00:00:00Z", "deadbeef",
            )
        self.assertIn("unknown event_type", str(caught.exception))
        shadow.close()

    def test_a_neutral_type_is_not_merely_absent_from_the_handled_set(self):
        """Guards a subtle wrong fix: deleting the raise, or adding the types to some
        handled branch that happens to no-op for them, would also make the suite pass.
        What must be true is that these specific types are recognised BY NAME as neutral,
        which is what makes the exclusion reviewable."""
        self.assertIn("ClosedWithNoClaim", schema.PROJECTION_NEUTRAL_EVENTS)
        self.assertIn("ClaimDiscrepancyChecked", schema.PROJECTION_NEUTRAL_EVENTS)
        self.assertIsInstance(schema.PROJECTION_NEUTRAL_EVENTS, frozenset)

    def event_count_internal(self):
        return self.store.conn_internal().execute(
            "SELECT count(*) FROM events"
        ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
