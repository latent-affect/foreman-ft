import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tessera.tessguard import gitgate

from .fixtures import make_store, make_ticket


def init_repo_internal(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)


class GitgateTests(unittest.TestCase):
    def test_blocks_with_no_recent_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="BLOCKX", source_root=str(repo))
            result = gitgate.check_gate(str(repo), store)
            self.assertFalse(result.passed)
            self.assertIn("BLOCKX", result.reason)

    def test_passes_with_recent_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="PASSX", source_root=str(repo))
            tid = make_ticket(store, "PASSX")
            store.add_comment(tid, "tester", "logged")
            result = gitgate.check_gate(str(repo), store)
            self.assertTrue(result.passed)
            self.assertGreater(result.events_found, 0)

    def test_zero_registered_projects_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="OTHERX", source_root="/nowhere/related")
            result = gitgate.check_gate(str(repo), store)
            self.assertFalse(result.passed)
            self.assertEqual(result.projects_checked, [])
            self.assertIn("no TESSERA project registered", result.reason)

    def test_main_blocks_closed_on_missing_db_path(self):
        # Real invocation of gitgate.main() (not check_gate directly) with a TESSGUARD_DB_PATH
        # that resolves to no file at all -- exercises config.resolve_db_path()'s DbPathError
        # catch block in main(), the layer-2 fail-closed path for "can't reach the data source".
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            old_cwd = os.getcwd()
            old_db = os.environ.get("TESSGUARD_DB_PATH")
            os.chdir(repo)
            os.environ["TESSGUARD_DB_PATH"] = str(Path(tmp) / "does-not-exist.db")
            try:
                exit_code = gitgate.main()
                self.assertEqual(exit_code, 1)
            finally:
                os.chdir(old_cwd)
                if old_db is None:
                    os.environ.pop("TESSGUARD_DB_PATH", None)
                else:
                    os.environ["TESSGUARD_DB_PATH"] = old_db

    def test_main_blocks_closed_on_unopenable_db(self):
        # A real file that exists (passes resolve_db_path()'s is_file() check) but is not a
        # valid SQLite database -- exercises main()'s Store()-construction except-Exception
        # catch block, the fail-closed path for "the data source exists but can't be opened".
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            bad_db = Path(tmp) / "not-a-database.db"
            bad_db.write_text("this is not a sqlite database file")
            old_cwd = os.getcwd()
            old_db = os.environ.get("TESSGUARD_DB_PATH")
            os.chdir(repo)
            os.environ["TESSGUARD_DB_PATH"] = str(bad_db)
            try:
                exit_code = gitgate.main()
                self.assertEqual(exit_code, 1)
            finally:
                os.chdir(old_cwd)
                if old_db is None:
                    os.environ.pop("TESSGUARD_DB_PATH", None)
                else:
                    os.environ["TESSGUARD_DB_PATH"] = old_db

    def test_main_exit_codes_and_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, db_path = make_store(tmp, prefix="MAINX", source_root=str(repo))

            old_cwd = os.getcwd()
            old_db = os.environ.get("TESSGUARD_DB_PATH")
            os.chdir(repo)
            os.environ["TESSGUARD_DB_PATH"] = str(db_path)
            try:
                exit_code = gitgate.main()
                self.assertEqual(exit_code, 1)  # no activity yet -- blocked

                tid = make_ticket(store, "MAINX")
                store.add_comment(tid, "tester", "logged")
                exit_code = gitgate.main()
                self.assertEqual(exit_code, 0)  # now passes
            finally:
                os.chdir(old_cwd)
                if old_db is None:
                    os.environ.pop("TESSGUARD_DB_PATH", None)
                else:
                    os.environ["TESSGUARD_DB_PATH"] = old_db

    def test_commit_message_blocks_with_no_ticket_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="MSGX", source_root=str(repo))
            result = gitgate.check_commit_message(str(repo), store, "just a plain commit message")
            self.assertFalse(result.passed)
            self.assertIn("MSGX", result.reason)
            self.assertIsNone(result.matched_ticket)

    def test_commit_message_passes_with_real_ticket_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="MSGY", source_root=str(repo))
            tid = make_ticket(store, "MSGY")
            result = gitgate.check_commit_message(str(repo), store, f"{tid}: real fix, closes the bug")
            self.assertTrue(result.passed)
            self.assertEqual(result.matched_ticket, tid)

    def test_commit_message_rejects_ticket_shaped_string_that_does_not_exist(self):
        # A message that LOOKS like it names a ticket but the ticket was never actually
        # created must still block -- this is the whole point of T-7 over a bare regex check:
        # binding to a real, checkable artifact, not a plausible-looking string.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="MSGZ", source_root=str(repo))
            result = gitgate.check_commit_message(str(repo), store, "MSGZ-9999: fake ticket that was never created")
            self.assertFalse(result.passed)
            self.assertIsNone(result.matched_ticket)

    def test_commit_message_ignores_ticket_from_unregistered_project(self):
        # A real-looking ticket ID for a DIFFERENT project (not registered against this repo)
        # must not satisfy the check -- it's real evidence of the wrong thing.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="HEREX", source_root=str(repo))
            # Register a second, unrelated project in the same store with a real ticket.
            store.register_project("THEREX", "THEREX", source_root="/somewhere/else")
            other_tid = store.create_ticket(
                ticket_type="Task", reporter="tester", actor="tester", project="THEREX",
                summary="unrelated ticket",
            )
            result = gitgate.check_commit_message(str(repo), store, f"{other_tid}: wrong project's ticket")
            self.assertFalse(result.passed)
            self.assertIsNone(result.matched_ticket)

    def test_commit_message_zero_registered_projects_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, _ = make_store(tmp, prefix="OTHERY", source_root="/nowhere/related")
            result = gitgate.check_commit_message(str(repo), store, "OTHERY-1: doesn't matter")
            self.assertFalse(result.passed)
            self.assertEqual(result.projects_checked, [])
            self.assertIn("no TESSERA project registered", result.reason)

    def test_main_commit_msg_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, db_path = make_store(tmp, prefix="CMSGX", source_root=str(repo))
            msg_file = Path(tmp) / "COMMIT_EDITMSG"

            old_cwd = os.getcwd()
            old_db = os.environ.get("TESSGUARD_DB_PATH")
            old_argv = sys.argv
            os.chdir(repo)
            os.environ["TESSGUARD_DB_PATH"] = str(db_path)
            try:
                msg_file.write_text("no ticket named here")
                sys.argv = ["gitgate", "--commit-msg", str(msg_file)]
                self.assertEqual(gitgate.main_commit_msg(), 1)  # blocked, no real ticket named

                tid = make_ticket(store, "CMSGX")
                msg_file.write_text(f"{tid}: real fix")
                sys.argv = ["gitgate", "--commit-msg", str(msg_file)]
                self.assertEqual(gitgate.main_commit_msg(), 0)  # passes, real ticket named
            finally:
                os.chdir(old_cwd)
                sys.argv = old_argv
                if old_db is None:
                    os.environ.pop("TESSGUARD_DB_PATH", None)
                else:
                    os.environ["TESSGUARD_DB_PATH"] = old_db

    def test_e2e_real_commit_msg_hook_block_then_pass(self):
        """No mocks: a real git repo, a real commit-msg hook invocation via subprocess, a real
        tessera.db -- exercising the actual .githooks/commit-msg shim's invocation shape."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, db_path = make_store(tmp, prefix="E2EM", source_root=str(repo))
            msg_file = Path(tmp) / "MSG"

            ticket_system_root = str(Path(__file__).resolve().parents[3])
            env = dict(os.environ, TESSGUARD_DB_PATH=str(db_path), PYTHONPATH=ticket_system_root)

            msg_file.write_text("no real ticket referenced")
            proc = subprocess.run(
                ["python3", "-m", "tessera.tessguard.gitgate", "--commit-msg", str(msg_file)],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)

            tid = make_ticket(store, "E2EM")
            msg_file.write_text(f"{tid}: real, checkable fix")
            proc2 = subprocess.run(
                ["python3", "-m", "tessera.tessguard.gitgate", "--commit-msg", str(msg_file)],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            self.assertEqual(proc2.returncode, 0)

    def test_e2e_real_repo_block_then_pass(self):
        """No mocks on git or the DB: a real git repo, a real commit attempt, a real
        tessera.db -- exercising check_gate the same way the installed .githooks/pre-commit
        shim would."""
        import os
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo_internal(repo)
            store, db_path = make_store(tmp, prefix="E2EX", source_root=str(repo))

            # Negative case: check_gate blocks before any real activity exists.
            result = gitgate.check_gate(str(repo), store)
            self.assertFalse(result.passed)

            # Real commit attempt, gated by literally invoking gitgate.main() the way the
            # shim does, with a real subprocess so the exit code is genuinely observed by
            # something outside this process.
            # The temp repo is a standalone fixture, not ticket-system itself -- unlike the
            # real installed shim (which always runs with cwd=ticket-system, where `tessera`
            # is naturally importable), this subprocess needs PYTHONPATH pointed at
            # ticket-system explicitly so `python3 -m tessera.tessguard.gitgate` resolves.
            ticket_system_root = str(Path(__file__).resolve().parents[3])
            env = dict(os.environ, TESSGUARD_DB_PATH=str(db_path), PYTHONPATH=ticket_system_root)
            proc = subprocess.run(
                ["python3", "-m", "tessera.tessguard.gitgate"],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)

            # Positive case: after real activity, the same invocation passes.
            tid = make_ticket(store, "E2EX")
            store.add_comment(tid, "tester", "logged before commit")
            proc2 = subprocess.run(
                ["python3", "-m", "tessera.tessguard.gitgate"],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            self.assertEqual(proc2.returncode, 0)


if __name__ == "__main__":
    unittest.main()
