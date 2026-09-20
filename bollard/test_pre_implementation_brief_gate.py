#!/usr/bin/env python3
"""Regression tests for REQ-45's pre_implementation_brief_gate.py.

Every DENY case here was reproduced against the real hook via subprocess (not the internal
functions in isolation) during first authorship, and one of them caught a real bug before it
shipped: component_root() returns a project-relative STRING ("store/"), not a Path, and the
first draft's _goals_json_path() tried Path-style division on that string directly, which raised
TypeError and was only visible because the fail-closed entrypoint converted it into a generic
"internal error" deny instead of the real, useful reason. test_goals_citation_denies_correctly
below pins the FIX (the specific correct deny reason), not just "some deny happened" -- a test
that only checked for exit-code-nonzero or "any deny" would have passed against the broken
version too, since the internal-error path also denies. This is the exact class of weak-oracle
test this project's own review discipline exists to catch; asserting the specific reason string
is what makes it a real regression test rather than decoration.

    python3 -m unittest test_pre_implementation_brief_gate -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "pre_implementation_brief_gate.py"


def run_hook(cwd, file_path, tool_name="Write"):
    payload = {
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "session_id": "test",
        "cwd": str(cwd),
    }
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result


def make_fixture(tmp_root):
    project = Path(tmp_root) / "proj"
    (project / ".foreman").mkdir(parents=True)
    (project / "store").mkdir()
    (project / "ARCHITECTURE.md").write_text(
        "# Fixture\n\n```yaml components\nstore: [\"store/\"]\n```\n"
    )
    (project / "ARCHITECTURE-REVIEW.md").write_text("reviewed\n")
    return project


class PreImplementationBriefGateTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.project = make_fixture(self.tmpdir)
        self.target = str(self.project / "store" / "foo.py")

    def _deny_reason(self, result):
        out = json.loads(result.stdout)
        return out["hookSpecificOutput"]["permissionDecisionReason"]

    def test_no_brief_marker_denies(self):
        result = run_hook(self.project, self.target)
        self.assertIn("no pre-implementation brief is recorded", self._deny_reason(result))

    def test_malformed_marker_denies(self):
        briefs = self.project / ".foreman" / "briefs"
        briefs.mkdir(parents=True)
        (briefs / "store.json").write_text("not valid json{")
        result = run_hook(self.project, self.target)
        self.assertIn("is malformed", self._deny_reason(result))

    def test_marker_missing_ticket_id_denies(self):
        briefs = self.project / ".foreman" / "briefs"
        briefs.mkdir(parents=True)
        (briefs / "store.json").write_text(json.dumps({"persona": "dana-okafor"}))
        result = run_hook(self.project, self.target)
        self.assertIn("is malformed", self._deny_reason(result))

    def test_goals_citation_denies_correctly(self):
        """The fix this test pins: a real, correct-content deny reason naming the exact ticket
        and GOALS.json path -- not the generic 'internal error' the first, buggy draft produced
        for this exact scenario."""
        briefs = self.project / ".foreman" / "briefs"
        briefs.mkdir(parents=True)
        (briefs / "store.json").write_text(
            json.dumps({"ticket_id": "FORE-999", "persona": "dana-okafor"})
        )
        (self.project / "store" / "GOALS.json").write_text(
            json.dumps({"criteria": "no citation here"})
        )
        result = run_hook(self.project, self.target)
        reason = self._deny_reason(result)
        self.assertIn("FORE-999", reason)
        self.assertIn("does not cite that ticket", reason)
        self.assertNotIn("internal error", reason)

    def test_fully_satisfied_allows_silently(self):
        briefs = self.project / ".foreman" / "briefs"
        briefs.mkdir(parents=True)
        (briefs / "store.json").write_text(
            json.dumps({"ticket_id": "FORE-999", "persona": "dana-okafor"})
        )
        (self.project / "store" / "GOALS.json").write_text(
            json.dumps({"criteria": "matches FORE-999"})
        )
        result = run_hook(self.project, self.target)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_undeclared_component_is_not_gated(self):
        """A write outside any declared component is architecture_gate.py's problem, not this
        gate's -- confirms this gate doesn't overreach into scope it doesn't own."""
        outside = self.project / "docs" / "notes.md"
        outside.parent.mkdir()
        result = run_hook(self.project, str(outside))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_negative_control_sabotaged_citation_check_is_caught(self):
        """Falsification per house standard: sabotage the citation check to always pass, confirm
        test_goals_citation_denies_correctly actually goes red against the sabotaged hook -- if
        it didn't, the test would be decoration, not a real check.

        Must sit next to the real hook, not in a scratch tmpdir -- sys.path.insert in both this
        hook and every sibling module resolves relative to the running file's own directory, so
        a copy anywhere else can't import hook_common/component_coupling/audit_lib/
        verdict_ledger and dies on ImportError before the sabotaged line is ever reached. The
        first version of this test made exactly that mistake: the sabotaged copy still denied,
        but from an import failure caught by the fail-closed entrypoint, not from the citation
        check actually being exercised -- a false pass on the negative control itself."""
        sabotaged = HOOK.parent / "sabotage-scratch-pre-implementation-brief-gate.py"
        self.addCleanup(sabotaged.unlink, missing_ok=True)
        original = HOOK.read_text()
        broken = original.replace(
            "return ticket_id in text",
            "return True  # SABOTAGE: citation check always passes",
        )
        self.assertNotEqual(original, broken, "sabotage string not found -- fix this test")
        sabotaged.write_text(broken)

        briefs = self.project / ".foreman" / "briefs"
        briefs.mkdir(parents=True)
        (briefs / "store.json").write_text(
            json.dumps({"ticket_id": "FORE-999", "persona": "dana-okafor"})
        )
        (self.project / "store" / "GOALS.json").write_text(json.dumps({"criteria": "no citation"}))

        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self.target},
            "session_id": "test",
            "cwd": str(self.project),
        }
        result = subprocess.run(
            [sys.executable, sabotaged],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=10,
        )
        # The real hook denies here (test_goals_citation_denies_correctly). The sabotaged one
        # must NOT -- if it also denies, the sabotage didn't remove the property under test.
        self.assertEqual(result.returncode, 0, "sabotage failed to defeat the citation check")
        self.assertEqual(result.stdout.strip(), "")


class CrossProjectJurisdictionTests(unittest.TestCase):
    """FORE-581. project_root came from the payload cwd, so a write into ANOTHER project's
    declared component was attributed to the writer's components and checked against the
    writer's briefs -- which is to say, not checked at all.

    THREE projects, not two, and the third is the point. A and C each declare a component the
    other does not and neither records a brief, so both cross arms must newly deny, in both
    directions. S records a real brief AND cites it in GOALS.json, so a cross-project write into
    S must STAY SILENT. Without S every arm here is a deny and a gate that had simply become
    stricter would score identically -- the same polarity check FORE-580's authored tests were
    missing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.a = self._project("proj-a", "widget", brief_ticket=None)
        self.c = self._project("proj-c", "sprocket", brief_ticket=None)
        self.satisfied = self._project("proj-s", "gizmo", brief_ticket="PROBE-77")
        self.outsider = Path(self.tmpdir) / "outside-foreman"
        self.outsider.mkdir()

    def _project(self, dirname, component, brief_ticket):
        root = Path(self.tmpdir) / dirname
        (root / ".foreman" / "briefs").mkdir(parents=True)
        (root / component).mkdir()
        (root / "ARCHITECTURE.md").write_text(
            f'# Fixture\n\n```yaml components\n{component}: ["{component}/"]\n```\n')
        (root / "ARCHITECTURE-REVIEW.md").write_text("reviewed\n")
        goals = {"component": component}
        if brief_ticket:
            (root / ".foreman" / "briefs" / f"{component}.json").write_text(
                json.dumps({"ticket_id": brief_ticket, "persona": "dana-okafor"}))
            goals["brief"] = brief_ticket
        (root / component / "GOALS.json").write_text(json.dumps(goals))
        return root

    def _decision(self, result):
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]

    def test_own_cwd_still_denies(self):
        """Positive control: without this the cross arms could pass because nothing fires."""
        result = run_hook(self.a, str(self.a / "widget" / "impl.py"))
        self.assertEqual(self._decision(result), "deny")

    def test_foreign_cwd_into_a_denies_and_names_a(self):
        result = run_hook(self.c, str(self.a / "widget" / "impl.py"))
        self.assertEqual(self._decision(result), "deny")
        self.assertIn(str(self.a), json.loads(result.stdout)["hookSpecificOutput"]
                      ["permissionDecisionReason"])

    def test_foreign_cwd_into_c_denies_and_names_c(self):
        """The other direction. Two projects declaring DIFFERENT components is what makes this
        pair meaningful: a gate reading the writer's component map cannot attribute the other
        project's path at all, so it goes quiet rather than reaching a wrong answer loudly."""
        result = run_hook(self.a, str(self.c / "sprocket" / "impl.py"))
        self.assertEqual(self._decision(result), "deny")
        self.assertIn(str(self.c), json.loads(result.stdout)["hookSpecificOutput"]
                      ["permissionDecisionReason"])

    def test_foreign_cwd_into_a_satisfied_project_stays_silent(self):
        """THE POLARITY CHECK. Jurisdiction moving to the target must consult the target's
        brief, which here is satisfied -- so this must NOT deny. A gate that merely became
        stricter fails only this test."""
        result = run_hook(self.a, str(self.satisfied / "gizmo" / "impl.py"))
        self.assertIsNone(self._decision(result))

    def test_writer_outside_any_foreman_project_is_still_gated_by_the_target(self):
        """The opt-in belongs to the project that declared the component and would have to
        record the brief, not to the directory the writer happens to be standing in."""
        result = run_hook(self.outsider, str(self.a / "widget" / "impl.py"))
        self.assertEqual(self._decision(result), "deny")

    def test_bash_write_into_a_foreign_component_denies(self):
        payload = {"tool_name": "Bash", "session_id": "test", "cwd": str(self.c),
                   "tool_input": {"command": f"cp /etc/hosts {self.a}/widget/impl.py"}}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(self._decision(result), "deny")

    def test_bash_write_into_a_satisfied_foreign_component_stays_silent(self):
        payload = {"tool_name": "Bash", "session_id": "test", "cwd": str(self.a),
                   "tool_input": {"command": f"cp /etc/hosts {self.satisfied}/gizmo/impl.py"}}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=10)
        self.assertIsNone(self._decision(result))


if __name__ == "__main__":
    unittest.main()
