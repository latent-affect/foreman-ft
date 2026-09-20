"""ATLASSN-27 / ATLASSN-28: the live plane's ingest source. Fixture tests for the mechanics, plus
real-corpus assertions for the two claims that only real data can settle -- that the cited
reference implementation's discovery shape is wrong, and that a second consecutive pull inserts
nothing.

Run from the repo root:
    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_subagent_pull -v

FIX 2026-09-20 (Bob's real-test finding, TESSERA comment 3800 on ATLASSN-188): MappingTests calls
subagent_pull.map_transcript_line directly and used to unpack a 3-tuple (calls, results, texts).
ATLASSN-104/REQ-64 (ARCHITECTURE.md section 40) adds a fourth, structural-only return value,
blocks -- this file's own call sites are updated to unpack all four, matching subagent_pull.py's
own call site (already fixed in that ticket's proposal)."""

import json
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import subagent_pull
from atlas.ingest.transcript_parse import LEDGER_ONLY_DENY_HANDLERS, PROSE_DENY_HANDLERS
from atlas.warehouse import migrate

REAL_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def writeTranscript(session_dir, agent_filename, records, meta=None, workflow=None):
    """Builds a real on-disk transcript in the real layout. Returns its path."""
    subagents = session_dir / "subagents"
    if workflow:
        subagents = subagents / "workflows" / workflow
    subagents.mkdir(parents=True, exist_ok=True)
    path = subagents / agent_filename
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    if meta is not None:
        (subagents / (path.stem + ".meta.json")).write_text(json.dumps(meta), encoding="utf-8")
    return path


def assistantRecord(ts, agent_id, session_id, blocks, cwd="/proj", uuid="u1"):
    return {
        "type": "assistant", "timestamp": ts, "agentId": agent_id, "sessionId": session_id,
        "cwd": cwd, "gitBranch": "main", "uuid": uuid,
        "message": {"content": blocks},
    }


def toolUse(tool_use_id, name, tool_input):
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}


def toolResult(tool_use_id, is_error=None, content="ok"):
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error is not None:
        block["is_error"] = is_error
    return block


class DiscoveryTests(unittest.TestCase):
    """C21."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.session = self.root / "-Users-m5-proj" / "sess-1"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_finds_flat_and_workflow_nested_transcripts(self):
        flat = writeTranscript(self.session, "agent-aaa.jsonl", [])
        nested = writeTranscript(self.session, "agent-bbb.jsonl", [], workflow="wf_123")
        found = subagent_pull.discover_transcripts(self.root)
        self.assertIn(flat, found)
        self.assertIn(nested, found, "a Workflow-dispatched transcript must not be missed")

    def test_never_returns_a_workflow_journal(self):
        writeTranscript(self.session, "agent-aaa.jsonl", [])
        journal = self.session / "subagents" / "workflows" / "wf_123" / "journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text('{"x": 1}\n', encoding="utf-8")
        found = subagent_pull.discover_transcripts(self.root)
        self.assertNotIn(journal, found)
        self.assertTrue(all(p.name != "journal.jsonl" for p in found))

    def test_missing_projects_root_is_an_empty_list_not_a_crash(self):
        self.assertEqual(subagent_pull.discover_transcripts(self.root / "nope"), [])

    def test_agent_kind_classification(self):
        self.assertEqual(subagent_pull.classify_agent_kind("agent-abc123.jsonl"), "agent")
        self.assertEqual(
            subagent_pull.classify_agent_kind("agent-aside_question-abc.jsonl"), "aside_question")
        self.assertEqual(
            subagent_pull.classify_agent_kind("agent-acompact-abc.jsonl"), "compact")

    def test_path_identity_includes_workflow_id_when_nested(self):
        nested = writeTranscript(self.session, "agent-bbb.jsonl", [], workflow="wf_123")
        identity = subagent_pull.parse_transcript_path(nested, self.root)
        self.assertEqual(identity["workflow_id"], "wf_123")
        self.assertEqual(identity["parent_session_id"], "sess-1")
        self.assertEqual(identity["agent_id"], "bbb")

        flat = writeTranscript(self.session, "agent-aaa.jsonl", [])
        self.assertIsNone(subagent_pull.parse_transcript_path(flat, self.root)["workflow_id"])


class RealCorpusDiscoveryTests(unittest.TestCase):
    """C21 against the live corpus -- the claim that the reference implementation cited in
    ATLASSN-27 is wrong is only settleable on real data."""

    @unittest.skipUnless(REAL_PROJECTS_ROOT.is_dir(), "no real ~/.claude/projects on this machine")
    def test_recursive_discovery_finds_more_than_the_reference_flat_glob(self):
        recursive = subagent_pull.discover_transcripts(REAL_PROJECTS_ROOT)
        flat = []
        for project_dir in REAL_PROJECTS_ROOT.iterdir():
            if not project_dir.is_dir():
                continue
            for session_dir in project_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                subagents = session_dir / "subagents"
                if subagents.is_dir():
                    flat.extend(subagents.glob("*.jsonl"))
        self.assertGreater(
            len(recursive), len(flat),
            "recursive discovery must find the Workflow-dispatched transcripts the flat glob "
            "shape of the cited reference implementation misses",
        )
        self.assertTrue(all(p.name != "journal.jsonl" for p in recursive))
        self.assertTrue(all(p.name.startswith("agent-") for p in recursive))


class SidecarTests(unittest.TestCase):
    """C23."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.session = self.root / "-Users-m5-proj" / "sess-1"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_present_sidecar_yields_the_parents_dispatch_id(self):
        meta = {"agentType": "clint-eastwood", "description": "review the thing",
                "toolUseId": "toolu_PARENT", "spawnDepth": 1}
        path = writeTranscript(self.session, "agent-aaa.jsonl", [], meta=meta)
        state, got = subagent_pull.read_sidecar(path)
        self.assertEqual(state, "present")
        self.assertEqual(got["toolUseId"], "toolu_PARENT")

    def test_absent_sidecar_is_a_recorded_state(self):
        path = writeTranscript(self.session, "agent-aside_question-x.jsonl", [])
        state, got = subagent_pull.read_sidecar(path)
        self.assertEqual(state, "absent")
        self.assertEqual(got, {})

    def test_unreadable_sidecar_is_distinct_from_absent(self):
        path = writeTranscript(self.session, "agent-aaa.jsonl", [])
        (path.parent / (path.stem + ".meta.json")).write_text("{not json", encoding="utf-8")
        state, _ = subagent_pull.read_sidecar(path)
        self.assertEqual(state, "unreadable")


class MappingTests(unittest.TestCase):
    """C22 -- the one-line-many-blocks case, which is the shape a busy subagent produces."""

    def test_one_line_with_three_tool_use_blocks_yields_three_rows(self):
        record = assistantRecord("2026-01-01T00:00:00Z", "a1", "s1", [
            {"type": "text", "text": "thinking"},
            toolUse("toolu_1", "Bash", {"command": "ls"}),
            toolUse("toolu_2", "Read", {"file_path": "/a"}),
            toolUse("toolu_3", "Bash", {"command": "pwd"}),
        ])
        calls, results, texts, blocks = subagent_pull.map_transcript_line(record, 100)
        self.assertEqual(len(calls), 3)
        self.assertEqual(results, [])
        self.assertEqual([c["block_index"] for c in calls], [1, 2, 3])
        self.assertEqual({c["byte_offset"] for c in calls}, {100})
        self.assertEqual([c["tool_name"] for c in calls], ["Bash", "Read", "Bash"])

    def test_tool_result_is_error_is_tri_state(self):
        for raw, expected in ((True, 1), (False, 0), (None, None)):
            record = {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                      "message": {"content": [toolResult("toolu_1", is_error=raw)]}}
            calls, results, texts, blocks = subagent_pull.map_transcript_line(record, 0)
            self.assertEqual(results[0]["is_error"], expected,
                             f"is_error={raw!r} must map to {expected!r}, not be coerced")

    def test_a_record_with_no_content_list_yields_nothing(self):
        self.assertEqual(
            subagent_pull.map_transcript_line({"type": "summary"}, 0), ([], [], [], []))
        self.assertEqual(
            subagent_pull.map_transcript_line({"message": {"content": "plain string"}}, 0),
            ([], [], [], []))

    def test_partial_trailing_line_is_not_consumed(self):
        complete, consumed = subagent_pull.split_complete_lines(b'{"a":1}\n{"b":2')
        self.assertEqual(complete, [b'{"a":1}'])
        self.assertEqual(consumed, 8)
        complete, consumed = subagent_pull.split_complete_lines(b'{"a":1}\n')
        self.assertEqual(complete, [b'{"a":1}'])
        self.assertEqual(consumed, 8)


class DenyExtractionTests(unittest.TestCase):
    """ATLASSN-32. The rubric needs the deny's named remedy, and is_error does not carry it --
    measured, 0 of 8 real hook-deny bodies had is_error truthy."""

    def test_each_real_handler_message_shape_is_recognised(self):
        # These are verbatim opening strings from the real corpus, one per emitting handler.
        for body in (
            "Foreman: ARCHITECTURE.md exists but no ARCHITECTURE-REVIEW.md review event is "
            "recorded at /Users/m5/dev/hyphy. Run the Clint Eastwood architecture review "
            "before writing implementation files.",
            "Foreman: component 'frontend' has no GOALS.json at /Users/m5/dev/bay-area/web/"
            "GOALS.json. Run foreman:design-and-scope for this component before writing "
            "implementation files.",
            "Blocked destructive command: SQL DROP DATABASE/TABLE/SCHEMA. If this is genuinely "
            "intended, run it yourself in a terminal.",
            "Blocked recursive-force delete targeting a home/root/system path. Narrow the "
            "target or run it yourself.",
            "This edit adds a dependency, playwright, whose documentation has not been read in "
            "this session. The code-safety:library-scout skill covers this.",
        ):
            self.assertIsNotNone(
                subagent_pull.extract_deny_text(body),
                f"a real deny message shape was not recognised: {body[:60]!r}")

    def test_the_whole_body_is_the_remedy(self):
        body = ("Foreman: component 'x' has no GOALS.json. Run foreman:design-and-scope for "
                "this component before writing implementation files.")
        self.assertEqual(subagent_pull.extract_deny_text(body), body)

    def test_a_deny_wrapped_in_a_json_content_list_is_still_recognised(self):
        """The same deny arrives as a bare string in some records and inside a one-element list
        in others; both must resolve to the same remedy."""
        self.assertIsNotNone(subagent_pull.extract_deny_text(
            '["Foreman: ARCHITECTURE.md exists but no review is recorded."]'))

    def test_ordinary_output_is_not_a_deny(self):
        for body in ("", "total 48\ndrwxr-xr-x  12 m5  staff", "OK", None,
                     "All tests passed", '{"result": "ok"}'):
            self.assertIsNone(subagent_pull.extract_deny_text(body), repr(body))

    def test_prose_that_merely_quotes_a_deny_is_not_a_deny(self):
        """An agent pasting a deny into a report is not a denied call. The prefix must anchor at
        the start of the body, which is what distinguishes the two."""
        self.assertIsNone(subagent_pull.extract_deny_text(
            "I hit this earlier: Foreman: ARCHITECTURE.md exists but no review is recorded. "
            "So I routed to the architecture skill instead."))


class RealCorpusDenySignatureTests(unittest.TestCase):
    """Two independent invariants against the real, live warehouse -- deliberately kept as two,
    not collapsed into one (ATLASSN-194's follow-up, comment thread with Bob 2026-09-20).

    1. Every ledger-confirmed deny must carry a named remedy via EITHER route (deny_text IS NOT
       NULL) -- the actual correctness contract every consumer of deny_text depends on, and the
       same invariant test_session_pull.py's sibling test already checks via
       v_transcript_deny_join's COALESCE(sr.deny_text, mr.deny_text). ATLASSN-194 found and fixed
       a real gap here: a cross-ingest-pipeline race (ledger_deny_ids()'s one-shot-per-pull
       snapshot missing a verdict not yet inserted), not a prefix-shape miss -- see
       reconcile_deny_text()'s own docstring in subagent_pull.py for the full incident.

    2. Separately, DENY_BODY_PREFIXES' own coverage must not silently erode for a handler that is
       not a DECLARED exception (PROSE_DENY_HANDLERS or LEDGER_ONLY_DENY_HANDLERS, both in
       transcript_parse.py). Collapsing to invariant 1 alone would stop testing this: the
       ledger-fallback route resolves deny_text for ANY confirmed deny regardless of its message
       shape, so a genuinely new handler whose shape escapes DENY_BODY_PREFIXES would satisfy
       invariant 1 forever and never be noticed. That erosion matters independently of deny_text,
       because the prefix route is the backup for a deny the LEDGER itself misses (FORE-190) --
       if its coverage quietly narrows to nothing, that backup stops existing for anyone who
       needed it. Measured directly against the real warehouse before writing this: write_gate.py
       (287 rows) and agent_dispatch_gate.py (1 row) are the only handlers with 0% prefix
       coverage in the current corpus; every other handler present (architecture_gate.py,
       concept_gate.py, goals_freeze_gate.py, guard_destructive.py, preflight_blocking_gate.py,
       rule_dependency_docs.py) is 100% covered -- so declaring exactly those two as
       LEDGER_ONLY_DENY_HANDLERS, rather than silently exempting anything unflagged, is complete
       against today's data and this test still fails if a THIRD, undeclared handler appears."""

    WAREHOUSE = Path(__file__).resolve().parents[2] / "warehouse" / "atlas.db"

    def _fetch_rows(self, select_cols):
        import sqlite3
        conn = sqlite3.connect(f"file:{self.WAREHOUSE}?mode=ro", uri=True)
        try:
            rows = conn.execute(f"""
                SELECT {select_cols},
                       (SELECT GROUP_CONCAT(DISTINCT hv.handler_id) FROM hook_verdict hv
                         WHERE hv.tool_use_id = c.tool_use_id AND hv.decision='deny')
                FROM subagent_tool_call c
                JOIN subagent_tool_result r ON r.transcript_id = c.transcript_id
                                           AND r.tool_use_id = c.tool_use_id
                WHERE EXISTS (SELECT 1 FROM hook_verdict hv
                               WHERE hv.tool_use_id = c.tool_use_id AND hv.decision='deny')
            """).fetchall()
        except sqlite3.OperationalError as exc:
            self.skipTest(f"live plane not migrated on this warehouse: {exc}")
        finally:
            conn.close()
        return rows

    @unittest.skipUnless(WAREHOUSE.is_file(), "no real warehouse on this machine")
    def test_every_ledger_confirmed_deny_carries_a_named_remedy(self):
        rows = self._fetch_rows("r.deny_text")
        if not rows:
            self.skipTest("no ledger-confirmed denied subagent results ingested yet")
        missing = sorted({handler for deny_text, handler in rows if deny_text is None})
        self.assertEqual(
            missing, [],
            f"{sum(1 for t, _ in rows if t is None)} of {len(rows)} ledger-confirmed denies "
            f"have no remedy text via EITHER route. Handlers affected: {missing}. This is a "
            f"real data-capture gap (see reconcile_deny_text()'s own docstring in "
            f"subagent_pull.py), not a prefix-shape miss -- do not fix by widening "
            f"DENY_BODY_PREFIXES.")

    @unittest.skipUnless(WAREHOUSE.is_file(), "no real warehouse on this machine")
    def test_the_prefix_set_covers_every_handler_not_declared_as_an_exception(self):
        rows = self._fetch_rows("r.is_hook_deny")
        if not rows:
            self.skipTest("no ledger-confirmed denied subagent results ingested yet")
        declared = PROSE_DENY_HANDLERS | LEDGER_ONLY_DENY_HANDLERS
        undeclared_gap = sorted({
            handler for flagged, handler in rows
            if not flagged and handler and not set(handler.split(",")) <= declared
        })
        self.assertEqual(
            undeclared_gap, [],
            f"a handler whose denies the prefix route never recognises, and that is not a "
            f"declared exception (transcript_parse.PROSE_DENY_HANDLERS or "
            f"LEDGER_ONLY_DENY_HANDLERS): {undeclared_gap}. Either add its real message shape "
            f"to DENY_BODY_PREFIXES, or add it to one of those two declared sets with the "
            f"evidence for why -- see either set's own docstring for the standard to meet.")


class StreamIdentityTests(unittest.TestCase):
    """Permanent regression test for the defect this file's own tests found: a 4096-byte-head
    identity is not stable for a file shorter than 4096 bytes, so every append reads as a
    rotation and the re-read from zero collides with committed rows."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_identity_is_stable_across_appends_on_a_short_file(self):
        path = self.root / "agent-aaa.jsonl"
        path.write_text('{"first": "record"}\n', encoding="utf-8")
        first = subagent_pull.stream_id_of_path(path)
        self.assertIsNotNone(first)
        for i in range(5):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"n": i}) + "\n")
            self.assertEqual(
                subagent_pull.stream_id_of_path(path), first,
                "appending to a short transcript must not change its stream identity")

    def test_identity_is_stable_across_appends_on_a_long_first_record(self):
        path = self.root / "agent-bbb.jsonl"
        path.write_text(json.dumps({"pad": "x" * 8000}) + "\n", encoding="utf-8")
        first = subagent_pull.stream_id_of_path(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"n": 1}) + "\n")
        self.assertEqual(subagent_pull.stream_id_of_path(path), first)

    def test_identity_uses_the_head_when_no_newline_is_in_range(self):
        """405 of 1,527 real transcripts have no newline in their first 4096 bytes, so this
        branch is taken on real data, not a theoretical fallback."""
        path = self.root / "agent-ccc.jsonl"
        path.write_bytes(b"x" * (subagent_pull.STREAM_ID_HEAD_BYTES + 100))
        self.assertIsNotNone(subagent_pull.stream_id_of_path(path))

    def test_no_identity_until_the_first_record_is_complete(self):
        path = self.root / "agent-ddd.jsonl"
        path.write_bytes(b'{"partial": "still being writ')
        self.assertIsNone(
            subagent_pull.stream_id_of_path(path),
            "a transcript whose first record is mid-write has no stable identity, and nothing "
            "complete to ingest either")

    def test_a_genuinely_replaced_file_does_change_identity(self):
        path = self.root / "agent-eee.jsonl"
        path.write_text('{"first": "record"}\n', encoding="utf-8")
        first = subagent_pull.stream_id_of_path(path)
        path.write_text('{"different": "first record"}\n', encoding="utf-8")
        self.assertNotEqual(subagent_pull.stream_id_of_path(path), first)


class PullTests(unittest.TestCase):
    """C24, C25 -- incremental behaviour and the two things the pull must not do."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.projects = self.root / "projects"
        self.session = self.projects / "-Users-m5-proj" / "sess-1"
        self.db = self.root / "atlas.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def makeTranscript(self, records, meta=None, name="agent-aaa.jsonl"):
        return writeTranscript(self.session, name, records, meta=meta)

    def test_a_real_pull_lands_calls_results_and_attribution(self):
        meta = {"agentType": "muse", "description": "blind-spot pass",
                "toolUseId": "toolu_PARENT", "spawnDepth": 1}
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
            {"type": "user", "timestamp": "2026-01-01T00:00:01Z",
             "message": {"content": [toolResult("toolu_1", is_error=False)]}},
        ], meta=meta)
        summary = subagent_pull.run(self.db, self.projects)
        self.assertEqual(summary["calls_inserted"], 1)
        self.assertEqual(summary["results_inserted"], 1)
        self.assertEqual(summary["live_state"], "live")

        conn = migrate.connect(str(self.db))
        row = conn.execute(
            "SELECT agent_type, parent_tool_use_id, meta_state, workflow_id "
            "FROM subagent_transcript").fetchone()
        self.assertEqual(row, ("muse", "toolu_PARENT", "present", None))
        served = conn.execute(
            "SELECT trust_state, tool_name, result_is_error, agent_type, parent_tool_use_id "
            "FROM v_subagent_tool_call").fetchall()
        self.assertEqual(served, [("live", "Bash", 0, "muse", "toolu_PARENT")])
        conn.close()

    def test_a_sidecarless_transcript_still_contributes_its_calls(self):
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Read", {"file_path": "/a"})]),
        ], meta=None, name="agent-aside_question-x.jsonl")
        summary = subagent_pull.run(self.db, self.projects)
        self.assertEqual(summary["calls_inserted"], 1)
        conn = migrate.connect(str(self.db))
        row = conn.execute(
            "SELECT meta_state, agent_kind, parent_tool_use_id, agent_type "
            "FROM subagent_transcript").fetchone()
        self.assertEqual(row, ("absent", "aside_question", None, None))
        conn.close()

    def test_second_consecutive_pull_inserts_nothing(self):
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ], meta={"toolUseId": "toolu_P"})
        first = subagent_pull.run(self.db, self.projects)
        second = subagent_pull.run(self.db, self.projects)
        self.assertEqual(first["calls_inserted"], 1)
        self.assertEqual(second["calls_inserted"], 0)
        self.assertEqual(second["results_inserted"], 0)
        self.assertEqual(second["files_read"], 1, "the file is still read, it just has no new bytes")

    def test_appended_bytes_are_read_from_the_watermark_only(self):
        path = self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(assistantRecord(
                "2026-01-01T00:00:05Z", "a1", "s1",
                [toolUse("toolu_2", "Read", {"file_path": "/b"})])) + "\n")
        second = subagent_pull.run(self.db, self.projects)
        self.assertEqual(second["calls_inserted"], 1)
        conn = migrate.connect(str(self.db))
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM subagent_tool_call").fetchone()[0], 2)
        conn.close()

    def test_a_trailing_partial_line_is_left_for_the_next_pull(self):
        path = self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        full_line = json.dumps(assistantRecord(
            "2026-01-01T00:00:05Z", "a1", "s1",
            [toolUse("toolu_2", "Read", {"file_path": "/b"})]))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(full_line[:20])          # a subagent mid-write
        mid = subagent_pull.run(self.db, self.projects)
        self.assertEqual(mid["calls_inserted"], 0, "a partial line must not be ingested")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(full_line[20:] + "\n")   # the same subagent finishing the write
        after = subagent_pull.run(self.db, self.projects)
        self.assertEqual(after["calls_inserted"], 1, "and must be picked up once complete")

    def test_a_malformed_line_is_counted_and_skipped_not_fatal(self):
        path = self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ], meta={"toolUseId": "toolu_P"})
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{ this is not json }\n")
        summary = subagent_pull.run(self.db, self.projects)
        self.assertEqual(summary["calls_inserted"], 1)
        self.assertEqual(summary["lines_malformed"], 1)
        self.assertEqual(summary["live_state"], "live")

    def test_re_reading_committed_bytes_raises_rather_than_deduplicating(self):
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        # Simulate the watermark arithmetic going backwards -- a real bug, which must fail loudly
        # rather than be silently absorbed by an INSERT OR IGNORE.
        conn = migrate.connect(str(self.db))
        conn.execute("UPDATE subagent_transcript SET byte_offset = 0")
        conn.commit()
        conn.close()
        with self.assertRaises(Exception) as ctx:
            subagent_pull.run(self.db, self.projects)
        self.assertIn("UNIQUE", str(ctx.exception).upper())

    def test_a_failed_pull_closes_its_run_row_as_error_with_finished_at_set(self):
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        conn.execute("UPDATE subagent_transcript SET byte_offset = 0")
        conn.commit()
        conn.close()
        with self.assertRaises(Exception):
            subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        status, finished = conn.execute(
            "SELECT status, finished_at FROM subagent_pull_run "
            "ORDER BY pull_run_id DESC LIMIT 1").fetchone()
        self.assertEqual(status, "error")
        self.assertIsNotNone(
            finished, "finished_at must be set so the gate can see the failure at all")
        self.assertEqual(
            conn.execute("SELECT live_state FROM v_subagent_live_status").fetchone()[0],
            "live-pull-failed")
        conn.close()

    def test_pull_writes_no_ingest_run_row(self):
        """C25 / C6. The whole query facade refuses everything if v_atlas_status's MAX(run_id)
        row reports checks_evaluated = 0, so a 60-second job creating ingest_run rows would
        refuse all 18 gated views every minute."""
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Bash", {"command": "ls"})]),
        ], meta={"toolUseId": "toolu_P"})
        conn = migrate.connect(str(self.db))
        run_id = migrate.new_ingest_run(conn, status="ok")
        before = conn.execute(
            "SELECT run_id, status, checks_evaluated FROM v_atlas_status").fetchone()
        conn.close()

        subagent_pull.run(self.db, self.projects)

        conn = migrate.connect(str(self.db))
        after = conn.execute(
            "SELECT run_id, status, checks_evaluated FROM v_atlas_status").fetchone()
        self.assertEqual(before, after, "a subagent pull must not move v_atlas_status")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM ingest_run").fetchone()[0], 1)
        self.assertEqual(after[0], run_id)
        conn.close()

    def test_assistant_text_is_captured_and_user_text_is_not(self):
        """ATLASSN-32. Stated intent comes from the AGENT's words. A text block on a user record
        is the operator's prompt, and attributing it to the agent would let the rubric label an
        agent for something a human said."""
        self.makeTranscript([
            {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "agentId": "a1",
             "sessionId": "s1", "cwd": "/proj", "uuid": "u1", "message": {"content": [
                 {"type": "text", "text": "I will route around the scanner by using a variable."},
                 toolUse("toolu_1", "Bash", {"command": "ls"})]}},
            {"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": [
                {"type": "text", "text": "operator instruction, not the agent's words"}]}},
        ], meta={"toolUseId": "toolu_P"})
        summary = subagent_pull.run(self.db, self.projects)
        self.assertEqual(summary["texts_inserted"], 1)
        conn = migrate.connect(str(self.db))
        rows = conn.execute("SELECT text, truncated FROM subagent_assistant_text").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertIn("route around the scanner", rows[0][0])
        self.assertEqual(rows[0][1], 0)

    def test_an_oversized_text_block_is_capped_and_flagged(self):
        big = "x" * (subagent_pull.ASSISTANT_TEXT_CAP_BYTES + 5000)
        self.makeTranscript([
            {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "agentId": "a1",
             "sessionId": "s1", "cwd": "/proj", "uuid": "u1",
             "message": {"content": [{"type": "text", "text": big}]}},
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        text, text_bytes, truncated = conn.execute(
            "SELECT text, text_bytes, truncated FROM subagent_assistant_text").fetchone()
        conn.close()
        self.assertEqual(truncated, 1)
        self.assertEqual(len(text), subagent_pull.ASSISTANT_TEXT_CAP_BYTES)
        self.assertEqual(text_bytes, len(big),
                         "text_bytes must record the REAL size, not the stored size")

    def test_a_denied_result_lands_its_named_remedy(self):
        deny = ("Foreman: component 'x' has no GOALS.json at /p/GOALS.json. Run "
                "foreman:design-and-scope for this component before writing implementation files.")
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1",
                            [toolUse("toolu_1", "Write", {"file_path": "/p/x.py"})]),
            {"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": [
                toolResult("toolu_1", is_error=None, content=deny)]}},
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        is_deny, deny_text, is_error = conn.execute(
            "SELECT is_hook_deny, deny_text, is_error FROM subagent_tool_result").fetchone()
        conn.close()
        self.assertEqual(is_deny, 1)
        self.assertEqual(deny_text, deny)
        self.assertIsNone(is_error, "is_error stays absent -- which is exactly why deny_text "
                                    "had to exist as a separate field")

    def test_rebuild_re_derives_and_carries_acknowledgements_across_by_payload_hash(self):
        """call_id does not survive a rebuild; payload_sha256 does. An acknowledgement keyed on
        the former would silently vanish, taking a human triage decision with it."""
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1", [toolUse(
                "toolu_1", "Bash",
                {"command": "export GEMINI_API_KEY=AIzaSyD-realistic-fake-value-here"})]),
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        first_call_id = conn.execute("SELECT call_id FROM subagent_tool_call").fetchone()[0]
        subagent_pull.acknowledge_credential_hit(
            conn, first_call_id, "literal_export_assignment", "false-positive",
            "placeholder in an example script", "test-actor")
        conn.close()

        cleared = subagent_pull.rebuild(self.db)
        self.assertEqual(len(cleared["saved_acks"]), 1)
        subagent_pull.run(self.db, self.projects)
        restored, orphaned = subagent_pull.restore_credential_acks(
            self.db, cleared["saved_acks"])
        self.assertEqual((restored, orphaned), (1, 0))

        conn = migrate.connect(str(self.db))
        new_call_id, rationale = conn.execute(
            "SELECT call_id, rationale FROM subagent_credential_ack").fetchone()
        conn.close()
        self.assertEqual(rationale, "placeholder in an example script")
        self.assertEqual(
            subagent_pull.run(self.db, self.projects)["live_state"], "live",
            "the carried-across acknowledgement must still hold the gate open")

    def test_an_acknowledged_hit_stops_closing_the_gate(self):
        """The triage path. Without it the gate is fail-closed AND permanently closed: the first
        real pull matched 8 rows and all 8 were placeholder text a subagent wrote into a script
        it was authoring."""
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1", [toolUse(
                "toolu_1", "Bash",
                {"command": "export GEMINI_API_KEY=AIzaSyD-realistic-fake-value-here"})]),
        ], meta={"toolUseId": "toolu_P"})
        self.assertEqual(
            subagent_pull.run(self.db, self.projects)["live_state"], "live-credential-hit")

        conn = migrate.connect(str(self.db))
        call_id = conn.execute("SELECT call_id FROM subagent_tool_call").fetchone()[0]
        subagent_pull.acknowledge_credential_hit(
            conn, call_id, "literal_export_assignment", "false-positive",
            "placeholder in an example script, triaged without reading the value",
            "test-actor")
        conn.close()

        after = subagent_pull.run(self.db, self.projects)
        self.assertEqual(after["credential_hits"], 0)
        self.assertEqual(after["live_state"], "live")

    def test_an_acknowledgement_does_not_survive_the_payload_changing(self):
        """An ack keyed on call_id alone would be a blanket off-switch. It binds to a hash of the
        exact payload triaged, so a row whose stored input later differs is a hit again."""
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1", [toolUse(
                "toolu_1", "Bash", {"command": "export API_KEY=placeholder-value"})]),
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        call_id = conn.execute("SELECT call_id FROM subagent_tool_call").fetchone()[0]
        subagent_pull.acknowledge_credential_hit(
            conn, call_id, "literal_export_assignment", "false-positive", "placeholder",
            "test-actor")
        conn.close()
        self.assertEqual(subagent_pull.run(self.db, self.projects)["credential_hits"], 0)

        conn = migrate.connect(str(self.db))
        conn.execute(
            "UPDATE subagent_tool_call SET tool_input_json = ? WHERE call_id = ?",
            ('{"command":"export API_KEY=AIzaSyD-a-genuinely-different-value"}', call_id))
        conn.commit()
        conn.close()
        re_armed = subagent_pull.run(self.db, self.projects)
        self.assertEqual(
            re_armed["credential_hits"], 1,
            "a changed payload must re-arm the gate rather than staying silenced")
        self.assertEqual(re_armed["live_state"], "live-credential-hit")

    def test_an_acknowledgement_requires_a_rationale(self):
        self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1", [toolUse(
                "toolu_1", "Bash", {"command": "export API_KEY=placeholder"})]),
        ], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        call_id = conn.execute("SELECT call_id FROM subagent_tool_call").fetchone()[0]
        with self.assertRaises(Exception):
            subagent_pull.acknowledge_credential_hit(
                conn, call_id, "literal_export_assignment", "false-positive", "   ", "test-actor")
        conn.close()

    def test_acknowledging_a_nonexistent_call_raises(self):
        self.makeTranscript([], meta={"toolUseId": "toolu_P"})
        subagent_pull.run(self.db, self.projects)
        conn = migrate.connect(str(self.db))
        with self.assertRaises(subagent_pull.SubagentPullError):
            subagent_pull.acknowledge_credential_hit(
                conn, 999999, "literal_export_assignment", "false-positive", "why", "test-actor")
        conn.close()

    def test_credential_hit_darks_the_view_and_stays_dark_on_the_next_pull(self):
        """C25. A delta-only scan would flag the ingesting run, dark the view for one tick, then
        come back clean with the secret still stored. This is the regression test for that."""
        path = self.makeTranscript([
            assistantRecord("2026-01-01T00:00:00Z", "a1", "s1", [toolUse(
                "toolu_1", "Bash",
                {"command": "export GEMINI_API_KEY=AIzaSyD-realistic-fake-value-here"})]),
        ], meta={"toolUseId": "toolu_P"})
        first = subagent_pull.run(self.db, self.projects)
        self.assertGreater(first["credential_hits"], 0)
        self.assertEqual(first["live_state"], "live-credential-hit")

        # A second pull with NOTHING new to ingest must still find it.
        second = subagent_pull.run(self.db, self.projects)
        self.assertEqual(second["calls_inserted"], 0)
        self.assertGreater(
            second["credential_hits"], 0,
            "the scan must cover every stored tool input, not just this pull's delta")
        self.assertEqual(second["live_state"], "live-credential-hit")

        conn = migrate.connect(str(self.db))
        self.assertEqual(
            conn.execute("SELECT trust_state FROM v_subagent_tool_call").fetchone()[0],
            "LIVE-PLANE-NOT-SERVABLE-DO-NOT-USE")
        conn.close()
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
