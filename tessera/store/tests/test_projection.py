import tempfile
import unittest
from pathlib import Path

from ..store import Store


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def representative_sequence(self):
        s = self.store
        a = s.create_ticket(ticket_type="Epic", reporter="me", actor="agent")
        b = s.create_ticket(
            ticket_type="Task", reporter="me", actor="agent", assignee="alice",
            custom_fields={"team": "core"},
        )
        bug = s.create_ticket(
            ticket_type="Bug", reporter="me", actor="agent", severity=1,
            repro_steps="click X", environment="macOS",
        )
        sub = s.create_ticket(
            ticket_type="Sub-task", reporter="me", actor="agent", parent_id=bug,
        )
        s.add_comment(b, "agent", "working on it")
        s.freeze_ticket_criteria(b, "agent", [{"id": "c1", "statement": "works", "verification": "manual", "verifiable": True}])
        s.transition_status(b, "agent", "in_progress")
        s.add_link(b, bug, "blocks", "agent")
        s.set_reference_docs(bug, "agent", ["docs/investigation.md"])
        s.set_custom_field(a, "agent", "owner", "team-x")
        s.archive_ticket(sub, "agent")
        s.record_claim(b, "agent", "fixed the thing", ["tessera/store/store.py"], "deadbeef")
        s.record_stage_promotion("dev", "abc123", "agent")
        s.link_ticket_commit(b, "dev", "agent", commit_sha="abc123")
        return {"a": a, "b": b, "bug": bug, "sub": sub}

    def test_rebuild_equals_live_across_all_projection_tables(self):
        self.representative_sequence()
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(set(live.keys()), set(rebuilt.keys()))
        for table in live:
            self.assertEqual(
                sorted(live[table]), sorted(rebuilt[table]),
                f"table {table!r} diverged between live and rebuilt projections",
            )

    def test_rebuild_covers_a_registered_project_with_zero_tickets(self):
        # register_project() seeds a real live counters row (value=0) the moment a
        # project is registered, before any ticket is ever created in it -- found by
        # direct repro against the real, multi-project data/tessera.db: AREM had zero
        # tickets, and rebuild_projection()'s first version only created a counters entry
        # when it saw a TicketCreated event for that project, so AREM's live row had no
        # rebuilt counterpart at all (a regression not caught by any existing
        # test since every prior test used exactly one project with real tickets in it).
        self.store.register_project("EMPTY", "EMP")
        live = self.store.live_projection()
        rebuilt = self.store.rebuild_projection()
        self.assertEqual(sorted(live["counters"]), sorted(rebuilt["counters"]))
        empty_project_id = self.store.get_project("EMP")["id"]
        self.assertIn((empty_project_id, "ticket_id", 0), rebuilt["counters"])

    def test_verify_chain_one_root_one_tip_no_orphans(self):
        self.representative_sequence()
        result = self.store.verify_chain()
        self.assertEqual(result["roots"], 1)
        self.assertEqual(result["tips"], 1)
        self.assertEqual(result["orphans"], 0)
        self.assertEqual(result["hash_mismatches"], 0)

    def test_archival_is_event_not_projection_only(self):
        ids = self.representative_sequence()
        sub = ids["sub"]
        # Archived via the normal event path in representative_sequence(); rebuild must
        # preserve that.
        self.assertTrue(self.store.get_ticket(sub)["archived"])
        rebuilt = self.store.rebuild_projection()
        rebuilt_row = next(r for r in rebuilt["tickets"] if r[0] == sub)
        # canonical_columns_internal(), not PRAGMA table_info() on the live connection --
        # the latter reflects a table's PHYSICAL column order, which only matches
        # rebuild_projection()'s DDL-declared order on a never-migrated db. This test's own
        # temp db is always fresh, so the bug was latent here (flagged by Clint Eastwood's
        # adversarial review, not caught by this test failing) -- exactly the trap
        # canonical_columns_internal() exists to close for real, migrated databases.
        tickets_cols = self.store.canonical_columns_internal()["tickets"]
        archived_idx = tickets_cols.index("archived")
        self.assertEqual(rebuilt_row[archived_idx], 1)

        # Now simulate the failure mode the architecture doc warns about: a projection-only
        # mutation that bypasses the event log entirely. rebuild_projection() must NOT
        # reproduce it, proving the live/rebuilt comparison would catch this drift.
        self.store.conn_internal().execute(
            "UPDATE tickets SET archived=1 WHERE ticket_id=?", (ids["a"],)
        )
        self.store.conn_internal().commit()
        live = self.store.live_projection()
        rebuilt2 = self.store.rebuild_projection()
        self.assertNotEqual(
            sorted(live["tickets"]), sorted(rebuilt2["tickets"]),
            "a projection-only archival mutation with no backing event should be "
            "detectable as drift by comparing live vs rebuilt -- it was not",
        )


if __name__ == "__main__":
    unittest.main()
