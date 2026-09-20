import tempfile
import unittest
from pathlib import Path

from ..store import Store


class RecordClaimCommitShaValidationTests(unittest.TestCase):
    """TESS-159: record_claim is the store-boundary gate -- an invalid commit_sha must
    never reach the claims table (and therefore never reach a git call site downstream)."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")
        self.tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_valid_full_sha_is_recorded(self):
        self.store.record_claim(self.tid, "agent", "did the thing", ["a.py"], "a" * 40)
        claim = self.store.get_latest_claim(self.tid)
        self.assertEqual(claim["commit_sha"], "a" * 40)

    def test_valid_short_sha_is_recorded(self):
        self.store.record_claim(self.tid, "agent", "did the thing", ["a.py"], "deadbee")
        claim = self.store.get_latest_claim(self.tid)
        self.assertEqual(claim["commit_sha"], "deadbee")

    def test_argument_injection_shaped_commit_sha_rejected_not_recorded(self):
        # The exact live-reproduced TESS-159 payload: a leading '-' makes git parse this
        # as an option (--output=<path>) rather than a revision, if it ever reached a
        # git subprocess. Must be rejected here, before any write happens.
        with self.assertRaises(ValueError):
            self.store.record_claim(
                self.tid, "agent", "malicious claim", ["a.py"], "--output=/tmp/tess159-pwned"
            )
        self.assertIsNone(self.store.get_latest_claim(self.tid))

    def test_multi_sha_field_rejected_not_recorded(self):
        # The live SEED-17 case on record: two SHAs in one field.
        with self.assertRaises(ValueError):
            self.store.record_claim(
                self.tid, "agent", "two shas", ["a.py"], "e805454, 8989b4b"
            )
        self.assertIsNone(self.store.get_latest_claim(self.tid))

    def test_branch_name_rejected_not_recorded(self):
        with self.assertRaises(ValueError):
            self.store.record_claim(self.tid, "agent", "branch not sha", ["a.py"], "master")
        self.assertIsNone(self.store.get_latest_claim(self.tid))

    def test_empty_commit_sha_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record_claim(self.tid, "agent", "empty sha", ["a.py"], "")


if __name__ == "__main__":
    unittest.main()
