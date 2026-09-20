#!/Users/m5/.venv/bin/python3
"""CHV2-40 acceptance: alice_bob_fable_drift_gate.py, run as a real subprocess against
throwaway deploy/anchor fixture trees (ALICE_BOB_FABLE_DEPLOY_ROOT/ALICE_BOB_FABLE_ANCHOR_ROOT
env overrides -- same test-isolation pattern every sibling hook in this directory already
uses), never touching the real repo or the real anchor. Same dispatch convention as
test_orchestrator_stop_gate.py: real subprocess, hook-shaped stdin.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The gate does `sys.path.insert(0, <its own dir>); import hook_common as hc`, so it must be
# invoked from wherever hook_common.py actually lives. In the real repo this test file and the
# gate are siblings in hooks/, so the default is HERE's own sibling; the env override exists
# for a verification pass run from somewhere else (e.g. scratchpad, before this proposal has
# landed), pointed at a real hooks/-resident copy.
HERE = Path(__file__).resolve().parent
GATE = Path(os.environ.get("ALICE_BOB_FABLE_DRIFT_GATE_PATH")
           or (HERE / "alice_bob_fable_drift_gate.py"))


def fire(payload, deploy_root, anchor_root):
    env = {**os.environ,
           "ALICE_BOB_FABLE_DEPLOY_ROOT": str(deploy_root),
           "ALICE_BOB_FABLE_ANCHOR_ROOT": str(anchor_root)}
    p = subprocess.run(["/usr/bin/python3", str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    denied = '"permissionDecision": "deny"' in p.stdout or '"permissionDecision":"deny"' in p.stdout
    return denied, p.stdout.strip(), p.stderr.strip()


class DriftGateTests(unittest.TestCase):
    def setUp(self):
        self.deploy = Path(tempfile.mkdtemp(prefix="chv2-40-deploy-"))
        self.anchor = Path(tempfile.mkdtemp(prefix="chv2-40-anchor-"))
        (self.anchor / "sub").mkdir()
        (self.anchor / "sub" / "mod.py").write_text("print('anchor content')\n")
        (self.deploy / "sub").mkdir()
        (self.deploy / "sub" / "mod.py").write_text("print('anchor content')\n")

    def tearDown(self):
        shutil.rmtree(self.deploy, ignore_errors=True)
        shutil.rmtree(self.anchor, ignore_errors=True)

    def write_payload(self, relpath, content):
        return {"hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": str(self.deploy / relpath), "content": content}}

    def edit_payload(self, relpath):
        return {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                "tool_input": {"file_path": str(self.deploy / relpath),
                               "old_string": "x", "new_string": "y"}}

    def test_write_matching_anchor_content_allowed(self):
        payload = self.write_payload("sub/mod.py", "print('anchor content')\n")
        denied, out, err = fire(payload, self.deploy, self.anchor)
        self.assertFalse(denied, f"a byte-exact resync write must be allowed; out={out!r} err={err!r}")

    def test_write_diverging_from_anchor_denied(self):
        payload = self.write_payload("sub/mod.py", "print('HAND EDITED')\n")
        denied, out, err = fire(payload, self.deploy, self.anchor)
        self.assertTrue(denied, f"content differing from the anchor must be denied; out={out!r}")
        self.assertIn("does not match the anchor", out)

    def test_edit_denied_unconditionally_even_if_harmless(self):
        """Edit is refused outright for any in-scope path, regardless of what it would produce
        -- this gate never attempts to simulate old_string/new_string application."""
        payload = self.edit_payload("sub/mod.py")
        denied, out, err = fire(payload, self.deploy, self.anchor)
        self.assertTrue(denied, f"Edit must be denied unconditionally in scope; out={out!r}")
        self.assertIn("Edit is refused here unconditionally", out)

    def test_write_with_no_anchor_counterpart_denied(self):
        payload = self.write_payload("sub/new-file-not-in-anchor.py", "print('new')\n")
        denied, out, err = fire(payload, self.deploy, self.anchor)
        self.assertTrue(denied, f"a target absent from the anchor must be denied; out={out!r}")
        self.assertIn("could not read the anchor", out)

    def test_out_of_scope_path_untouched(self):
        outside = Path(tempfile.mkdtemp(prefix="chv2-40-outside-"))
        try:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                      "tool_input": {"file_path": str(outside / "unrelated.py"),
                                     "content": "anything at all\n"}}
            denied, out, err = fire(payload, self.deploy, self.anchor)
            self.assertFalse(denied, f"a path outside the deploy root must be untouched; out={out!r}")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_pycache_path_untouched_even_if_diverging(self):
        (self.deploy / "sub" / "__pycache__").mkdir()
        payload = self.write_payload("sub/__pycache__/mod.cpython-314.pyc", "not real bytecode")
        denied, out, err = fire(payload, self.deploy, self.anchor)
        self.assertFalse(denied, f"__pycache__ is generated, never a provenance source; "
                                  f"out={out!r}")

    def test_unreachable_anchor_root_fails_closed(self):
        missing_anchor = Path(tempfile.mkdtemp(prefix="chv2-40-anchor-")) / "does-not-exist"
        payload = self.write_payload("sub/mod.py", "print('anchor content')\n")
        denied, out, err = fire(payload, self.deploy, missing_anchor)
        self.assertTrue(denied, f"an unreachable anchor must fail closed, not silently allow; "
                                 f"out={out!r}")

    def test_other_tool_untouched(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                  "tool_input": {"command": f"cat {self.deploy / 'sub' / 'mod.py'}"}}
        denied, out, err = fire(payload, self.deploy, self.anchor)
        self.assertFalse(denied, f"a non-Edit/Write tool must be untouched; out={out!r}")


if __name__ == "__main__":
    unittest.main()
