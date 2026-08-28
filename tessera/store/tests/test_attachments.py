import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ..store import Store


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_rollback_leaves_orphan_not_dangling_reference(self):
        tid = self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")
        data = b"some file bytes"
        digest = self.store.add_attachment(tid, "agent", "notes.txt", data)

        blob_path = self.store.blobs_dir / digest
        self.assertTrue(blob_path.exists())
        self.assertEqual(blob_path.read_bytes(), data)
        row = self.store.conn_internal().execute(
            "SELECT sha256 FROM attachments WHERE ticket_id=?", (tid,)
        ).fetchone()
        self.assertEqual(row[0], digest)

        # Force the write transaction to fail AFTER the blob has already been written to
        # disk (blob write happens unconditionally before write_txn_internal opens) but BEFORE the
        # referencing row commits, by making the row-insert step raise.
        data2 = b"other bytes"
        digest2 = hashlib.sha256(data2).hexdigest()

        # append_event_internal runs inside the write transaction, before the attachments row
        # insert -- forcing it to fail exercises the same invariant (blob already on disk
        # unconditionally, transaction rolls back, no row/event ever references the blob).
        with mock.patch.object(
            self.store, "append_event_internal", side_effect=RuntimeError("forced failure")
        ):
            with self.assertRaises(RuntimeError):
                self.store.add_attachment(tid, "agent", "notes2.txt", data2)

        blob2_path = self.store.blobs_dir / digest2
        self.assertTrue(blob2_path.exists(), "blob should still be on disk (written before the txn)")
        row_count = self.store.conn_internal().execute(
            "SELECT COUNT(*) FROM attachments WHERE sha256=?", (digest2,)
        ).fetchone()[0]
        self.assertEqual(row_count, 0, "no row should reference the blob that failed to commit")
        # And the transaction itself is not left open.
        self.assertFalse(self.store.conn_internal().in_transaction)


if __name__ == "__main__":
    unittest.main()
