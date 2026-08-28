import tempfile
import unittest

from tessera.tessguard import event_activity

from .fixtures import make_store, make_ticket


class EventActivityTests(unittest.TestCase):
    def test_event_type_set_exact(self):
        self.assertEqual(
            event_activity.REAL_TESSERA_WRITE_EVENT_TYPES,
            frozenset({
                "TicketCreated", "CommentAdded", "StatusChanged",
                "TicketCriteriaFrozen", "ClaimRecorded",
            }),
        )

    def test_null_ticket_id_does_not_raise(self):
        self.assertIsNone(event_activity.project_prefix_of_ticket(None))
        self.assertIsNone(event_activity.project_prefix_of_ticket(""))

    def test_prefix_derivation(self):
        self.assertEqual(event_activity.project_prefix_of_ticket("DEMO-73"), "DEMO")
        self.assertEqual(event_activity.project_prefix_of_ticket("SAMPLEFT-12"), "SAMPLEFT")

    def test_real_events_found_for_registered_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="TEST", source_root="/tmp/fake-repo")
            tid = make_ticket(store, "TEST")
            store.add_comment(tid, "tester", "hello")
            events = event_activity.real_events_for_projects(store, ["TEST"])
            event_types = {e["event_type"] for e in events}
            self.assertIn("TicketCreated", event_types)
            self.assertIn("CommentAdded", event_types)

    def test_read_only_list_get_never_counted(self):
        # list/get never emit events at all (store never appends an event for a read) --
        # confirm indirectly: a fresh store with only ticket creation has exactly the
        # write-type events, nothing extra from any read-shaped call.
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="TEST", source_root="/tmp/fake-repo")
            make_ticket(store, "TEST")
            store.list_tickets()
            store.get_project("TEST")
            events = event_activity.real_events_for_projects(store, ["TEST"])
            self.assertEqual(len(events), 1)  # only the TicketCreated

    def test_window_excludes_events_outside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = make_store(tmp, prefix="TEST", source_root="/tmp/fake-repo")
            make_ticket(store, "TEST")
            # A window entirely in the future should find nothing.
            events = event_activity.real_events_for_projects(
                store, ["TEST"],
                window_start_iso="2099-01-01T00:00:00.000000Z",
                window_end_iso="2099-01-02T00:00:00.000000Z",
            )
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
