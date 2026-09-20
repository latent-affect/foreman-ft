"""TESS-174: a project registration's own lifecycle -- archive/unarchive, and following a
source_root that moved.

Before this, register_project() was the ONLY write verb a project row had. Four dead
registrations (WIDG/MIDB/F9FR/TCHK, all naming scratchpad directories deleted 2026-08-16)
sat in the live registry indistinguishable from live projects, and a project whose checkout
genuinely moved had no path short of archive-and-re-register, which throws away its id, its
ticket_id counter and the continuity of every ticket already minted under it.

The tests that matter most here are not the happy paths. They are:
  - test_direct_update_bypassing_the_verb_is_caught_by_rebuild -- the negative control for
    the whole event-sourced design. It deliberately corrupts the db the way a careless
    UPDATE would and requires the projection check to notice. Verified to discriminate by
    stubbing out the replay handlers and watching only this test go red.
  - test_archived_project_stops_resolving_to_its_source_root -- the behavioural payoff. An
    archived registration that still resolved would make the flag decorative.
  - test_the_blind_spot_is_per_column_not_per_project -- the limit of the check above, which
    is per COLUMN, not per project. A project with a source_root event but no status event
    has an unverified status, and status is what gates resolution.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ...tessguard import project_resolve
from .. import schema
from ..exceptions import UnknownProjectError
from ..store import Store


class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.db_path = self.root / "test.db"
        self.store = Store(self.db_path, codename="SOURCE", prefix="SRC")

    def tearDown(self):
        self.tmp_dir.cleanup()

    # ---- the status field ------------------------------------------------

    def test_a_new_registration_is_active(self):
        self.assertEqual(self.store.get_project("SRC")["status"], schema.PROJECT_STATUS_ACTIVE)
        self.assertEqual(
            [p["status"] for p in self.store.list_projects()],
            [schema.PROJECT_STATUS_ACTIVE],
        )

    def test_archive_marks_the_row_without_removing_it(self):
        s = self.store
        result = s.archive_project("SRC", "agent", note="scratchpad dir deleted")
        self.assertTrue(result["changed"])
        self.assertEqual(result["status"], schema.PROJECT_STATUS_ARCHIVED)

        # The row, and everything hanging off it, survives -- this is a mark, not a delete.
        self.assertEqual(len(s.list_projects()), 1)
        self.assertEqual(s.get_project("SRC")["codename"], "SOURCE")

    def test_archived_project_keeps_its_tickets_addressable(self):
        s = self.store
        tid = s.create_ticket(ticket_type="Task", reporter="jon", actor="agent", summary="live work")
        s.archive_project("SRC", "agent")
        self.assertEqual(s.get_ticket(tid)["summary"], "live work")

    def test_unarchive_returns_the_row_to_active(self):
        s = self.store
        s.archive_project("SRC", "agent")
        result = s.unarchive_project("SRC", "agent", note="turns out it is still live")
        self.assertTrue(result["changed"])
        self.assertEqual(result["status"], schema.PROJECT_STATUS_ACTIVE)

    def test_list_projects_can_filter_by_status(self):
        s = self.store
        s.register_project("TARGET", "TGT")
        s.archive_project("TGT", "agent")

        self.assertEqual(
            [p["prefix"] for p in s.list_projects()], ["SRC", "TGT"],
            "the unfiltered listing must still return the WHOLE registry -- silently "
            "hiding archived rows would break every caller that audits the registry",
        )
        self.assertEqual(
            [p["prefix"] for p in s.list_projects(status=schema.PROJECT_STATUS_ACTIVE)], ["SRC"]
        )
        self.assertEqual(
            [p["prefix"] for p in s.list_projects(status=schema.PROJECT_STATUS_ARCHIVED)], ["TGT"]
        )

    def test_unknown_status_rejected_on_set_and_on_filter(self):
        s = self.store
        with self.assertRaises(ValueError):
            s.set_project_status("SRC", "agent", "retired")
        with self.assertRaises(ValueError):
            s.list_projects(status="retired")

    def test_unknown_project_rejected(self):
        s = self.store
        with self.assertRaises(UnknownProjectError):
            s.archive_project("NOPE", "agent")
        with self.assertRaises(UnknownProjectError):
            s.set_project_source_root("NOPE", "agent", "/tmp/somewhere")

    # ---- no-op writes emit no event --------------------------------------

    def test_re_archiving_an_archived_project_is_a_no_op_with_no_event(self):
        s = self.store
        s.archive_project("SRC", "agent")
        before = self.event_types_internal()

        again = s.archive_project("SRC", "agent")
        self.assertFalse(again["changed"])
        self.assertEqual(again["status"], schema.PROJECT_STATUS_ARCHIVED)
        self.assertEqual(
            self.event_types_internal(), before,
            "a re-run that changed nothing must not write a change into the audit log",
        )

    def test_setting_the_same_source_root_is_a_no_op_with_no_event(self):
        s = self.store
        s.set_project_source_root("SRC", "agent", "/Users/m5/dev/moved-here")
        before = self.event_types_internal()

        again = s.set_project_source_root("SRC", "agent", "/Users/m5/dev/moved-here")
        self.assertFalse(again["changed"])
        self.assertEqual(self.event_types_internal(), before)

    def test_trailing_slash_is_normalized_not_treated_as_a_new_value(self):
        s = self.store
        s.set_project_source_root("SRC", "agent", "/Users/m5/dev/moved-here")
        before = self.event_types_internal()

        again = s.set_project_source_root("SRC", "agent", "/Users/m5/dev/moved-here/")
        self.assertFalse(again["changed"], "a trailing slash is the same directory")
        self.assertEqual(again["source_root"], "/Users/m5/dev/moved-here")
        self.assertEqual(self.event_types_internal(), before)

    # ---- source_root ------------------------------------------------------

    def test_source_root_follows_a_move_keeping_project_identity(self):
        s = self.store
        s.register_project("MOVER", "MOV", source_root="/Users/m5/dev/old-home")
        before = s.get_project("MOV")
        tid = s.create_ticket(ticket_type="Task", reporter="jon", actor="agent", project="MOV")

        result = s.set_project_source_root("MOV", "agent", "/Users/m5/dev/new-home",
                                           note="relocated during the DEVORG reorg")
        self.assertTrue(result["changed"])
        self.assertEqual(result["source_root"], "/Users/m5/dev/new-home")

        # The whole point of this verb over archive-and-re-register: identity survives.
        self.assertEqual(result["id"], before["id"])
        self.assertEqual(result["prefix"], before["prefix"])
        self.assertEqual(result["codename"], before["codename"])
        self.assertEqual(result["created_at"], before["created_at"])
        self.assertEqual(s.get_ticket(tid)["project_prefix"], "MOV")

        # And the counter keeps counting from where it was, rather than restarting.
        next_tid = s.create_ticket(ticket_type="Task", reporter="jon", actor="agent", project="MOV")
        self.assertEqual(next_tid, "MOV-2")

    def test_source_root_can_be_cleared_to_none(self):
        s = self.store
        s.register_project("MOVER", "MOV", source_root="/Users/m5/dev/old-home")
        result = s.set_project_source_root("MOV", "agent", None)
        self.assertTrue(result["changed"])
        self.assertIsNone(result["source_root"])

    def test_relative_source_root_refused(self):
        s = self.store
        with self.assertRaises(ValueError) as caught:
            s.set_project_source_root("SRC", "agent", "dev/relative-path")
        self.assertIn("absolute", str(caught.exception))
        self.assertIsNone(
            s.get_project("SRC")["source_root"],
            "a refused write must leave the row untouched",
        )

    # ---- events -----------------------------------------------------------

    def test_both_verbs_emit_real_attributed_events(self):
        s = self.store
        s.archive_project("SRC", "jon-via-agent", note="dead registration")
        s.set_project_source_root("SRC", "jon-via-agent", "/Users/m5/dev/somewhere")

        rows = self.conn_internal().execute(
            "SELECT event_type, actor, payload, project_id FROM events"
            " WHERE event_type IN ('ProjectStatusChanged','ProjectSourceRootChanged')"
            " ORDER BY id"
        ).fetchall()
        self.assertEqual([r[0] for r in rows],
                         ["ProjectStatusChanged", "ProjectSourceRootChanged"])
        self.assertEqual([r[1] for r in rows], ["jon-via-agent", "jon-via-agent"])

        import json
        status_payload = json.loads(rows[0][2])
        self.assertEqual(status_payload["project"], "SRC")
        self.assertEqual(status_payload["old_status"], schema.PROJECT_STATUS_ACTIVE)
        self.assertEqual(status_payload["new_status"], schema.PROJECT_STATUS_ARCHIVED)
        self.assertEqual(status_payload["note"], "dead registration")

        root_payload = json.loads(rows[1][2])
        self.assertIsNone(root_payload["old_source_root"])
        self.assertEqual(root_payload["new_source_root"], "/Users/m5/dev/somewhere")

        project_id = s.get_project("SRC")["id"]
        self.assertEqual([r[3] for r in rows], [project_id, project_id],
                         "both events must be attributed to the project they changed")

    def test_hash_chain_stays_intact_across_project_events(self):
        s = self.store
        s.create_ticket(ticket_type="Task", reporter="jon", actor="agent")
        s.archive_project("SRC", "agent")
        s.set_project_source_root("SRC", "agent", "/Users/m5/dev/somewhere")
        self.assertEqual(
            s.verify_chain(),
            {"roots": 1, "tips": 1, "orphans": 0, "hash_mismatches": 0},
        )

    # ---- projection verification -----------------------------------------

    def test_rebuild_projection_matches_live_after_lifecycle_changes(self):
        s = self.store
        s.register_project("MOVER", "MOV", source_root="/Users/m5/dev/old-home")
        s.create_ticket(ticket_type="Task", reporter="jon", actor="agent", project="MOV")
        s.set_project_source_root("MOV", "agent", "/Users/m5/dev/new-home")
        s.set_project_source_root("MOV", "agent", "/Users/m5/dev/newer-home")
        s.archive_project("SRC", "agent")
        s.unarchive_project("SRC", "agent")
        s.archive_project("SRC", "agent")

        live = s.live_projection()
        rebuilt = s.rebuild_projection()
        self.assertEqual(set(live.keys()), set(rebuilt.keys()))
        for table in live:
            self.assertEqual(
                sorted(live[table]), sorted(rebuilt[table]),
                f"table {table!r} diverged between live and rebuilt projections",
            )

    def test_direct_update_bypassing_the_verb_is_caught_by_rebuild(self):
        """The negative control for the entire event-sourced design.

        Every rebuild-vs-live assertion above is only worth something if the check can
        actually FAIL, so this one makes exactly the edit the verbs exist to prevent -- a
        hand-written UPDATE, no event -- and requires the projection check to catch it.

        Proven to discriminate, not assumed to: stubbing the ProjectStatusChanged /
        ProjectSourceRootChanged replay handlers in store.py to no-ops makes THIS test fail
        and leaves the other 22 green. That is what earns it. (It also killed an earlier
        version of the rebuild seeding: seeding the shadow's mutable project columns from
        each project's first recorded OLD value looked like it was what made this check
        non-vacuous, and sabotaging it changed nothing here -- because replay-forward lands
        on the same final value regardless of the seed. The detection was always in the
        replay handlers. The seeding elaboration was deleted.)
        """
        s = self.store
        s.set_project_source_root("SRC", "agent", "/Users/m5/dev/recorded-home")
        s.archive_project("SRC", "agent")

        # A careless hand-edit: change the live row, write no event.
        raw = sqlite3.connect(str(self.db_path))
        raw.execute(
            "UPDATE projects SET source_root=?, status=? WHERE prefix=?",
            ("/Users/m5/dev/never-recorded", schema.PROJECT_STATUS_ACTIVE, "SRC"),
        )
        raw.commit()
        raw.close()

        reopened = Store(self.db_path)
        live = reopened.live_projection()["projects"]
        rebuilt = reopened.rebuild_projection()["projects"]
        self.assertNotEqual(
            sorted(live), sorted(rebuilt),
            "a direct UPDATE that bypassed the lifecycle verbs must show up as a "
            "projection mismatch, not pass silently",
        )

    def test_project_with_no_lifecycle_event_still_reconciles(self):
        """One half of the honest limit: a project nobody has ever archived or moved has no
        lifecycle event to replay, so its mutable columns are as unverified as its identity
        columns are, and it must reconcile rather than be reported as a mismatch."""
        s = self.store
        s.register_project("UNTOUCHED", "UNT", source_root="/Users/m5/dev/untouched")
        live = s.live_projection()
        rebuilt = s.rebuild_projection()
        self.assertEqual(sorted(live["projects"]), sorted(rebuilt["projects"]))

    def test_the_blind_spot_is_per_column_not_per_project(self):
        """The other half, and the sharper one -- ticket-system-ed's review caught the
        original wording understating it as per-PROJECT.

        Verification is per COLUMN: a column is only checked if that column has an event to
        replay. A project can therefore be half-verified. Here MOV has a
        ProjectSourceRootChanged event but no ProjectStatusChanged event, so its
        source_root is covered and its status is not -- and status is the column
        project_resolve.py gates on, so this is the half with real consequences: someone
        can hand-flip a project between active and archived, changing which repos resolve,
        and the projection check stays green.

        Asserted rather than described, so that if the coverage is ever extended this test
        fails and forces the docs to be updated with it."""
        s = self.store
        s.register_project("MOVER", "MOV", source_root="/Users/m5/dev/old-home")
        s.set_project_source_root("MOV", "agent", "/Users/m5/dev/new-home")

        raw = sqlite3.connect(str(self.db_path))
        raw.execute(
            "UPDATE projects SET status=? WHERE prefix=?",
            (schema.PROJECT_STATUS_ARCHIVED, "MOV"),
        )
        raw.commit()
        raw.close()

        reopened = Store(self.db_path)
        self.assertEqual(
            sorted(reopened.live_projection()["projects"]),
            sorted(reopened.rebuild_projection()["projects"]),
            "KNOWN GAP: status has no event on this project, so a hand-edit to it is not "
            "caught. If this assertion ever fails, per-column coverage has changed and the "
            "limit documented in rebuild_projection() and in this module's docstring needs "
            "updating to match.",
        )

    def test_source_root_hand_edit_IS_caught_when_that_column_has_an_event(self):
        """The contrast that makes the test above a statement about column coverage rather
        than about hand-edits being undetectable in general."""
        s = self.store
        s.register_project("MOVER", "MOV", source_root="/Users/m5/dev/old-home")
        s.set_project_source_root("MOV", "agent", "/Users/m5/dev/new-home")

        raw = sqlite3.connect(str(self.db_path))
        raw.execute(
            "UPDATE projects SET source_root=? WHERE prefix=?",
            ("/Users/m5/dev/never-recorded", "MOV"),
        )
        raw.commit()
        raw.close()

        reopened = Store(self.db_path)
        self.assertNotEqual(
            sorted(reopened.live_projection()["projects"]),
            sorted(reopened.rebuild_projection()["projects"]),
        )

    # ---- resolution behaviour --------------------------------------------

    def test_archived_project_stops_resolving_to_its_source_root(self):
        """The behavioural payoff of the flag. TESS-174's own words for the dead
        WIDG/MIDB/F9FR/TCHK registrations: they "should never resolve to a real directory
        again"."""
        s = self.store
        repo = self.root / "some-repo"
        repo.mkdir()
        s.register_project("DEADPROJ", "DEAD", source_root=str(repo))

        self.assertEqual(
            [p["prefix"] for p in project_resolve.resolve_projects_for_repo(repo, s)],
            ["DEAD"],
        )

        s.archive_project("DEAD", "agent", note="scratchpad dir is gone")
        self.assertEqual(
            project_resolve.resolve_projects_for_repo(repo, s), [],
            "an archived registration must stop claiming a filesystem root",
        )
        self.assertEqual(
            [p["prefix"] for p in
             project_resolve.resolve_projects_for_repo(repo, s, include_archived=True)],
            ["DEAD"],
            "a caller auditing the registry itself can still see it",
        )

    def test_unarchiving_restores_resolution(self):
        s = self.store
        repo = self.root / "revived-repo"
        repo.mkdir()
        s.register_project("REVIVED", "REVI", source_root=str(repo))
        s.archive_project("REVI", "agent")
        s.unarchive_project("REVI", "agent")
        self.assertEqual(
            [p["prefix"] for p in project_resolve.resolve_projects_for_repo(repo, s)],
            ["REVI"],
        )

    def test_moved_source_root_resolves_at_the_new_location_only(self):
        s = self.store
        old_home = self.root / "old-home"
        new_home = self.root / "new-home"
        old_home.mkdir()
        new_home.mkdir()
        s.register_project("MOVER", "MOV", source_root=str(old_home))

        s.set_project_source_root("MOV", "agent", str(new_home))
        self.assertEqual(
            [p["prefix"] for p in project_resolve.resolve_projects_for_repo(new_home, s)],
            ["MOV"],
        )
        self.assertEqual(
            project_resolve.resolve_projects_for_repo(old_home, s), [],
            "the registration must not keep claiming the directory it left",
        )

    def test_gitgate_names_a_reachable_remedy_for_an_archived_repo(self):
        """TESS-174 review fallout. Archiving a project makes gitgate refuse commits in its
        repo -- correct, fail-closed, and the point of archiving. But the refusal used to
        say "no TESSERA project registered for this repo" about a repo that IS registered,
        and advise `tessera register-project`, which is idempotent by prefix and therefore
        a no-op on an existing archived row. The operator was told to run a command that
        provably changes nothing, then blocked again.

        This asserts the remedy is reachable, not merely that the gate blocks."""
        from ...tessguard import gitgate

        s = self.store
        repo = self.root / "archived-repo"
        repo.mkdir()
        s.register_project("TOMB", "TMB", source_root=str(repo))
        s.archive_project("TMB", "agent", note="retired")

        result = gitgate.check_gate(str(repo), s)
        self.assertFalse(result.passed, "an archived repo must still be gated")
        self.assertIn("ARCHIVED", result.reason)
        self.assertIn("unarchive-project TMB", result.reason)
        self.assertNotIn(
            "no TESSERA project registered", result.reason,
            "the repo IS registered -- saying otherwise sends the operator hunting for a "
            "registration that already exists",
        )

        # And the named remedy actually clears THIS block, which is the whole claim. It
        # does not necessarily make the gate pass outright -- check_gate has a further,
        # unrelated recent-activity check -- so asserting `passed` here would be asserting
        # something the remedy never promised. What must be true is that the repo stops
        # being refused for being archived and the gate moves on to its real question.
        s.unarchive_project("TMB", "agent")
        after = gitgate.check_gate(str(repo), s)
        self.assertNotIn("ARCHIVED", after.reason or "")
        self.assertIn("no real TESSERA activity", after.reason or "")

    def test_unregistered_repo_keeps_the_original_message(self):
        """The archived-specific branch must not swallow the genuinely-unregistered case."""
        from ...tessguard import gitgate

        repo = self.root / "stranger"
        repo.mkdir()
        result = gitgate.check_gate(str(repo), self.store)
        self.assertFalse(result.passed)
        self.assertIn("no TESSERA project registered", result.reason)
        self.assertNotIn("ARCHIVED", result.reason)

    # ---- migration --------------------------------------------------------

    def test_status_column_is_added_to_a_database_that_predates_it(self):
        """ensure_project_status_column() against a projects table built WITHOUT the
        column -- the real shape of data/tessera.db before this change. CREATE TABLE IF
        NOT EXISTS is a no-op against an existing table, column differences included, so
        without this migration a live database would keep a status-less projects table and
        every read above would fail on it."""
        legacy_path = self.root / "legacy.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " codename TEXT NOT NULL, prefix TEXT NOT NULL UNIQUE, source_root TEXT,"
            " created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO projects (codename, prefix, source_root, created_at)"
            " VALUES ('LEGACY','LEG','/Users/m5/dev/legacy','2026-01-01T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        migrated = Store(legacy_path)
        row = migrated.get_project("LEG")
        self.assertEqual(row["status"], schema.PROJECT_STATUS_ACTIVE,
                         "an existing registration nobody has marked stale is active")
        self.assertEqual(row["source_root"], "/Users/m5/dev/legacy")

        # And it is a working row afterwards, not just a readable one.
        self.assertTrue(migrated.archive_project("LEG", "agent")["changed"])

    def test_migration_is_idempotent(self):
        conn = self.conn_internal()
        schema.ensure_project_status_column(conn)
        schema.ensure_project_status_column(conn)
        self.assertEqual(self.store.get_project("SRC")["status"], schema.PROJECT_STATUS_ACTIVE)

    # ---- helpers ----------------------------------------------------------

    def conn_internal(self):
        return self.store.conn_internal()

    def event_types_internal(self):
        return [
            r[0] for r in self.conn_internal().execute(
                "SELECT event_type FROM events ORDER BY id"
            ).fetchall()
        ]


if __name__ == "__main__":
    unittest.main()
