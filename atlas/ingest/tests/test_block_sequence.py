"""ATLASSN-104/REQ-64, ARCHITECTURE.md section 40, GOALS.json C26-C30. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.ingest.tests.test_block_sequence -v

UNEXECUTED AS OF THIS PROPOSAL. Written by Alice, who has no Bash/Python execution in this
session -- every assertion below is reasoned against the real, currently-live code
(map_transcript_line, pull_session, the ddl.py/migrate.py additions in this same proposal), not
run. One self-caught bug already fixed before handing this off: the integration fixtures below
write each session transcript under its own <project_dir>/<session>.jsonl, two path segments
under projects_root -- session_pull.parse_session_path() requires exactly that shape (len(parts)
== 2) and returns None otherwise, which would have made every integration test below silently
pass on a no-op pull_session call rather than actually exercising anything. Caught by re-reading
parse_session_path's own real code after drafting the fixtures flat, not by running the test.

TestSessionBlockSequenceIntegration is additionally EXPECTED to fail with ddl.DdlExtractionError
until ARCHITECTURE.md section 40.1 is added (see ATLASSN-104-ARCHITECTURE-DDL-HANDOFF.md) and
migrate.apply_migration23's call in connect() is uncommented -- that is not a defect in this
test, it is the same "not yet wired" state migration 22 is already in. Run this for real, and
fix whatever it finds, before landing.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.ingest import session_pull
from atlas.ingest.transcript_parse import map_transcript_line
from atlas.warehouse import ddl, migrate

# The real record cited in ARCHITECTURE.md section 40: a text block, a thinking block, and a
# tool_use block, in that order, from transcript 204fee05-1ca3-41a8-8dbc-5b4b5ddcfedd.
MIXED_RECORD = {
    "type": "assistant",
    "uuid": "204fee05-1ca3-41a8-8dbc-5b4b5ddcfedd",
    "timestamp": "2026-09-18T00:00:00.000000Z",
    "sessionId": "11111111-1111-1111-1111-111111111111",
    "cwd": "/Users/m5/dev/atlas-sonnet",
    "gitBranch": "main",
    "message": {
        "content": [
            {"type": "text", "text": "checking the parser"},
            {"type": "thinking", "thinking": "the elif chain has no final else"},
            {"type": "tool_use", "id": "toolu_01", "name": "Read", "input": {"file_path": "/x"}},
        ],
    },
}

# A real, confirmed-live fourth block type (ARCHITECTURE.md section 40's correction): a Claude
# Code harness event marking a mid-turn model-fallback transition. Not text/tool_use/tool_result,
# and pre-ATLASSN-104 map_transcript_line() had no branch for it either.
FALLBACK_RECORD = {
    "type": "assistant",
    "uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "timestamp": "2026-09-18T00:00:01.000000Z",
    "sessionId": "11111111-1111-1111-1111-111111111111",
    "message": {
        "content": [
            {"type": "fallback", "from": {"model": "claude-fable-5"}, "to": {"model": "claude-opus-4-8"}},
        ],
    },
}


class MapTranscriptLineBlocksTests(unittest.TestCase):
    """Pure-function tests against map_transcript_line() -- no database, matching C26/C28/C29."""

    def test_mixed_record_produces_three_ordered_blocks(self):
        """C26, C30's own-table half: block_index in blocks must match enumerate(content), the
        same shared index calls/texts already use."""
        calls, results, texts, blocks = map_transcript_line(MIXED_RECORD, byte_offset=0)
        self.assertEqual(len(blocks), 3)
        self.assertEqual([b["block_index"] for b in blocks], [0, 1, 2])
        self.assertEqual([b["block_type"] for b in blocks], ["text", "thinking", "tool_use"])
        # C30: the same index texts/calls independently assign for the same positions.
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0]["block_index"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["block_index"], 2)

    def test_thinking_block_is_captured_but_not_texts_or_calls(self):
        """The load-bearing defect this ticket exists for: a thinking block must now produce a
        blocks row even though it produces no texts/calls row (unchanged)."""
        _, _, texts, blocks = map_transcript_line(MIXED_RECORD, byte_offset=0)
        thinking_rows = [b for b in blocks if b["block_type"] == "thinking"]
        self.assertEqual(len(thinking_rows), 1)
        self.assertEqual(len(texts), 1)  # only the text block, never the thinking block

    def test_fallback_block_is_captured(self):
        """A second real type the pre-ticket parser also silently dropped. Must not need a named
        branch -- captured by the same unconditional per-position append as thinking."""
        _, _, _, blocks = map_transcript_line(FALLBACK_RECORD, byte_offset=100)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["block_type"], "fallback")

    def test_an_unnamed_future_type_is_also_captured(self):
        """C26's own point: this must not be an elif-chain-plus-a-fourth-branch. A type nobody
        has named yet still gets a row."""
        record = {
            "type": "assistant", "uuid": "u1", "timestamp": "t1", "sessionId": "s1",
            "message": {"content": [{"type": "some_future_block_type", "payload": "x"}]},
        }
        _, _, _, blocks = map_transcript_line(record, byte_offset=0)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["block_type"], "some_future_block_type")

    def test_content_not_a_list_returns_empty_blocks_not_a_crash(self):
        """C28 probe 1: message.content is not a list."""
        record = {"type": "assistant", "uuid": "u2", "message": {"content": "not-a-list"}}
        calls, results, texts, blocks = map_transcript_line(record, byte_offset=0)
        self.assertEqual((calls, results, texts, blocks), ([], [], [], []))

    def test_non_dict_block_gets_a_null_type_row_and_does_not_drop_siblings(self):
        """C28 probe 2: a block that is not a dict. Must still produce a positional row (so C26's
        'every position produces exactly one row' holds) and must not stop the well-formed
        sibling block after it from being captured."""
        record = {
            "type": "assistant", "uuid": "u3", "timestamp": "t3", "sessionId": "s3",
            "message": {"content": ["not-a-dict", {"type": "text", "text": "still here"}]},
        }
        calls, results, texts, blocks = map_transcript_line(record, byte_offset=0)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["block_index"], 0)
        self.assertIsNone(blocks[0]["block_type"])
        self.assertEqual(blocks[1]["block_index"], 1)
        self.assertEqual(blocks[1]["block_type"], "text")
        self.assertEqual(len(texts), 1)  # the well-formed sibling still captured normally

    def test_block_missing_type_key_produces_null_type_row(self):
        """C28 probe 3: a block dict with no 'type' key at all."""
        record = {
            "type": "assistant", "uuid": "u4", "timestamp": "t4", "sessionId": "s4",
            "message": {"content": [{"no_type_here": True}]},
        }
        _, _, _, blocks = map_transcript_line(record, byte_offset=0)
        self.assertEqual(len(blocks), 1)
        self.assertIsNone(blocks[0]["block_type"])

    def test_blocks_never_carry_content(self):
        """C29: structural only. Even a real thinking block's own text must not appear anywhere
        in its blocks entry -- checked by key set, not by absence of one string, so a future
        column addition here would have to do it deliberately."""
        _, _, _, blocks = map_transcript_line(MIXED_RECORD, byte_offset=0)
        allowed_keys = {"byte_offset", "block_index", "ts", "record_uuid", "block_type"}
        for b in blocks:
            self.assertEqual(set(b.keys()), allowed_keys)
        thinking_row = [b for b in blocks if b["block_type"] == "thinking"][0]
        for value in thinking_row.values():
            if isinstance(value, str):
                self.assertNotIn("elif chain", value)


class SessionBlockSequenceIntegrationTests(unittest.TestCase):
    """Real sqlite, via migrate.connect() + a DIRECT call to migrate.apply_migration23 (not yet
    wired into connect() itself -- see that function's own docstring). EXPECTED TO FAIL with
    ddl.DdlExtractionError until ARCHITECTURE.md section 40.1 exists; that is the correct failure
    mode for unlanded design, not a bug in this test.

    Fixtures live at <tmpdir>/projects/<project_dir>/<session_id>.jsonl -- two path segments
    under projects_root, matching session_pull.parse_session_path()'s real, checked shape."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.projects_root = self.root / "projects"
        self.project_dir = self.projects_root / "-Users-m5-dev-atlas-sonnet"
        self.project_dir.mkdir(parents=True)
        self.db_path = self.root / "w.db"
        self.conn = migrate.connect(str(self.db_path))
        migrate.apply_migration23(self.conn)  # raises DdlExtractionError pre-landing; expected
        self.pull_run_id = self.conn.execute(
            "INSERT INTO subagent_pull_run (started_at, status, atlas_version, host_id) "
            "VALUES ('t0', 'running', '0.1.0', 'h')"
        ).lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _write_session(self, session_id, records):
        path = self.project_dir / f"{session_id}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
        return path

    def _pull(self, path):
        return session_pull.pull_session(
            self.conn, path, self.pull_run_id, projects_root=self.projects_root,
        )

    def test_pull_session_populates_block_sequence_with_matching_block_index(self):
        """C30: block_index in session_block_sequence must agree with the SAME position's row
        in session_tool_call/session_assistant_text, joined on (transcript_id, byte_offset)."""
        path = self._write_session("sess1", [MIXED_RECORD])
        outcome = self._pull(path)
        self.assertIsNotNone(outcome, "pull_session returned None -- fixture path shape is wrong")
        transcript_id = self.conn.execute(
            "SELECT transcript_id FROM session_transcript WHERE source_path = ?",
            (str(path),),
        ).fetchone()[0]
        block_rows = dict(self.conn.execute(
            "SELECT block_index, block_type FROM session_block_sequence "
            "WHERE transcript_id = ? ORDER BY block_index", (transcript_id,)
        ).fetchall())
        self.assertEqual(block_rows, {0: "text", 1: "thinking", 2: "tool_use"})
        text_block_index = self.conn.execute(
            "SELECT block_index FROM session_assistant_text WHERE transcript_id = ?",
            (transcript_id,),
        ).fetchone()[0]
        call_block_index = self.conn.execute(
            "SELECT block_index FROM session_tool_call WHERE transcript_id = ?",
            (transcript_id,),
        ).fetchone()[0]
        self.assertEqual(text_block_index, 0)
        self.assertEqual(call_block_index, 2)

    def test_turn_ended_without_a_tool_call_answers_from_block_sequence_alone(self):
        """C27, REQ-64's own verification clause, verbatim: a turn with no tool_use block must be
        answerable as 'ended without a tool call' directly from session_block_sequence."""
        no_tool_record = {
            "type": "assistant", "uuid": "u5", "timestamp": "t5", "sessionId": "s1",
            "message": {"content": [{"type": "text", "text": "just a status update"}]},
        }
        path = self._write_session("sess2", [no_tool_record])
        outcome = self._pull(path)
        self.assertIsNotNone(outcome, "pull_session returned None -- fixture path shape is wrong")
        transcript_id = self.conn.execute(
            "SELECT transcript_id FROM session_transcript WHERE source_path = ?",
            (str(path),),
        ).fetchone()[0]
        has_tool_use = self.conn.execute(
            "SELECT COUNT(*) FROM session_block_sequence "
            "WHERE transcript_id = ? AND block_type = 'tool_use'", (transcript_id,)
        ).fetchone()[0]
        self.assertEqual(has_tool_use, 0)

    def test_schema_has_no_content_bearing_column(self):
        """C29, checked at the schema level rather than only by inspecting one row: the table
        must have no column beyond the structural set, ever."""
        cols = {row[1] for row in self.conn.execute(
            "PRAGMA table_info(session_block_sequence)")}
        self.assertEqual(
            cols,
            {"block_seq_id", "transcript_id", "byte_offset", "block_index", "pull_run_id",
             "ts", "record_uuid", "block_type"},
        )

    def test_rebuild_clears_session_block_sequence(self):
        path = self._write_session("sess3", [MIXED_RECORD])
        self._pull(path)
        self.conn.close()
        session_pull.rebuild(self.db_path)
        conn2 = migrate.connect(str(self.db_path))
        count = conn2.execute("SELECT COUNT(*) FROM session_block_sequence").fetchone()[0]
        conn2.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
