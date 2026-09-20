import json
import tempfile
import unittest
from pathlib import Path

from .. import cli


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp_dir.name) / "test.db")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def run_cli_internal(self, capsys_args, first=False):
        import io
        import contextlib

        prefix_args = ["--codename", "TESTPROJ", "--prefix", "TP"] if first else []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--db", self.db] + prefix_args + capsys_args)
        return code, out.getvalue().strip()

    def test_core_subcommands_roundtrip(self):
        code, out = self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
            "--summary", "Login is broken", "--description", "Nothing happens on click.",
        ], first=True)
        self.assertEqual(code, 0)
        tid = json.loads(out)["ticket_id"]
        self.assertTrue(tid)

        code, out = self.run_cli_internal(["get", tid])
        self.assertEqual(code, 0)
        got = json.loads(out)
        self.assertEqual(got["ticket_id"], tid)
        self.assertEqual(got["summary"], "Login is broken")
        self.assertEqual(got["description"], "Nothing happens on click.")

        code, out = self.run_cli_internal([
            "set-summary", tid, "--actor", "agent", "--summary", "Login silently no-ops",
        ])
        self.assertEqual(code, 0)
        code, out = self.run_cli_internal([
            "set-description", tid, "--actor", "agent", "--description", "Only on Safari.",
        ])
        self.assertEqual(code, 0)
        code, out = self.run_cli_internal(["get", tid])
        got = json.loads(out)
        self.assertEqual(got["summary"], "Login silently no-ops")
        self.assertEqual(got["description"], "Only on Safari.")

        code, out = self.run_cli_internal(["comment", tid, "--actor", "agent", "--body", "hi"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True})

        criteria = json.dumps([
            {"id": "c1", "statement": "works", "verification": "manual", "verifiable": True}
        ])
        code, out = self.run_cli_internal([
            "freeze-criteria", tid, "--actor", "agent", "--criteria", criteria,
        ])
        self.assertEqual(code, 0)
        self.assertIn("criteria_hash", json.loads(out))

        code, out = self.run_cli_internal(["transition", tid, "--actor", "agent", "--status", "in_progress"])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal(["list"])
        self.assertEqual(code, 0)
        ids = [t["ticket_id"] for t in json.loads(out)["tickets"]]
        self.assertIn(tid, ids)

        code, out = self.run_cli_internal([
            "set-field", tid, "--actor", "agent", "--name", "sprint", "--value", "7",
        ])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal([
            "set-reference-docs", tid, "--actor", "agent", "--path", "docs/investigation.md",
        ])
        self.assertEqual(code, 0)

        tid2 = json.loads(self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
        ])[1])["ticket_id"]
        code, out = self.run_cli_internal(["link", tid, "--to", tid2, "--type", "blocks", "--actor", "agent"])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal([
            "claim", tid, "--actor", "agent", "--summary", "did the thing",
            "--file", "tessera/api/cli.py", "--commit", "deadbeef",
        ])
        self.assertEqual(code, 0)
        self.assertIn("event_hash", json.loads(out))

        code, out = self.run_cli_internal(["get-criteria", tid])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["hash_matches"])

        code, out = self.run_cli_internal([
            "watch", tid2, "--watcher", "other-session", "--actor", "agent",
        ])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal(["list-watched", "--watcher", "other-session"])
        self.assertEqual(code, 0)
        watched_ids = [t["ticket_id"] for t in json.loads(out)["tickets"]]
        self.assertEqual(watched_ids, [tid2])

        code, out = self.run_cli_internal([
            "unwatch", tid2, "--watcher", "other-session", "--actor", "agent",
        ])
        self.assertEqual(code, 0)
        code, out = self.run_cli_internal(["list-watched", "--watcher", "other-session"])
        self.assertEqual(json.loads(out)["tickets"], [])

        code, out = self.run_cli_internal(["list-projects"])
        self.assertEqual(code, 0)
        prefixes = [p["prefix"] for p in json.loads(out)["projects"]]
        self.assertEqual(prefixes, ["TP"])

        code, out = self.run_cli_internal([
            "register-project", "--new-codename", "OTHERPROJ", "--new-prefix", "OP",
            "--source-root", "/tmp/otherproj",
        ])
        self.assertEqual(code, 0)
        self.assertIn("project_id", json.loads(out))

        code, out = self.run_cli_internal(["list-projects"])
        registered = {p["prefix"]: p for p in json.loads(out)["projects"]}
        self.assertEqual(registered["OP"]["source_root"], "/tmp/otherproj")

        code, out = self.run_cli_internal([
            "hotlist-create", "standup-2026-08-16", "--actor", "agent",
        ])
        self.assertEqual(code, 0)
        self.assertIn("hotlist_id", json.loads(out))

        code, out = self.run_cli_internal([
            "hotlist-add", "standup-2026-08-16", tid, "--actor", "agent", "--note", "mention",
        ])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal(["hotlist-show", "standup-2026-08-16"])
        self.assertEqual(code, 0)
        shown = json.loads(out)
        self.assertEqual([i["ticket_id"] for i in shown["items"]], [tid])
        self.assertEqual(shown["items"][0]["note"], "mention")

        code, out = self.run_cli_internal(["hotlist-list"])
        self.assertEqual(code, 0)
        names = [h["name"] for h in json.loads(out)["hotlists"]]
        self.assertIn("standup-2026-08-16", names)

        code, out = self.run_cli_internal([
            "hotlist-remove", "standup-2026-08-16", tid, "--actor", "agent",
        ])
        self.assertEqual(code, 0)
        code, out = self.run_cli_internal(["hotlist-show", "standup-2026-08-16"])
        self.assertEqual(json.loads(out)["items"], [])

    def test_comment_code_snippet_flag_roundtrip(self):
        code, out = self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
            "--summary", "x",
        ], first=True)
        tid = json.loads(out)["ticket_id"]

        code, out = self.run_cli_internal([
            "comment", tid, "--actor", "agent", "--body", "fixed it",
            "--code-snippet", "def f():\n    return 1",
        ])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal(["comment", tid, "--actor", "agent", "--body", "no code here"])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal(["get", tid])
        comments = json.loads(out)["comments"]
        self.assertEqual(comments[0]["code_snippet"], "def f():\n    return 1")
        self.assertIsNone(comments[1]["code_snippet"])

    def test_reassign_project_mints_ticket_in_target_and_closes_source(self):
        code, out = self.run_cli_internal([
            "create", "--type", "Bug", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
            "--summary", "placeholder bug", "--description", "wrong project for now",
        ], first=True)
        self.assertEqual(code, 0)
        tid = json.loads(out)["ticket_id"]

        code, out = self.run_cli_internal([
            "register-project", "--new-codename", "TARGET", "--new-prefix", "TGT",
        ])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal([
            "reassign-project", tid, "--project", "TGT", "--actor", "agent",
        ])
        self.assertEqual(code, 0)
        new_tid = json.loads(out)["ticket_id"]
        self.assertTrue(new_tid.startswith("TGT-"))

        code, out = self.run_cli_internal(["get", new_tid])
        got = json.loads(out)
        self.assertEqual(got["project_prefix"], "TGT")
        self.assertEqual(got["summary"], "placeholder bug")

        code, out = self.run_cli_internal(["get", tid])
        self.assertEqual(json.loads(out)["status"], "closed")

        code, out = self.run_cli_internal([
            "reassign-project", new_tid, "--project", "TGT", "--actor", "agent",
        ])
        self.assertEqual(code, 1)

    def test_set_priority_and_severity_move_the_real_column(self):
        # TESS-98. FORE-23's thread said "Marked P1" while its column stayed NULL, so what
        # this test checks is the value a triage view would actually read back.
        code, out = self.run_cli_internal([
            "create", "--type", "Bug", "--reporter", "me", "--actor", "agent",
            "--priority", "4", "--severity", "4",
        ], first=True)
        tid = json.loads(out)["ticket_id"]

        code, out = self.run_cli_internal(["set-priority", tid, "--actor", "agent", "--value", "1"])
        self.assertEqual(code, 0)
        code, out = self.run_cli_internal(["set-severity", tid, "--actor", "agent", "--value", "0"])
        self.assertEqual(code, 0)

        got = json.loads(self.run_cli_internal(["get", tid])[1])
        self.assertEqual(got["priority"], 1)
        self.assertEqual(got["severity"], 0)
        self.assertEqual(got["custom_fields"], {})

        code, _ = self.run_cli_internal(["set-priority", tid, "--actor", "agent", "--clear"])
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(self.run_cli_internal(["get", tid])[1])["priority"])

    def test_set_field_refuses_a_first_class_name_cleanly(self):
        # Two assertions, and the second is the point: the refusal has to arrive as a
        # clean nonzero exit with a JSON error, not as a traceback. A traceback reads as
        # "the tool broke" and invites a retry with the same wrong command.
        import contextlib
        import io

        code, out = self.run_cli_internal([
            "create", "--type", "Bug", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
        ], first=True)
        tid = json.loads(out)["ticket_id"]

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, out = self.run_cli_internal([
                "set-field", tid, "--actor", "agent", "--name", "priority", "--value", "1",
            ])
        self.assertEqual(code, 1)
        self.assertIn("priority", self.error_json_from_stderr_internal(err.getvalue())["error"])

        got = json.loads(self.run_cli_internal(["get", tid])[1])
        self.assertEqual(got["custom_fields"], {})
        # TESS-127: priority is now required at creation (was 2 above), so the refusal's
        # point -- set-field must not have shadow-written through custom_fields -- is that
        # the real column is untouched, not that it's null.
        self.assertEqual(got["priority"], 2)

        code, _ = self.run_cli_internal([
            "set-field", tid, "--actor", "agent", "--name", "sprint", "--value", "9",
        ])
        self.assertEqual(code, 0)

    def test_get_compact_drops_null_and_empty_fields_but_keeps_real_ones(self):
        # TESS-119. A minimal ticket has every optional column null and every relation
        # array empty -- exactly the boilerplate compact mode exists to strip.
        code, out = self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
            "--summary", "x",
        ], first=True)
        tid = json.loads(out)["ticket_id"]

        code, out = self.run_cli_internal(["get", tid])
        self.assertEqual(code, 0)
        full = json.loads(out)
        self.assertIn("assignee", full)
        self.assertIsNone(full["assignee"])
        self.assertEqual(full["reference_docs"], [])

        code, out = self.run_cli_internal(["get", tid, "--compact"])
        self.assertEqual(code, 0)
        got = json.loads(out)
        self.assertNotIn("assignee", got)
        self.assertNotIn("reference_docs", got)
        self.assertNotIn("blocked_by", got)
        self.assertNotIn("custom_fields", got)
        # real content survives compaction untouched
        self.assertEqual(got["ticket_id"], tid)
        self.assertEqual(got["summary"], "x")
        self.assertEqual(got["status"], "open")
        self.assertLess(len(out), len(json.dumps(full)))

    def test_get_compact_keeps_falsy_but_meaningful_values(self):
        # A compact mode that strips on falsiness rather than on None/empty-container would
        # silently eat real signal -- archived=False and priority=0 (P0, the highest
        # priority) both have to survive.
        code, out = self.run_cli_internal([
            "create", "--type", "Bug", "--reporter", "me", "--actor", "agent",
            "--priority", "0", "--severity", "0",
        ], first=True)
        tid = json.loads(out)["ticket_id"]

        got = json.loads(self.run_cli_internal(["get", tid, "--compact"])[1])
        self.assertIn("archived", got)
        self.assertEqual(got["archived"], False)
        self.assertIn("priority", got)
        self.assertEqual(got["priority"], 0)

    def test_list_priority_max_and_severity_max_are_thresholds_not_exact_match(self):
        # TESS-120. "all open S0/S1" is a set, not a single level -- 0 is the highest, so
        # severity_max=1 must include both 0 and 1, and exclude 2+ and NULL.
        #
        # TESS-127: create now requires --priority/--severity (a real value, not silently
        # null), so a NULL cell in this table is produced by creating with a placeholder
        # value and then clearing it -- same end state (column is NULL), not creation-time
        # omission.
        for pri, sev in [(0, None), (1, 1), (2, 0), (3, 3), (None, None)]:
            args = [
                "create", "--type", "Bug", "--reporter", "me", "--actor", "agent",
                "--priority", str(pri if pri is not None else 4),
                "--severity", str(sev if sev is not None else 4),
            ]
            code, out = self.run_cli_internal(args, first=not hasattr(self, "_seeded"))
            self._seeded = True
            tid = json.loads(out)["ticket_id"]
            if pri is None:
                self.run_cli_internal(["set-priority", tid, "--actor", "agent", "--clear"])
            if sev is None:
                self.run_cli_internal(["set-severity", tid, "--actor", "agent", "--clear"])

        code, out = self.run_cli_internal(["list", "--priority-max", "1"])
        self.assertEqual(code, 0)
        priorities = sorted(t["priority"] for t in json.loads(out)["tickets"])
        self.assertEqual(priorities, [0, 1])  # NOT the None- or 2-priority tickets

        code, out = self.run_cli_internal(["list", "--severity-max", "1"])
        self.assertEqual(code, 0)
        severities = sorted(t["severity"] for t in json.loads(out)["tickets"])
        self.assertEqual(severities, [0, 1])

        code, out = self.run_cli_internal(["list", "--priority-max", "4"])
        priorities = [t["priority"] for t in json.loads(out)["tickets"]]
        self.assertNotIn(None, priorities)  # NULL never matches a threshold, even the loosest one

    def test_list_compact_applies_per_ticket(self):
        self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
            "--summary", "a",
        ], first=True)
        self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
            "--priority", "2", "--severity", "2",
            "--summary", "b",
        ])

        code, out = self.run_cli_internal(["list", "--compact"])
        self.assertEqual(code, 0)
        tickets = json.loads(out)["tickets"]
        self.assertEqual(len(tickets), 2)
        for t in tickets:
            self.assertNotIn("assignee", t)
            self.assertNotIn("parent_id", t)

    # ---- TESS-174: project lifecycle verbs --------------------------------

    def test_project_lifecycle_verbs_roundtrip(self):
        code, out = self.run_cli_internal(["list-projects"], first=True)
        self.assertEqual(code, 0)
        self.assertEqual([p["status"] for p in json.loads(out)["projects"]], ["active"])

        code, out = self.run_cli_internal([
            "register-project", "--new-codename", "MOVER", "--new-prefix", "MOV",
            "--source-root", "/Users/m5/dev/old-home",
        ])
        self.assertEqual(code, 0)

        code, out = self.run_cli_internal([
            "set-project-source-root", "MOV", "/Users/m5/dev/new-home",
            "--actor", "agent", "--note", "relocated",
        ])
        self.assertEqual(code, 0)
        moved = json.loads(out)
        self.assertTrue(moved["changed"])
        self.assertEqual(moved["source_root"], "/Users/m5/dev/new-home")

        code, out = self.run_cli_internal([
            "archive-project", "MOV", "--actor", "agent", "--note", "done with it",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "archived")

        code, out = self.run_cli_internal(["list-projects", "--status", "active"])
        self.assertEqual(code, 0)
        self.assertEqual([p["prefix"] for p in json.loads(out)["projects"]], ["TP"])

        code, out = self.run_cli_internal(["list-projects", "--status", "archived"])
        self.assertEqual(code, 0)
        self.assertEqual([p["prefix"] for p in json.loads(out)["projects"]], ["MOV"])

        code, out = self.run_cli_internal(["unarchive-project", "MOV", "--actor", "agent"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "active")

    def test_project_lifecycle_verbs_do_not_clobber_the_top_level_prefix_flag(self):
        """The collision register-project's own comment documents, asserted for the three
        verbs added after it: a subparser argument sharing a dest with the top-level
        --prefix/--codename silently overwrites them in the single shared Namespace, and
        main() builds the Store from those BEFORE dispatch runs. That really did make
        register-project a silent no-op once. These verbs take a POSITIONAL
        project_prefix, so args.prefix must survive untouched."""
        parser = cli.build_parser()
        for verb, extra in (
            ("archive-project", []),
            ("unarchive-project", []),
            ("set-project-source-root", ["/Users/m5/dev/somewhere"]),
        ):
            args = parser.parse_args(
                ["--db", self.db, "--codename", "TESTPROJ", "--prefix", "TP",
                 verb, "MOV", *extra, "--actor", "agent"]
            )
            self.assertEqual(args.prefix, "TP", f"{verb} clobbered the top-level --prefix")
            self.assertEqual(args.codename, "TESTPROJ", f"{verb} clobbered --codename")
            self.assertEqual(args.project_prefix, "MOV")

    def error_json_from_stderr_internal(self, raw):
        """Parse the CLI's error JSON out of a captured stderr buffer.

        Never json.loads() the raw buffer: under the unittest runner (which enables warnings
        that `python3 -c` does not) a ResourceWarning about an unclosed sqlite connection
        lands on redirected stderr AHEAD of the JSON, so the parse dies at char 0. In a full
        suite run the once-per-location warning filter has already fired and the buffer is
        clean, so a raw parse passes in-suite and errors standalone -- which is exactly the
        defect filed as TESS-179, reproduced here by direct execution of both paths, not by
        reading. The CLI writes its error as the last line, so take that."""
        lines = [line for line in raw.splitlines() if line.strip()]
        self.assertTrue(lines, "expected something on stderr, got an empty buffer")
        return json.loads(lines[-1])

    def test_relative_source_root_is_a_clean_cli_error_not_a_traceback(self):
        import contextlib
        import io

        self.run_cli_internal(["list-projects"], first=True)
        err = io.StringIO()
        out = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = cli.main(["--db", self.db, "set-project-source-root", "TP",
                             "dev/relative", "--actor", "agent"])
        self.assertEqual(code, 1)
        self.assertIn("absolute", self.error_json_from_stderr_internal(err.getvalue())["error"])

    def test_provenance_error_for_an_archived_assignee_names_a_reachable_remedy(self):
        """TESS-174 review fallout. known_prefixes is built from the FULL registry while cwd
        resolution now EXCLUDES archived projects, so for a ticket assigned to an archived
        project the two can never agree. The generic message then told the operator to run
        the command from the assignee's own project root while they were standing in
        exactly that root, and claimed the cwd resolved to "no registered project" about a
        project that is registered. Refusing is right; that explanation was not."""
        import subprocess
        import tempfile
        from pathlib import Path as P

        from ...store.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            root = P(tmp)
            repo = root / "tomb-repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)

            store = Store(root / "t.db", codename="HOST", prefix="HOST")
            store.register_project("TOMB", "TMB", source_root=str(repo))
            tid = store.create_ticket(ticket_type="Task", reporter="jon", actor="agent",
                                      project="TMB")
            store.set_assignee(tid, "agent", "TMB")

            self.assertIsNone(
                cli.assignee_provenance_error(store, tid, cwd=str(repo)),
                "a live project commenting from its own root must pass",
            )

            store.archive_project("TMB", "agent")
            err = cli.assignee_provenance_error(store, tid, cwd=str(repo))
            self.assertIsNotNone(err, "an archived assignee must still be refused")
            self.assertIn("is archived", err)
            self.assertIn("unarchive-project TMB", err)
            self.assertNotIn(
                "run this from TMB's own project root", err,
                "they ARE in that root -- this was the misleading half",
            )

            store.unarchive_project("TMB", "agent")
            self.assertIsNone(cli.assignee_provenance_error(store, tid, cwd=str(repo)),
                              "the named remedy must actually clear the refusal")

if __name__ == "__main__":
    unittest.main()
