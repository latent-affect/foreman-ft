"""Regression tests for FORE-533's k2_invocation_gate.py, one test per
hooks/k2_invocation_gate/GOALS.json criterion (C1-C3) plus its two failure criteria (F1-F2),
plus FORE-543/FORE-544's own regression coverage and its adversarial-code-review follow-up
(see the bottom of this file).

    python3 -m unittest test_k2_invocation_gate -v

Real subprocess dispatch against the real hook file, same convention as this suite's other
gate tests (test_stage_order_gate.py, test_architecture_gate.py): a hook-shaped stdin payload
in, stdout/exit code out, nothing mocked.

TESSERA STUB, DISCLOSED: the tessera-cli shape (C1(b)) tests set K2_TESSERA_STUB_PATH so
k2_invocation_gate.ticket_mechanism_backed() reads a fixture file instead of calling the real,
live tessera CLI against the real production database
(/Users/m5/dev/ticket-system/data/tessera.db). Depending on or mutating that live database from
a test run is exactly the class of incident this repo's own operating memory already warns
against (test traffic landing in production data/ledgers) -- this module's own docstring
discloses the same stub as a deliberate, env-var-gated test-only escape hatch.

FORE-544 FIX, DISCLOSED CHANGE TO THIS FIXTURE: the stub is now also gated behind a real-test-
runner signal (ticket_mechanism_backed()'s own docstring). A subprocess-dispatched child
interpreter launched as `python3 k2_invocation_gate.py` never imports `unittest` itself, so
`tessera_env()` below now also passes `PYTEST_CURRENT_TEST` in the child's env -- an honest
signal, not a workaround: this really is a real test runner setting it, the process boundary
just means the in-process `"unittest" in sys.modules` half of the idiom can't reach across it.

FORE-543 FIX, NEW TEST-INJECTABLE SEAM: K2_TESSERA_DB_PATH_OVERRIDE lets a test point the real
tessera-cli subprocess at a real, nonexistent database path, producing a genuine query failure
end to end through a real subprocess -- without ever touching the live production tessera.db.
Same convention as write_gate.py's WRITE_GATE_DISPATCH_ROOT; the gate's own module docstring
discloses it.

ADVERSARIAL-CODE-REVIEW FOLLOW-UP, DISCLOSED CHANGE TO THIS FIXTURE
(ADVERSARIAL-REVIEW-FORE-543-544-20260915.md, check 4): K2_TESSERA_DB_PATH_OVERRIDE is now
gated behind the same real-test-runner signal as K2_TESSERA_STUB_PATH
(_resolve_tessera_db_path() in the gate module). Every existing test below that relies on the
override firing now ALSO carries PYTEST_SIGNAL in its env, matching tessera_env()'s own
convention -- an honest signal, since these really are real test-runner dispatches. One new
test proves the guard itself: K2_TESSERA_DB_PATH_OVERRIDE set with no signal must be ignored.

FORE-544's "stub present but no real-test-runner signal" test, and this follow-up's matching
DB_PATH_OVERRIDE test, both read ONE REAL, DISCLOSED PRODUCTION TICKET (FORE-543 and FORE-544
themselves) via a real, read-only `tessera.api.cli get` call -- never a write, never a
mutation -- specifically because each ticket's own `custom_fields` is real,
already-confirmed-empty production data (verified directly against the live tickets this same
session, no fixture needed) that discriminates the fix: if a fix is broken and an override is
still trusted with no signal, the ticket would incorrectly resolve as mechanism_backed (the
stub) or as could-not-determine or a confirmed-negative that never actually queried this
ticket at all (the DB path override pointing somewhere else); if a fix is correct, the override
is ignored, the real query runs, and the ticket correctly resolves as NOT mechanism_backed.
Same "real disclosed ticket" convention Dana's Build 3 interface pass already used for k2
(FORE-545).

VERDICT LEDGER, DISCLOSED, NOT FIXED HERE: like every other subprocess-dispatched gate test in
this suite (stage_order_gate's own test file included), each subprocess call below runs through
hook_common.run(), which always appends a real row to the real
~/.claude/telemetry/verdicts.jsonl on this machine -- this is inherited, pre-existing behavior
of hc.run()/verdict_ledger.py, not something this test file introduces, and fixing the
test-traffic-pollution question is out of scope for FORE-533/FORE-543/FORE-544.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = str(HERE / "k2_invocation_gate.py")

# FORE-544 test-runner signal, disclosed above: an honest signal a real test runner (this file)
# is setting deliberately, threaded through to the subprocess child so its own
# ticket_mechanism_backed() can tell "a real test set this stub" apart from "a stale shell
# export set this stub" -- the exact ambiguity FORE-544 exists to close. Reused, unchanged, by
# the adversarial-code-review follow-up below for the matching K2_TESSERA_DB_PATH_OVERRIDE
# guard.
PYTEST_SIGNAL = {"PYTEST_CURRENT_TEST": "test_k2_invocation_gate (real subprocess dispatch)"}

# FORE-544's own regression test (test_fore544_stub_without_signal_is_ignored, below) needs one
# real ticket it can query read-only and that is KNOWN, at the moment this file was written, not
# to be mechanism_backed -- FORE-544 itself, verified directly against the live production
# ticket this same session (custom_fields: {}). Read-only `get`, never mutated by this suite.
FORE544_REAL_TICKET_ID = "FORE-544"

# Adversarial-code-review follow-up's own discriminator ticket, same convention as
# FORE544_REAL_TICKET_ID immediately above -- a second real, disclosed, read-only production
# ticket (FORE-543 itself, custom_fields also confirmed empty this session) so the DB_PATH
# guard test below queries a DIFFERENT real ticket than the stub-guard test, keeping the two
# regression tests independent of each other.
FORE543_REAL_TICKET_ID = "FORE-543"

GOALS_TEMPLATE = {
    "criteria": [
        {"id": "C1", "mechanism_backed": True},
        {"id": "C2", "mechanism_backed": False},
    ],
    "results": [],
}


def run_gate(payload, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, SCRIPT], input=json.dumps(payload), capture_output=True, text=True,
        env=full_env,
    )
    return proc


def parsed_decision(stdout_text):
    stripped = stdout_text.strip()
    if stripped == "":
        return None, None, None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, None, f"stdout was not valid JSON and not empty: {exc}"
    hso = obj.get("hookSpecificOutput") if isinstance(obj, dict) else None
    if not isinstance(hso, dict):
        return None, None, "stdout was valid JSON but had no hookSpecificOutput object"
    return hso.get("permissionDecision"), hso.get("permissionDecisionReason"), None


def write_goals_json(path, results):
    obj = dict(GOALS_TEMPLATE)
    obj["results"] = results
    path.write_text(json.dumps(obj, indent=2))


class K2InvocationGateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="k2-invocation-gate-test-")
        self.root = Path(self.tempdir.name)
        (self.root / ".foreman").mkdir()
        self.goals_path = self.root / "worksite" / "GOALS.json"
        self.goals_path.parent.mkdir(parents=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def edit_payload(self, old_results, new_results, tool_name="Write"):
        write_goals_json(self.goals_path, old_results)
        old_text = self.goals_path.read_text()
        new_obj = dict(GOALS_TEMPLATE)
        new_obj["results"] = new_results
        new_text = json.dumps(new_obj, indent=2)
        if tool_name == "Write":
            tool_input = {"file_path": str(self.goals_path), "content": new_text}
        else:
            tool_input = {"file_path": str(self.goals_path),
                          "old_string": old_text, "new_string": new_text}
        return {
            "session_id": "test", "cwd": str(self.root), "tool_name": tool_name,
            "tool_input": tool_input,
        }

    def write_evidence(self, key, probe_result="pass"):
        evidence_dir = self.root / ".foreman" / "invocation-evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / f"{key}.json").write_text(json.dumps({
            "probe_result": probe_result, "probed_at": "2026-09-08T00:00:00Z",
            "probe_command": "echo probe", "evidence_sha256": "sha256:deadbeef",
        }))

    def write_override(self, expiry):
        (self.root / ".foreman" / "k2-override.json").write_text(json.dumps({
            "overridden_by": "test", "reason": "test override", "ticket": "FORE-1",
            "expiry": expiry,
        }))

    def tessera_env(self, ticket_id, mechanism_backed):
        stub_path = self.root / "tessera-stub.json"
        stub_path.write_text(json.dumps({ticket_id: mechanism_backed}))
        env = {"K2_TESSERA_STUB_PATH": str(stub_path)}
        env.update(PYTEST_SIGNAL)
        return env

    def tessera_close_payload(self, ticket_id):
        return {
            "session_id": "test", "cwd": str(self.root), "tool_name": "Bash",
            "tool_input": {"command": (
                f"/usr/bin/python3 -m tessera.api.cli --db data/tessera.db transition "
                f"{ticket_id} --actor claude --status closed"
            )},
        }

    # ---- bootstrap ----------------------------------------------------------

    def test_not_a_foreman_project_is_silent(self):
        with tempfile.TemporaryDirectory(prefix="not-foreman-") as tmp:
            no_foreman_root = Path(tmp)
            payload = {
                "session_id": "test", "cwd": str(no_foreman_root), "tool_name": "Write",
                "tool_input": {"file_path": str(no_foreman_root / "GOALS.json"),
                               "content": "{}"},
            }
            proc = run_gate(payload)
            self.assertEqual(proc.stdout.strip(), "")

    # ---- C1: matcher ---------------------------------------------------------

    def test_c1_unrelated_write_is_silent(self):
        target = self.root / "worksite" / "notes.txt"
        target.write_text("hello")
        payload = {
            "session_id": "test", "cwd": str(self.root), "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "hello world"},
        }
        proc = run_gate(payload)
        self.assertEqual(proc.stdout.strip(), "")

    def test_c1_goals_json_met_write_intercepted(self):
        """A Write adding a MET result for a mechanism_backed criterion (C1) with no
        invocation-evidence file (C2) must deny."""
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}])
        proc = run_gate(payload)
        decision, reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")
        self.assertIn("C1", reason)

    def test_c1_non_mechanism_backed_met_write_is_silent(self):
        """The identical write shape for a criterion WITHOUT mechanism_backed:true must stay
        silent -- proves the matcher is scoped to mechanism_backed criteria, not blanket."""
        payload = self.edit_payload([], [{"id": "C2", "status": "MET"}])
        proc = run_gate(payload)
        self.assertEqual(proc.stdout.strip(), "")

    def test_c1_goals_json_met_write_via_edit_intercepted(self):
        """Same shape via the Edit tool (old_string/new_string reconstruction), not just
        Write."""
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}], tool_name="Edit")
        proc = run_gate(payload)
        decision, reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")

    def test_c1_already_met_criterion_not_rematched(self):
        """A criterion already MET before this write is not "newly met" -- an unrelated
        second edit to the same file must not re-trigger the gate for it."""
        payload = self.edit_payload(
            [{"id": "C1", "status": "MET"}], [{"id": "C1", "status": "MET"}],
        )
        proc = run_gate(payload)
        self.assertEqual(proc.stdout.strip(), "")

    def test_c1_tessera_close_intercepted(self):
        """A Bash command invoking the REAL tessera-cli transition-to-closed shape against a
        mechanism_backed ticket, with no invocation evidence, must deny."""
        env = self.tessera_env("FORE-999", True)
        proc = run_gate(self.tessera_close_payload("FORE-999"), env=env)
        decision, reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")
        self.assertIn("FORE-999", reason)

    def test_c1_tessera_close_non_mechanism_backed_is_silent(self):
        env = self.tessera_env("FORE-998", False)
        proc = run_gate(self.tessera_close_payload("FORE-998"), env=env)
        self.assertEqual(proc.stdout.strip(), "")

    def test_c1_tessera_comment_not_closed_is_silent(self):
        """A tessera-cli command that touches the ticket but never transitions to closed
        (e.g. `comment`) must not match -- proves the matcher requires the real closing verb,
        not just "any tessera-cli call naming this ticket"."""
        env = self.tessera_env("FORE-997", True)
        payload = {
            "session_id": "test", "cwd": str(self.root), "tool_name": "Bash",
            "tool_input": {"command": (
                "/usr/bin/python3 -m tessera.api.cli --db data/tessera.db comment "
                "FORE-997 --actor claude --body 'status update'"
            )},
        }
        proc = run_gate(payload, env=env)
        self.assertEqual(proc.stdout.strip(), "")

    # ---- C2: evidence lookup --------------------------------------------------

    def test_c2_close_without_evidence_denied(self):
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}])
        proc = run_gate(payload)
        decision, _reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")

    def test_c2_close_with_passing_evidence_allowed(self):
        self.write_evidence("C1", probe_result="pass")
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}])
        proc = run_gate(payload)
        self.assertEqual(proc.stdout.strip(), "")

    def test_c2_close_with_failing_evidence_denied(self):
        self.write_evidence("C1", probe_result="fail")
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}])
        proc = run_gate(payload)
        decision, reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")
        self.assertIn("fail", reason)

    def test_c2_unreadable_evidence_file_denies(self):
        """A malformed (not-JSON) evidence file must deny, not be treated as absent-and-
        therefore-skippable, and not be treated as passing."""
        evidence_dir = self.root / ".foreman" / "invocation-evidence"
        evidence_dir.mkdir(parents=True)
        (evidence_dir / "C1.json").write_text("{ not valid json")
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}])
        proc = run_gate(payload)
        decision, reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")
        self.assertIn("not valid JSON", reason)

    # ---- C3: override -----------------------------------------------------------

    def test_c3_override_present_allows_and_logs(self):
        self.write_override(expiry="2099-01-01T00:00:00Z")
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}])
        run_id = "k2-fore533-test-c3"
        proc = run_gate(payload, env={"CLAUDE_RUN_ID": run_id})
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)
        verdicts_path = Path.home() / ".claude" / "telemetry" / "verdicts.jsonl"
        found = False
        if verdicts_path.is_file():
            for line in verdicts_path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("run_id") == run_id and row.get("kind") == "k2-override-used":
                    found = True
                    self.assertEqual(row.get("decision"), "allow")
                    self.assertTrue(row.get("target", "").endswith("k2-override.json"))
                    break
        self.assertTrue(found, "expected a kind='k2-override-used' row in verdicts.jsonl")

    def test_c3_expired_override_denies_like_absent(self):
        self.write_override(expiry="2020-01-01T00:00:00Z")
        payload = self.edit_payload([], [{"id": "C1", "status": "MET"}])
        proc = run_gate(payload)
        decision, _reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")

    # ---- FORE-543: TESSERA query failure must fail CLOSED, not silently skip --------------

    def test_fore543_tessera_query_failure_fails_closed_and_denies(self):
        """A real TESSERA subprocess dispatch against a real, nonexistent --db path (the same
        failure shape Iris Chen's pre-registration probe and Dana's Build 3 interface pass both
        confirmed live) must route the close attempt into evaluate()'s own fail-closed
        override/evidence check, not fall through to the silent no-match branch. No override, no
        evidence recorded -- expect deny, matching bob_write_gate.py's posture for the identical
        external-dependency-failure shape.

        Before the FORE-543 fix, ticket_mechanism_backed() returned a bare False on this exact
        failure, match_tessera_close() folded that into "not a match", and main() never called
        evaluate() at all -- proc.stdout.strip() would have been "" (silent), not a deny. That is
        the precise regression this test exists to catch; it fails against the pre-fix code
        (already confirmed by real execution in the FORE-543 ticket thread, not re-derived here)
        and passes only because match_tessera_close() now treats a `None` (could-not-determine)
        result the same as a confirmed `True`.

        PYTEST_SIGNAL is now required in this env dict (adversarial-code-review follow-up,
        ADVERSARIAL-REVIEW-FORE-543-544-20260915.md): K2_TESSERA_DB_PATH_OVERRIDE is gated
        behind the same real-test-runner signal as K2_TESSERA_STUB_PATH, so this test doubles
        as that guard's own positive control -- the override is honored here because the
        signal really is present, not because the guard was removed."""
        bad_db = self.root / "does-not-exist" / "tessera.db"
        env = {"K2_TESSERA_DB_PATH_OVERRIDE": str(bad_db)}
        env.update(PYTEST_SIGNAL)
        proc = run_gate(self.tessera_close_payload("FORE-543-DOES-NOT-EXIST"), env=env)
        decision, reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, f"{err}\nstderr: {proc.stderr}")
        self.assertEqual(decision, "deny", f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        self.assertIn("FORE-543-DOES-NOT-EXIST", reason)

    def test_fore543_confirmed_not_mechanism_backed_still_silent(self):
        """Negative control for the fix above: a CONFIRMED negative lookup (stub says False,
        with the real-test-runner signal present so the stub is honored) must still be silent,
        not swept into the new fail-closed branch by an overly broad fix. Proves the fix
        distinguishes "confirmed no" from "could not determine" rather than just gating
        everything unconditionally."""
        env = self.tessera_env("FORE-543-CONFIRMED-NO", False)
        proc = run_gate(self.tessera_close_payload("FORE-543-CONFIRMED-NO"), env=env)
        self.assertEqual(proc.stdout.strip(), "", proc.stderr)

    # ---- FORE-544: stub escape hatch requires a real test-runner signal --------------------

    def test_fore544_stub_without_signal_is_ignored_falls_through_to_real_query(self):
        """A K2_TESSERA_STUB_PATH set with NO PYTEST_CURRENT_TEST (or any other real-test-
        runner signal) present -- exactly the shape of an ordinary leftover shell export Iris
        Chen's pre-registration probe fired -- must be ignored outright, not trusted. Verified
        against one real, disclosed, read-only production ticket (FORE-544 itself, whose
        custom_fields is real production data already confirmed empty this session): the stub
        falsely claims it IS mechanism_backed, but the real ticket is not, so a correct fix
        falls through to the real query and stays silent. A broken fix (stub still trusted with
        no signal) would instead treat this as mechanism_backed and deny for lack of evidence --
        the two outcomes are distinguishable, so this test discriminates rather than passing
        either way."""
        stub_path = self.root / "leftover-stub.json"
        stub_path.write_text(json.dumps({FORE544_REAL_TICKET_ID: True}))
        env = {"K2_TESSERA_STUB_PATH": str(stub_path)}  # no PYTEST_CURRENT_TEST on purpose
        proc = run_gate(self.tessera_close_payload(FORE544_REAL_TICKET_ID), env=env)
        self.assertEqual(
            proc.stdout.strip(), "",
            f"stub with no real-test-runner signal was trusted -- FORE-544 regression. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}")

    def test_fore544_stub_with_signal_still_honored(self):
        """Companion positive control: the SAME stub shape, with the real-test-runner signal
        present, must still be honored exactly as before -- proves the FORE-544 fix narrows the
        escape hatch rather than removing it outright (this suite's own C1(b)/C3 tests above
        depend on the stub continuing to work under a real test run)."""
        env = self.tessera_env("FORE-544-STUB-HONORED", True)
        proc = run_gate(self.tessera_close_payload("FORE-544-STUB-HONORED"), env=env)
        decision, reason, err = parsed_decision(proc.stdout)
        self.assertIsNone(err, err)
        self.assertEqual(decision, "deny")
        self.assertIn("FORE-544-STUB-HONORED", reason)

    # ---- Adversarial-code-review follow-up: K2_TESSERA_DB_PATH_OVERRIDE requires the same
    # ---- real-test-runner signal as K2_TESSERA_STUB_PATH ------------------------------------

    def test_dbpath_override_without_signal_is_ignored_falls_through_to_real_query(self):
        """ADVERSARIAL-REVIEW-FORE-543-544-20260915.md, check 4 (SERIOUS): before this follow-
        up, K2_TESSERA_DB_PATH_OVERRIDE fired unconditionally -- anything able to set it in the
        hook's real launch environment could redirect the TESSERA query to an attacker-
        controlled sqlite file with no test-runner signal required at all, forcing a False/None
        result and collapsing match_tessera_close() to "not a match" (the exact FORE-543
        silent-bypass shape, reopened on this seam).

        Verified against one real, disclosed, read-only production ticket (FORE-543 itself,
        whose custom_fields is real production data already confirmed empty this session,
        deliberately DIFFERENT from FORE-544's own discriminator ticket above so the two guard
        tests stay independent): the override points at a real, nonexistent --db path with NO
        PYTEST_CURRENT_TEST present. If the guard is broken, the bogus override is honored, the
        real ticket is never actually queried, ticket_mechanism_backed() returns None
        (could-not-determine), and the write is denied for lack of evidence. If the guard is
        correct, the override is ignored, DB_PATH resolves to the real production path, the
        real query runs against the real FORE-543 ticket, and it correctly resolves as NOT
        mechanism_backed -- silent. The two outcomes are distinguishable, so this test
        discriminates rather than passing either way."""
        bad_db = self.root / "attacker-controlled" / "tessera.db"
        env = {"K2_TESSERA_DB_PATH_OVERRIDE": str(bad_db)}  # no PYTEST_CURRENT_TEST on purpose
        proc = run_gate(self.tessera_close_payload(FORE543_REAL_TICKET_ID), env=env)
        self.assertEqual(
            proc.stdout.strip(), "",
            f"K2_TESSERA_DB_PATH_OVERRIDE with no real-test-runner signal was honored -- "
            f"adversarial-code-review follow-up regression. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}")


if __name__ == "__main__":
    unittest.main()
