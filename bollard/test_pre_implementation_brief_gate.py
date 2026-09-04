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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolate_verdict_ledger  # noqa: E402,F401 -- FORE-314: redirects HOME before any guard
# subprocess spawns below, so tests never write to the operator's real
# ~/.claude/telemetry/verdicts.jsonl.

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


if __name__ == "__main__":
    unittest.main()
