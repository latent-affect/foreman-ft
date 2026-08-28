import tempfile
import threading
import unittest
from pathlib import Path

from ..exceptions import CycleError, HierarchyError, WorkflowError
from ..store import Store


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_illegal_transition_rejected_in_store(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        with self.assertRaises(WorkflowError):
            self.store.transition_status(tid, "agent", "in_review")
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["status"], "open")
        # A legal transition still works, once criteria are frozen (document-before-build).
        self.store.freeze_ticket_criteria(
            tid, "agent",
            [{"id": "c1", "statement": "works", "verification": "manual", "verifiable": True}],
        )
        self.store.transition_status(tid, "agent", "in_progress")
        self.assertEqual(self.store.get_ticket(tid)["status"], "in_progress")

    def test_subtask_requires_parent_enforced_in_store(self):
        with self.assertRaises(HierarchyError):
            self.store.create_ticket(ticket_type="Sub-task", reporter="me", actor="agent")

        bug_id = self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent")
        sub_id = self.store.create_ticket(
            ticket_type="Sub-task", reporter="me", actor="agent", parent_id=bug_id
        )
        self.assertEqual(self.store.get_ticket(sub_id)["parent_id"], bug_id)

        epic_id = self.store.create_ticket(ticket_type="Epic", reporter="me", actor="agent")
        with self.assertRaises(HierarchyError):
            self.store.create_ticket(
                ticket_type="Sub-task", reporter="me", actor="agent", parent_id=epic_id
            )

    def test_link_cycle_rejected_including_concurrent(self):
        a = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        b = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        c = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")

        self.store.add_link(a, b, "blocks", "agent")
        self.store.add_link(b, c, "blocks", "agent")
        with self.assertRaises(CycleError):
            self.store.add_link(c, a, "blocks", "agent")

        # Concurrent case: two threads (separate connections) each add one edge of the
        # same 2-cycle at once, released together via a barrier to maximize the race.
        d = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        e = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        errors = []
        successes = []
        barrier = threading.Barrier(2)

        def link(frm, to):
            store = Store(self.db_path)
            barrier.wait()
            try:
                store.add_link(frm, to, "blocks", "agent")
                successes.append((frm, to))
            except CycleError as exc:
                errors.append(exc)

        t1 = threading.Thread(target=link, args=(d, e))
        t2 = threading.Thread(target=link, args=(e, d))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Exactly one edge of the cycle wins (whichever commits its BEGIN IMMEDIATE
        # transaction first); the other must see the committed edge and be rejected.
        # Both succeeding would mean the store missed a real concurrent cycle.
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)

    def test_blocked_by_cycle_rejected_same_as_blocks(self):
        # code-review finding: cycle detection originally checked only
        # link_type == "blocks", so a caller phrasing the same edge as "blocked-by"
        # bypassed it entirely. A blocks B blocks C, then closing the loop as
        # "A blocked-by C" (equivalent to "C blocks A") must be rejected too.
        a = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        b = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        c = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.add_link(a, b, "blocks", "agent")
        self.store.add_link(b, c, "blocks", "agent")
        with self.assertRaises(CycleError):
            self.store.add_link(a, c, "blocked-by", "agent")

    def test_unknown_link_type_rejected(self):
        a = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        b = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        with self.assertRaises(ValueError):
            self.store.add_link(a, b, "some-made-up-type", "agent")


if __name__ == "__main__":
    unittest.main()
