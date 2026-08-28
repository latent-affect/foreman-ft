import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from ..store import Store


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        Store(self.db_path, codename="TESTPROJ", prefix="TP").project_metadata()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_concurrent_same_key_one_ticket_counter_not_burned(self):
        results = []
        lock = threading.Lock()

        def worker():
            store = Store(self.db_path)
            tid = store.create_ticket(
                ticket_type="Task", reporter="me", actor="agent",
                idempotency_key="fixed-key-1",
            )
            with lock:
                results.append(tid)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(set(results)), 1)
        store = Store(self.db_path)
        counter = store.conn_internal().execute(
            "SELECT value FROM counters WHERE name='ticket_id'"
        ).fetchone()[0]
        self.assertEqual(counter, 1)
        events = store.conn_internal().execute(
            "SELECT COUNT(*) FROM events WHERE event_type='TicketCreated'"
        ).fetchone()[0]
        self.assertEqual(events, 1)

    def test_catch_site_does_not_swallow_chain_violations(self):
        store = Store(self.db_path)
        # A prev_hash UNIQUE violation, forced directly (not via create_ticket), must not
        # be interpretable as an idempotency conflict by any code path that reuses this
        # exception-discrimination logic.
        conn = store.conn_internal()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash, created_at)"
            " VALUES ('X','a','{}','GENESIS','fixedhash1','t1')"
        )
        conn.execute("COMMIT")
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            conn.execute(
                "INSERT INTO events (event_type, actor, payload, prev_hash, event_hash,"
                " created_at) VALUES ('X','a','{}','GENESIS','fixedhash2','t2')"
            )
        self.assertIn("events.prev_hash", str(ctx.exception))
        self.assertNotIn("idempotency_key", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
