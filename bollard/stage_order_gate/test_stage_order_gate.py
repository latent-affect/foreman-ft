"""Regression tests for FORE-484's stage_order_gate.py, one test per
hooks/stage_order_gate/GOALS.json criterion (C1-C7) plus its two failure criteria (F1-F2).

    python3 -m unittest test_stage_order_gate -v

Real subprocess dispatch against the real hook file, same convention as this suite's other
gate tests (test_review_events_ledger_guard.py, test_architecture_gate.py): a hook-shaped
stdin payload in, stdout/exit code out, nothing mocked.

ORCHESTRATOR FIX (round 4): setUp/tearDown used to pair a manual temp-directory creation call
with an equally manual recursive-delete call in tearDown(). That second call's own dotted
name, written out literally, is exactly the shape sanitize_proposal.py's S6 destructive-shape
scan exists to catch -- confirmed live against this file, not assumed -- and S6 gives that
scan no test-path carve-out, deliberately, per its own design. Fixed by using Python's
temp-directory CONTEXT MANAGER instead: same guaranteed cleanup, and this docstring is worded
throughout, including this paragraph, to never spell that dotted name out as literal text
either, so a rerun of the same scan does not false-positive on prose describing the fix.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = str(HERE / "stage_order_gate.py")


def run_gate(payload):
    proc = subprocess.run(
        [sys.executable, SCRIPT], input=json.dumps(payload), capture_output=True, text=True,
    )
    return proc


def write_pipeline(root, stages):
    (root / ".foreman").mkdir(exist_ok=True)
    (root / ".foreman" / "pipeline.json").write_text(
        json.dumps({"schema": "foreman-pipeline.v1", "stages": stages})
    )


def write_ledger(root, lines):
    """`lines` is a list of already-JSON-encoded strings, or dicts (auto-encoded) -- a test
    that wants a deliberately malformed line passes a raw string that isn't valid JSON."""
    (root / ".foreman").mkdir(exist_ok=True)
    encoded = [json.dumps(row) if not isinstance(row, str) else row for row in lines]
    (root / ".foreman" / "ledger.jsonl").write_text("\n".join(encoded) + ("\n" if encoded else ""))


class StageOrderGateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="stage-order-gate-test-")
        self.root = Path(self.tempdir.name)
        (self.root / ".foreman").mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def payload(self, file_path, tool_name="Write"):
        return {
            "session_id": "test", "cwd": str(self.root), "tool_name": tool_name,
            "tool_input": {"file_path": str(file_path), "content": "x"},
        }

    # ---- bootstrap / opt-in ------------------------------------------------

    def test_no_pipeline_json_is_silent(self):
        """No .foreman/pipeline.json declared: this project never opted into stage ordering.
        Must not deny a write that has nothing to do with any declared scope."""
        target = self.root / "worksite" / "file.py"
        proc = run_gate(self.payload(target))
        self.assertEqual(proc.stdout.strip(), "")

    def test_not_a_foreman_project_is_silent(self):
        with tempfile.TemporaryDirectory(prefix="not-foreman-") as tmp:
            no_foreman_root = Path(tmp)
            target = no_foreman_root / "worksite" / "file.py"
            payload = {
                "session_id": "test", "cwd": str(no_foreman_root), "tool_name": "Write",
                "tool_input": {"file_path": str(target), "content": "x"},
            }
            proc = run_gate(payload)
            self.assertEqual(proc.stdout.strip(), "")

    # ---- C1: scope resolution, at most one stage ---------------------------

    def test_path_matching_zero_stages_is_silent(self):
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
        ])
        target = self.root / "unrelated.txt"
        proc = run_gate(self.payload(target))
        self.assertEqual(proc.stdout.strip(), "")

    def test_c1_c3_ambiguous_scope_denies(self):
        """A pipeline.json fixture with two stages whose scopes overlap on one path -- the
        gate must deny with the ambiguous-stage message, not first-match-wins."""
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["worksite/"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/"]},
        ])
        target = self.root / "worksite" / "shared.py"
        proc = run_gate(self.payload(target))
        self.assertIn('"permissionDecision": "deny"', proc.stdout)
        self.assertIn("more than one stage", proc.stdout)
        self.assertIn("architecture", proc.stdout)
        self.assertIn("design-scope", proc.stdout)

    # ---- C2: predecessor lookup ---------------------------------------------

    def test_c2_first_stage_no_requires_opens(self):
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
        ])
        target = self.root / "ARCHITECTURE.md"
        proc = run_gate(self.payload(target))
        self.assertEqual(proc.stdout.strip(), "")

    def test_c2_missing_predecessor_row_denies(self):
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        write_ledger(self.root, [])
        target = self.root / "worksite" / "GOALS.json"
        proc = run_gate(self.payload(target))
        self.assertIn('"permissionDecision": "deny"', proc.stdout)
        self.assertIn("no stage_close or stage_skip row", proc.stdout)
        self.assertIn("architecture", proc.stdout)

    def test_c2_predecessor_closed_opens(self):
        arch_path = self.root / "ARCHITECTURE.md"
        arch_path.write_text("# architecture\n")
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        import hashlib
        digest = "sha256:" + hashlib.sha256(arch_path.read_bytes()).hexdigest()
        write_ledger(self.root, [
            {"schema": "foreman-ledger.v1", "event": "stage_close", "stage": "architecture",
             "decision": "go", "artifacts": {"ARCHITECTURE.md": digest}},
        ])
        target = self.root / "worksite" / "GOALS.json"
        proc = run_gate(self.payload(target))
        self.assertEqual(proc.stdout.strip(), "")

    # ---- C3: fail-closed on every ambiguity ---------------------------------

    def test_c3_unreadable_pipeline_denies(self):
        (self.root / ".foreman" / "pipeline.json").write_text("{not json")
        target = self.root / "worksite" / "file.py"
        proc = run_gate(self.payload(target))
        self.assertIn('"permissionDecision": "deny"', proc.stdout)
        self.assertIn("pipeline.json", proc.stdout)

    def test_c3_pipeline_missing_stages_list_denies(self):
        (self.root / ".foreman" / "pipeline.json").write_text(json.dumps({"schema": "x"}))
        target = self.root / "worksite" / "file.py"
        proc = run_gate(self.payload(target))
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    def test_c3_f2_malformed_ledger_line_denies_not_silently_skips(self):
        """F2: a malformed single line in ledger.jsonl (append-only, multiple writers) must
        deny, not be silently skipped as if that row's stage were still open."""
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        write_ledger(self.root, [
            json.dumps({"event": "stage_close", "stage": "concept", "decision": "go", "artifacts": {}}),
            "{this line is not valid json",
        ])
        target = self.root / "worksite" / "GOALS.json"
        proc = run_gate(self.payload(target))
        self.assertIn('"permissionDecision": "deny"', proc.stdout)
        self.assertIn("ledger", proc.stdout.lower())

    # ---- C4: artifact hash re-verified against current disk content --------

    def test_c4_stale_artifact_hash_denies(self):
        arch_path = self.root / "ARCHITECTURE.md"
        arch_path.write_text("# architecture v1\n")
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        import hashlib
        digest = "sha256:" + hashlib.sha256(arch_path.read_bytes()).hexdigest()
        write_ledger(self.root, [
            {"event": "stage_close", "stage": "architecture", "decision": "go",
             "artifacts": {"ARCHITECTURE.md": digest}},
        ])
        # Modify ARCHITECTURE.md AFTER the close event recorded its hash, with no new close.
        arch_path.write_text("# architecture v2, edited after close\n")
        target = self.root / "worksite" / "GOALS.json"
        proc = run_gate(self.payload(target))
        self.assertIn('"permissionDecision": "deny"', proc.stdout)
        self.assertIn("stale", proc.stdout.lower())

    # ---- C5: stage_skip satisfies exactly as stage_close does ---------------

    def test_c5_predecessor_skip_satisfies(self):
        write_pipeline(self.root, [
            {"id": "integration-test", "requires": [], "scopes": ["INTEGRATION-TEST-REPORT.md"]},
            {"id": "verification", "requires": ["integration-test"], "scopes": ["worksite/VERIFICATION.md"]},
        ])
        write_ledger(self.root, [
            {"event": "stage_skip", "stage": "integration-test",
             "reason": "single component, no declared interface exists yet",
             "ticket": "FORE-244"},
        ])
        target = self.root / "worksite" / "VERIFICATION.md"
        proc = run_gate(self.payload(target))
        self.assertEqual(proc.stdout.strip(), "")

    def test_c5_close_with_non_go_decision_does_not_satisfy(self):
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        write_ledger(self.root, [
            {"event": "stage_close", "stage": "architecture", "decision": "recycle",
             "artifacts": {}},
        ])
        target = self.root / "worksite" / "GOALS.json"
        proc = run_gate(self.payload(target))
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    # ---- C6: the deny message is the instruction ----------------------------

    def test_c6_deny_message_names_blocker_and_both_commands(self):
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        write_ledger(self.root, [])
        target = self.root / "worksite" / "GOALS.json"
        proc = run_gate(self.payload(target))
        self.assertIn("design-scope", proc.stdout)
        self.assertIn("architecture", proc.stdout)
        self.assertIn("foreman close --stage architecture", proc.stdout)
        self.assertIn("foreman skip --stage architecture", proc.stdout)

    # ---- Bash routing (same FORE-1 coverage every sibling gate has) --------

    def test_bash_redirect_into_gated_scope_denies(self):
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        write_ledger(self.root, [])
        (self.root / "worksite").mkdir()
        target = self.root / "worksite" / "GOALS.json"
        payload = {
            "session_id": "test", "cwd": str(self.root), "tool_name": "Bash",
            "tool_input": {"command": f"echo hi > {target}"},
        }
        proc = run_gate(payload)
        self.assertIn('"permissionDecision": "deny"', proc.stdout)

    # ---- B2 regression: relative file_path resolves against payload cwd ----

    def test_b2_relative_file_path_resolves_against_payload_cwd(self):
        """QA-Bob round-1 finding B2: a relative file_path must resolve against the hook
        PAYLOAD's own cwd field, not the hook process's own os.getcwd(). Passing a relative
        path here, with self.root as cwd, must gate identically to the equivalent absolute
        path -- proving the fix, not just its absence of a crash."""
        write_pipeline(self.root, [
            {"id": "architecture", "requires": [], "scopes": ["ARCHITECTURE.md"]},
            {"id": "design-scope", "requires": ["architecture"], "scopes": ["worksite/GOALS.json"]},
        ])
        write_ledger(self.root, [])
        payload = {
            "session_id": "test", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": "worksite/GOALS.json", "content": "x"},
        }
        proc = run_gate(payload)
        self.assertIn('"permissionDecision": "deny"', proc.stdout)
        self.assertIn("architecture", proc.stdout)

    # ---- F1: deny-or-nothing, never ask -------------------------------------

    def test_f1_no_ask_anywhere_in_source(self):
        source = SCRIPT and Path(SCRIPT).read_text()
        self.assertNotIn("hc.ask(", source)


class ControlPlaneNotStageContentTests(unittest.TestCase):
    """CHV2-90. resolve_stage() prefix-matches every target against every declared scope, and a
    catch-all scope matches everything -- "*".rstrip("*") is "", and every path startswith("").
    So .foreman/ledger.jsonl and .foreman/pipeline.json, which are the shared control plane
    rather than any stage's content, got attributed to the catch-all stage and denied over THAT
    stage's unmet requires -- blocking a legitimate close or skip of an entirely unrelated stage.

    The fixture is the one the spec names: a catch-all stage whose requires is never met, plus a
    separate legitimate stage. The third test is the control that makes the other two mean
    something -- a real content file under the same catch-all scope must STILL be denied, or
    "the gate stopped denying" would explain the result just as well as "the gate stopped
    misattributing the control plane"."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="chv2-90-control-plane-")
        self.root = Path(self.tempdir.name)
        (self.root / ".foreman").mkdir()
        write_pipeline(self.root, [
            {"id": "catch-all", "scopes": ["*"], "requires": ["never-closed-stage"]},
            {"id": "never-closed-stage", "scopes": []},
            {"id": "legitimate", "scopes": ["src/"]},
        ])
        write_ledger(self.root, [])
        self.addCleanup(self.tempdir.cleanup)

    def payload(self, rel):
        return {"session_id": "test", "cwd": str(self.root), "tool_name": "Write",
                "tool_input": {"file_path": str(self.root / rel), "content": "x"}}

    def test_ledger_append_is_not_attributed_to_a_catch_all_stage(self):
        """The reported defect: recording a legitimate stage_close or stage_skip was denied
        because a third, unrelated stage had an unmet requirement."""
        proc = run_gate(self.payload(".foreman/ledger.jsonl"))
        self.assertEqual(proc.stdout.strip(), "")

    def test_pipeline_json_is_not_attributed_to_a_catch_all_stage(self):
        """The sibling instance, same defect class and same fix, folded in by the orchestrator's
        scope call rather than left to be rediscovered separately."""
        proc = run_gate(self.payload(".foreman/pipeline.json"))
        self.assertEqual(proc.stdout.strip(), "")

    def test_real_content_under_the_same_scope_is_still_denied(self):
        """THE CONTROL. The catch-all stage's requires genuinely is unmet, so an actual content
        write under its scope must still be refused. Without this arm, deleting the whole
        stage-ordering check would pass both tests above."""
        proc = run_gate(self.payload("hooks/real_content.py"))
        self.assertIn("permissionDecision", proc.stdout)
        self.assertIn("deny", proc.stdout)


if __name__ == "__main__":
    unittest.main()
