import tempfile
import threading
import unittest
from pathlib import Path

from ..exceptions import UnknownProjectError
from ..store import Store


class ProjectMetadataTests(unittest.TestCase):
    def test_second_instance_different_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_a = Path(tmp) / "a.db"
            db_b = Path(tmp) / "b.db"
            store_a = Store(db_a, codename="TESSERA", prefix="TESS")
            store_b = Store(db_b, codename="OTHERPROJ", prefix="OP")

            tid_a = store_a.create_ticket(ticket_type="Task", reporter="me", actor="agent")
            tid_b = store_b.create_ticket(ticket_type="Task", reporter="me", actor="agent")

            self.assertTrue(tid_a.startswith("TESS-"))
            self.assertTrue(tid_b.startswith("OP-"))
            self.assertEqual(store_a.project_metadata()["codename"], "TESSERA")
            self.assertEqual(store_b.project_metadata()["codename"], "OTHERPROJ")

    def test_reopen_without_codename_uses_existing_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "c.db"
            Store(db, codename="TESSERA", prefix="TESS")
            reopened = Store(db)  # no codename/prefix -- must reuse existing
            self.assertEqual(reopened.project_metadata()["prefix"], "TESS")

    def test_first_open_without_codename_is_a_valid_empty_multi_project_store(self):
        # Superseded assumption (pre-multi-project): a fresh db with no codename/prefix
        # given used to be an error, because a store with zero projects had no sensible
        # meaning. Multi-project support (TESS-8) makes "zero registered projects, wait
        # for register_project() calls" a real, intentional state -- Store(db) alone no
        # longer raises. project_metadata() (the single-project backward-compat accessor)
        # still raises when there's no default project to describe, just via
        # UnknownProjectError instead of a bare ValueError.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "d.db"
            store = Store(db)
            self.assertEqual(store.list_projects(), [])
            with self.assertRaises(UnknownProjectError):
                store.project_metadata()

    def test_concurrent_first_open_does_not_crash(self):
        # 10 callers construct Store() against the SAME new db file for the first time
        # at once. Two separate real races live here, both found by code review and
        # direct repro, not by reading the code: (1) the loser of the project_metadata
        # INSERT would hit a real PRIMARY KEY conflict if not handled -- that INSERT
        # was originally not wrapped in the retry/IntegrityError-tolerant pattern every
        # other store write uses; (2) PRAGMA journal_mode=WAL, run once per connection
        # during bootstrap, can itself raise "database is locked" immediately (not a
        # busy_timeout expiry -- confirmed via direct repro: 0.0004s, before any write-
        # transaction code was even reached) when several threads race to create the
        # database file's very first WAL/SHM sidecars at once. Flaked ~1/6 runs before
        # the second fix; 100/100 direct-repro trials clean after it.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "race.db"
            errors = []
            barrier = threading.Barrier(10)

            def open_store():
                barrier.wait()
                try:
                    Store(db, codename="RACETEST", prefix="RT")
                except Exception as exc:  # noqa: BLE001 - any exception here is the bug
                    errors.append(exc)

            threads = [threading.Thread(target=open_store) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [], f"concurrent first-open crashed: {errors}")
            final = Store(db)
            self.assertEqual(final.project_metadata()["prefix"], "RT")


if __name__ == "__main__":
    unittest.main()
