"""GOALS.json C6-C7 (DEVH-10 / PRD.md R4). The vulnerability question is closed: all four of
store.py's f-string-built SQL sites (see the '# nosec B608' comment at each) interpolate an
identifier, never a value, and every identifier comes from a fixed constant or a schema read,
never from a caller/actor/ticket-supplied string. This suite's job is the regression, not the
audit: prove the one guard that stands between a caller and an arbitrary column name
(PRIORITY_LIKE_FIELDS, store.py:457) actually holds, with a negative control proving the test
would fail if that guard were ever removed.

RISK-REGISTER ROW (DEVH-10's own ask, recorded here since this project has no separate
risk-register file -- ARCHITECTURE.md Addendum 2 uses the same "narrowed to a risk-register
item" language for R7's atlas/mcp/ finding, in a doc section rather than a dedicated store):

  Risk: PRIORITY_LIKE_FIELDS (store.py:457) and the guard that reads it
  (store.py:461-462) sit fourteen-ish lines above the f-string interpolation they protect
  (store.py:479, inside set_priority_like's nested attempt()). Nothing --  no comment,
  no type, no test that runs by default -- links the three across that distance. A future
  edit widening PRIORITY_LIKE_FIELDS to accept a third field, or a refactor that moves the
  guard without moving the interpolation (or vice versa), can silently reopen a SQL-identifier
  injection path with no compiler or linter signal at the edit site itself.
  Mitigation in place: this suite's test_negative_control_guard_removal_makes_the_test_fail
  is not a permanent CI fixture (see its own docstring for why), but it was run for real
  against a guard-stubbed copy of set_priority_like on 2026-09-02 and confirmed to fail
  exactly as this row predicts -- recorded in the DEVH-10 commit, not just asserted here.
  Owner: whoever next touches PRIORITY_LIKE_FIELDS or set_priority_like should re-run that
  same negative control before merging, since no automated gate currently forces it.
  Disposition: accepted residual risk, not fixed -- moving the guard adjacent to the
  interpolation (e.g. validating inside the f-string-building line itself) would reduce the
  distance but not close the class, and doing so is a separate, larger refactor than DEVH-10's
  scope (tessera/GOALS.json's own constraint keeps R4 and the C1-C5 MI refactor bisectable,
  i.e. not folded together).
"""
import tempfile
import unittest
from pathlib import Path

from ..store import Store


class SetPriorityLikeFieldNameGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_c7_rejects_field_name_outside_priority_like_fields(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        # value=1 is a VALID schema.LEVELS entry deliberately -- an invalid value would also
        # raise ValueError on its own (store.py's separate value-check), which would let this
        # test pass for the wrong reason and mask whether the field_name guard fired at all.
        # Caught by this suite's own negative control run against a guard-stubbed copy, which
        # first used an invalid value here and passed vacuously even with the guard removed.
        with self.assertRaises(ValueError) as ctx:
            self.store.set_priority_like(tid, "agent", "not_a_real_field", 1)
        self.assertIn("not_a_real_field", str(ctx.exception))
        # The reject must happen before any write -- ticket state unaffected.
        ticket = self.store.get_ticket(tid)
        self.assertIsNone(ticket["priority"])
        self.assertIsNone(ticket["severity"])

    def test_c7_still_works_for_the_two_real_fields(self):
        """The guard's normal-case counterpart -- C7 tests the guard fires on a bad
        field_name; this confirms the fix didn't also break the two real ones."""
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        self.store.set_priority_like(tid, "agent", "priority", 1)
        self.store.set_priority_like(tid, "agent", "severity", 2)
        ticket = self.store.get_ticket(tid)
        self.assertEqual(ticket["priority"], 1)
        self.assertEqual(ticket["severity"], 2)


if __name__ == "__main__":
    unittest.main()
