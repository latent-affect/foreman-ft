"""TESS-180: the standing check that every emittable event type is covered by replay.

TESS-178 fixed the two event types that were actually uncovered. Nothing kept the set
covered. Adding an event type with no replay branch and no PROJECTION_NEUTRAL_EVENTS entry
breaks rebuild_projection() against any database containing it, and the entire suite stays
green until someone runs a rebuild against real data -- which is exactly how the original
defect hid behind 137 real ClosedWithNoClaim events.

The history that shaped this module is worth keeping, because the naive version of this
check is convincing and wrong. Two independent literal-scanning sweeps -- ticket-system-ed's
during TESS-178's review, and one this session wrote to verify ed's -- both reported
"26 emitted, 24 handled, 2 neutral, 0 uncovered" and agreed with each other. The real count
is 32. They agreed because both scanned only string literals at the call site and were
structurally blind to the same six indirect types; agreement between two methods sharing a
blind spot is not corroboration. Both numbers have been retracted.

So the tests that carry weight here are the ones about what the checker CANNOT read:

  - test_unreadable_emission_fails_rather_than_being_skipped: a genuinely computed
    event_type must be reported, not silently dropped. This is what makes the check
    fail-closed rather than merely broader; a broader check still fails open at its own new
    edge.
  - test_partially_readable_helper_is_not_treated_as_readable: a helper with literal call
    sites AND one variable call site must report the variable one, even though the literals
    resolve cleanly. Predicted by ed when reviewing the design, and the resolver did have
    this gap when they predicted it.
  - test_resolver_is_structural_not_keyed_to_known_helper_names: a helper this codebase has
    never contained must resolve on shape alone. If pass 1 were secretly keyed to the three
    known helper names, it would be the same fail-open trap one layer up.
"""

import tempfile
import textwrap
import unittest
from pathlib import Path

from .. import event_coverage, schema

REPO_ROOT = Path(__file__).resolve().parents[3]
TESSERA_ROOT = REPO_ROOT / "tessera"
STORE_PATH = TESSERA_ROOT / "store" / "store.py"

# Hand-resolved by ticket-system-ed against all three indirection sites, and independently
# reproduced by this module's resolver. Pinned as a literal fixture rather than recomputed,
# so that a resolver regression shows up as a diff against a known-good answer instead of
# quietly redefining what "correct" means.
GROUND_TRUTH_EVENT_TYPES = frozenset({
    "AssigneeSet", "AttachmentAdded", "ClaimDiscrepancyChecked", "ClaimRecorded",
    "ClosedWithNoClaim", "ColumnDescriptionSet", "CommentAdded", "CommentCodeSnippetSet",
    "DatasetCreated", "DescriptionSet", "FieldSet", "HotlistCreated", "LinkAdded",
    "PrioritySet", "ProjectAddedToDataset", "ProjectSourceRootChanged",
    "ProjectStatusChanged", "ReferenceDocsSet", "SeveritySet", "StagePromoted",
    "StageRolledBack", "StatusChanged", "SummarySet", "TicketAddedToHotlist",
    "TicketArchived", "TicketCommitLinked", "TicketCreated", "TicketCriteriaFrozen",
    "TicketRemovedFromHotlist", "TicketUnarchived", "TicketUnwatched", "TicketWatched",
})

# The six a literal scan cannot see. Named individually so a regression says WHICH
# indirection shape broke, not just that the count moved.
INDIRECT_EVENT_TYPES = frozenset({
    "PrioritySet", "SeveritySet",          # dict-literal subscript
    "TicketArchived", "TicketUnarchived",  # forwarded param of set_archived_internal
    "StagePromoted", "StageRolledBack",    # forwarded param of record_stage_event_internal
})


class EventCoverageTests(unittest.TestCase):
    """The real check, against the real tree."""

    def setUp(self):
        self.types, self.unresolved = event_coverage.emitted_event_types(TESSERA_ROOT)
        self.handled = event_coverage.handled_event_types(STORE_PATH)

    def test_no_emitted_event_type_lacks_a_replay_branch_or_a_neutral_entry(self):
        """The point of the ticket. Every type this codebase can emit must either replay or
        be a declared, documented non-projector."""
        covered = self.handled | set(schema.PROJECTION_NEUTRAL_EVENTS)
        uncovered = sorted(set(self.types) - covered)
        self.assertEqual(
            uncovered, [],
            "these event types can be emitted but rebuild_projection() would raise on "
            "them, which makes the projection check unrunnable against any database "
            "containing one: "
            + ", ".join(f"{t} (emitted at {self.types[t][0]})" for t in uncovered),
        )

    def test_every_emission_is_readable(self):
        """Fail-closed, on the real tree. An unresolved emission is a failure, never a
        footnote -- a check that skips what it cannot parse reports green over precisely
        the part it cannot see."""
        self.assertEqual(
            [str(u) for u in self.unresolved], [],
            "event_coverage could not read these emissions, so the coverage result above "
            "does not cover them",
        )

    def test_census_matches_the_hand_resolved_ground_truth(self):
        self.assertEqual(set(self.types), set(GROUND_TRUTH_EVENT_TYPES))

    def test_the_six_indirect_types_are_resolved(self):
        """Guards the specific regression that would silently shrink this check back to a
        literal scan: 26 of 32, reporting full coverage, exactly as the two retracted
        sweeps did."""
        for event_type in sorted(INDIRECT_EVENT_TYPES):
            with self.subTest(event_type=event_type):
                self.assertIn(
                    event_type, self.types,
                    f"{event_type} is emitted only through indirection; failing to resolve "
                    f"it means this check has regressed to a literal scan",
                )


class ResolverBehaviourTests(unittest.TestCase):
    """The resolver's own behaviour, against synthetic trees. Synthetic rather than the real
    one because these need emission shapes the real codebase does not contain -- including
    ones it must never contain silently."""

    def analyse(self, source):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "pkg"
        root.mkdir()
        (root / "module.py").write_text(textwrap.dedent(source))
        return event_coverage.emitted_event_types(root)

    def test_plain_literal_resolves(self):
        types, unresolved = self.analyse('''
            class S:
                def go(self, conn, actor):
                    self.append_event_internal(conn, "PlainLiteral", actor, {})
        ''')
        self.assertEqual(set(types), {"PlainLiteral"})
        self.assertEqual(unresolved, [])

    def test_dict_literal_subscript_resolves(self):
        types, unresolved = self.analyse('''
            class S:
                FIELD_EVENTS = {"a": "AlphaSet", "b": "BetaSet"}

                def go(self, conn, actor, field):
                    event_type = self.FIELD_EVENTS[field]

                    def attempt():
                        self.append_event_internal(conn, event_type, actor, {})

                    attempt()
        ''')
        self.assertEqual(set(types), {"AlphaSet", "BetaSet"})
        self.assertEqual(unresolved, [])

    def test_resolver_is_structural_not_keyed_to_known_helper_names(self):
        """A helper this codebase has never contained. If pass 1 recognised forwarding by
        name (set_archived_internal, record_stage_event_internal) rather than by shape, this
        would come back unresolved or empty -- and the check would silently stop covering
        every helper added after today."""
        types, unresolved = self.analyse('''
            class S:
                def a_helper_nobody_has_ever_written(self, conn, actor, event_type):
                    self.append_event_internal(conn, event_type, actor, {})

                def go(self, conn, actor):
                    self.a_helper_nobody_has_ever_written(conn, actor, "BrandNewShape")
        ''')
        self.assertEqual(set(types), {"BrandNewShape"})
        self.assertEqual(unresolved, [])

    def test_unreadable_emission_fails_rather_than_being_skipped(self):
        """The control that separates fail-closed from merely broader. A genuinely computed
        event type cannot be resolved by any static analysis -- what matters is that it is
        REPORTED rather than dropped."""
        types, unresolved = self.analyse('''
            class S:
                def go(self, conn, actor, suffix):
                    self.append_event_internal(conn, "Prefix" + suffix, actor, {})
        ''')
        self.assertEqual(set(types), set(), "a computed type must not be invented")
        self.assertEqual(len(unresolved), 1)
        self.assertIn("will not guess", unresolved[0].reason)

    def test_partially_readable_helper_is_not_treated_as_readable(self):
        """Ed's predicted edge, and the resolver genuinely had this gap when they predicted
        it. One helper, two call sites: one literal, one forwarding a variable. Resolving
        the literal and saying nothing about the variable would report full coverage while
        an entire emission path went unread."""
        types, unresolved = self.analyse('''
            class S:
                def helper(self, conn, actor, event_type):
                    self.append_event_internal(conn, event_type, actor, {})

                def known(self, conn, actor):
                    self.helper(conn, actor, "KnownType")

                def unknown(self, conn, actor, whatever):
                    self.helper(conn, actor, whatever)
        ''')
        self.assertEqual(
            set(types), {"KnownType"},
            "the literal call site is still a real emitted type and belongs in the census",
        )
        self.assertEqual(
            len(unresolved), 1,
            "the variable call site is an unread emission path and must be reported; "
            "resolving only the literal one is the fail-open shape this module exists to "
            "prevent",
        )
        self.assertIn("non-literal", unresolved[0].reason)

    def test_missing_event_type_argument_is_reported(self):
        types, unresolved = self.analyse('''
            class S:
                def go(self, conn):
                    self.append_event_internal(conn)
        ''')
        self.assertEqual(set(types), set())
        self.assertEqual(len(unresolved), 1)

    def test_tests_directories_are_excluded_from_the_census(self):
        """Test modules legitimately emit synthetic types -- the neutral-event module
        replays a deliberately unknown one. Counting those would make the census describe
        the tests rather than the system."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "pkg"
        (root / "tests").mkdir(parents=True)
        (root / "module.py").write_text(textwrap.dedent('''
            class S:
                def go(self, conn, actor):
                    self.append_event_internal(conn, "RealType", actor, {})
        '''))
        (root / "tests" / "test_thing.py").write_text(textwrap.dedent('''
            class T:
                def go(self, conn, actor):
                    self.append_event_internal(conn, "SyntheticTestOnlyType", actor, {})
        '''))
        types, unresolved = event_coverage.emitted_event_types(root)
        self.assertEqual(set(types), {"RealType"})
        self.assertEqual(unresolved, [])


class HandledEventTypeTests(unittest.TestCase):
    def test_both_comparison_forms_are_read(self):
        """A regex over the `event_type == "X"` form alone is what made this project's first
        census of unhandled types wrong (it missed PrioritySet/SeveritySet, handled jointly
        in a tuple membership test). Both forms are read here, and this pins that."""
        handled = event_coverage.handled_event_types(STORE_PATH)
        self.assertIn("TicketCreated", handled, "the == form")
        self.assertIn("SeveritySet", handled, "the tuple-membership form")
        self.assertIn("PrioritySet", handled, "the tuple-membership form")


if __name__ == "__main__":
    unittest.main()
