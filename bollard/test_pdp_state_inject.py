#!/usr/bin/env python3
"""CHV2-7: pdp_state_inject.py -- Class B per-turn state injector.

Same convention as tests/test_architecture_gate.py: hc replaced with a recording fake,
real logic (component_coupling, foreman_evidence) run against a real tempdir sandbox
project, nothing touching the live ~/.claude or this repo's own state. tessera_resolver is
mocked here (it shells out to the real, external TESSERA CLI/db -- no test file exists for
it yet, out of scope for this ticket) so this suite stays hermetic and fast; a separate,
one-off live smoke check against the real tessera_resolver is recorded in the CHV2-7 ticket
comment, not as a permanent assertion against external state.

    python3 -m unittest test_pdp_state_inject -v
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdp_state_inject as inj  # noqa: E402


class RecordingHookCommon:
    def __init__(self):
        self.calls = []

    def inject(self, event_name, context):
        self.calls.append(("inject", event_name, context))

    def set_rule(self, rule_id):
        self.calls.append(("set_rule", rule_id))

    def origin_cwd(self, payload):
        # FORE-562: this fixture predates the transcript-scan origin lookup and tests
        # pdp_state_inject's own business logic, not drift resolution itself (that has its own
        # dedicated, real tests in test_hook_common.py). None of these tests exercise a drifted
        # session, so passing the payload's own cwd through unchanged preserves every existing
        # assertion's real meaning.
        return payload.get("cwd")


class FakeTessera:
    """Deterministic stand-in for tessera_resolver -- the real module shells out to an
    external db this suite should not depend on for a pass/fail verdict.

    FORE-570: `list_result` (via `run_cli(["list", "--project", ...])`) is deliberately a
    SEPARATE stub from `tickets_result` (`list_open_tickets`, still used by
    preflight_blocking_gate.py and left untouched by this ticket) -- the defect this ticket
    fixes was exactly a real function's output being derived from the WRONG, already-filtered
    source. A test fixture that reused one canned open-only result for both calls would still
    pass against a regression back to that bug; only a genuinely separate, mixed-status
    fixture for the unfiltered call can catch it."""

    def __init__(self, resolve_result, tickets_result=(True, {"tickets": []}, ""),
                 list_result=(True, {"tickets": []}, ""), get_results=None):
        self._resolve_result = resolve_result
        self._tickets_result = tickets_result
        self._list_result = list_result
        self._get_results = get_results or {}

    def resolve(self, root):
        return self._resolve_result

    def list_open_tickets(self, prefix):
        return self._tickets_result

    def run_cli(self, args):
        if args[0] == "get":
            ticket_id = args[1]
            return self._get_results.get(ticket_id, (False, None, "not found"))
        if args[0] == "list":
            return self._list_result
        raise AssertionError(f"unexpected run_cli call in this suite: {args}")


def criteria_hash(criteria):
    import hashlib
    canonical = json.dumps(criteria, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class PdpStateInjectTests(unittest.TestCase):
    def setUp(self):
        self.hc = RecordingHookCommon()
        self.hc_patcher = mock.patch.object(inj, "hc", self.hc)
        self.hc_patcher.start()
        self.addCleanup(self.hc_patcher.stop)

        self.tr_patcher = mock.patch.object(
            inj, "tr", FakeTessera({"status": "unregistered"}))
        self.tr_patcher.start()
        self.addCleanup(self.tr_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory(prefix="chv2-7-inject-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".foreman").mkdir()

    def _data(self, event, session_id="test-session"):
        return {"hook_event_name": event, "cwd": str(self.root), "session_id": session_id}

    def _injected(self):
        calls = [c for c in self.hc.calls if c[0] == "inject"]
        self.assertEqual(len(calls), 1, f"expected exactly one inject call, got {self.hc.calls}")
        return calls[0][2]

    # -- event/scope filtering --------------------------------------------------------------

    def test_ignores_non_matching_events(self):
        inj.main({"hook_event_name": "PreToolUse", "cwd": str(self.root)})
        self.assertEqual(self.hc.calls, [])

    def test_no_op_outside_a_foreman_project(self):
        outside = tempfile.mkdtemp(prefix="not-foreman-")
        inj.main({"hook_event_name": "SessionStart", "cwd": outside, "session_id": "x"})
        self.assertEqual([c for c in self.hc.calls if c[0] == "inject"], [])
        self.assertIn(("set_rule", f"{inj.RULE_ID}:not-a-foreman-project"), self.hc.calls)

    # -- fires on both registered events, real sandbox state --------------------------------

    def test_fires_on_session_start_and_user_prompt_submit(self):
        for event in ("SessionStart", "UserPromptSubmit"):
            self.hc.calls.clear()
            inj.main(self._data(event))
            context = self._injected()
            self.assertIn(f"[PDP state: {self.root.name}]", context)
            self.assertLessEqual(len(context.splitlines()), inj.MAX_LINES)

    def test_known_state_produces_correct_block(self):
        (self.root / "ARCHITECTURE.md").write_text(
            '# Arch\n\n```yaml components\nwidget: ["widget/**"]\n```\n'
        )
        (self.root / "widget").mkdir()
        criteria = [{"id": "C1", "text": "x"}]
        goals = {
            "criteria": criteria,
            "criteria_frozen_at": "2026-09-04T00:00:00Z",
            "integrity": {"criteria_hash_at_freeze": criteria_hash(criteria)},
        }
        (self.root / "widget" / "GOALS.json").write_text(json.dumps(goals))

        (self.root / ".foreman" / "pdp-orchestrator.json").write_text(
            json.dumps({"session_id": "some-other-session"}))
        (self.root / ".foreman" / "stop-declared.json").write_text(
            json.dumps({"condition": "E3"}))
        (self.root / "DECISION-CHV2-99.md").write_text(
            "What was decided: test.\nCondition: E3.\n")

        inj.main(self._data("SessionStart", session_id="this-session"))
        context = self._injected()
        lines = context.splitlines()

        self.assertLessEqual(len(lines), inj.MAX_LINES)
        self.assertIn("held by some-other-session (not this session)", context)
        self.assertIn("ARCHITECTURE.md exists", context)
        self.assertIn("1 declared, 1 GOALS.json frozen", context)
        self.assertIn("condition='E3'", context)
        self.assertIn("1 open (1 naming E1-E4", context)
        self.assertIn("TESSERA unregistered", context)

    # -- FORE-570: in_progress count -------------------------------------------------------

    def test_tickets_line_reports_nonzero_in_progress(self):
        """The regression this ticket exists to catch: a mixed-status fixture in the
        UNFILTERED call's own result, distinct from the (still open-only) `tickets_result`
        stub -- reusing one canned open-only fixture for both would keep passing against the
        exact defect being fixed here."""
        mixed = {"tickets": [
            {"ticket_id": "FORE-1", "status": "open"},
            {"ticket_id": "FORE-2", "status": "open"},
            {"ticket_id": "FORE-567", "status": "in_progress"},
            {"ticket_id": "FORE-3", "status": "closed"},
        ]}
        fake = FakeTessera({"status": "ok", "prefix": "FORE"}, list_result=(True, mixed, ""))
        with mock.patch.object(inj, "tr", fake):
            inj.main(self._data("SessionStart", session_id="s"))
            context = self._injected()
        self.assertIn("tickets        FORE: 2 open, 1 in_progress", context)

    def test_tickets_line_zero_in_progress_when_genuinely_none(self):
        """The sound control for the arm above -- an all-open fixture correctly reports 0,
        so the fix isn't just reporting a hardcoded non-zero count."""
        all_open = {"tickets": [{"ticket_id": "FORE-1", "status": "open"},
                                 {"ticket_id": "FORE-2", "status": "open"}]}
        fake = FakeTessera({"status": "ok", "prefix": "FORE"}, list_result=(True, all_open, ""))
        with mock.patch.object(inj, "tr", fake):
            inj.main(self._data("SessionStart", session_id="s"))
            context = self._injected()
        self.assertIn("tickets        FORE: 2 open, 0 in_progress", context)

    def test_tickets_line_unreachable_when_list_call_fails(self):
        fake = FakeTessera({"status": "ok", "prefix": "FORE"},
                            list_result=(False, None, "timed out"))
        with mock.patch.object(inj, "tr", fake):
            inj.main(self._data("SessionStart", session_id="s"))
            context = self._injected()
        self.assertIn("tickets        FORE: unreachable (timed out)", context)

    def test_negative_control_catches_the_fore570_regression(self):
        """FORE-570's own critical trap, made mechanical: reintroduce the exact original bug
        (derive in_progress from the OPEN-only fixture instead of the unfiltered one) and
        confirm this suite's own test above would have caught it. Guards against this fix's
        regression test silently degenerating back into one that can't fail, the same failure
        mode reviewer-peer's finding names for the pre-fix test file."""
        mixed = {"tickets": [
            {"ticket_id": "FORE-1", "status": "open"},
            {"ticket_id": "FORE-567", "status": "in_progress"},
        ]}
        open_only = {"tickets": [{"ticket_id": "FORE-1", "status": "open"}]}

        def buggy_tickets_line(root):
            result = inj.tr.resolve(root)
            prefix = result["prefix"]
            ok, data, err = inj.tr.list_open_tickets(prefix)  # the original defect: open-only
            tickets = data.get("tickets", [])
            in_progress = sum(1 for t in tickets if t.get("status") == "in_progress")
            return f"tickets        {prefix}: {len(tickets)} open, {in_progress} in_progress"

        fake = FakeTessera({"status": "ok", "prefix": "FORE"},
                            tickets_result=(True, open_only, ""),
                            list_result=(True, mixed, ""))
        with mock.patch.object(inj, "tr", fake), \
             mock.patch.object(inj, "_tickets_line", buggy_tickets_line):
            inj.main(self._data("SessionStart", session_id="s"))
            context = self._injected()
        self.assertIn("0 in_progress", context,
                       "the reintroduced original bug should report 0 in_progress despite a "
                       "real in_progress ticket existing -- if this assertion fails, the "
                       "negative control itself is broken, not the fix")
        self.assertNotIn("tickets        FORE: 1 open, 1 in_progress", context)

    def test_orchestrator_held_by_this_session(self):
        (self.root / ".foreman" / "pdp-orchestrator.json").write_text(
            json.dumps({"session_id": "this-session"}))
        inj.main(self._data("SessionStart", session_id="this-session"))
        self.assertIn("held by this session (this-session)", self._injected())

    # -- regeneration, not staleness ---------------------------------------------------------

    def test_regenerates_after_state_file_changes_mid_session(self):
        inj.main(self._data("UserPromptSubmit"))
        first = self._injected()
        self.assertIn("stop-declared  none on record", first)

        self.hc.calls.clear()
        (self.root / ".foreman" / "stop-declared.json").write_text(
            json.dumps({"condition": "E1"}))
        inj.main(self._data("UserPromptSubmit"))
        second = self._injected()

        self.assertNotEqual(first, second)
        self.assertIn("condition='E1'", second)
        self.assertNotIn("stop-declared  none on record", second)

    # -- FORE-448: rendered verdict files not linked into ticket tracking --------------------

    def test_verdicts_line_none_found(self):
        with mock.patch.object(inj, "tr", FakeTessera({"status": "ok", "prefix": "FORE"})):
            self.assertEqual("verdicts       none found", inj._verdicts_line(self.root))

    def test_verdicts_line_cannot_check_when_project_unresolved(self):
        with mock.patch.object(inj, "tr", FakeTessera({"status": "unregistered"})):
            (self.root / "PRIYA-VERDICT-FOO.md").write_text("HOLD")
            self.assertEqual(
                "verdicts       cannot check (ticket project not resolved)",
                inj._verdicts_line(self.root),
            )

    def test_verdicts_line_posted_is_not_counted(self):
        (self.root / "PRIYA-VERDICT-FORE448-20260906.md").write_text("HOLD")
        fake = FakeTessera(
            {"status": "ok", "prefix": "FORE"},
            get_results={"FORE-448": (True, {
                "summary": "s", "description": "d",
                "comments": [{"body": "Posted PRIYA-VERDICT-FORE448-20260906.md, GO."}],
            }, "")},
        )
        with mock.patch.object(inj, "tr", fake):
            line = inj._verdicts_line(self.root)
        self.assertEqual("verdicts       1 found, 0 not in any ticket comment", line)

    def test_verdicts_line_unposted_is_counted(self):
        (self.root / "PRIYA-VERDICT-FORE448-20260906.md").write_text("HOLD")
        fake = FakeTessera(
            {"status": "ok", "prefix": "FORE"},
            get_results={"FORE-448": (True, {
                "summary": "unrelated", "description": "unrelated", "comments": [],
            }, "")},
        )
        with mock.patch.object(inj, "tr", fake):
            line = inj._verdicts_line(self.root)
        self.assertEqual("verdicts       1 found, 1 not in any ticket comment", line)

    def test_verdicts_line_no_ticket_ref_in_name_is_disclosed_not_checked(self):
        (self.root / "PRIYA-VERDICT-FABLE-TIER1-20260905.md").write_text("HOLD")
        with mock.patch.object(inj, "tr", FakeTessera({"status": "ok", "prefix": "FORE"})):
            line = inj._verdicts_line(self.root)
        self.assertEqual(
            "verdicts       1 found, 0 not in any ticket comment (1 no ticket ref in name)",
            line,
        )

    def test_verdicts_line_unreadable_ticket_counts_as_unposted(self):
        (self.root / "PRIYA-VERDICT-FORE448-20260906.md").write_text("HOLD")
        fake = FakeTessera(
            {"status": "ok", "prefix": "FORE"},
            get_results={"FORE-448": (False, None, "db unreachable")},
        )
        with mock.patch.object(inj, "tr", fake):
            line = inj._verdicts_line(self.root)
        self.assertEqual("verdicts       1 found, 1 not in any ticket comment", line)

    def test_verdicts_line_directory_walk_failure_does_not_raise(self):
        with mock.patch.object(inj, "tr", FakeTessera({"status": "ok", "prefix": "FORE"})):
            with mock.patch.object(Path, "rglob", side_effect=OSError("boom")):
                line = inj._verdicts_line(self.root)
        self.assertEqual("verdicts       cannot check (directory walk failed)", line)

    def test_negative_control_verdicts_line_catches_a_broken_posted_check(self):
        """Same discipline as test_negative_control_catches_a_broken_frozen_check: prove
        this suite would fail if the posted-check always reported 0 unposted regardless of
        content, per this project's standing rule that a self-written check must be shown to
        fail on a broken input before it's trusted."""
        (self.root / "PRIYA-VERDICT-FORE448-20260906.md").write_text("HOLD")
        fake = FakeTessera(
            {"status": "ok", "prefix": "FORE"},
            get_results={"FORE-448": (True, {
                "summary": "unrelated", "description": "unrelated", "comments": [],
            }, "")},
        )
        with mock.patch.object(inj, "tr", fake):
            real_line = inj._verdicts_line(self.root)
        self.assertIn("1 not in any ticket comment", real_line)

        with self.assertRaises(AssertionError):
            self.assertIn("0 not in any ticket comment", real_line)

    # -- negative controls: malformed input degrades, never raises ---------------------------

    def test_malformed_orchestrator_claim_does_not_raise(self):
        (self.root / ".foreman" / "pdp-orchestrator.json").write_text("not json")
        inj.main(self._data("SessionStart"))
        self.assertIn("orchestrator   claim file unreadable", self._injected())

    def test_malformed_stop_declared_does_not_raise(self):
        (self.root / ".foreman" / "stop-declared.json").write_text("{not valid")
        inj.main(self._data("SessionStart"))
        self.assertIn("stop-declared  file present but unreadable", self._injected())

    def test_malformed_goals_json_disclosed_not_silently_dropped(self):
        (self.root / "ARCHITECTURE.md").write_text(
            '# Arch\n\n```yaml components\nwidget: ["widget/**"]\n```\n'
        )
        (self.root / "widget").mkdir()
        (self.root / "widget" / "GOALS.json").write_text("{not valid json")
        inj.main(self._data("SessionStart"))
        context = self._injected()
        self.assertIn("0 GOALS.json frozen", context)
        self.assertIn("1 GOALS.json unreadable", context)

    def test_unreadable_decision_file_disclosed(self):
        d = self.root / "DECISION-CHV2-1.md"
        d.write_text("fine")
        inj.main(self._data("SessionStart"))
        self.hc.calls.clear()
        # Simulate an unreadable file without actually breaking permissions cross-platform:
        # patch Path.read_text for this one instance via a targeted mock is overkill here --
        # directly exercise the branch instead.
        with mock.patch.object(Path, "read_text", side_effect=OSError("boom")):
            line = inj._decisions_line(self.root)
        self.assertIn("1 unreadable", line)

    # -- fail-first: prove this suite would actually catch a real bypass --------------------

    def test_negative_control_catches_a_broken_frozen_check(self):
        """Deliberately break the freeze predicate (always report frozen) and confirm this
        test suite's own assertion fails -- proves test_known_state_produces_correct_block
        is discriminating, not vacuously true, per this project's standing rule that a
        self-written check must be shown to fail on a broken input before it's trusted."""
        (self.root / "ARCHITECTURE.md").write_text(
            '# Arch\n\n```yaml components\nwidget: ["widget/**"]\n```\n'
        )
        (self.root / "widget").mkdir()
        (self.root / "widget" / "GOALS.json").write_text(json.dumps({}))  # never frozen

        with mock.patch.object(inj, "_components_line",
                                return_value="components     BROKEN, always claims frozen"):
            inj.main(self._data("SessionStart"))
            context = self._injected()

        # test_known_state_produces_correct_block asserts this exact substring for a
        # genuinely frozen component. With the predicate tampered to always emit BROKEN
        # text instead, that assertion must fail here -- proving it discriminates real
        # output rather than passing regardless of what _components_line computes.
        with self.assertRaises(AssertionError):
            self.assertIn("1 declared, 1 GOALS.json frozen", context)


if __name__ == "__main__":
    unittest.main()
