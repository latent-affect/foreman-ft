"""ATLASSN-130 / GOALS.json C6 -- the availability matrix. From the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_availability_matrix -v

Criteria E1-E6 frozen on the ticket (criteria_hash sha256:1d7a39fd).

This is the fail-closed battery. F1 -- a gate that looks alive and does nothing -- is the
project's signature failure mode, and every fault below is built for real on a real store file:
a real exclusive lock, real garbage bytes, a real hot WAL in a real read-only directory. Nothing
here is simulated by patching the thing under test to return what the probe wants to see.

The one exception is the catch-all read-error probe, which injects an opener that raises. That
branch exists for errors nobody enumerated, so by construction no specific fault produces it;
the injection is named as such rather than dressed up as a real condition.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.registry import availability, status
from atlas.registry.tests import failopen_mutant, helpers

NOW = helpers.BASE_NOW
EDGES = ["e:one", "e:two"]


class FaultBuilder:
    """Builds each availability fault on a real store. Returns (store_path, extra_kwargs)."""

    def __init__(self, test_case):
        self.test = test_case
        self.directory = tempfile.TemporaryDirectory()
        test_case.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def healthy(self, name="healthy.db"):
        """`name` matters: several faults are built in one directory within a single test, and a
        builder that always used one filename would trip create_store's own
        refusing-to-overwrite guard. That guard caught this fixture bug on the first run."""
        path = self.root / name
        connection = helpers.make_store(path)
        helpers.insert(connection, "e:one", helpers.at_delta(1))
        helpers.insert(connection, "e:two", helpers.at_delta(2))
        connection.execute("PRAGMA wal_checkpoint(FULL)")
        connection.close()
        return path, {}

    def absent(self):
        return self.root / "no-store-here.db", {}

    def corrupt(self):
        path, _ = self.healthy("corrupt.db")
        path.write_bytes(b"this is not a database at all" * 40)
        return path, {}

    def schema_version(self):
        path, _ = self.healthy("schema-version.db")
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA user_version = 99")
        connection.close()
        return path, {}

    def locked(self):
        path, _ = self.healthy("locked.db")
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("PRAGMA locking_mode = EXCLUSIVE")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE assertion SET component = 'held' WHERE edge_id = 'e:one'")
        self.test.addCleanup(holder.close)
        # Budget of zero: the point is "past budget", and waiting out a real lock would make the
        # suite slow without making the assertion any stronger.
        return path, {"busy_budget_ms": 0}

    def recovery_needed(self):
        """A hot WAL the read-only opener cannot replay: a store copied mid-log into a
        directory nothing may write to. Measured to surface as SQLITE_CANTOPEN on this sqlite
        (3.53.4), which availability.classify maps to recovery-needed when a -wal is present."""
        source = self.root / "src"
        source.mkdir()
        path = source / "registry.db"
        connection = helpers.make_store(path)
        helpers.insert(connection, "e:one", helpers.at_delta(1))
        wal = Path(str(path) + "-wal")
        self.test.assertTrue(wal.exists() and wal.stat().st_size > 0,
                             "fixture precondition: the WAL must be hot, or this probe is testing "
                             "something other than what it names")

        target = self.root / "readonly"
        target.mkdir()
        shutil.copy2(path, target / "registry.db")
        shutil.copy2(wal, target / "registry.db-wal")
        connection.close()

        for name in ("registry.db", "registry.db-wal"):
            os.chmod(target / name, 0o444)
        os.chmod(target, 0o555)

        def restore():
            os.chmod(target, 0o755)
            for name in ("registry.db", "registry.db-wal"):
                candidate = target / name
                if candidate.exists():
                    os.chmod(candidate, 0o644)
        self.test.addCleanup(restore)
        return target / "registry.db", {}

    def read_error(self):
        path, _ = self.healthy("read-error.db")

        def exploding_opener(store_path, busy_budget_ms):
            raise RuntimeError("an error nobody enumerated")

        return path, {"connect": exploding_opener}

    def malformed_row(self):
        path = self.root / "malformed.db"
        connection = helpers.make_store(path)
        helpers.insert(connection, "e:one", helpers.at_delta(1))
        helpers.insert(connection, "e:two", helpers.at_delta(1))
        connection.execute("UPDATE assertion SET verified_at = 'not-a-time' WHERE edge_id='e:one'")
        connection.execute("PRAGMA wal_checkpoint(FULL)")
        connection.close()
        return path, {}


# name -> (builder attribute, expected store-level sub-reason)
STORE_LEVEL_FAULTS = (
    ("absent", "absent", availability.SUB_ABSENT),
    ("locked", "locked", availability.SUB_LOCKED),
    ("corrupt", "corrupt", availability.SUB_CORRUPT),
    ("recovery-needed", "recovery_needed", availability.SUB_RECOVERY_NEEDED),
    ("schema-version", "schema_version", availability.SUB_SCHEMA_VERSION),
    ("read-error", "read_error", availability.SUB_READ_ERROR),
)


class TestStoreLevelFaults(unittest.TestCase):
    """E1, E3 (store-level half), E6 -- every fault denies every edge, with its own reason."""

    def setUp(self):
        self.faults = FaultBuilder(self)

    def test_each_store_level_fault_denies_every_edge_with_its_own_sub_reason(self):
        for name, builder, expected in STORE_LEVEL_FAULTS:
            with self.subTest(fault=name):
                path, kwargs = getattr(self.faults, builder)()
                results = availability.read_guarded(path, EDGES, NOW, **kwargs)

                self.assertEqual(set(results), set(EDGES),
                                 "a store-level fault must still answer for every requested edge")
                for edge_id, result in results.items():
                    self.assertEqual(result.decision, status.DENY)
                    self.assertEqual(result.status, status.REGISTRY_UNAVAILABLE)
                    self.assertEqual(result.sub_reason, expected)

    def test_the_six_faults_produce_six_distinct_sub_reasons(self):
        # Control on the probes themselves: if two faults collapsed into one reason, the loop
        # above would still pass while the audit distinction C6 requires had been lost.
        observed = set()
        for name, builder, _ in STORE_LEVEL_FAULTS:
            path, kwargs = getattr(self.faults, builder)()
            results = availability.read_guarded(path, EDGES, NOW, **kwargs)
            observed.add(next(iter(results.values())).sub_reason)
        self.assertEqual(len(observed), 6, f"faults collapsed into {sorted(observed)}")
        self.assertEqual(observed, set(availability.STORE_LEVEL_SUB_REASONS))

    def test_no_store_level_fault_ever_allows(self):
        for name, builder, _ in STORE_LEVEL_FAULTS:
            with self.subTest(fault=name):
                path, kwargs = getattr(self.faults, builder)()
                results = availability.read_guarded(path, EDGES, NOW, **kwargs)
                self.assertFalse(any(r.decision == status.ALLOW for r in results.values()))


class TestHealthyStoreAndGranularity(unittest.TestCase):
    """E3 (per-assertion half), E4 -- the battery is not satisfiable by denying everything."""

    def setUp(self):
        self.faults = FaultBuilder(self)

    def test_a_healthy_store_allows(self):
        path, kwargs = self.faults.healthy()
        results = availability.read_guarded(path, EDGES, NOW, **kwargs)
        for edge_id, result in results.items():
            self.assertEqual(result.decision, status.ALLOW, f"{edge_id} should be served")
            self.assertEqual(result.status, status.ACTIVE)

    def test_a_malformed_row_denies_only_its_own_edge(self):
        path, kwargs = self.faults.malformed_row()
        results = availability.read_guarded(path, EDGES, NOW, **kwargs)
        self.assertEqual(results["e:one"].decision, status.DENY)
        self.assertEqual(results["e:one"].sub_reason, status.SUB_MALFORMED)
        self.assertEqual(results["e:two"].decision, status.ALLOW,
                         "a malformed sibling must not take down a healthy assertion")

    def test_per_assertion_denial_is_not_registry_unavailable(self):
        # The two granularities must stay distinguishable: a malformed row is not a broken store.
        path, kwargs = self.faults.malformed_row()
        results = availability.read_guarded(path, EDGES, NOW, **kwargs)
        self.assertNotEqual(results["e:one"].status, status.REGISTRY_UNAVAILABLE)


class TestUnavailableIsNotNeverRegistered(unittest.TestCase):
    """E2 -- 'nobody could ask' is not 'nobody verified this'."""

    def setUp(self):
        self.faults = FaultBuilder(self)

    def test_absent_store_and_unknown_edge_are_different_statuses(self):
        absent_path, _ = self.faults.absent()
        absent = availability.read_one(absent_path, "e:whatever", NOW)

        healthy_path, _ = self.faults.healthy()
        unknown = availability.read_one(healthy_path, "e:never-seen", NOW)

        self.assertEqual(absent.status, status.REGISTRY_UNAVAILABLE)
        self.assertEqual(unknown.status, status.NEVER_REGISTERED)
        self.assertNotEqual(absent.status, unknown.status)
        # Both deny, so a test asserting only the decision would not tell them apart.
        self.assertEqual(absent.decision, status.DENY)
        self.assertEqual(unknown.decision, status.DENY)


class TestReadPathNeverWrites(unittest.TestCase):
    """E6 -- a read reports a fault, it never heals one."""

    def setUp(self):
        self.faults = FaultBuilder(self)

    def test_reading_an_absent_store_does_not_create_it(self):
        path, _ = self.faults.absent()
        availability.read_guarded(path, EDGES, NOW)
        self.assertFalse(path.exists(), "a read created the store it was supposed to report missing")

    def test_reading_a_corrupt_store_does_not_repair_it(self):
        path, _ = self.faults.corrupt()
        before = path.read_bytes()
        availability.read_guarded(path, EDGES, NOW)
        self.assertEqual(path.read_bytes(), before)

    def test_the_opener_really_is_read_only(self):
        """Directly assert the property, because the two tests above do not.

        Found by mutation: switching the opener from mode=ro to mode=rwc left the whole suite
        green. read_guarded checks path.is_file() before opening anything, so the absent probe
        short-circuits and never exercises the opener's mode at all -- it was proving the
        early-return, not the read-only open. The only way to assert read-only is to open the
        store and try to write through it.
        """
        path, _ = self.faults.healthy()
        connection = availability.open_readonly(path, availability.DEFAULT_BUSY_BUDGET_MS)
        self.addCleanup(connection.close)
        with self.assertRaises(sqlite3.OperationalError) as caught:
            connection.execute("UPDATE assertion SET component = 'rewritten'")
        self.assertIn("readonly", str(caught.exception).lower())

    def test_a_read_through_the_guarded_path_leaves_a_healthy_store_unchanged(self):
        path, _ = self.faults.healthy()
        before = path.read_bytes()
        availability.read_guarded(path, EDGES, NOW)
        self.assertEqual(path.read_bytes(), before)


class TestFailOpenMutantIsCaught(unittest.TestCase):
    """E5 -- the permanent fixture. The battery must keep proving it can tell the difference.

    If this class ever passes trivially, the fault probes above have stopped meaning anything.
    """

    def setUp(self):
        self.faults = FaultBuilder(self)

    def mutant_decisions(self):
        """Which store-level faults does the mutant serve ALLOW on?"""
        allowed = []
        for name, builder, _ in STORE_LEVEL_FAULTS:
            path, kwargs = getattr(self.faults, builder)()
            results = failopen_mutant.read_guarded(path, EDGES, NOW, **kwargs)
            if any(r.decision == status.ALLOW for r in results.values()):
                allowed.append(name)
        return allowed

    def test_the_battery_rejects_the_fail_open_mutant(self):
        allowed = self.mutant_decisions()
        self.assertTrue(allowed, "the mutant passed every fault probe -- the battery proves nothing")
        self.assertEqual(len(allowed), len(STORE_LEVEL_FAULTS),
                         f"expected the mutant to fail open on every store-level fault, got {allowed}")

    def test_absence_and_error_are_where_fail_open_hides(self):
        # The toy model's actual finding, encoded: a fail-open variant was caught on the
        # never-registered and error probes. These two carry the weight.
        allowed = self.mutant_decisions()
        self.assertIn("absent", allowed)
        self.assertIn("read-error", allowed)

    def test_the_mutant_passes_an_expiry_only_probe(self):
        # And this is WHY the two above carry it. An expired assertion on a HEALTHY store is
        # ordinary status computation, which the mutant delegates faithfully -- so a battery that
        # only probed expiry would give this fail-open implementation a clean bill of health.
        path = self.faults.root / "expiring.db"
        connection = helpers.make_store(path)
        helpers.insert(connection, "e:old", helpers.at_delta(helpers.LEASE_S + 1))
        connection.execute("PRAGMA wal_checkpoint(FULL)")
        connection.close()

        real = availability.read_one(path, "e:old", NOW)
        mutant = failopen_mutant.read_guarded(path, ["e:old"], NOW)["e:old"]

        self.assertEqual(real.decision, status.DENY)
        self.assertEqual(mutant.decision, status.DENY,
                         "if the mutant failed this too, it would be caught by the easy probe "
                         "and this fixture would not be testing what it claims to test")
        self.assertEqual(real.status, mutant.status)

    def test_the_mutant_and_the_real_path_agree_on_a_healthy_store(self):
        # Another way the fixture stays honest: it must be indistinguishable when nothing is
        # wrong. A mutant that broke the happy path would be caught by every probe and would
        # prove nothing about fail-open detection specifically.
        path, kwargs = self.faults.healthy()
        real = availability.read_guarded(path, EDGES, NOW, **kwargs)
        mutant = failopen_mutant.read_guarded(path, EDGES, NOW, **kwargs)
        self.assertEqual(real, mutant)


if __name__ == "__main__":
    unittest.main()
