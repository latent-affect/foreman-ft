import json
import tempfile
import unittest
from pathlib import Path

from tessera.tessguard import audit, config

from .fixtures import edit_record, make_store, make_ticket, write_transcript


class AuditTests(unittest.TestCase):
    def test_binary_predicate_gif_fixture_flags(self):
        # GIF-shape fixture: real in-repo edits, zero real TESSERA activity for the project.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            store, db_path = make_store(tmp, prefix="GIFX", source_root=str(repo))
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(repo), "2026-08-19T00:00:00.000Z", str(repo / "a.py")),
                edit_record(str(repo), "2026-08-19T00:00:01.000Z", str(repo / "b.py")),
            ])
            result = audit.audit_session(
                str(transcript_path), str(repo), db_path=str(db_path),
                log_path=Path(tmp) / "audit-log.jsonl",
            )
            self.assertTrue(result["flagged"])
            self.assertEqual(result["real_events"], 0)
            self.assertEqual(result["in_repo_edits"], 2)

    def test_binary_predicate_good_session_does_not_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            store, db_path = make_store(tmp, prefix="GOODX", source_root=str(repo))
            # The ticket/comment get real wall-clock timestamps (store.utc_now_iso()) --
            # the transcript's session span must bracket that real "now", not a fixed
            # placeholder time, or the event-window check correctly finds nothing and this
            # test would be asserting the wrong thing rather than testing the real behavior.
            from tessera.common import utc_now_iso
            before = utc_now_iso()
            tid = make_ticket(store, "GOODX")
            store.add_comment(tid, "tester", "logged the work")
            after = utc_now_iso()
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(repo), before, str(repo / "a.py")),
                edit_record(str(repo), after, str(repo / "b.py")),
            ])
            result = audit.audit_session(
                str(transcript_path), str(repo), db_path=str(db_path),
                log_path=Path(tmp) / "audit-log.jsonl",
            )
            self.assertFalse(result["flagged"])
            self.assertGreater(result["real_events"], 0)

    def test_transcript_accepted_by_cwd_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            transcript_path = Path(tmp) / "session.jsonl"
            # cwd matches, zero edits -- should NOT raise (cwd match alone is sufficient).
            write_transcript(transcript_path, [
                {"type": "user", "cwd": str(repo), "timestamp": "2026-08-19T00:00:00.000Z",
                 "message": {"content": []}},
            ])
            summary, cwd_matches = audit.validate_transcript(str(transcript_path), str(repo))
            self.assertTrue(cwd_matches)

    def test_transcript_accepted_by_edits_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            other_cwd = Path(tmp) / "unrelated"
            other_cwd.mkdir()
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(other_cwd), "2026-08-19T00:00:00.000Z", str(repo / "a.py")),
            ])
            summary, cwd_matches = audit.validate_transcript(str(transcript_path), str(repo))
            self.assertFalse(cwd_matches)
            self.assertEqual(summary["in_repo_edits"], 1)

    def test_transcript_rejected_when_neither(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            other_cwd = Path(tmp) / "unrelated"
            other_cwd.mkdir()
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(other_cwd), "2026-08-19T00:00:00.000Z", str(other_cwd / "a.py")),
            ])
            with self.assertRaises(audit.AuditInputError):
                audit.validate_transcript(str(transcript_path), str(repo))

    def test_transcript_accepted_by_subagent_edits_alone(self):
        # cwd mismatches AND the parent transcript itself has zero edits, but a subagent
        # transcript recorded a real in-repo edit -- the OR-rule's edit-count side must be
        # subagent-inclusive, not just the parent's own count, or a session whose real work
        # was entirely delegated to a subagent gets wrongly rejected as invalid input.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            other_cwd = Path(tmp) / "unrelated"
            other_cwd.mkdir()
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                {"type": "user", "cwd": str(other_cwd), "timestamp": "2026-08-19T00:00:00.000Z",
                 "message": {"content": []}},
            ])
            subagent_dir = Path(tmp) / "session" / "subagents"
            subagent_dir.mkdir(parents=True)
            write_transcript(subagent_dir / "agent-1.jsonl", [
                edit_record(str(other_cwd), "2026-08-19T00:00:01.000Z", str(repo / "a.py")),
            ])
            summary, cwd_matches = audit.validate_transcript(str(transcript_path), str(repo))
            self.assertFalse(cwd_matches)
            self.assertEqual(summary["in_repo_edits"], 0)  # parent-only count stays 0

    def test_missing_db_path_produces_distinct_outcome(self):
        # config.resolve_db_path() is the one place a wrong/missing DB path must be caught
        # BEFORE Store() gets a chance to silently create an empty one there -- confirms
        # the fix for the exact bug an architecture review found live in Store.__init__.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.db"
            self.assertFalse(missing.is_file())
            import os
            old = os.environ.get("TESSGUARD_DB_PATH")
            os.environ["TESSGUARD_DB_PATH"] = str(missing)
            try:
                with self.assertRaises(config.DbPathError):
                    config.resolve_db_path()
                self.assertFalse(missing.is_file(), "resolve_db_path must not create the file")
            finally:
                if old is None:
                    os.environ.pop("TESSGUARD_DB_PATH", None)
                else:
                    os.environ["TESSGUARD_DB_PATH"] = old

    def test_flagged_result_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            store, db_path = make_store(tmp, prefix="FLAGX", source_root=str(repo))
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(repo), "2026-08-19T00:00:00.000Z", str(repo / "a.py")),
            ])
            import os
            old = os.environ.get("TESSGUARD_DB_PATH")
            os.environ["TESSGUARD_DB_PATH"] = str(db_path)
            try:
                exit_code = audit.run_periodic_audit(
                    str(transcript_path), str(repo), log_path=Path(tmp) / "audit-log.jsonl",
                )
            finally:
                if old is None:
                    os.environ.pop("TESSGUARD_DB_PATH", None)
                else:
                    os.environ["TESSGUARD_DB_PATH"] = old
            self.assertEqual(exit_code, 0)

    def test_run_appends_to_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            store, db_path = make_store(tmp, prefix="LOGX", source_root=str(repo))
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(repo), "2026-08-19T00:00:00.000Z", str(repo / "a.py")),
            ])
            log_path = Path(tmp) / "audit-log.jsonl"
            audit.audit_session(str(transcript_path), str(repo), db_path=str(db_path), log_path=log_path)
            self.assertTrue(log_path.is_file())
            lines = log_path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["kind"], "audit")

    def test_audit_session_rejects_explicit_missing_db_path(self):
        # audit_session's own db_path= kwarg must not bypass config.resolve_db_path()'s
        # existence check -- an explicitly-passed but nonexistent path must raise
        # DbPathError, not let Store() silently create a fresh empty db there.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            transcript_path = Path(tmp) / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(repo), "2026-08-19T00:00:00.000Z", str(repo / "a.py")),
            ])
            missing_db = Path(tmp) / "does-not-exist.db"
            with self.assertRaises(config.DbPathError):
                audit.audit_session(
                    str(transcript_path), str(repo), db_path=str(missing_db),
                    log_path=Path(tmp) / "audit-log.jsonl",
                )
            self.assertFalse(missing_db.is_file(), "audit_session must not create the db file")

    def test_validate_transcript_rejects_missing_file(self):
        # A missing transcript path must raise AuditInputError (the documented "genuinely
        # invalid input" exception), not leak transcript.py's raw FileNotFoundError.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            missing_transcript = Path(tmp) / "does-not-exist.jsonl"
            with self.assertRaises(audit.AuditInputError):
                audit.validate_transcript(str(missing_transcript), str(repo))

    def test_self_check_detects_shim_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks_dir = Path(tmp) / ".githooks"
            hooks_dir.mkdir()
            (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
            (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 0\n")
            import subprocess
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=tmp, check=True)
            log_path = Path(tmp) / "audit-log.jsonl"
            result = audit.self_check_hard_gate(
                repo_root=tmp,
                expected_shas={"pre-commit": "0" * 64, "pre-push": "0" * 64},
                log_path=log_path,
            )
            self.assertFalse(result["healthy"])
            self.assertTrue(result["shims"]["pre-commit"].startswith("MISMATCH"))

    def test_home_relative_internal_strips_home_prefix(self):
        # SP-1 (0e's security-privacy-review, ported from tessera-v2 -- TESS-171): a
        # resolved absolute path under the real home directory must not carry the
        # account name into a persisted, git-tracked audit record. Path.resolve() works
        # on a path that doesn't exist on disk, so this needs no real file.
        under_home = str(Path.home() / "some" / "path.jsonl")
        self.assertEqual(audit.home_relative_internal(under_home), "~/some/path.jsonl")

    def test_home_relative_internal_falls_back_outside_home(self):
        # A path genuinely outside home (this project's own real paths never are, but
        # silently mis-stating one as home-relative would be worse than leaving it
        # absolute) must be returned unchanged, not silently mangled.
        outside = "/var/definitely-not-under-home/whatever.jsonl"
        self.assertEqual(audit.home_relative_internal(outside), str(Path(outside).resolve()))

    def test_sanitize_for_log_internal_only_touches_the_two_named_keys(self):
        # b9's own fixup to SP-1: transcript is reduced to its basename (a bare '~/'
        # strip still left Claude Code's own embedded hyphenated-cwd directory name,
        # e.g. '-Users-m5-dev-x', spelling out the account name in every new record)
        # -- repo_root keeps the '~'-relative form, which has no such embedded
        # occurrence and where a repo name is still useful information to keep.
        entry = {
            "kind": "audit",
            "transcript": str(Path.home() / "-Users-m5-dev-x" / "t.jsonl"),
            "repo_root": str(Path.home() / "r"),
            "registered_projects": ["X"],
            "flagged": False,
        }
        sanitized = audit.sanitize_for_log_internal(entry)
        self.assertEqual(sanitized["transcript"], "t.jsonl")
        self.assertEqual(sanitized["repo_root"], "~/r")
        self.assertEqual(sanitized["registered_projects"], ["X"])
        self.assertEqual(sanitized["flagged"], False)
        self.assertEqual(
            entry["transcript"], str(Path.home() / "-Users-m5-dev-x" / "t.jsonl"),
            "must not mutate the input dict",
        )

    def test_sanitize_removes_account_name_from_a_real_shaped_hyphenated_transcript_path(self):
        # b9's actual measured finding, reproduced directly: a real Claude Code
        # transcript path hyphenates the session's full original cwd into its own
        # directory name, sitting AFTER any home-prefix strip. Confirms the account
        # name is gone entirely now, not just moved past the '~/' this fix used to add.
        account = Path.home().name
        realistic_transcript = str(
            Path.home() / ".claude" / "projects" / f"-Users-{account}-dev-ticket-system"
            / "3faed77f-a0b9-415b-9b68-f7cd969759fb.jsonl"
        )
        entry = {
            "kind": "audit", "transcript": realistic_transcript,
            "repo_root": str(Path.home() / "dev" / "ticket-system"),
        }
        sanitized = audit.sanitize_for_log_internal(entry)
        self.assertEqual(sanitized["transcript"], "3faed77f-a0b9-415b-9b68-f7cd969759fb.jsonl")
        self.assertNotIn(account, sanitized["transcript"])
        self.assertNotIn(account, sanitized["repo_root"])

    def test_real_home_directory_audit_run_persists_relative_but_returns_absolute(self):
        # SP-1's actual fix, exercised end to end with real paths under the real home
        # directory (not a tempfile.TemporaryDirectory(), which every OTHER test in this
        # file uses and which is never under home -- those tests are correctly
        # unaffected by this fix, verified separately; this is the one that must
        # actually exercise it). A scratch directory under home stands in for a real
        # repo/transcript; removed in a finally block regardless of outcome.
        scratch = Path.home() / f".ticket-system-sp1-test-scratch-{id(self)}"
        try:
            repo = scratch / "repo"
            repo.mkdir(parents=True)
            transcript_path = scratch / "session.jsonl"
            write_transcript(transcript_path, [
                edit_record(str(repo), "2026-08-19T00:00:00.000Z", str(repo / "a.py")),
            ])
            store, db_path = make_store(str(scratch), prefix="SP1X", source_root=str(repo))
            log_path = scratch / "audit-log.jsonl"

            result = audit.audit_session(
                str(transcript_path), str(repo), db_path=str(db_path), log_path=str(log_path),
            )
            # Returned/printable result: full absolute paths, unchanged -- local,
            # non-persisted consumption keeps the immediately-useful value.
            self.assertEqual(result["transcript"], str(transcript_path))
            self.assertEqual(result["repo_root"], str(repo))

            # Persisted log entry: repo_root home-relative, transcript reduced to its
            # basename. Neither carries the account name.
            entry = json.loads(log_path.read_text().strip().splitlines()[0])
            self.assertTrue(entry["repo_root"].startswith("~/"), entry["repo_root"])
            self.assertEqual(entry["transcript"], transcript_path.name)
            self.assertNotIn(str(Path.home()), entry["repo_root"])
            self.assertNotIn(str(Path.home()), entry["transcript"])
        finally:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
