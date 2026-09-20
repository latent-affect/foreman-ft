#!/Users/m5/.venv/bin/python3
"""CHV2-37 acceptance: session_role.py's resolve_role() against the live pdp_turn_gate.py
orchestrator branch, reproducing the actual incident shape from
POSTMORTEM-ORCHESTRATOR-STALL-20260904.md -- an orchestrator session and an unrelated session
sharing one repo, and confirming the SAME hook run treats them differently because it can now
tell them apart.

Two layers, not collapsed into one assertion:

1. resolve_role() itself: does it return the right role for four real session shapes
   (orchestrator claim, continuation-authority claim, alice-labeled persona, and
   unclassified/none)?
2. Does pdp_turn_gate.py's ALREADY-SHIPPED (FORE-186) role-scoped behavior agree with what
   resolve_role() reports? This is the ticket's "a real test that dispatches two sessions with
   different roles against the same hook" -- run as a real subprocess against a throwaway copy
   of the hook, same dispatch convention as test_orchestrator_stop_gate.py's own `fire()`
   (real subprocess, hook-shaped stdin, HOME redirected into a throwaway dir), reused here
   rather than reinvented.

WHAT THIS DOES NOT TEST. pdp_turn_gate.py does not import session_role.py in this proposal --
that rewire is a separate, larger change (touching a hard-gated file with its own extensive
existing test suite) and is out of scope for CHV2-37's own ticket text, which asks for "a
role-check primitive" and "a real test," not a rewrite of every existing gate to consume it.
This test proves the primitive's answer already agrees with the one hook in this repo that
already does the right thing by hand -- the evidence a future rewire of pdp_turn_gate.py itself
would need before touching that file.

WHAT THIS DOES NOT SIMULATE. "Two sessions" here means two distinct session_id strings and the
on-disk claim/label state a real dispatched session would have produced, fired against the real
hook via a real subprocess -- not two live `claude` CLI processes. That is the established
convention across this repo's own hook tests (test_orchestrator_stop_gate.py included); spawning
live sessions inside a unit test is neither this repo's practice nor practical for CI. Stated
here rather than left implicit, per Alice's own working posture: state what was verified and
what wasn't.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import session_role  # noqa: E402
import persona_attribution as pa  # noqa: E402

LIVE_HOOKS = Path("/Users/m5/dev/claude-hooks-v2/hooks")

ORCH = "orch-session-role-test"
BUILDER = "builder-session-role-test"
ALICE_SESSION = "alice-session-role-test"
UNRELATED = "unrelated-reviewer-session-role-test"


class ResolveRoleTests(unittest.TestCase):
    """Layer 1: the primitive in isolation, no subprocess."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="chv2-37-root-"))
        (self.root / ".foreman").mkdir()
        self.ledger_dir = Path(tempfile.mkdtemp(prefix="chv2-37-ledger-"))
        self.ledger = self.ledger_dir / "attr.jsonl"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.ledger_dir, ignore_errors=True)

    def test_orchestrator_claim_wins(self):
        (self.root / ".foreman" / "pdp-orchestrator.json").write_text(
            json.dumps({"session_id": ORCH, "claimed_at": time.time()}))
        self.assertEqual(session_role.resolve_role(self.root, ORCH), "orchestrator")

    def test_continuation_authority_claim(self):
        (self.root / ".foreman" / "pdp-continuation-authority.json").write_text(
            json.dumps({"session_id": BUILDER}))
        self.assertEqual(session_role.resolve_role(self.root, BUILDER),
                         "continuation-authority")

    def test_alice_persona_label(self):
        os.environ["PERSONA_ATTRIBUTION_LEDGER"] = str(self.ledger)
        try:
            pa.record_attribution(ALICE_SESSION, "alice", source="chv2-37-test")
        finally:
            del os.environ["PERSONA_ATTRIBUTION_LEDGER"]
        self.assertEqual(
            session_role.resolve_role(self.root, ALICE_SESSION,
                                      persona_ledger_override=self.ledger),
            "alice")

    def test_unrelated_session_is_none(self):
        """The incident shape: a session with no claim file and no persona label at all."""
        self.assertIsNone(session_role.resolve_role(self.root, UNRELATED))

    def test_missing_session_id_is_none(self):
        self.assertIsNone(session_role.resolve_role(self.root, None))
        self.assertIsNone(session_role.resolve_role(self.root, ""))

    def test_malformed_claim_file_does_not_match(self):
        (self.root / ".foreman" / "pdp-orchestrator.json").write_text("{not json")
        self.assertIsNone(session_role.resolve_role(self.root, ORCH))

    def test_orchestrator_claim_for_a_different_session_does_not_match(self):
        (self.root / ".foreman" / "pdp-orchestrator.json").write_text(
            json.dumps({"session_id": ORCH, "claimed_at": time.time()}))
        self.assertIsNone(session_role.resolve_role(self.root, UNRELATED))


def build_hooks_dir():
    d = Path(tempfile.mkdtemp(prefix="chv2-37-hooks-"))
    shutil.copytree(LIVE_HOOKS, d / "hooks")
    return d


def make_project(orchestrator_session=None):
    root = Path(tempfile.mkdtemp(prefix="chv2-37-proj-"))
    (root / ".foreman").mkdir()
    if orchestrator_session is not None:
        (root / ".foreman" / "pdp-orchestrator.json").write_text(
            json.dumps({"session_id": orchestrator_session, "claimed_at": time.time()}))
    return root


def fire_turn_gate(hooks_dir, root, session_id):
    """Same dispatch shape as test_orchestrator_stop_gate.py's fire(): a real subprocess,
    hook-shaped stdin, HOME redirected into the throwaway hooks_dir so nothing live is
    touched."""
    payload = {"hook_event_name": "Stop", "session_id": session_id, "cwd": str(root)}
    p = subprocess.run(
        ["/usr/bin/python3", str(hooks_dir / "hooks" / "pdp_turn_gate.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "HOME": str(hooks_dir)})
    blocked = '"decision": "block"' in p.stdout or '"decision":"block"' in p.stdout
    return blocked, p.stdout.strip(), p.stderr.strip()


class HookAgreementTests(unittest.TestCase):
    """Layer 2: does the live hook's own role-scoping (FORE-186) agree with what resolve_role()
    independently reports for the same two session_ids in the same project? This is the
    ticket's "dispatch two sessions with different roles against the same hook" case,
    reproducing the historical incident shape directly rather than a synthetic stand-in: ORCH
    holds the orchestrator claim (long-running, open-ended -- REQ-35's continuation-authority
    obligations conceptually apply), UNRELATED is a session with no claim and no label at all
    -- the exact shape of the reviewer session POSTMORTEM-ORCHESTRATOR-STALL-20260904.md
    documents as wrongly blocked before FORE-186 landed."""

    def setUp(self):
        self.hooks_dir = build_hooks_dir()
        self.root = make_project(orchestrator_session=ORCH)

    def tearDown(self):
        shutil.rmtree(self.hooks_dir, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_orchestrator_session_is_gated(self):
        role = session_role.resolve_role(self.root, ORCH)
        self.assertEqual(role, "orchestrator")
        blocked, out, err = fire_turn_gate(self.hooks_dir, self.root, ORCH)
        self.assertTrue(blocked, f"orchestrator turn with no evidence should block; "
                                  f"stdout={out!r} stderr={err!r}")

    def test_unrelated_session_in_same_repo_is_not_gated(self):
        """The regression this ticket exists to prevent: a second, unrelated session in the
        SAME project as a live orchestrator claim must not inherit the orchestrator's
        Stop-turn obligations."""
        role = session_role.resolve_role(self.root, UNRELATED)
        self.assertIsNone(role)
        blocked, out, err = fire_turn_gate(self.hooks_dir, self.root, UNRELATED)
        self.assertFalse(blocked, f"a session holding no role claim must never be blocked by "
                                   f"the orchestrator-scoped branch; stdout={out!r} err={err!r}")

    def test_only_the_claim_holder_is_affected_same_dispatch(self):
        """Both fired against the identical on-disk project state, in the same test, so this is
        one dispatch pair proving the differential directly rather than two isolated assertions
        that could each pass by accident of setup order."""
        orch_blocked, _, _ = fire_turn_gate(self.hooks_dir, self.root, ORCH)
        other_blocked, _, _ = fire_turn_gate(self.hooks_dir, self.root, UNRELATED)
        self.assertTrue(orch_blocked)
        self.assertFalse(other_blocked)


if __name__ == "__main__":
    unittest.main()
