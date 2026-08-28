import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ..store import Store


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_rejected_write_rolls_back_and_does_not_stall_next_writer(self):
        # A write that raises inside the transaction body must not leave the lock held.
        with self.assertRaises(RuntimeError):
            with self.store.write_txn_internal() as conn:
                conn.execute(
                    "INSERT INTO tickets (ticket_id, project_id, type, status, reporter,"
                    " created_at, updated_at, reference_docs, archived) VALUES ('TP-1',1,"
                    "'Task','open','me','t','t','[]',0)"
                )
                raise RuntimeError("simulated rejection")

        # Next writer must not be stalled by the rejected transaction's lock.
        started = time.time()
        with self.store.write_txn_internal() as conn:
            conn.execute(
                "INSERT INTO tickets (ticket_id, project_id, type, status, reporter,"
                " created_at, updated_at, reference_docs, archived) VALUES ('TP-2',1,"
                "'Task','open','me','t','t','[]',0)"
            )
        elapsed = time.time() - started
        self.assertLess(elapsed, 1.0)
        # And the rejected insert must genuinely not have committed.
        row = self.store.conn_internal().execute(
            "SELECT ticket_id FROM tickets WHERE ticket_id='TP-1'"
        ).fetchone()
        self.assertIsNone(row)

    def test_write_txn_not_reentrant_no_leak(self):
        with self.assertRaises(sqlite3.OperationalError):
            with self.store.write_txn_internal() as conn:
                with self.store.write_txn_internal():
                    pass
        conn = self.store.conn_internal()
        self.assertFalse(conn.in_transaction)
        # Confirm no lock leak: a fresh connection can write immediately.
        started = time.time()
        with self.store.write_txn_internal() as c:
            c.execute(
                "INSERT INTO tickets (ticket_id, project_id, type, status, reporter,"
                " created_at, updated_at, reference_docs, archived) VALUES ('TP-3',1,"
                "'Task','open','me','t','t','[]',0)"
            )
        self.assertLess(time.time() - started, 1.0)

    def test_per_thread_connections_and_pragmas(self):
        conn_ids = {}
        pragmas = {}

        def grab():
            conn = self.store.conn_internal()
            conn_ids[threading.get_ident()] = id(conn)
            pragmas[threading.get_ident()] = (
                conn.execute("PRAGMA journal_mode").fetchone()[0],
                conn.execute("PRAGMA foreign_keys").fetchone()[0],
            )

        threads = [threading.Thread(target=grab) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(conn_ids.values())), 4)
        for mode, fk in pragmas.values():
            self.assertEqual(mode.lower(), "wal")
            self.assertEqual(fk, 1)


if __name__ == "__main__":
    unittest.main()
