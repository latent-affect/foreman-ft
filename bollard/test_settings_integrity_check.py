#!/usr/bin/env python3
"""CHV2-2: settings_integrity_check.py, run as a real subprocess against an isolated HOME.

Same convention as test_ledger_origin.py: every case runs the real script as a real subprocess
with HOME redirected to a tempdir, and asserts on its actual stdout (the JSON the harness reads
to inject additionalContext) and on the real verdict-ledger row it wrote under that same tempdir.
Nothing here touches the live ~/.claude/settings.json or ~/.claude/telemetry/verdicts.jsonl.

Encodes the five scenarios manually verified during CHV2-2's implementation (absence, invalid
JSON, mismatch, no-reference, and the intact/silent negative control) as permanent regression
tests, per this project's own standing rule that a self-written check must be proven to fail on a
deliberately broken input before its passing result is trusted -- a one-off manual sandbox run
proves the mechanism once; this proves it stays proven.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
SCRIPT = HOOKS / "settings_integrity_check.py"
REAL_REFERENCE = HOOKS / "reference" / "settings-hooks-canonical.json"

PAYLOAD = json.dumps({"session_id": "test", "cwd": "/tmp", "hook_event_name": "SessionStart"})

REAL_HOOKS_BLOCK = {
    "PreToolUse": [{"hooks": [{"type": "command", "command": "python3 real_gate.py"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 real_start.py"}]}],
}


def run_against(home, payload=PAYLOAD):
    """Invoke the real script as a real subprocess, HOME redirected to `home`."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload, capture_output=True, text=True, env=env,
    )
    return result


def read_ledger(home):
    log = Path(home) / ".claude" / "telemetry" / "verdicts.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


class SettingsIntegrityCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chv2-2-test-")
        self.home = Path(self.tmp)
        (self.home / ".claude").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_settings(self, hooks_block):
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": hooks_block}))
        return settings_path

    def test_absence_produces_loud_marker_and_fire_verdict(self):
        # No settings.json written at all under this HOME.
        result = run_against(self.home)
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("is MISSING", context)
        self.assertIn("CHV2-2", context)
        rows = read_ledger(self.home)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "fire")
        self.assertEqual(rows[0]["kind"], "settings-integrity-missing")

    def test_userpromptsubmit_event_name_passed_through_not_hardcoded(self):
        # CHV2-11: registered on UserPromptSubmit too (registration is re-read from disk
        # mid-session, FORE-322 -- a mid-session loss needs the same catch a fresh session
        # gets). Real bug this test guards: every inject() call used to hardcode "SessionStart"
        # regardless of which event actually fired -- correct only by accident when this only
        # ever ran at SessionStart. A UserPromptSubmit-triggered alert must report its own
        # event name, not someone else's.
        upsub_payload = json.dumps(
            {"session_id": "test", "cwd": "/tmp", "hook_event_name": "UserPromptSubmit"})
        result = run_against(self.home, payload=upsub_payload)
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")

    def test_invalid_json_produces_loud_marker(self):
        (self.home / ".claude" / "settings.json").write_text("{ not valid json")
        result = run_against(self.home)
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("not valid JSON", context)
        self.assertIn("62486", context)
        rows = read_ledger(self.home)
        self.assertEqual(rows[0]["kind"], "settings-integrity-invalid-json")

    def test_intact_case_is_silent_negative_control(self):
        """The negative control: a settings.json whose hooks block matches the REAL pinned
        reference exactly must produce zero stdout and a `silent` verdict, not merely
        'no crash.' If this test ever fails by producing an ALERT, the hash function itself is
        broken (e.g. order-sensitive when it should not be), not the fixture."""
        self.assertTrue(REAL_REFERENCE.exists(),
                        "real reference must exist for this test to mean anything -- "
                        "run `python3 settings_integrity_check.py --pin` first")
        real_reference = json.loads(REAL_REFERENCE.read_text())
        self.write_settings(real_reference["hooks"])
        result = run_against(self.home)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        rows = read_ledger(self.home)
        self.assertEqual(rows[0]["verdict"], "silent")
        self.assertNotIn("kind", rows[0])

    def test_mismatch_is_detected_when_a_handler_is_dropped(self):
        """Proves the check actually discriminates: start from the real pinned block (so this
        is a true positive against real content, not a synthetic fixture the parser was never
        going to accept anyway), remove one handler, confirm it fires."""
        real_reference = json.loads(REAL_REFERENCE.read_text())
        tampered = json.loads(json.dumps(real_reference["hooks"]))  # deep copy
        first_event = next(iter(tampered))
        if tampered[first_event]:
            tampered[first_event] = tampered[first_event][:-1]
        self.write_settings(tampered)
        result = run_against(self.home)
        out = json.loads(result.stdout)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("does not match the pinned canonical copy", context)
        rows = read_ledger(self.home)
        self.assertEqual(rows[0]["kind"], "settings-integrity-mismatch")

    def _context_for(self, hooks_block):
        """A fresh, independent HOME per call -- not self.home -- so two fixtures can be scored
        in one test method without one's settings.json or ledger leaking into the other."""
        home = Path(tempfile.mkdtemp(prefix="chv2-135-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": hooks_block}))
        result = run_against(home)
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_stripped_handler_alert_is_distinguishable_from_added_handler_alert(self):
        """CHV2-135. Before this fix, a STRIPPED handler (real incident, investigate first) and
        an ADDED handler (safe, re-pin) within the same shared event type produced byte-identical
        alert text: 'same event types present; the handler list differs within at least one.' A
        reader following that alert's own recommended action (re-pin) against the stripped case
        would silently bless the incident it exists to catch. Both fixtures start from the real
        pinned block, matching this file's own true-positive convention above."""
        real_reference = json.loads(REAL_REFERENCE.read_text())
        first_event = next(iter(real_reference["hooks"]))

        stripped = json.loads(json.dumps(real_reference["hooks"]))
        self.assertTrue(stripped[first_event], "fixture event must have a handler to strip")
        stripped[first_event] = stripped[first_event][:-1]
        stripped_context = self._context_for(stripped)

        added = json.loads(json.dumps(real_reference["hooks"]))
        added[first_event] = added[first_event] + [
            {"hooks": [{"type": "command", "command": "python3 injected_handler.py"}]}]
        added_context = self._context_for(added)

        self.assertNotEqual(stripped_context, added_context,
                            "a stripped handler and an added handler must not read identically")
        self.assertIn("MISSING", stripped_context)
        self.assertIn("do NOT assume", stripped_context)
        self.assertNotIn("MISSING", added_context)
        self.assertIn("NEW", added_context)
        self.assertNotIn("do NOT assume", added_context)

    def test_no_reference_file_is_its_own_loud_case(self):
        """Temporarily hides the real reference (restored in addCleanup no matter how the test
        exits) to prove the script distinguishes 'nothing to compare against' from 'compared and
        found different' -- collapsing the two would make a fresh machine's first-ever run look
        identical to a real drift."""
        if not REAL_REFERENCE.exists():
            self.skipTest("no real reference to hide")
        moved_to = REAL_REFERENCE.with_name(REAL_REFERENCE.name + ".hidden-for-test")
        REAL_REFERENCE.rename(moved_to)
        self.addCleanup(lambda: moved_to.exists() and moved_to.rename(REAL_REFERENCE))
        self.write_settings(REAL_HOOKS_BLOCK)
        result = run_against(self.home)
        out = json.loads(result.stdout)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("No canonical reference found", context)
        rows = read_ledger(self.home)
        self.assertEqual(rows[0]["kind"], "settings-integrity-no-reference")


if __name__ == "__main__":
    unittest.main()
