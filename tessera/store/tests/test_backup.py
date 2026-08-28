import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from ..store import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_vacuum_into_consistent_under_concurrent_writers(self):
        stop = threading.Event()
        errors = []

        def hammer():
            store = Store(self.db_path)
            while not stop.is_set():
                try:
                    store.create_ticket(ticket_type="Task", reporter="me", actor="writer")
                except Exception as exc:  # pragma: no cover - only fires on a real bug
                    errors.append(exc)

        writers = [threading.Thread(target=hammer) for _ in range(3)]
        for t in writers:
            t.start()

        backup_path = Path(self.tmp_dir.name) / "backup.db"
        self.store.backup(backup_path)

        stop.set()
        for t in writers:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue(backup_path.exists())

        conn = sqlite3.connect(str(backup_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(result, "ok")
        # The snapshot must itself be a coherent point-in-time state: every ticket that
        # exists in the snapshot's projection also has a matching TicketCreated event in
        # the snapshot's event log, and vice versa -- VACUUM INTO must not have captured
        # torn state mid-write.
        ticket_count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        event_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='TicketCreated'"
        ).fetchone()[0]
        self.assertEqual(ticket_count, event_count)
        conn.close()


if __name__ == "__main__":
    unittest.main()
