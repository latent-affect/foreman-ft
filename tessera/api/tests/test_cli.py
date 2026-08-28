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
        # An earlier review thread said "Marked P1" while its column stayed NULL, so what
        # this test checks is the value a triage view would actually read back.
        code, out = self.run_cli_internal([
            "create", "--type", "Bug", "--reporter", "me", "--actor", "agent",
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
        ], first=True)
        tid = json.loads(out)["ticket_id"]

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, out = self.run_cli_internal([
                "set-field", tid, "--actor", "agent", "--name", "priority", "--value", "1",
            ])
        self.assertEqual(code, 1)
        self.assertIn("priority", json.loads(err.getvalue())["error"])

        got = json.loads(self.run_cli_internal(["get", tid])[1])
        self.assertEqual(got["custom_fields"], {})
        self.assertIsNone(got["priority"])

        code, _ = self.run_cli_internal([
            "set-field", tid, "--actor", "agent", "--name", "sprint", "--value", "9",
        ])
        self.assertEqual(code, 0)

    def test_get_compact_drops_null_and_empty_fields_but_keeps_real_ones(self):
        # A minimal ticket has every optional column null and every relation
        # array empty -- exactly the boilerplate compact mode exists to strip.
        code, out = self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
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
            "--priority", "0",
        ], first=True)
        tid = json.loads(out)["ticket_id"]

        got = json.loads(self.run_cli_internal(["get", tid, "--compact"])[1])
        self.assertIn("archived", got)
        self.assertEqual(got["archived"], False)
        self.assertIn("priority", got)
        self.assertEqual(got["priority"], 0)

    def test_list_priority_max_and_severity_max_are_thresholds_not_exact_match(self):
        # "all open S0/S1" is a set, not a single level -- 0 is the highest, so
        # severity_max=1 must include both 0 and 1, and exclude 2+ and NULL.
        for pri, sev in [(0, None), (1, 1), (2, 0), (3, 3), (None, None)]:
            args = ["create", "--type", "Bug", "--reporter", "me", "--actor", "agent"]
            if pri is not None:
                args += ["--priority", str(pri)]
            if sev is not None:
                args += ["--severity", str(sev)]
            code, out = self.run_cli_internal(args, first=not hasattr(self, "_seeded"))
            self._seeded = True

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
            "--summary", "a",
        ], first=True)
        self.run_cli_internal([
            "create", "--type", "Task", "--reporter", "me", "--actor", "agent",
            "--summary", "b",
        ])

        code, out = self.run_cli_internal(["list", "--compact"])
        self.assertEqual(code, 0)
        tickets = json.loads(out)["tickets"]
        self.assertEqual(len(tickets), 2)
        for t in tickets:
            self.assertNotIn("assignee", t)
            self.assertNotIn("parent_id", t)


if __name__ == "__main__":
    unittest.main()
