"""Regression tests for FORE-537's defeater_ledger.py, one section per GOALS.json criterion
(C1-C5, the last two added by this ticket's own falsification-record amendment), plus
FORE-635's own Python-write-detection tests.

    python3 -m unittest test_defeater_ledger -v

Gate tests use the same real-subprocess dispatch convention as test_ledger_write_guard.py -- a
hook-shaped stdin payload in, stdout out, nothing mocked. Library tests (append_defeat/read_all)
call the module directly against a tempdir-backed store_path, never the real
.foreman/defeaters.jsonl.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SCRIPT = str(HERE / "defeater_ledger.py")

sys.path.insert(0, str(HERE))
import defeater_ledger as dl  # noqa: E402

sys.path.insert(0, str(HERE.parent))
import component_coupling as cc  # noqa: E402

VERDICT_LOG = Path.home() / ".claude" / "telemetry" / "verdicts.jsonl"


def run(payload):
    proc = subprocess.run(
        [sys.executable, SCRIPT], input=json.dumps(payload), capture_output=True, text=True,
    )
    return proc


def parsed_decision(stdout_text):
    stripped = stdout_text.strip()
    if stripped == "":
        return None, None
    obj = json.loads(stripped)
    hso = obj.get("hookSpecificOutput") if isinstance(obj, dict) else None
    if not isinstance(hso, dict):
        return None, None
    return hso.get("permissionDecision"), hso.get("permissionDecisionReason")


class C1AppendOnlyTests(unittest.TestCase):
    """C1: append via the blessed function succeeds; direct Edit/Write/Bash mutation denied."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="defeater-ledger-test-")
        self.project_root = Path(self.tempdir.name)
        (self.project_root / ".foreman").mkdir()
        self.store_path = self.project_root / ".foreman" / "defeaters.jsonl"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_c1_append_via_function_succeeds(self):
        row = dl.append_defeat(
            defeater_id="TEST-D1", signal="sig", description="desc",
            first_observed="2026-09-08T00:00:00Z", ticket_or_incident_ref="TEST-1",
            status="open", store_path=self.store_path,
        )
        self.assertEqual(row["defeater_id"], "TEST-D1")
        on_disk = dl.read_all(store_path=self.store_path)
        self.assertEqual(len(on_disk), 1)
        self.assertEqual(on_disk[0]["defeater_id"], "TEST-D1")

    def test_c1_direct_edit_to_existing_line_denied(self):
        proc = run({
            "session_id": "s1", "cwd": str(self.project_root), "tool_name": "Edit",
            "tool_input": {"file_path": str(self.store_path), "old_string": "x",
                            "new_string": "forged"},
        })
        decision, reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("defeaters.jsonl", reason)

    def test_c1_direct_write_to_file_denied(self):
        proc = run({
            "session_id": "s2", "cwd": str(self.project_root), "tool_name": "Write",
            "tool_input": {"file_path": str(self.store_path), "content": "forged\n"},
        })
        decision, _reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")

    def test_c1_bash_sed_i_mutation_denied(self):
        proc = run({
            "session_id": "s3", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": f"sed -i '' 's/x/y/' {self.store_path}"},
        })
        decision, _reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")

    def test_c1_bash_redirect_denied(self):
        proc = run({
            "session_id": "s4", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": f"echo forged >> {self.store_path}"},
        })
        decision, _reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")

    def test_c1_unrelated_write_not_blocked(self):
        proc = run({
            "session_id": "s5", "cwd": str(self.project_root), "tool_name": "Write",
            "tool_input": {"file_path": str(self.project_root / "notes.md"), "content": "x"},
        })
        self.assertEqual(proc.stdout.strip(), "")


class FORE635PythonWriteDetectionTests(unittest.TestCase):
    """FORE-635 (F5, LOW-MEDIUM): the reproduced gap -- a Bash-invoked python -c write bypassed
    both the structural shell-write extractor and the shell-operator text backstop, with zero
    denial. Widened detection must catch the reproduced shape while leaving read-only python
    commands and unrelated python writes alone."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="defeater-ledger-fore635-")
        self.project_root = Path(self.tempdir.name)
        (self.project_root / ".foreman").mkdir()
        self.store_path = self.project_root / ".foreman" / "defeaters.jsonl"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_the_exact_reproduced_gap_is_now_denied(self):
        """The literal shape named in the finding: python3 -c "open(TARGET,'w').write(...)".
        Before this fix, this command produced NO decision at all (silent stdout) -- neither
        the structural extractor nor the pre-existing text backstop matched it."""
        command = (
            f"python3 -c \"open('{self.store_path}', 'w').write('{{}}')\""
        )
        proc = run({
            "session_id": "f635-1", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        decision, reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("defeaters.jsonl", reason)

    def test_python_append_mode_also_denied(self):
        command = f"python3 -c \"open('{self.store_path}', 'a').write('x\\n')\""
        proc = run({
            "session_id": "f635-2", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        decision, _reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")

    def test_pathlib_write_text_denied(self):
        command = (
            f"python3 -c \"from pathlib import Path; "
            f"Path('{self.store_path}').write_text('{{}}')\""
        )
        proc = run({
            "session_id": "f635-3", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        decision, _reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")

    def test_os_open_with_write_flag_denied(self):
        command = (
            f"python3 -c \"import os; "
            f"os.open('{self.store_path}', os.O_WRONLY|os.O_CREAT)\""
        )
        proc = run({
            "session_id": "f635-4", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        decision, _reason = parsed_decision(proc.stdout)
        self.assertEqual(decision, "deny")

    def test_python_read_only_command_not_blocked(self):
        """Negative control: a read-only python command referencing the file must NOT be
        blocked -- this heuristic targets write-shaped calls specifically, not merely any
        python invocation that mentions the filename."""
        command = f"python3 -c \"print(open('{self.store_path}').read())\""
        proc = run({
            "session_id": "f635-5", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_python_write_to_unrelated_file_not_blocked(self):
        """Negative control: a genuine python write to some OTHER file must not be blocked --
        the heuristic requires the store's own name in the command text, not any write-shaped
        python call."""
        other = self.project_root / "notes.txt"
        command = f"python3 -c \"open('{other}', 'w').write('hi')\""
        proc = run({
            "session_id": "f635-6", "cwd": str(self.project_root), "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        self.assertEqual(proc.stdout.strip(), "")

    def test_unit_level_bash_text_mentions_python_write(self):
        """Direct unit coverage of the predicate itself, independent of subprocess dispatch."""
        self.assertTrue(dl.bash_text_mentions_python_write(
            "python3 -c \"open('.foreman/defeaters.jsonl','w').write('x')\""))
        self.assertTrue(dl.bash_text_mentions_python_write(
            "python -c \"open('.foreman/defeaters.jsonl','wb').write(b'x')\""))
        self.assertFalse(dl.bash_text_mentions_python_write(
            "python3 -c \"print(open('.foreman/defeaters.jsonl').read())\""),
            "a read-only open() must not trigger the write-signal check")
        self.assertFalse(dl.bash_text_mentions_python_write(
            "python3 -c \"open('/tmp/unrelated.txt','w').write('x')\""),
            "a write to an unrelated file must not trigger the check")
        self.assertFalse(dl.bash_text_mentions_python_write(
            "echo 'open(defeaters.jsonl, w)' >> unrelated.log"),
            "no python invocation present -- must not fire on prose alone")


class C2SeedCitationTests(unittest.TestCase):
    """C2: the shipped store contains all five named defeats, each with a citation that
    resolves to something real -- not merely a non-empty string."""

    STORE_PATH = REPO_ROOT / ".foreman" / "defeaters.jsonl"
    REQUIRED_IDS = {
        "D1-HOOK-IDENTITY-SIGNALS-FORGEABLE",
        "D2-LEDGER-ORIGIN-HARNESS-CLAUDE-PID-SPOOF",
        "D3-K2-DIRECT-DATABASE-WRITE-BYPASS",
        "D4-DISPATCH-ID-LEDGER-FIELDS-FORGEABLE",
        "D5-DECLARED-TIMESTAMP-FORGEABLE-UNCHECKED",
    }
    # CHV2-88 part A: the resolution logic that used to live here, as _resolve_sub_ref and
    # _resolve_ref, now lives in defeater_ledger.resolve_citation and this class calls it. One
    # implementation, not two written to look alike. What these tests assert is unchanged --
    # they were the tests that verified the logic in the first place, which is exactly why it
    # is worth having the shipped module run the same code they proved rather than a copy of it.
    def _resolve_ref(self, ref):
        return dl.resolve_citation(ref)

    def test_c2_seed_set_contains_five_named_defeats(self):
        self.assertTrue(self.STORE_PATH.is_file(), f"{self.STORE_PATH} does not exist -- run seed_defeaters.py")
        rows = dl.read_all(store_path=self.STORE_PATH)
        seen_ids = {row.get("defeater_id") for row in rows}
        missing = self.REQUIRED_IDS - seen_ids
        self.assertFalse(missing, f"seed set is missing: {missing}")
        for row in rows:
            if row.get("defeater_id") in self.REQUIRED_IDS:
                ref = row.get("ticket_or_incident_ref")
                self.assertTrue(isinstance(ref, str) and ref.strip(),
                                 f"{row.get('defeater_id')} has no non-empty ticket_or_incident_ref")

    def test_c2_seed_citations_resolve_to_real_artifacts(self):
        rows = dl.read_all(store_path=self.STORE_PATH)
        by_id = {row["defeater_id"]: row for row in rows if row.get("defeater_id") in self.REQUIRED_IDS}
        self.assertEqual(set(by_id), self.REQUIRED_IDS)
        for defeater_id, row in sorted(by_id.items()):
            ok, detail = self._resolve_ref(row["ticket_or_incident_ref"])
            self.assertTrue(ok, f"{defeater_id}'s ref {row['ticket_or_incident_ref']!r} did not "
                                 f"resolve to a real artifact: {detail}")

    def test_c2_fabricated_ref_fails_resolution(self):
        """The discriminating half of C2's strengthened check: a well-formed but fake ticket
        ID, a fake commit hash, and a fake filename must all FAIL resolution -- proving this
        test can actually catch a fabricated-but-plausible citation, not just rubber-stamp
        whatever's in the file."""
        ok, _detail = self._resolve_ref("FORE-99999999")
        self.assertFalse(ok, "a nonexistent ticket ID must not resolve")
        ok, _detail = self._resolve_ref("0000000deadbeef0000000deadbeef00000000")
        self.assertFalse(ok, "a nonexistent commit hash must not resolve")
        ok, _detail = self._resolve_ref("THIS-DOCUMENT-DOES-NOT-EXIST-20260101.md")
        self.assertFalse(ok, "a nonexistent named document must not resolve")

    def test_c2_empty_ref_does_not_resolve(self):
        """CHV2-88 part A. The only behaviour that genuinely CHANGED in the move, so it is the
        only one a passing pre-existing test could not already cover. The version inside this
        class raised a unittest assertion on a ref with no sub-references; a module function has
        no assertion to make, so it returns False. False is also the fail-closed direction: a
        citation naming nothing cannot be said to resolve to something real."""
        for empty in ("", "   ", ",", " , , "):
            ok, detail = self._resolve_ref(empty)
            self.assertFalse(ok, f"ref {empty!r} must not resolve")
            self.assertTrue(detail, "a refusal must still say why")

    def test_c2_resolution_delegates_to_the_module_rather_than_a_local_copy(self):
        """The point of part A was removing the duplicate implementation. If this class ever
        grows its own copy back, these C2 tests would keep passing while the shipped module
        drifted away from the logic they verify -- which is the situation part A exists to end.

        Asserts delegation by result over inputs that exercise all four branches, rather than by
        inspecting the function object: a local copy that still agreed on every one of these
        would be a copy that had not drifted, which is the property actually worth having."""
        for ref in ("", "FORE-99999999", "0000000deadbeef0000000deadbeef00000000",
                    "ARCHITECTURE.md", "THIS-DOES-NOT-EXIST-20260101.md"):
            self.assertEqual(self._resolve_ref(ref), dl.resolve_citation(ref),
                             f"{ref!r} resolved differently here than in the module")


class C3StatusChangeTests(unittest.TestCase):
    """C3: a status change is a NEW appended row referencing the original defeater_id, never
    an in-place edit of the original."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="defeater-ledger-c3-")
        self.store_path = Path(self.tempdir.name) / "defeaters.jsonl"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_c3_status_change_appends_new_row_not_edits_original(self):
        original = dl.append_defeat(
            defeater_id="TEST-STATUS-CHANGE", signal="sig", description="original desc",
            first_observed="2026-09-01T00:00:00Z", ticket_or_incident_ref="TEST-1",
            status="open", store_path=self.store_path,
        )
        mitigated = dl.append_defeat(
            defeater_id="TEST-STATUS-CHANGE", signal="sig", description="now mitigated",
            first_observed="2026-09-01T00:00:00Z", ticket_or_incident_ref="TEST-1",
            status="mitigated", evidence_path=str(Path(__file__)), store_path=self.store_path,
        )
        rows = dl.rows_for("TEST-STATUS-CHANGE", store_path=self.store_path)
        self.assertEqual(len(rows), 2, "status change must append a new row, not edit in place")
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["description"], "original desc")
        self.assertEqual(rows[1]["status"], "mitigated")
        self.assertNotEqual(original["appended_at"], mitigated["appended_at"])
        self.assertEqual(dl.current_status("TEST-STATUS-CHANGE", store_path=self.store_path),
                          "mitigated")

    def test_c3_raw_file_has_two_distinct_lines(self):
        dl.append_defeat(defeater_id="TEST-RAW", signal="s", description="d1",
                          first_observed="2026-09-01T00:00:00Z", ticket_or_incident_ref="T-1",
                          status="open", store_path=self.store_path)
        dl.append_defeat(defeater_id="TEST-RAW", signal="s", description="d2",
                          first_observed="2026-09-01T00:00:00Z", ticket_or_incident_ref="T-1",
                          status="mitigated", evidence_path=str(Path(__file__)),
                          store_path=self.store_path)
        lines = self.store_path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotEqual(lines[0], lines[1])


class C4ComponentCouplingTests(unittest.TestCase):
    """C4: the gate resolves target paths through component_coupling, reusing
    extract_bash_write_targets rather than a locally reimplemented parser."""

    def test_c4_gate_imports_component_coupling(self):
        source = (HERE / "defeater_ledger.py").read_text()
        self.assertRegex(source, re.compile(r"^import component_coupling as cc", re.MULTILINE))

    def test_c4_bash_target_extraction_call_site_uses_shared_function(self):
        source = (HERE / "defeater_ledger.py").read_text()
        self.assertIn("cc.extract_bash_write_targets(command, cwd)", source)
        # No locally reimplemented regex-based Bash-target *extractor* alongside it -- the
        # module's own BASH_WRITE_OPERATOR_RE is the coarser text-scan backstop (C1's own
        # "same posture" language), not a second target-extraction path.
        self.assertNotIn("def extract_bash_write_targets", source)

    def test_c4_gate_uses_find_project_root_and_component_root(self):
        source = (HERE / "defeater_ledger.py").read_text()
        self.assertIn("cc.find_project_root(", source)
        self.assertIn("cc.component_root(", source)

    def test_c4_real_bash_write_denied_via_shared_extractor(self):
        """End-to-end: a real cp targeting the store, resolved by the SAME
        extract_bash_write_targets component_coupling.py's other consumers already trust, not
        a bespoke parser this file invented."""
        with tempfile.TemporaryDirectory(prefix="defeater-ledger-c4-") as tmp:
            project_root = Path(tmp)
            (project_root / ".foreman").mkdir()
            store = project_root / ".foreman" / "defeaters.jsonl"
            store.write_text("")
            targets = cc.extract_bash_write_targets(f"cp forged.jsonl {store}", str(project_root))
            self.assertTrue(any(str(t) == str(store.resolve()) for t in targets))
            proc = run({
                "session_id": "c4", "cwd": str(project_root), "tool_name": "Bash",
                "tool_input": {"command": f"cp forged.jsonl {store}"},
            })
            decision, _reason = parsed_decision(proc.stdout)
            self.assertEqual(decision, "deny")


class C5VerdictLedgerTests(unittest.TestCase):
    """C5: a denial produces a real, on-disk verdict-ledger row -- not merely a nonzero exit or
    a printed message. Reads ~/.claude/telemetry/verdicts.jsonl directly, before and after."""

    def _tail_lines(self, path, n=5000):
        try:
            return path.read_text().splitlines()[-n:]
        except FileNotFoundError:
            return []

    def test_c5_denial_produces_real_verdict_ledger_row(self):
        with tempfile.TemporaryDirectory(prefix="defeater-ledger-c5-") as tmp:
            project_root = Path(tmp)
            (project_root / ".foreman").mkdir()
            store = project_root / ".foreman" / "defeaters.jsonl"
            store.write_text("")

            before = set(self._tail_lines(VERDICT_LOG))
            session_id = f"c5-verdict-check-{id(self)}-{__import__('time').time()}"
            proc = run({
                "session_id": session_id, "cwd": str(project_root), "tool_name": "Edit",
                "tool_input": {"file_path": str(store), "old_string": "x", "new_string": "y"},
            })
            decision, _reason = parsed_decision(proc.stdout)
            self.assertEqual(decision, "deny")

            after = self._tail_lines(VERDICT_LOG)
            new_lines = [line for line in after if line not in before]
            matching = []
            for line in new_lines:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if row.get("session_id") == session_id:
                    matching.append(row)
            self.assertTrue(matching, "no new verdict-ledger row found for this session_id -- "
                                       "the denial did not route through hook_common's "
                                       "deny()/verdict-ledger contract")
            row = matching[0]
            self.assertEqual(row.get("verdict"), "fire")
            self.assertEqual(row.get("decision"), "deny")
            self.assertIn("FOREMAN-DEFEATER-LEDGER-WRITE-GUARD", row.get("rule_id") or "")

    def test_c5_bare_exit_would_fail_this_test(self):
        """Documents the discriminating property directly: hc.deny() is what makes C5 real.
        Calling sys.exit(1) with no hook_common call (the exact gap the falsification record
        named) would leave zero matching rows, and the assertion above would fail -- this test
        doesn't assert that failure mode (it can't, without mutating the shipped gate), but the
        C4/C5 structural test above already confirms hc.deny() -- not a bare exit -- is what's
        actually called on every denial path."""
        source = (HERE / "defeater_ledger.py").read_text()
        self.assertNotIn("sys.exit(1)", source)
        self.assertIn("hc.deny(", source)


class C6MitigatedRequiresEvidenceTests(unittest.TestCase):
    """CHV2-88 / PDP section 11.6 provisional decision (orchestrator-7fe5ba, 2026-09-13):
    append_defeat() must not accept a status="mitigated" claim with no resolvable evidence --
    mirrors efficacy_meta_check's own C2 _evidence_resolves() bar, one component over."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="defeater-ledger-c6-")
        self.store_path = Path(self.tempdir.name) / "defeaters.jsonl"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_c6_mitigated_requires_evidence_path(self):
        with self.assertRaises(dl.DefeaterLedgerError):
            dl.append_defeat(defeater_id="TEST-C6-NOEV", signal="s", description="d",
                              first_observed="2026-09-13T00:00:00Z", ticket_or_incident_ref="T-1",
                              status="mitigated", store_path=self.store_path)

    def test_c6_mitigated_evidence_path_must_resolve(self):
        with self.assertRaises(dl.DefeaterLedgerError):
            dl.append_defeat(defeater_id="TEST-C6-FAKE", signal="s", description="d",
                              first_observed="2026-09-13T00:00:00Z", ticket_or_incident_ref="T-1",
                              status="mitigated", evidence_path="/does/not/exist/anywhere.txt",
                              store_path=self.store_path)

    def test_c6_mitigated_with_real_evidence_path_succeeds(self):
        row = dl.append_defeat(defeater_id="TEST-C6-REAL", signal="s", description="d",
                                first_observed="2026-09-13T00:00:00Z", ticket_or_incident_ref="T-1",
                                status="mitigated", evidence_path=str(Path(__file__)),
                                store_path=self.store_path)
        self.assertEqual(row["evidence_path"], str(Path(__file__)))

    def test_c6_open_status_does_not_require_evidence_path(self):
        """Regression control: this requirement must not over-tighten onto status='open'."""
        row = dl.append_defeat(defeater_id="TEST-C6-OPEN", signal="s", description="d",
                                first_observed="2026-09-13T00:00:00Z", ticket_or_incident_ref="T-1",
                                status="open", store_path=self.store_path)
        self.assertIsNone(row.get("evidence_path"))


if __name__ == "__main__":
    unittest.main()
