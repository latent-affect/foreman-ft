import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .. import sse
from ...store.store import Store


class SseTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_sse_sees_cross_process_write(self):
        # Write from a genuinely separate OS process (not this test's own connection),
        # exactly the blind spot ARCHITECTURE-REVIEW.md's pass-1 review found: an
        # in-process-only notification mechanism would never see this.
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from tessera.store.store import Store\n"
            "s = Store(%r)\n"
            "s.create_ticket(ticket_type='Task', reporter='other-proc', actor='other-proc')\n"
        ) % (str(Path(__file__).resolve().parents[3]), str(self.db_path))
        subprocess.run([sys.executable, "-c", code], check=True)

        events = []
        # poll_interval/max_iterations: test-only tuning for speed, not the real 500ms
        # production interval (which is CITED elsewhere in sse.py).
        for chunk in sse.sse_stream(self.store, poll_interval=0.01, max_iterations=3, start_rowid=0):
            # chunks are now "id: {n}\ndata: {...}\n\n" (TESS-31 added the id: line so a
            # reconnecting client's Last-Event-ID can be honored) -- pull out the data:
            # line specifically rather than assuming it's the whole chunk.
            data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
            payload = json.loads(data_line[len("data: "):])
            events.append(payload)

        ticket_created = [e for e in events if e["event_type"] == "TicketCreated"]
        self.assertEqual(len(ticket_created), 1)
        self.assertEqual(ticket_created[0]["actor"], "other-proc")


if __name__ == "__main__":
    unittest.main()
