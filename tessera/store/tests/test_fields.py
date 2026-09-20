import tempfile
import unittest
from pathlib import Path

from ..store import Store


class FieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_base_and_bug_fields_first_class(self):
        # TESS-44: severity/priority are a 0-4 ordered int scale (0=Highest, 4=Lowest),
        # not free text -- priority=1 is "High" (P1), severity=0 is "Highest" (S0).
        tid = self.store.create_ticket(
            ticket_type="Bug", reporter="me", actor="agent", assignee="alice",
            priority=1, severity=0, repro_steps="1. click 2. crash",
            environment="macOS 15",
        )
        ticket = self.store.get_ticket(tid)
        for col in ("type", "status", "reporter", "assignee", "priority",
                    "severity", "repro_steps", "environment"):
            self.assertIn(col, ticket)
        self.assertEqual(ticket["severity"], 0)
        self.assertEqual(ticket["repro_steps"], "1. click 2. crash")
        self.assertEqual(ticket["environment"], "macOS 15")
        self.assertEqual(ticket["assignee"], "alice")
        self.assertEqual(ticket["priority"], 1)

    def test_severity_and_priority_reject_out_of_range_and_unknown_values(self):
        with self.assertRaises(ValueError):
            self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent", severity="high")
        with self.assertRaises(ValueError):
            self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent", priority=5)
        with self.assertRaises(ValueError):
            self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent", severity=-1)

    def test_custom_fields_roundtrip(self):
        tid = self.store.create_ticket(
            ticket_type="Task", reporter="me", actor="agent",
            custom_fields={"sprint": 7, "labels": ["a", "b"]},
        )
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["custom_fields"], {"sprint": 7, "labels": ["a", "b"]})
        self.store.set_custom_field(tid, "agent", "sprint", 8)
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["custom_fields"]["sprint"], 8)

    def test_custom_field_rebuild_matches_live_for_dict_and_non_ascii_values(self):
        # TESS-20: ticket_fields.field_value was serialized with plain json.dumps() on
        # the live path (insertion key order, ensure_ascii default True) but with
        # canonical_json() on replay (sorted keys, ensure_ascii=False) -- a dict-valued
        # field or any non-ASCII text diverged between live and rebuilt for identical
        # data. Both fixed to use canonical_json consistently; this exercises both
        # failure shapes directly (dict key order via create_ticket's custom_fields, and
        # non-ASCII text via set_custom_field) rather than only the scalar-ASCII case
        # test_custom_fields_roundtrip already covered, which couldn't have caught this.
        tid = self.store.create_ticket(
            ticket_type="Task", reporter="me", actor="agent",
            custom_fields={"meta": {"zebra": 1, "apple": 2}},
        )
        self.store.set_custom_field(tid, "agent", "note", "candidate — discarded")
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["ticket_fields"]), sorted(rebuilt["ticket_fields"]))

    def test_summary_and_description_at_creation_and_via_setters(self):
        # TESS-42: real first-class fields (Jira parity -- Summary/Description are
        # distinct from Steps to Reproduce there), not the ad hoc custom_fields.summary
        # convention this replaces.
        tid = self.store.create_ticket(
            ticket_type="Bug", reporter="me", actor="agent",
            summary="Login button does nothing", description="Clicking Login has no effect at all.",
        )
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["summary"], "Login button does nothing")
        self.assertEqual(ticket["description"], "Clicking Login has no effect at all.")

        self.store.set_summary(tid, "agent", "Login button silently no-ops")
        self.store.set_description(tid, "agent", "Updated: only on Safari.")
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["summary"], "Login button silently no-ops")
        self.assertEqual(ticket["description"], "Updated: only on Safari.")

        summary_events = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='SummarySet' AND ticket_id=?", (tid,)
        ).fetchone()[0]
        description_events = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='DescriptionSet' AND ticket_id=?", (tid,)
        ).fetchone()[0]
        self.assertEqual(summary_events, 1)
        self.assertEqual(description_events, 1)

        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["tickets"]), sorted(rebuilt["tickets"]))

    def test_set_summary_and_description_reject_nonexistent_ticket(self):
        with self.assertRaises(ValueError):
            self.store.set_summary("TP-999", "agent", "x")
        with self.assertRaises(ValueError):
            self.store.set_description("TP-999", "agent", "x")

    def test_reference_docs_write_is_visible_event(self):
        tid = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent")
        before = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='ReferenceDocsSet'"
        ).fetchone()[0]
        self.store.set_reference_docs(tid, "agent", ["docs/sme-notes.md"])
        after = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='ReferenceDocsSet'"
        ).fetchone()[0]
        self.assertEqual(after, before + 1)
        # Not folded into a generic FieldSet event.
        generic = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='FieldSet' AND ticket_id=?", (tid,)
        ).fetchone()[0]
        self.assertEqual(generic, 0)
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["reference_docs"], ["docs/sme-notes.md"])


    def test_priority_and_severity_are_settable_after_create(self):
        # TESS-98. The whole defect was that this could not be done at all, so the test
        # asserts the COLUMN moved, not just that the call returned.
        tid = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent")
        self.assertIsNone(self.store.get_ticket(tid)["priority"])

        self.store.set_priority_like(tid, "agent", "priority", 1)
        self.store.set_priority_like(tid, "agent", "severity", 0)
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["priority"], 1)
        self.assertEqual(ticket["severity"], 0)
        self.assertEqual(ticket["custom_fields"], {})

        # Its own event type, like every other first-class field, and never a FieldSet.
        counts = dict(self.store.conn_internal().execute(
            "SELECT event_type, COUNT(*) FROM events WHERE ticket_id=?"
            " AND event_type IN ('PrioritySet','SeveritySet','FieldSet') GROUP BY event_type",
            (tid,),
        ).fetchall())
        self.assertEqual(counts.get("PrioritySet"), 1)
        self.assertEqual(counts.get("SeveritySet"), 1)
        self.assertIsNone(counts.get("FieldSet"))

        # Clearing is a real triage action, not an error.
        self.store.set_priority_like(tid, "agent", "priority", None)
        self.assertIsNone(self.store.get_ticket(tid)["priority"])

        # The replay handler exists and agrees with the live path -- a first-class column
        # written by an event type replay does not know about is silently lost on rebuild.
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["tickets"]), sorted(rebuilt["tickets"]))

    def test_priority_setter_validates_against_the_same_constant_as_create(self):
        tid = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent")
        for bad in (5, -1, "high", "1"):
            with self.assertRaises(ValueError):
                self.store.set_priority_like(tid, "agent", "priority", bad)
        with self.assertRaises(ValueError):
            self.store.set_priority_like(tid, "agent", "tier", 1)
        with self.assertRaises(ValueError):
            self.store.set_priority_like("TP-999", "agent", "priority", 1)
        # Nothing partial got through on the way.
        self.assertIsNone(self.store.get_ticket(tid)["priority"])

    def test_set_custom_field_refuses_first_class_columns(self):
        # The silent-success path is the defect, so this asserts the REFUSAL, and then
        # asserts no shadow row and no event were written by the attempt.
        tid = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent")
        for name in ("priority", "severity", "summary", "description", "status"):
            with self.assertRaises(ValueError) as caught:
                self.store.set_custom_field(tid, "agent", name, "1")
            self.assertIn(name, str(caught.exception))
        self.assertEqual(self.store.get_ticket(tid)["custom_fields"], {})
        events = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE ticket_id=? AND event_type='FieldSet'", (tid,)
        ).fetchone()[0]
        self.assertEqual(events, 0)
        # A genuine custom field still works -- the guard is a name check, not a freeze.
        self.store.set_custom_field(tid, "agent", "sprint", 9)
        self.assertEqual(self.store.get_ticket(tid)["custom_fields"], {"sprint": 9})

    def test_create_refuses_custom_fields_that_shadow_a_column(self):
        with self.assertRaises(ValueError):
            self.store.create_ticket(
                ticket_type="Bug", reporter="me", actor="agent",
                custom_fields={"priority": 1},
            )

    def test_first_class_field_list_matches_schema(self):
        # The guard is a literal list, so it can drift from the table. If a column is added
        # and not listed here, set-field silently starts diverting it again -- which is the
        # exact bug, reopened. This test is the thing that stops that.
        columns = {row[1] for row in self.store.conn_internal().execute(
            "PRAGMA table_info(tickets)"
        )}
        self.assertEqual(columns, set(self.store.FIRST_CLASS_TICKET_FIELDS))

    def test_historical_shadow_rows_still_replay(self):
        # The guard belongs on the write path only. Refusing these names in replay would
        # make the 22 shadow rows already on disk unreplayable, turning a data-quality
        # problem into a rebuild that crashes.
        tid = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent")
        with self.store.write_txn_internal() as conn:
            self.store.append_event_internal(
                conn, "FieldSet", "agent",
                {"field_name": "priority", "field_value": "1"}, ticket_id=tid,
            )
            conn.execute(
                "INSERT INTO ticket_fields (ticket_id, field_name, field_value) VALUES (?,?,?)",
                (tid, "priority", '"1"'),
            )
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(self.store.live_projection()["tickets"]),
                         sorted(rebuilt["tickets"]))


if __name__ == "__main__":
    unittest.main()
