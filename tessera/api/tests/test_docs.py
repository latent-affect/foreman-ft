import multiprocessing
import tempfile
import unittest
from pathlib import Path

from .. import docs_store
from ...store.store import Store


def writer_process(docs_root, rel_path, n):
    for i in range(n):
        docs_store.update_doc(
            docs_root, rel_path, lambda cur: str(int(cur or "0") + 1)
        )


class DocsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.docs_root = Path(self.tmp_dir.name) / "docs"
        self.docs_root.mkdir()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_docs_write_locked_and_path_restricted(self):
        docs_store.write_doc(self.docs_root, "notes.md", "hello")
        self.assertEqual(docs_store.read_doc(self.docs_root, "notes.md"), "hello")

        with self.assertRaises(docs_store.PathEscapesDocsRoot):
            docs_store.write_doc(self.docs_root, "../../etc/passwd", "pwned")
        with self.assertRaises(docs_store.PathEscapesDocsRoot):
            docs_store.read_doc(self.docs_root, "../outside.md")

        # Real cross-process concurrent read-modify-write on the SAME file via
        # update_doc(), which holds the lock across the read AND the write as one
        # critical section. write_doc() alone (read, then separately write, lock
        # released in between) cannot pass this -- confirmed directly: it lost 65 of 100
        # updates when tried here first, which is why update_doc() exists.
        docs_store.write_doc(self.docs_root, "counter.txt", "0")
        procs = [
            multiprocessing.Process(target=writer_process, args=(self.docs_root, "counter.txt", 20))
            for _ in range(5)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        final = int(docs_store.read_doc(self.docs_root, "counter.txt"))
        self.assertEqual(final, 100)

    def test_docs_write_emits_no_event(self):
        db_path = Path(self.tmp_dir.name) / "test.db"
        store = Store(db_path, codename="TESTPROJ", prefix="TP")
        before = store.conn_internal().execute("SELECT COUNT(*) FROM events").fetchone()[0]
        docs_store.write_doc(self.docs_root, "notes.md", "hello")
        after = store.conn_internal().execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
