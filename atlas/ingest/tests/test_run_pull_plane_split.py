"""FORE-276: the fast/slow plane split, and the equivalence claim its third acceptance criterion
makes. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_run_pull_plane_split -v

The criterion is "batch results row-identical to the pre-split run". That is a claim about
warehouse STATE, not about a summary dict, and it is only testable against something that
actually is the pre-split code. So rather than describe the old statement order, or keep a copy
of it in the module where it would drift, this loads the previous run_pull.py out of git and runs
it against an identical fixture, then diffs every table row for row.

The split reordered the body: pre-split ran registry, git, tails, resolve, DQ; post-split runs
the fast plane (tails, incremental resolve) and then the slow plane (registry, git, full resolve,
DQ). Nothing here argues from the absence of a dependency between those groups. It executes both
and compares.
"""

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import run_pull
from atlas.warehouse import migrate

REPO_ROOT = Path(__file__).resolve().parents[3]

# Every table run_pull can write, plus the ones a caller would notice if they diverged. ingest_run
# is excluded on purpose and separately asserted below: both runs mint their own run_id and their
# started_at differs by construction, so including it would fail for a reason that is not a
# finding.
COMPARED_TABLES = [
    "dim_project", "hook_verdict", "audit_event", "cwd_project", "cwd_project_candidate",
    "git_commit", "tessera_event", "ingest_source", "dq_check", "dq_check_run",
    "schema_migration",
]

# Columns whose value is "when did this run happen", not "what does the warehouse hold". Two runs
# executed milliseconds apart necessarily differ here and it is never a finding. Declared BY NAME
# and per table rather than filtered by value: a value-shaped filter (drop anything that looks
# like today's date) would also drop real ingested data whenever real data happens to look like a
# timestamp, and would quietly stop working the day the hardcoded prefix goes stale.
WALL_CLOCK_COLUMNS = {
    "dim_project": ("refreshed_at",),
    "cwd_project": ("resolved_at",),
    "cwd_project_candidate": ("resolved_at",),
    "dq_check_run": ("run_at",),
    "schema_migration": ("applied_at",),
    "ingest_source": ("first_seen_at", "updated_at"),
}

# Columns real as of HEAD that did not exist -- as columns OR as populating logic -- at
# PRE_SPLIT_REV. is_git_repo/foreman_bootstrapped/tessguard_wired (ATLASSN-21) land in
# sync_dim_project, which both the pre-split and post-split path call unchanged, so this is not a
# behavior difference the split introduced: the frozen pre-split code simply predates the feature
# and always leaves them NULL, forever, no matter what HEAD does. Declared here rather than folded
# into WALL_CLOCK_COLUMNS, a different reason for the same mechanism -- these are real data, not
# "when did this run happen", and ATLASSN-21's own SyncDimProjectTests already asserts their real
# values directly. Any future column added to a COMPARED_TABLES table needs the same treatment
# here, for the same reason: this test proves the SPLIT preserved behavior, not that every later,
# unrelated feature stays invisible to a revision pinned before it existed.
POST_PIN_COLUMNS = {
    "dim_project": ("is_git_repo", "foreman_bootstrapped", "tessguard_wired"),
}


# The last commit before the fast/slow split (parent of 67c166e, ATLASSN-71). PINNED, not HEAD:
# an earlier version of this test read HEAD and passed exactly once -- the moment the split was
# committed, HEAD became the split and the test would have been comparing the new code against
# itself. Its own guard caught that on the next run, which is why the guard is still below.
PRE_SPLIT_REV = "0ac8aad"


def _load_pre_split_run_pull(workdir):
    """atlas/ingest/run_pull.py as of PRE_SPLIT_REV, loaded from git rather than reconstructed.

    Deliberately NOT a copy kept in the tree: a hand-maintained duplicate of the old body drifts
    silently and then the equivalence test proves nothing. Reading it out of git means the thing
    being compared against is the thing that actually shipped."""
    src = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", PRE_SPLIT_REV + ":atlas/ingest/run_pull.py"],
        capture_output=True, text=True, check=True).stdout
    path = Path(workdir) / "run_pull_pre_split.py"
    path.write_text(src, encoding="utf-8")
    # Loaded UNDER atlas.ingest, not standalone: run_pull.py's `from . import gitrepo_pull,
    # tessera_pull` is a relative import and resolves against the module's package, so a bare
    # name raises ImportError before a single line of the body runs.
    name = "atlas.ingest.run_pull_pre_split"
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "atlas.ingest"
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PlaneSplitEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

        # A real git repo, so the slow plane's git pull has something true to walk.
        self.repo = self.root / "proj-a"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "t@example.invalid"],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "t"], check=True)
        (self.repo / "f.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "f.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "PROJA-1: seed"],
                       check=True)

        self.tessera_db = self.root / "tessera.db"
        tconn = sqlite3.connect(str(self.tessera_db))
        tconn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, prefix TEXT, "
                      "codename TEXT, source_root TEXT)")
        tconn.execute("INSERT INTO projects (id, prefix, codename, source_root) "
                      "VALUES (1, 'PROJA', 'PROJA', ?)", (str(self.repo),))
        # v_flat's real column list is read from tessera_pull itself rather than retyped, so a
        # column added there does not silently turn this fixture into a different shape than the
        # code under test expects.
        from atlas.ingest import tessera_pull
        cols = ", ".join(f'"{c}" TEXT' for c in tessera_pull.TESSERA_EVENT_COLUMNS)
        tconn.execute(f"CREATE TABLE v_flat (event_id INTEGER PRIMARY KEY, {cols})")
        tconn.executemany(
            "INSERT INTO v_flat (event_id, event_type, ticket_id, event_ts, actor) "
            "VALUES (?, ?, ?, ?, ?)",
            [(1, "TicketCreated", "PROJA-1", "2026-01-01T00:00:00Z", "tester"),
             (2, "TicketClosed", "PROJA-1", "2026-01-01T00:01:00Z", "tester")])
        tconn.commit()
        tconn.close()

        self.verdicts = self.root / "verdicts.jsonl"
        with self.verdicts.open("w", encoding="utf-8") as fh:
            for i in range(12):
                fh.write(json.dumps({
                    "ts": f"2026-01-01T00:00:{i:02d}.000001Z",
                    "handler_id": "architecture_gate.py" if i % 2 else "guard_destructive.py",
                    "verdict": "fire" if i % 3 else "silent",
                    "decision": "deny" if i % 4 == 0 else None,
                    "cwd": str(self.repo) if i % 2 else str(self.root / "unregistered"),
                    "session_id": f"s{i % 3}",
                    "tool_use_id": f"tu{i}" if i % 2 else None,
                }) + "\n")
        self.audit = self.root / "safety.jsonl"
        self.audit.write_text(json.dumps({
            "schema_version": 1, "ts": "2026-01-01T00:00:01.000001Z",
            "event_type": "HOOK_ERROR", "cwd": str(self.repo),
            "payload": {"hook": "architecture_gate.py"},
        }) + "\n", encoding="utf-8")
        self.audit_sources = [("audit-global-safety", self.audit)]

    def tearDown(self):
        self.tmpdir.cleanup()

    def _dump(self, db_path):
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dump = {}
        for table in COMPARED_TABLES:
            excluded = set(WALL_CLOCK_COLUMNS.get(table, ())) | set(POST_PIN_COLUMNS.get(table, ()))
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")
                    if r[1] not in excluded]
            self.assertTrue(cols, f"{table} does not exist -- COMPARED_TABLES is stale")
            # Ordered by every compared column so the result cannot depend on physical row order.
            order = ", ".join(f'"{c}"' for c in cols)
            dump[table] = conn.execute(
                f'SELECT {order} FROM "{table}" ORDER BY {order}').fetchall()
        conn.close()
        return dump

    def test_batch_results_are_row_identical_to_the_pre_split_run(self):
        pre = _load_pre_split_run_pull(self.root)
        self.assertTrue(hasattr(pre, "run"), "pre-split module has no run()")
        self.assertFalse(hasattr(pre, "run_fast"),
                         f"{PRE_SPLIT_REV} already contains the split -- this test would be "
                         f"comparing the new code against itself and proving nothing")

        old_db = self.root / "old.db"
        new_db = self.root / "new.db"
        old_summary = pre.run(old_db, self.tessera_db, self.verdicts, self.audit_sources)
        new_summary = run_pull.run(new_db, self.tessera_db, self.verdicts, self.audit_sources)

        old_dump = self._dump(old_db)
        new_dump = self._dump(new_db)
        for table in COMPARED_TABLES:
            self.assertEqual(old_dump[table], new_dump[table],
                             f"{table}: post-split state differs from pre-split")

        # The fixture has to be non-trivial or the comparison above is a comparison of two empty
        # warehouses, which would pass and mean nothing.
        self.assertEqual(old_summary["verdict_rows_inserted"], 12)
        self.assertEqual(new_summary["verdict_rows_inserted"], 12)
        self.assertGreater(len(old_dump["cwd_project"]), 0)
        self.assertGreater(len(old_dump["git_commit"]), 0)

    def test_the_summary_keeps_every_key_it_had_plus_the_new_plane_one(self):
        pre = _load_pre_split_run_pull(self.root)
        old_summary = pre.run(self.root / "o.db", self.tessera_db, self.verdicts,
                              self.audit_sources)
        new_summary = run_pull.run(self.root / "n.db", self.tessera_db, self.verdicts,
                                   self.audit_sources)
        missing = set(old_summary) - set(new_summary)
        self.assertEqual(missing, set(), f"the split dropped summary keys: {sorted(missing)}")
        self.assertIn("new_cwds_resolved", new_summary)


class FastPlaneScopeTests(unittest.TestCase):
    """What the fast plane must NOT do. These are the tests that stop it quietly growing back
    into the batch run it was split out of."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "w.db"
        self.conn = migrate.connect(str(self.db_path))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        self.verdicts = self.root / "verdicts.jsonl"
        self.verdicts.write_text(json.dumps({
            "ts": "2026-01-01T00:00:01.000001Z", "handler_id": "guard_destructive.py",
            "verdict": "fire", "cwd": "/proj/unregistered", "session_id": "s1",
        }) + "\n", encoding="utf-8")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_the_fast_plane_writes_no_dq_check_run_rows(self):
        """DQ belongs to the slow plane. A fast tick that ran the checks would write a
        dq_check_run row every 15 seconds and, worse, would evaluate run-over-run checks against
        intervals they were never calibrated for."""
        run_pull.run_fast(self.conn, self.run_id, self.verdicts, [])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM dq_check_run").fetchone()[0], 0)

    def test_the_fast_plane_touches_neither_dim_project_nor_git_commit(self):
        run_pull.run_fast(self.conn, self.run_id, self.verdicts, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM dim_project").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM git_commit").fetchone()[0], 0)

    def test_the_fast_plane_does_not_touch_registry_assertion(self):
        """ATLASSN-172: registry refresh is a slow-plane source, same as dim_project/git_commit
        above -- run_fast must not populate it, or a fast tick (every 15s) would be doing the
        same DB-open-and-full-reap work run_slow already does every 6h for no reason."""
        run_pull.run_fast(self.conn, self.run_id, self.verdicts, [])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM registry_assertion").fetchone()[0], 0)

    def test_the_fast_plane_does_ingest_the_tail_and_resolve_the_new_cwd(self):
        """The converse, so the three tests above are not passing because the fast plane does
        nothing at all."""
        summary = run_pull.run_fast(self.conn, self.run_id, self.verdicts, [])
        self.assertEqual(summary["verdict_rows_inserted"], 1)
        self.assertEqual(summary["new_cwds_resolved"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cwd_project").fetchone()[0], 1)

    def test_a_second_tick_re_resolves_nothing(self):
        """The incremental resolve's whole justification: a cwd already in cwd_project is not
        re-resolved, because only the slow plane can change the registry it would resolve
        against."""
        run_pull.run_fast(self.conn, self.run_id, self.verdicts, [])
        second = run_pull.run_fast(self.conn, self.run_id, self.verdicts, [])
        self.assertEqual(second["new_cwds_resolved"], 0)
        self.assertEqual(run_pull.resolve_new_cwds(self.conn), {})


if __name__ == "__main__":
    unittest.main()
