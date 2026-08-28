import tempfile
import unittest
from pathlib import Path

from tessera.tessguard import transcript

from .fixtures import bash_record, edit_record, plain_record, write_transcript


class TranscriptTests(unittest.TestCase):
    def test_scopes_to_repo_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            write_transcript(path, [
                edit_record(tmp, "2026-08-19T00:00:00.000Z", str(Path(tmp) / "in_repo.py")),
                edit_record(tmp, "2026-08-19T00:00:01.000Z", "/somewhere/else/out.py"),
            ])
            summary = transcript.transcript_summary(str(path), tmp)
            self.assertEqual(summary["in_repo_edits"], 1)

    def test_includes_subagent_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp)
            parent = projects_dir / "session-abc.jsonl"
            write_transcript(parent, [
                edit_record(tmp, "2026-08-19T00:00:00.000Z", str(Path(tmp) / "a.py")),
            ])
            subagent_dir = projects_dir / "session-abc" / "subagents"
            subagent_dir.mkdir(parents=True)
            sub_path = subagent_dir / "agent-1.jsonl"
            write_transcript(sub_path, [
                edit_record(tmp, "2026-08-19T00:00:01.000Z", str(Path(tmp) / "b.py")),
            ])
            total = transcript.in_repo_edit_count_including_subagents(str(parent), tmp)
            self.assertEqual(total, 2)

    def test_excludes_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            write_transcript(path, [
                bash_record(tmp, "2026-08-19T00:00:00.000Z", f"touch {tmp}/new_file.py"),
            ])
            summary = transcript.transcript_summary(str(path), tmp)
            self.assertEqual(summary["in_repo_edits"], 0)

    def test_session_span_tracks_min_max_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            write_transcript(path, [
                plain_record(tmp, "2026-08-19T00:00:00.000Z"),
                edit_record(tmp, "2026-08-19T00:05:00.000Z", str(Path(tmp) / "a.py")),
                plain_record(tmp, "2026-08-19T00:10:00.000Z"),
            ])
            summary = transcript.transcript_summary(str(path), tmp)
            self.assertEqual(summary["session_start"], "2026-08-19T00:00:00.000Z")
            self.assertEqual(summary["session_end"], "2026-08-19T00:10:00.000Z")

    def test_fully_unparseable_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.jsonl"
            path.write_text("not json\nalso not json\n")
            with self.assertRaises(transcript.TranscriptParseError):
                transcript.transcript_summary(str(path), tmp)

    def test_hyphenated_project_dir_every_nonalnum(self):
        result = transcript.hyphenated_project_dir("/path/to/ticket-system")
        self.assertTrue(str(result).endswith("-path-to-ticket-system"))
        result2 = transcript.hyphenated_project_dir("/path/to/home/a_b.c+d")
        self.assertTrue(str(result2).endswith("-path-to-home-a-b-c-d"))


if __name__ == "__main__":
    unittest.main()
