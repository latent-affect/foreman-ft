"""Regression tests for ATLASSN-154's registry_write_guard.py.

    python3 -m unittest test_registry_write_guard -v

Same real-subprocess dispatch convention as this suite's sibling ledger guard tests
(test_ledger_write_guard.py, test_review_events_ledger_guard.py) -- a hook-shaped stdin
payload in, stdout out, nothing mocked.

CHV2-115 adds the alias/expansion class Iris Support's adversarial probe measured getting
through: tilde and $HOME spellings (RC1), cased leaf/parent/sidecar (RC2), cp/mv into the
store's directory (RC3), and the hardlink alias (RC4), plus the false-positive controls that
have to stay open for the fix to be worth landing -- above all the legitimate backup copy
whose SOURCE is the store, which Bob's CHV2-115 QA prep named in advance as the false positive
a careless RC3 fix would introduce.

The CHV2-115 tests assert on the PARSED permissionDecision, not on a substring of stdout. The
pre-existing tests above them still use `assertIn('"permissionDecision": "deny"', ...)`, which
passes on any stdout that merely contains that text -- including a stdout that also contains an
allow, or a decision emitted for a different reason than the test believes. They are left as
they are because rewriting them is not CHV2-115's change, but new assertions do not extend the
weaker convention.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent / "registry_write_guard.py")
REAL_REGISTRY_PATH = str(Path.home() / ".claude" / "foreman" / "registry" / "registry.db")
REAL_REGISTRY_DIR = str(Path.home() / ".claude" / "foreman" / "registry")


def run(payload):
    proc = subprocess.run(
        [sys.executable, SCRIPT], input=json.dumps(payload), capture_output=True, text=True,
    )
    return proc


def decision(proc):
    """The hook's permissionDecision, parsed from its real JSON response, or None when the hook
    stayed silent (which is how this suite's hooks spell "allow").

    Deliberately not a substring check. `"permissionDecision": "deny"` appearing ANYWHERE in
    stdout satisfies assertIn even when the surrounding object says something else, so a
    substring assertion cannot distinguish a real deny from a deny-shaped fragment. Parsing
    also fails loudly if the hook ever emits something that is not a single JSON object, which
    a substring match would silently tolerate."""
    out = proc.stdout.strip()
    if not out:
        return None
    return json.loads(out).get("hookSpecificOutput", {}).get("permissionDecision")


class RegistryWriteGuardTests(unittest.TestCase):
    def test_direct_write_to_registry_denied(self):
        proc = run({
            "session_id": "s1", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": REAL_REGISTRY_PATH, "content": "x"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_write_to_registry_relative_path_denied(self):
        # Resolved relative to the payload's own cwd, same as ledger_write_guard's convention --
        # exercises resolve_write_target's non-absolute branch for real.
        proc = run({
            "session_id": "s1b", "cwd": str(Path.home() / ".claude" / "foreman" / "registry"),
            "tool_name": "Write", "tool_input": {"file_path": "registry.db", "content": "x"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_write_to_wal_sidecar_denied(self):
        proc = run({
            "session_id": "s1c", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": REAL_REGISTRY_PATH + "-wal", "content": "x"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_multiedit_to_registry_denied(self):
        proc = run({
            "session_id": "s2b", "cwd": "/tmp", "tool_name": "MultiEdit",
            "tool_input": {"file_path": REAL_REGISTRY_PATH,
                            "edits": [{"old_string": "x", "new_string": "forged"}]},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_direct_edit_to_registry_denied(self):
        proc = run({
            "session_id": "s2", "cwd": "/tmp", "tool_name": "Edit",
            "tool_input": {"file_path": REAL_REGISTRY_PATH},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_bash_echo_redirect_denied(self):
        """CHV2-117: renamed from test_bash_sqlite3_cli_redirect_denied. That name claimed
        sqlite3-write coverage this test never provided -- its body has always been a plain
        `echo ... >> path` redirect, with no sqlite3 invocation anywhere in it. The 15/15 pass it
        contributed to read as though the guard's sqlite3-write behavior was under test; it was
        not. Kept and renamed rather than deleted: a plain `>>` at REAL_REGISTRY_PATH itself is a
        real, non-redundant case none of the alias tests below cover (they use tilde, $HOME, or
        cased spellings, never the bare path)."""
        proc = run({
            "session_id": "s3", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": f"echo forged >> {REAL_REGISTRY_PATH}"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_disclosed_residual_sqlite3_cli_update_not_caught(self):
        """CHV2-117. The real gap the renamed test's old name implied was covered: a genuine
        `sqlite3 <store> "UPDATE ..."` invocation carries no shell write operator anywhere in its
        own visible text -- no `>>`, `tee`, `sed -i`, `cp`, `mv`, or `dd ... of=` -- so the
        text-match backstop this guard relies on for Bash commands has nothing to match, the same
        shape as F1 and test_disclosed_residual_obfuscated_bash_write_not_caught's Python
        one-liner. Confirmed live against the real guard before this test was written: piping this
        exact command produced no output (allow).

        Documents, does not fix -- GOALS.json names this out of scope (F1, inherited from the
        shared component_coupling mechanism every sibling guard in this suite shares), and this
        assertion exists so that gap has real, executed coverage instead of a mislabeled test
        merely implying it does."""
        proc = run({
            "session_id": "s3b", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": f'sqlite3 {REAL_REGISTRY_PATH} "UPDATE assertion SET x=1"'},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_bash_sed_i_on_registry_denied(self):
        proc = run({
            "session_id": "s4", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": f"sed -i '' 's/x/y/' {REAL_REGISTRY_PATH}"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_bash_cp_over_registry_denied(self):
        proc = run({
            "session_id": "s4b", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": f"cp /tmp/forged.db {REAL_REGISTRY_PATH}"},
        })
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_unrelated_write_not_blocked(self):
        proc = run({
            "session_id": "s6", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/proj/some_other_file.py", "content": "x"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_unrelated_bash_command_not_blocked(self):
        proc = run({
            "session_id": "s7", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": "echo hello > /tmp/other-file.txt"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_reading_the_registry_is_not_blocked(self):
        proc = run({
            "session_id": "s8", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": f"sqlite3 {REAL_REGISTRY_PATH} 'select * from assertion'"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_same_named_file_elsewhere_not_blocked(self):
        # F3-shaped check (ledger_write_guard's own directory-anchoring finding): a file that
        # merely SHARES the basename "registry.db" somewhere else on the machine is a different
        # file and must not be caught by a bare-basename match.
        proc = run({
            "session_id": "s9", "cwd": "/tmp", "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/some-other-project/registry.db", "content": "x"},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_legitimate_write_path_api_call_not_caught(self):
        # Documents the disclosed, deliberate scope: an in-process write through
        # atlas.registry.write_path never names registry.db as a Bash tool-call target, so it
        # is structurally invisible to (and correctly untouched by) this guard.
        command = (
            "cd /Users/m5/dev/atlas-sonnet && /Users/m5/.venv/bin/python3 -c "
            "\"from atlas.registry import write_path; write_path.write(...)\""
        )
        proc = run({
            "session_id": "s10", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_commit_message_mentioning_registry_not_blocked(self):
        """CHV2-112: found live 2026-09-18 (Bob, landing ATLASSN-177). A commit message that
        merely DISCUSSES a registry.db finding, with an unrelated '>' character elsewhere in the
        same quoted message (e.g. a section-reference arrow), is not a write to the registry --
        neither operator's own destination is the registry path -- and must not be denied. Prior
        to the fix this reproduced the bounce exactly: denied with rule_id
        REGISTRY-WRITE-GUARD:bash-text-match-denied although the command never wrote anywhere."""
        command = (
            'git commit -m "Document ATLASSN-177 finding: registry.db bootstrap gap, '
            'see ARCHITECTURE.md section > 34.0 for detail"'
        )
        proc = run({
            "session_id": "s12", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_disclosed_residual_obfuscated_bash_write_not_caught(self):
        """Documents, does not fix, the same residual ledger_write_guard.py's own identical
        test names: a Bash command whose OWN visible text never spells "registry.db" -- built
        here from concatenated string parts inside a python3 -c one-liner -- is invisible to
        this guard's text-match check. This assertion documents the gap so nobody mistakes
        silence here for coverage; it is not a claim that this attack is blocked."""
        obfuscated = (
            "python3 -c \"import pathlib; "
            "p = pathlib.Path('.claude/foreman/registry') / ('regi' + 'stry.d' + 'b'); "
            "p.open('r+b')\""
        )
        self.assertNotIn("registry.db", obfuscated)  # confirms the command text itself is clean
        proc = run({
            "session_id": "s11", "cwd": "/tmp", "tool_name": "Bash",
            "tool_input": {"command": obfuscated},
        })
        self.assertEqual(proc.stdout.strip(), "")


class DecisionHelperTests(unittest.TestCase):
    """The assertion helper the CHV2-115 tests below depend on has to be shown to discriminate
    before its passing results mean anything. A helper that returned "deny" unconditionally, or
    None unconditionally, would make half the suite below vacuously green."""

    def test_silence_reads_as_no_decision(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="   \n", stderr="")
        self.assertIsNone(decision(proc))

    def test_deny_is_parsed_from_the_real_envelope(self):
        body = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                   "permissionDecision": "deny",
                                                   "permissionDecisionReason": "r"}})
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=body, stderr="")
        self.assertEqual(decision(proc), "deny")

    def test_deny_shaped_text_outside_the_envelope_is_not_a_deny(self):
        """The exact case a substring assertion cannot tell apart: the literal byte sequence
        '"permissionDecision": "deny"' really does appear in stdout, unescaped, but it belongs
        to a nested object beside the decision envelope rather than to the decision itself,
        which says allow. assertIn passes on this. The parsing helper must not.

        Synthetic -- no hook in this suite emits this shape today. Its job is to prove the
        helper discriminates rather than to model real output, and it is written so that the
        substring assertion below genuinely succeeds. An earlier draft built the fixture with
        json.dumps() around a string containing that text, which escaped the inner quotes so
        the substring never appeared, making the control pass for the wrong reason."""
        body = json.dumps({
            "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                    "permissionDecision": "allow",
                                    "permissionDecisionReason": "nothing to see"},
            "priorRun": {"permissionDecision": "deny"},
        })
        self.assertIn('"permissionDecision": "deny"', body)  # a substring check DOES pass here
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=body, stderr="")
        self.assertEqual(decision(proc), "allow")  # the parsing helper does not


class RegistryWriteGuardAliasTests(unittest.TestCase):
    """CHV2-115. Every spelling here resolves to the one real store on this machine. Iris
    Support measured the pre-fix guard allowing all of them (probe evidence:
    FINDING-CHV2-108-REGISTRY-WRITE-GUARD-ALIAS-PROBE-20260918.md). No test in this class
    executes the command it scores -- the guard is asked for a decision and nothing is written
    to the real store."""

    def denies(self, payload):
        self.assertEqual(decision(run(payload)), "deny")

    def allows(self, payload):
        self.assertIsNone(decision(run(payload)))

    # ---- RC1: home expansion -------------------------------------------------------------

    def test_bash_tilde_redirect_denied(self):
        """The spelling ARCHITECTURE.md 34.0 and this guard's own docstring both use. Not an
        evasion technique -- the default way the path is written."""
        self.denies({"session_id": "c1", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     "printf x > ~/.claude/foreman/registry/registry.db"}})

    def test_bash_home_variable_redirect_denied(self):
        self.denies({"session_id": "c2", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     "printf x > $HOME/.claude/foreman/registry/registry.db"}})

    def test_bash_quoted_home_variable_redirect_denied(self):
        self.denies({"session_id": "c3", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     'printf x > "$HOME/.claude/foreman/registry/registry.db"'}})

    def test_bash_tilde_tee_denied(self):
        self.denies({"session_id": "c4", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     "tee ~/.claude/foreman/registry/registry.db < /dev/null"}})

    def test_direct_write_tilde_path_denied(self):
        """RC1 on the Edit/Write branch, not just the Bash branch -- both reach the same
        is_registry_path(), so the expansion fix has to cover both and this pins it."""
        self.denies({"session_id": "c5", "cwd": "/tmp", "tool_name": "Write",
                      "tool_input": {"file_path": "~/.claude/foreman/registry/registry.db",
                                     "content": "x"}})

    # ---- RC2: case -----------------------------------------------------------------------

    def test_bash_cased_leaf_denied(self):
        self.denies({"session_id": "c6", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command": f"printf x >> {REAL_REGISTRY_DIR}/REGISTRY.DB"}})

    def test_bash_cased_parent_denied(self):
        """CHV2-116's own root cause, exercised here against this guard: only the LEAF being
        case-folded is not enough, because a cased ANCESTOR resolves to the same real directory
        on a case-insensitive filesystem."""
        cased_parent = str(Path.home() / ".claude" / "FOREMAN" / "registry" / "registry.db")
        self.denies({"session_id": "c7", "cwd": "/tmp", "tool_name": "Write",
                      "tool_input": {"file_path": cased_parent, "content": "x"}})

    def test_bash_cased_existing_sidecar_denied(self):
        self.denies({"session_id": "c8", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     f"printf x >> {REAL_REGISTRY_DIR}/REGISTRY.DB-WAL"}})

    def test_bash_cased_absent_sidecar_denied(self):
        """The one row whose only route to a deny is the measured case fallback, because
        registry.db-journal does not exist on disk and so has no inode to compare. Verified by
        re-scoring with REGISTRY_DIR_CASE_INSENSITIVE forced False, where this row -- and only
        this row -- flips to allow while the inode-backed rows above stay denied."""
        self.denies({"session_id": "c9", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     f"printf x > {REAL_REGISTRY_DIR}/REGISTRY.DB-JOURNAL"}})

    # ---- RC3: cp/mv into the store's directory -------------------------------------------

    def test_bash_cp_into_registry_dir_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "registry.db"
            src.write_text("replica\n")
            self.denies({"session_id": "c10", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command": f"cp {src} {REAL_REGISTRY_DIR}/"}})

    def test_bash_mv_into_registry_dir_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "registry.db"
            src.write_text("replica\n")
            self.denies({"session_id": "c11", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command": f"mv {src} {REAL_REGISTRY_DIR}/"}})

    def test_bash_cp_unrelated_file_into_registry_dir_not_blocked(self):
        """The destination IS the store's directory, but the landed filename would not be the
        store. Copying an unrelated file into that directory is not a write to the registry and
        must stay allowed -- this is what keeps the RC3 fix from degenerating into "deny any cp
        whose destination is that directory"."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "notes.txt"
            src.write_text("harmless\n")
            self.allows({"session_id": "c12", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command": f"cp {src} {REAL_REGISTRY_DIR}/"}})

    def test_bash_cp_store_to_backup_dir_not_blocked(self):
        """BOB'S CHV2-115 QA PREP, ITEM 3, pinned open. `cp <store> /some/backup/dir/` has a
        SOURCE basename matching the store and is a legitimate backup that writes nowhere near
        it. An RC3 fix keyed on the source's basename alone would deny this, trading CHV2-112's
        false positive straight back. The destination's own identity is what gates the branch;
        the source basename is consulted only after the destination has been confirmed to be
        the store's directory."""
        with tempfile.TemporaryDirectory() as tmp:
            self.allows({"session_id": "c13", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command": f"cp {REAL_REGISTRY_PATH} {tmp}/"}})

    def test_bash_cp_store_to_named_backup_file_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.allows({"session_id": "c14", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command":
                                         f"cp {REAL_REGISTRY_PATH} {tmp}/registry.db"}})

    # ---- RC4: hardlink alias --------------------------------------------------------------

    def test_hardlink_alias_denied(self):
        """A hardlink is a second directory entry for one inode, not a link to follow, so
        Path.resolve() cannot see through it and no string comparison ever will. The identity
        check closes it as a side effect of comparing (st_dev, st_ino) rather than names.

        Creates the link, scores the decision, unlinks. The command is never executed -- writing
        through this path would corrupt the real store, which is exactly why the guard has to
        deny it."""
        if not os.path.exists(REAL_REGISTRY_PATH):
            self.skipTest("registry.db absent; nothing to hardlink")
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "innocuous-name.db"
            try:
                os.link(REAL_REGISTRY_PATH, alias)
            except OSError as exc:
                self.skipTest(f"hardlink unavailable: {exc}")
            self.assertTrue(os.path.samefile(alias, REAL_REGISTRY_PATH))  # ground truth
            self.denies({"session_id": "c15", "cwd": "/tmp", "tool_name": "Write",
                          "tool_input": {"file_path": str(alias), "content": "x"}})

    # ---- disclosed residual ---------------------------------------------------------------

    def test_disclosed_residual_mv_store_away_not_caught(self):
        """Documents, does not fix. `mv <store> /elsewhere/` destroys the store by moving it,
        which is a real mutation, but the destination is not the registry and no destination
        check can see it. Out of CHV2-115's filed RC1-RC4 scope and named in the module
        docstring; asserted here so silence is not mistaken for coverage."""
        with tempfile.TemporaryDirectory() as tmp:
            self.allows({"session_id": "c16", "cwd": "/tmp", "tool_name": "Bash",
                          "tool_input": {"command": f"mv {REAL_REGISTRY_PATH} {tmp}/"}})


class RegistryWriteGuardRsyncWrapperTests(unittest.TestCase):
    """CHV2-180 (2026-09-20, Nadia Osei's real STRIDE/validate finding, Build 8). CHV2-177's
    GUARD_RSYNC_RE fix anchored `rsync` to command position, closing the plain unwrapped form --
    but the same bypass class reopens through a different wrapper shape: `rsync` piped through
    `xargs`, invoked via `find ... -exec`, or run under GNU `parallel` none put `rsync` at that
    anchored position. `xargs` is fixed here (added as a flag-swallowing wrapper, same shape as
    `sudo`); `find -exec` and `parallel` are disclosed, pinned-open residuals -- see
    GUARD_RSYNC_RE's own module comment for why each is a different, deeper problem than a
    trigger-regex gap and not one this fix attempts."""

    def denies(self, payload):
        self.assertEqual(decision(run(payload)), "deny")

    def allows(self, payload):
        self.assertIsNone(decision(run(payload)))

    # ---- the fix: xargs --------------------------------------------------------------------

    def test_bash_rsync_via_xargs_denied(self):
        """THE CORE REPRO. `xargs -I{} rsync {} <store>` never matched GUARD_RSYNC_RE's anchored
        shape before this fix -- confirmed live against the pristine pre-fix regex that this
        exact command returned decision=None against the real registry.db."""
        self.denies({"session_id": "c17", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     f"echo x | xargs -I{{}} rsync src {REAL_REGISTRY_PATH}"}})

    def test_bash_rsync_via_sudo_xargs_denied(self):
        """The new xargs wrapper composes with the pre-existing sudo wrapper, using the same
        flag-swallowing shape sudo alone already established."""
        self.denies({"session_id": "c18", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     f"sudo xargs -I{{}} rsync src {REAL_REGISTRY_PATH}"}})

    def test_bash_rsync_via_xargs_unrelated_destination_not_blocked(self):
        """Negative control: the new xargs wrapper must not turn into an unconditional deny on
        sight of xargs -- rsync wrapped in xargs to a genuinely unrelated destination stays
        allowed, exactly like the plain unwrapped form already does."""
        self.allows({"session_id": "c19", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command":
                                     "echo x | xargs -I{} rsync src /tmp/backup/"}})

    def test_bash_rsync_plain_still_denied(self):
        """Regression check: CHV2-177's own already-fixed unwrapped form must still deny after
        this change -- the new xargs alternative is additive, not a replacement."""
        self.denies({"session_id": "c22", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command": f"rsync src {REAL_REGISTRY_PATH}"}})

    # ---- disclosed residuals: find -exec, parallel ------------------------------------------

    def test_disclosed_residual_find_exec_rsync_not_caught(self):
        """Documents, does not fix. `find`'s wrapped command does not sit at command position at
        all (arbitrary find-predicate tokens precede `-exec`), and even a trigger match would hit
        the SAME tail-token destination-extraction failure the module's own GUARD_RSYNC_RE
        comment proves for cp/mv under `-exec` today. Out of this fix's scope and named in the
        module docstring; asserted here so silence is not mistaken for coverage."""
        command = f"find . -name x -exec rsync {{}} {REAL_REGISTRY_PATH} \\;"
        self.allows({"session_id": "c20", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command": command}})

    def test_disclosed_residual_parallel_rsync_not_caught(self):
        """Documents, does not fix. GNU parallel's own `:::` argument-list separator breaks the
        tail-token destination heuristic the same way find's `\\;` terminator does -- the tail
        positional token is an input list item, not the real destination."""
        command = f"parallel rsync {{}} {REAL_REGISTRY_PATH} ::: src"
        self.allows({"session_id": "c21", "cwd": "/tmp", "tool_name": "Bash",
                      "tool_input": {"command": command}})


if __name__ == "__main__":
    unittest.main()
