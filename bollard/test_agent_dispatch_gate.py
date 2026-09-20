#!/usr/bin/env python3
"""Regression tests for FORE-206's agent_dispatch_gate.py: blanket deny on tool_name=="Agent",
silent pass-through for every other tool, and the fail-closed entrypoint (section 4.5's
convention -- an internal bug must deny, never silently allow the exact in-process dispatch
this gate exists to stop). No test file existed for this gate before FORE-206's DF-sprint pass;
this is that artifact.

    python3 -m unittest test_agent_dispatch_gate -v
"""
import re
import unittest
from pathlib import Path
from unittest import mock

import agent_dispatch_gate as adg


class MainDenyTests(unittest.TestCase):
    def _run(self, payload):
        with mock.patch.object(adg.hc, "deny") as deny, \
             mock.patch.object(adg.hc, "set_rule") as set_rule, \
             mock.patch.object(adg.hc, "audit") as audit:
            adg.main(payload)
            return deny, set_rule, audit

    def test_agent_tool_denies(self):
        deny, set_rule, audit = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "general-purpose"}})
        deny.assert_called_once()
        reason = deny.call_args[0][0]
        self.assertIn("FORE-206", reason)
        self.assertIn("ListAgents", reason)
        self.assertIn("SendMessage", reason)

    def test_agent_tool_sets_rule_id(self):
        _, set_rule, _ = self._run(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "general-purpose"}})
        set_rule.assert_called_once_with("AGENT-DISPATCH-GATE:in-process-dispatch-denied")

    def test_agent_tool_audits_with_subagent_type(self):
        _, _, audit = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "muse"}})
        audit.assert_called_once()
        args, kwargs = audit.call_args
        event_type, event_data = args[0], args[1]
        self.assertEqual(event_type, "SAFETY_DENY")
        self.assertEqual(event_data["subagent_type"], "muse")
        self.assertEqual(kwargs.get("severity"), "high")

    def test_agent_tool_missing_subagent_type_reports_unspecified(self):
        _, _, audit = self._run({"tool_name": "Agent", "tool_input": {}})
        self.assertEqual(audit.call_args[0][1]["subagent_type"], "(unspecified)")

    def test_agent_tool_no_tool_input_key_at_all(self):
        # tool_input entirely absent (not just empty) -- main() does `data.get("tool_input") or
        # {}`, must not raise on a plain .get(None) chain.
        deny, _, audit = self._run({"tool_name": "Agent"})
        deny.assert_called_once()
        self.assertEqual(audit.call_args[0][1]["subagent_type"], "(unspecified)")


class ResearchAllowlistTests(unittest.TestCase):
    """FORE-462, against the frozen GOALS.json criteria (hooks/agent_dispatch_gate.GOALS.json,
    C2/C3/C4/C5/C6/C7/C8)."""

    def _run(self, payload):
        with mock.patch.object(adg.hc, "deny") as deny, \
             mock.patch.object(adg.hc, "set_rule") as set_rule, \
             mock.patch.object(adg.hc, "audit") as audit:
            adg.main(payload)
            return deny, set_rule, audit

    # C2
    def test_explore_subagent_type_is_allowed_and_audited(self):
        deny, set_rule, audit = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "Explore"}})
        deny.assert_not_called()
        set_rule.assert_called_once_with("AGENT-DISPATCH-GATE:research-only-allowed")
        audit.assert_called_once()
        event_type, event_data = audit.call_args[0][0], audit.call_args[0][1]
        self.assertEqual(event_type, "SAFETY_ALLOW")
        self.assertEqual(event_data["subagent_type"], "Explore")

    # C3
    def test_every_named_judgment_persona_is_denied(self):
        judgment_personas = ("marcus-webb", "dana-okafor", "clint-eastwood", "nadia-osei",
                              "priya-desai", "muse", "iris-chen", "owen-reyes")
        for persona in judgment_personas:
            with self.subTest(persona=persona):
                deny, _, _ = self._run(
                    {"tool_name": "Agent", "tool_input": {"subagent_type": persona}})
                deny.assert_called_once()

    # C4
    def test_general_purpose_and_claude_are_denied(self):
        for subagent_type in ("general-purpose", "claude"):
            with self.subTest(subagent_type=subagent_type):
                deny, _, _ = self._run(
                    {"tool_name": "Agent", "tool_input": {"subagent_type": subagent_type}})
                deny.assert_called_once()

    # C5
    def test_missing_or_empty_subagent_type_is_denied(self):
        for tool_input in ({}, {"subagent_type": None}, {"subagent_type": ""}):
            with self.subTest(tool_input=tool_input):
                deny, _, _ = self._run({"tool_name": "Agent", "tool_input": tool_input})
                deny.assert_called_once()

    # C6
    def test_unrecognized_subagent_type_defaults_to_denied(self):
        deny, _, _ = self._run(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "some-future-persona-fore462"}})
        deny.assert_called_once()

    # C7
    def test_near_miss_allowlist_strings_are_denied(self):
        near_misses = ("explore", "Explore ", "ExploreDeep", " Explore")
        for value in near_misses:
            with self.subTest(value=repr(value)):
                deny, _, _ = self._run({"tool_name": "Agent", "tool_input": {"subagent_type": value}})
                deny.assert_called_once()

    # C8
    def test_malformed_subagent_type_value_denies_not_crashes(self):
        for bad_value in (["Explore"], 42, {"nested": "Explore"}):
            with self.subTest(bad_value=bad_value):
                deny, _, audit = self._run(
                    {"tool_name": "Agent", "tool_input": {"subagent_type": bad_value}})
                deny.assert_called_once()
                self.assertEqual(audit.call_args[0][1]["subagent_type"], "(unspecified)")

    def test_deny_message_names_the_allowlisted_types_as_an_alternative(self):
        deny, _, _ = self._run({"tool_name": "Agent", "tool_input": {"subagent_type": "muse"}})
        self.assertIn("Explore", deny.call_args[0][0])

    # C10 (CHV2-66/67): muse-feedback is allowed and audited as a distinct visible event, same
    # shape as C2's Explore case.
    def test_muse_feedback_subagent_type_is_allowed_and_audited(self):
        deny, set_rule, audit = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "muse-feedback"}})
        deny.assert_not_called()
        set_rule.assert_called_once_with("AGENT-DISPATCH-GATE:research-only-allowed")
        audit.assert_called_once()
        event_type, event_data = audit.call_args[0][0], audit.call_args[0][1]
        self.assertEqual(event_type, "SAFETY_ALLOW")
        self.assertEqual(event_data["subagent_type"], "muse-feedback")

    # C7 generalized -- muse-feedback near-misses, per the CHV2-66 scope doc's named cases.
    def test_muse_feedback_near_miss_strings_are_denied(self):
        for value in ("muse-feedback ", "Muse-Feedback", "muse-feedbackX"):
            with self.subTest(value=value):
                deny, _, _ = self._run(
                    {"tool_name": "Agent", "tool_input": {"subagent_type": value}})
                deny.assert_called_once()

    # C11 -- the definition-binding test: the gate only ever sees a string (CHV2-66 section 7's
    # own named residual), so this is the separate check that the file the string names actually
    # carries the tools: line the allowlist decision assumed. Bound to the path the harness
    # actually resolves (~/.claude/agents/), not any repo-local copy (Clint addendum A2).
    def test_muse_feedback_definition_tools_line_is_exactly_read_grep_glob(self):
        definition_path = Path.home() / ".claude" / "agents" / "muse-feedback.md"
        self.assertTrue(definition_path.is_file(),
                         f"{definition_path} missing -- must be landed before this gate allows it")
        text = definition_path.read_text()
        match = re.search(r"^tools:\s*(.+)$", text, re.MULTILINE)
        self.assertIsNotNone(match, "no frontmatter 'tools:' line found")
        self.assertEqual(match.group(1).strip(), "Read, Grep, Glob")


class ResolvedDefinitionExceedsAdmissionTests(unittest.TestCase):
    """CHV2-121 GAP 1: _resolved_definition_exceeds_admission() itself, real file reads
    against a real temp directory -- no mock of file I/O, only of which cwd/HOME the real
    resolver sees."""

    def _with_fixture_agent_dirs(self, home):
        """agent_registry_staleness_check.AGENT_DIRS is a MODULE-LEVEL constant, computed
        ONCE at import time from the real Path.home() -- confirmed empirically before choosing
        this approach: mocking Path.home() during a test has no effect on it, since it was
        already evaluated before the mock ever applied. Patching AGENT_DIRS itself is the real,
        correct seam."""
        return mock.patch.object(
            adg.agent_registry_staleness_check, "AGENT_DIRS",
            (Path(home) / ".claude" / "agents",))

    def test_builtin_with_no_definition_file_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            with self._with_fixture_agent_dirs(home):
                result = adg._resolved_definition_exceeds_admission("Explore", cwd=None)
        self.assertIsNone(result)

    def test_definition_within_admitted_tools_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            agents = Path(home) / ".claude" / "agents"
            agents.mkdir(parents=True)
            (agents / "muse-feedback.md").write_text(
                "---\ntools: Read, Grep, Glob\n---\nbody\n", encoding="utf-8")
            with self._with_fixture_agent_dirs(home):
                result = adg._resolved_definition_exceeds_admission("muse-feedback", cwd=None)
        self.assertIsNone(result)

    def test_widened_definition_returns_a_real_reason(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            agents = Path(home) / ".claude" / "agents"
            agents.mkdir(parents=True)
            (agents / "muse-feedback.md").write_text(
                "---\ntools: Read, Grep, Glob, Bash, Write\n---\nbody\n", encoding="utf-8")
            with self._with_fixture_agent_dirs(home):
                result = adg._resolved_definition_exceeds_admission("muse-feedback", cwd=None)
        self.assertIsNotNone(result)
        self.assertIn("Bash", result)
        self.assertIn("Write", result)

    def test_missing_tools_key_is_the_maximal_grant_and_returns_a_reason(self):
        """CHV2-121 GAP 3's own case, exercised here at the production-code layer too: an
        absent tools: key grants every tool and must not read as a trivial pass."""
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            agents = Path(home) / ".claude" / "agents"
            agents.mkdir(parents=True)
            (agents / "muse-feedback.md").write_text("---\nname: x\n---\nbody\n",
                                                       encoding="utf-8")
            with self._with_fixture_agent_dirs(home):
                result = adg._resolved_definition_exceeds_admission("muse-feedback", cwd=None)
        self.assertIsNotNone(result)
        self.assertIn("EVERY", result)


class MainDeniesOnWidenedDefinitionTests(unittest.TestCase):
    """CHV2-121 GAP 1, at main()'s own real decision layer -- real file, real resolver,
    only hc.deny/audit/set_rule mocked (this file's own established convention throughout)."""

    def _run(self, payload):
        with mock.patch.object(adg.hc, "deny") as deny,              mock.patch.object(adg.hc, "set_rule") as set_rule,              mock.patch.object(adg.hc, "audit") as audit:
            adg.main(payload)
            return deny, set_rule, audit

    def test_widened_muse_feedback_definition_is_now_denied(self):
        """The exact real finding this ticket exists to close: before this fix, an ALLOW was
        produced regardless of what muse-feedback.md's own tools: line said. AGENT_DIRS is
        patched directly rather than Path.home() -- see
        ResolvedDefinitionExceedsAdmissionTests._with_fixture_agent_dirs's own docstring for
        why mocking Path.home() has no effect on that module-level constant."""
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            agents = Path(home) / ".claude" / "agents"
            agents.mkdir(parents=True)
            (agents / "muse-feedback.md").write_text(
                "---\ntools: Read, Grep, Glob, Bash, Write\n---\nbody\n", encoding="utf-8")
            with mock.patch.object(adg.agent_registry_staleness_check, "AGENT_DIRS",
                                   (agents,)):
                deny, set_rule, audit = self._run(
                    {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
                     "tool_input": {"subagent_type": "muse-feedback"}})
        deny.assert_called_once()
        set_rule.assert_called_once_with("AGENT-DISPATCH-GATE:definition-exceeds-admission")
        event_type, event_data = audit.call_args[0][0], audit.call_args[0][1]
        self.assertEqual(event_type, "SAFETY_DENY")
        self.assertEqual(event_data["reason"], "resolved-definition-exceeds-admission-rule")

    def test_unmodified_real_definition_still_allowed(self):
        """The control: this fix must not deny the real, correct, unmodified case."""
        deny, set_rule, audit = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "muse-feedback"}})
        deny.assert_not_called()
        set_rule.assert_called_once_with("AGENT-DISPATCH-GATE:research-only-allowed")


class MainPassThroughTests(unittest.TestCase):
    """Every non-Agent tool must be a total no-op: no deny, no audit, no rule set. This gate
    has no business opining on Write/Edit/Bash/anything else -- that is bob_write_gate.py's
    and the other hard gates' job."""

    def _run(self, tool_name):
        with mock.patch.object(adg.hc, "deny") as deny, \
             mock.patch.object(adg.hc, "set_rule") as set_rule, \
             mock.patch.object(adg.hc, "audit") as audit:
            result = adg.main({"tool_name": tool_name, "tool_input": {}})
            return result, deny, set_rule, audit

    def test_non_agent_tools_are_silent_no_ops(self):
        for tool_name in ("Write", "Edit", "Bash", "Read", "NotebookEdit", None):
            with self.subTest(tool_name=tool_name):
                result, deny, set_rule, audit = self._run(tool_name)
                self.assertIsNone(result)
                deny.assert_not_called()
                set_rule.assert_not_called()
                audit.assert_not_called()


class EntrypointTests(unittest.TestCase):
    """Section 4.5's fail-closed entrypoint (this gate's own module docstring: hc.run() would
    fail OPEN on an internal error, which for a blanket-deny policy gate means silently
    allowing the exact in-process dispatch it exists to stop -- so this file rolls its own
    fail-closed wrapper instead). hc.deny/hc.audit/verdict_ledger.record/audit_lib are mocked --
    this verifies fail-closed CONTROL FLOW, not the real audit plane's write behavior."""

    def test_non_dict_payload_denies_not_crashes(self):
        # Unlike bob_write_gate.py, this gate's except BaseException block reports only the
        # exception TYPE name in the deny reason (type(exc).__name__), not str(exc) -- so the
        # ValueError's own "not a JSON object" message never reaches the deny reason. Asserting
        # on the actual generic wording, not the more specific message the source raises.
        with mock.patch.object(adg.hc, "read_input", return_value=["not", "a", "dict"]), \
             mock.patch.object(adg.hc, "deny") as mock_deny, \
             mock.patch.object(adg.hc, "audit"), \
             mock.patch.object(adg.hc, "set_rule"), \
             mock.patch.object(adg, "verdict_ledger"), \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            adg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_called_once()
        reason = mock_deny.call_args[0][0]
        self.assertIn("internal error", reason)
        self.assertIn("ValueError", reason)

    def test_internal_exception_denies_fail_closed_not_open(self):
        """The exact failure mode this gate's docstring names: a bug in this gate's own logic
        must deny, never silently allow the in-process Agent dispatch through."""
        data = {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
                "tool_input": {"subagent_type": "general-purpose"}}
        with mock.patch.object(adg.hc, "read_input", return_value=data), \
             mock.patch.object(adg, "main", side_effect=RuntimeError("simulated internal bug")), \
             mock.patch.object(adg.hc, "deny") as mock_deny, \
             mock.patch.object(adg.hc, "audit"), \
             mock.patch.object(adg.hc, "set_rule"), \
             mock.patch.object(adg, "verdict_ledger"), \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            adg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_called_once()
        self.assertIn("internal error", mock_deny.call_args[0][0])

    def test_internal_exception_records_hook_error_to_audit_plane(self):
        data = {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp", "tool_input": {}}
        with mock.patch.object(adg.hc, "read_input", return_value=data), \
             mock.patch.object(adg, "main", side_effect=RuntimeError("boom")), \
             mock.patch.object(adg.hc, "deny"), \
             mock.patch.object(adg.hc, "audit"), \
             mock.patch.object(adg.hc, "set_rule"), \
             mock.patch.object(adg, "verdict_ledger"), \
             mock.patch.object(adg, "audit_lib") as mock_audit_lib, \
             self.assertRaises(SystemExit):
            adg._fail_closed_entrypoint()
        mock_audit_lib.audit_append.assert_called_once()
        kwargs = mock_audit_lib.audit_append.call_args.kwargs
        self.assertEqual(kwargs.get("session_id"), "s1")
        self.assertEqual(kwargs.get("hook"), "agent_dispatch_gate.py")

    def test_non_agent_tool_completes_without_deny_and_exits_zero(self):
        data = {"tool_name": "Write", "session_id": "s1", "cwd": "/tmp",
                "tool_input": {"file_path": "/tmp/x.py"}}
        with mock.patch.object(adg.hc, "read_input", return_value=data), \
             mock.patch.object(adg.hc, "deny") as mock_deny, \
             mock.patch.object(adg.hc, "audit"), \
             mock.patch.object(adg.hc, "set_rule"), \
             mock.patch.object(adg, "verdict_ledger") as mock_ledger, \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            adg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_not_called()
        mock_ledger.record.assert_called_once()

    # C9
    def test_internal_exception_during_allowlist_check_still_denies_fail_closed(self):
        data = {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
                "tool_input": {"subagent_type": "Explore"}}
        with mock.patch.object(adg.hc, "read_input", return_value=data), \
             mock.patch.object(adg, "RESEARCH_ONLY_SUBAGENT_TYPES") as mock_allowlist, \
             mock.patch.object(adg.hc, "deny") as mock_deny, \
             mock.patch.object(adg.hc, "audit"), \
             mock.patch.object(adg.hc, "set_rule"), \
             mock.patch.object(adg, "verdict_ledger"), \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            mock_allowlist.__contains__.side_effect = RuntimeError("simulated allowlist bug")
            adg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_called_once()
        self.assertIn("internal error", mock_deny.call_args[0][0])

    def test_verdict_ledger_write_failure_does_not_crash_the_entrypoint(self):
        """verdict_ledger.record raising must not turn into an unhandled exception that skips
        sys.exit(0) -- this is the audit plane's own failure, not a reason to change the
        gate's decision or its exit behavior."""
        data = {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp", "tool_input": {}}
        with mock.patch.object(adg.hc, "read_input", return_value=data), \
             mock.patch.object(adg.hc, "deny") as mock_deny, \
             mock.patch.object(adg.hc, "audit"), \
             mock.patch.object(adg.hc, "set_rule"), \
             mock.patch.object(adg, "verdict_ledger") as mock_ledger, \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            mock_ledger.record.side_effect = RuntimeError("ledger disk full")
            adg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_deny.assert_called_once()


class ExploreShapedSilentRoutingTests(unittest.TestCase):
    """FORE-660 (Stage 3, escalated design, operator decision 2026-09-17/18): silent routing
    for the narrow, evidenced EXPLORE_SHAPED_ALIASES set only ("fork" -- the real repro case
    this project's own incidents named), never a deny, never a blanket unrecognized-type
    substitution."""

    def _run(self, payload):
        with mock.patch.object(adg.hc, "deny") as deny, \
             mock.patch.object(adg.hc, "rewrite") as rewrite, \
             mock.patch.object(adg.hc, "set_rule") as set_rule, \
             mock.patch.object(adg.hc, "audit") as audit:
            adg.main(payload)
            return deny, rewrite, set_rule, audit

    def test_fork_is_silently_routed_not_denied(self):
        deny, rewrite, set_rule, audit = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "fork", "prompt": "look into the auth module"}})
        deny.assert_not_called()
        rewrite.assert_called_once()
        set_rule.assert_called_once_with("AGENT-DISPATCH-GATE:explore-shaped-silent-route")

    def test_fork_rewrite_preserves_every_other_field(self):
        """The real bug class this test guards against: a rewrite that drops the original
        prompt/description would silently lose the actual task, not just the persona choice."""
        _, rewrite, _, _ = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "fork", "prompt": "look into the auth module",
                             "description": "auth audit"}})
        updated_input = rewrite.call_args[0][0]
        self.assertEqual(updated_input["subagent_type"], "Explore")
        self.assertEqual(updated_input["prompt"], "look into the auth module")
        self.assertEqual(updated_input["description"], "auth audit")

    def test_substitution_is_logged_with_both_requested_and_routed_values(self):
        """Standing requirement (operator decision, FORE-660): silent-to-the-agent must not
        mean silent-to-the-record."""
        _, _, _, audit = self._run(
            {"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
             "tool_input": {"subagent_type": "fork"}})
        audit.assert_called_once()
        event_type, event_data = audit.call_args[0][0], audit.call_args[0][1]
        self.assertEqual(event_type, "SAFETY_ALLOW")
        self.assertEqual(event_data["requested_subagent_type"], "fork")
        self.assertEqual(event_data["routed_subagent_type"], "Explore")

    def test_muse_is_still_denied_not_silently_routed(self):
        """The explicitly REJECTED case (operator decision, FORE-660): 'for muse use Explore'
        would be the proxy-dispatch anti-pattern the architecture record already documents.
        muse must still deny, exactly as C3 already requires -- this test guards against a
        future change accidentally widening EXPLORE_SHAPED_ALIASES to cover it."""
        deny, rewrite, _, _ = self._run(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "muse"}})
        deny.assert_called_once()
        rewrite.assert_not_called()

    def test_an_unrecognized_type_outside_the_alias_set_still_denies(self):
        """Control against an overly broad reading of this fix: only the specific, evidenced
        alias set routes silently. A different unrecognized string (not a real persona, not in
        EXPLORE_SHAPED_ALIASES either) must still deny -- the fail-closed default for unknown
        input is unchanged for everything outside this one narrow set."""
        deny, rewrite, _, _ = self._run(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "banana"}})
        deny.assert_called_once()
        rewrite.assert_not_called()

    def test_case_variant_of_the_alias_still_denies(self):
        """Exact-match discipline, same as C7's own for RESEARCH_ONLY_SUBAGENT_TYPES: no
        case-folding for EXPLORE_SHAPED_ALIASES either."""
        deny, rewrite, _, _ = self._run(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "Fork"}})
        deny.assert_called_once()
        rewrite.assert_not_called()


class SubagentTypeBoundedInDenyMessageTests(unittest.TestCase):
    """CHV2-162. The deny message interpolated the caller's own subagent_type unbounded, and
    twice. A long one pushed the gate's own remedies out of the message, and therefore out of
    the verdict-ledger row that is the durable record of what the agent was told to do.

    Measured before the fix: a 600-char subagent_type produced a 1946-char reason, roughly 400
    stored characters of which were the caller's own string, with remedy (1) absent entirely.

    CHV2-119's head-and-tail ledger truncation cannot fix this, and that is why the bound lives
    here: text evicted from BOTH ends of the message never reaches the ledger to be truncated
    intelligently. Measured across every candidate strategy there, remedy (1) was lost under all
    of them."""

    def _run(self, subagent_type):
        with mock.patch.object(adg.hc, "deny") as deny,              mock.patch.object(adg.hc, "set_rule"),              mock.patch.object(adg.hc, "audit") as audit:
            adg.main({"tool_name": "Agent", "session_id": "s1", "cwd": "/tmp",
                      "tool_input": {"subagent_type": subagent_type}})
            return deny.call_args[0][0], audit

    def test_an_ordinary_subagent_type_is_not_truncated(self):
        """The control. A cap that mangled real type names would pass every test below while
        breaking the message for every genuine deny. clint-eastwood is the longest real type
        measured on this machine, at 14 characters."""
        reason, _ = self._run("clint-eastwood")
        self.assertIn("clint-eastwood", reason)
        self.assertNotIn("truncated", reason)

    def test_a_long_subagent_type_is_truncated_and_states_its_real_length(self):
        reason, _ = self._run("A" * 600)
        self.assertIn("600 chars, truncated", reason,
                      "the marker must state the real length -- a reader needs to know 600 "
                      "characters were sent, not merely that something was cut")
        self.assertNotIn("A" * 200, reason, "the full value still reached the message")

    def test_both_remedies_survive_into_the_LEDGER_ROW_not_just_the_message(self):
        """The finding itself, asserted at the layer the claim is actually about.

        A first version of this test checked the deny MESSAGE and passed against the unfixed
        gate -- correctly, because both remedies ARE present in the 1946-char message a 600-char
        type produces. The eviction happens when verdict_ledger bounds that message into the
        durable row. Asserting on the message tested the wrong layer and proved nothing, which
        is the proxy-assertion failure this session has now hit several times.

        So this fires the real gate as a real subprocess under a throwaway HOME and reads the
        real verdicts.jsonl row. It depends on CHV2-119's head-and-tail truncation being landed
        (it is): 119 preserves the tail, this ticket stops the caller evicting the middle, and
        neither is sufficient alone."""
        import json, os, subprocess, sys, tempfile
        home = tempfile.mkdtemp(prefix="chv2-162-home-")
        project = tempfile.mkdtemp(prefix="chv2-162-proj-")
        os.mkdir(os.path.join(project, ".foreman"))
        transcript = os.path.join(project, "t.jsonl")
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "cwd": project}) + chr(10))
        payload = {"session_id": "chv2-162-ledger", "cwd": project,
                   "transcript_path": transcript, "tool_name": "Agent",
                   "tool_input": {"subagent_type": "A" * 600}}
        hooks = str(Path(__file__).resolve().parent)
        env = dict(os.environ, HOME=home, CHV2_LEDGER_ORIGIN="test", PYTHONPATH=hooks)
        subprocess.run([sys.executable, os.path.join(hooks, "agent_dispatch_gate.py")],
                       input=json.dumps(payload), capture_output=True, text=True, env=env,
                       timeout=120)
        ledger = os.path.join(home, ".claude", "telemetry", "verdicts.jsonl")
        if not os.path.isfile(ledger):
            self.skipTest("no verdict row was written; the ledger path is not exercised here")
        with open(ledger, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        stored = rows[-1].get("reason") or ""
        self.assertIn("reissue this exact call", stored,
                      "remedy (1) did not survive into the durable record")
        self.assertIn("ListAgents()", stored,
                      "remedy (2) did not survive into the durable record")

    def test_both_remedies_survive_with_a_realistic_prompt_present(self):
        """CHV2-165. CHV2-162's own coupling test above fires with NO prompt/description field
        at all -- exactly the one case where the coupling this ticket found does not bite. A
        second, independently-varying field (example_task_snippet, built from the caller's own
        prompt/description) competed for the same fixed verdict_ledger head/tail budget and was
        never varied in CHV2-162's derivation. Measured before this fix: with subagent_type held
        at an ORDINARY, real length (no attack at all) and any real prompt present (as little as
        10 characters), remedy (1) was already evicted from the durable ledger row.

        Same real-subprocess-plus-real-ledger-row methodology as
        test_both_remedies_survive_into_the_LEDGER_ROW_not_just_the_message above, parametrized
        over subagent_type x prompt combinations spanning CHV2-165's own reported table:
        ordinary and cap-boundary subagent_type, no/short/long prompt."""
        import json, os, subprocess, sys, tempfile
        hooks = str(Path(__file__).resolve().parent)
        gate = os.path.join(hooks, "agent_dispatch_gate.py")

        cases = [
            ("ordinary-14", "clint-eastwood", None),
            ("ordinary-14", "clint-eastwood", "x" * 10),
            ("ordinary-14", "clint-eastwood", "x" * 60),
            ("cap-32-max", "x" * 32, "x" * 10),
            ("cap-32-max", "x" * 32, "x" * 60),
        ]
        for label, subagent_type, prompt in cases:
            with self.subTest(subagent_type=label, prompt_len=(len(prompt) if prompt else 0)):
                home = tempfile.mkdtemp(prefix="chv2-165-home-")
                project = tempfile.mkdtemp(prefix="chv2-165-proj-")
                os.mkdir(os.path.join(project, ".foreman"))
                transcript = os.path.join(project, "t.jsonl")
                with open(transcript, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps({"type": "user", "cwd": project}) + chr(10))
                tool_input = {"subagent_type": subagent_type}
                if prompt:
                    tool_input["prompt"] = prompt
                payload = {"session_id": "chv2-165-ledger", "cwd": project,
                           "transcript_path": transcript, "tool_name": "Agent",
                           "tool_input": tool_input}
                env = dict(os.environ, HOME=home, CHV2_LEDGER_ORIGIN="test", PYTHONPATH=hooks)
                subprocess.run([sys.executable, gate], input=json.dumps(payload),
                               capture_output=True, text=True, env=env, timeout=120)
                ledger = os.path.join(home, ".claude", "telemetry", "verdicts.jsonl")
                if not os.path.isfile(ledger):
                    self.skipTest("no verdict row was written; the ledger path is not "
                                  "exercised here")
                with open(ledger, encoding="utf-8") as fh:
                    rows = [json.loads(line) for line in fh if line.strip()]
                stored = rows[-1].get("reason") or ""
                self.assertIn("reissue this exact call", stored,
                              f"remedy (1) did not survive into the durable record for "
                              f"subagent_type={label!r} prompt_len="
                              f"{len(prompt) if prompt else 0}")
                self.assertIn("ListAgents()", stored,
                              f"remedy (2) did not survive into the durable record for "
                              f"subagent_type={label!r} prompt_len="
                              f"{len(prompt) if prompt else 0}")

    def test_message_length_is_bounded_by_the_gate_not_the_caller(self):
        """A caller must not be able to grow the message without limit."""
        short, _ = self._run("A" * 600)
        long, _ = self._run("A" * 10000)
        self.assertLess(abs(len(long) - len(short)), 40,
                        f"message grew from {len(short)} to {len(long)}: the caller still "
                        f"controls its length, which is the defect this ticket is about")

    def test_the_audit_record_keeps_the_untruncated_value(self):
        """Only the MESSAGE is bounded. The audit plane is a structured field a later
        investigation reads, so capping it would fix the message by damaging the evidence.
        Asserted from the real call the gate makes, not from its source text."""
        oversized = "A" * 600
        _, audit = self._run(oversized)
        recorded = audit.call_args[0][1]
        self.assertEqual(recorded["subagent_type"], oversized,
                         "the audit record was collaterally truncated: the message fix should "
                         "not cost the evidence")

class UnparseableStdinRecordsErrorNotSilentTests(unittest.TestCase):
    """CHV2-120 (Iris [b16c63]). hc.read_input() returns {} on unparseable stdin -- a valid
    dict -- so the entrypoint's isinstance(data, dict) check never catches it, and the pre-fix
    code ran main({}) against an empty payload and recorded the resulting silence as verdict=
    "silent", the ledger's own "looked and correctly found nothing" cell. A machinery failure
    read as a clean result, with session_id lost and no audit-plane record -- the exact
    divergence from hc.run_body()'s own dedicated branch for this input that this ticket names.

    Distinct from test_non_dict_payload_denies_not_crashes above: that fixture is a JSON array,
    which correctly raises inside main()'s own isinstance check. This one is the harder case --
    read_input() itself returns {}, so nothing downstream ever sees a wrong-shaped value to
    reject."""

    def test_main_is_never_called_against_a_salvaged_empty_payload(self):
        """The structural assertion: an unparseable payload must not reach main() as if it were
        a real, empty-but-valid one. Pre-fix, this is exactly what happened."""
        with mock.patch.object(adg.hc, "read_input", return_value={}), \
             mock.patch.object(adg.hc, "INPUT_UNPARSEABLE", True), \
             mock.patch.object(adg.hc, "RAW_INPUT_ON_PARSE_FAILURE",
                               '{"session_id": "s1", "tool_name": "Agent", "tool_input": {'), \
             mock.patch.object(adg, "main") as mock_main, \
             mock.patch.object(adg, "verdict_ledger"), \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit) as ctx:
            adg._fail_closed_entrypoint()
        self.assertEqual(ctx.exception.code, 0)
        mock_main.assert_not_called()

    def test_records_error_verdict_not_silent(self):
        with mock.patch.object(adg.hc, "read_input", return_value={}), \
             mock.patch.object(adg.hc, "INPUT_UNPARSEABLE", True), \
             mock.patch.object(adg.hc, "RAW_INPUT_ON_PARSE_FAILURE",
                               '{"session_id": "s1", "tool_name": "Agent", "tool_input": {'), \
             mock.patch.object(adg, "main"), \
             mock.patch.object(adg, "verdict_ledger") as mock_ledger, \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit):
            adg._fail_closed_entrypoint()
        mock_ledger.record.assert_called_once()
        args, kwargs = mock_ledger.record.call_args
        self.assertEqual(args[1], "error")
        self.assertEqual(kwargs.get("kind"), "unparseable-stdin-salvaged")

    def test_salvages_session_id_from_the_raw_truncated_bytes(self):
        """The session_id must survive even though the payload never parsed -- otherwise the
        one durable record of this failure cannot be attributed to a session at all."""
        with mock.patch.object(adg.hc, "read_input", return_value={}), \
             mock.patch.object(adg.hc, "INPUT_UNPARSEABLE", True), \
             mock.patch.object(adg.hc, "RAW_INPUT_ON_PARSE_FAILURE",
                               '{"session_id": "iris-i1-p6", "tool_name": "Agent", '
                               '"tool_input": {'), \
             mock.patch.object(adg, "main"), \
             mock.patch.object(adg, "verdict_ledger") as mock_ledger, \
             mock.patch.object(adg, "audit_lib") as mock_audit_lib, \
             self.assertRaises(SystemExit):
            adg._fail_closed_entrypoint()
        salvaged_data = mock_ledger.record.call_args[0][0]
        self.assertEqual(salvaged_data.get("session_id"), "iris-i1-p6")
        self.assertEqual(mock_audit_lib.audit_append.call_args.kwargs.get("session_id"),
                         "iris-i1-p6")

    def test_no_salvageable_fields_records_plain_unparseable_stdin_kind(self):
        with mock.patch.object(adg.hc, "read_input", return_value={}), \
             mock.patch.object(adg.hc, "INPUT_UNPARSEABLE", True), \
             mock.patch.object(adg.hc, "RAW_INPUT_ON_PARSE_FAILURE", 'not even close to json'), \
             mock.patch.object(adg, "main"), \
             mock.patch.object(adg, "verdict_ledger") as mock_ledger, \
             mock.patch.object(adg, "audit_lib"), \
             self.assertRaises(SystemExit):
            adg._fail_closed_entrypoint()
        self.assertEqual(mock_ledger.record.call_args.kwargs.get("kind"), "unparseable-stdin")

    def test_real_subprocess_records_error_not_silent_in_the_real_ledger_row(self):
        """The finding itself, asserted at the layer the claim is actually about -- a real
        subprocess under a throwaway HOME, reading the real verdicts.jsonl row, using Iris's
        exact truncated payload from her filed repro. Matches the convention
        test_both_remedies_survive_into_the_LEDGER_ROW_not_just_the_message already establishes
        in this file: a mock proves the control flow, a real subprocess proves the record."""
        import json, os, subprocess, sys, tempfile
        home = tempfile.mkdtemp(prefix="chv2-120-home-")
        truncated = '{"session_id": "iris-i1-p6", "tool_name": "Agent", "tool_input": {'
        gate = str(Path(__file__).resolve().parent / "agent_dispatch_gate.py")
        env = dict(os.environ, HOME=home, CHV2_LEDGER_ORIGIN="test")
        subprocess.run([sys.executable, gate], input=truncated, capture_output=True, text=True,
                       env=env, timeout=60)
        ledger = os.path.join(home, ".claude", "telemetry", "verdicts.jsonl")
        if not os.path.isfile(ledger):
            self.skipTest("no verdict row was written; the ledger path is not exercised here")
        with open(ledger, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        row = rows[-1]
        self.assertEqual(row.get("verdict"), "error",
                         f"a truncated/unparseable payload must record verdict=error, a "
                         f"machinery failure, never verdict=silent (correct silence) -- got "
                         f"{row!r}")
        self.assertIn("unparseable-stdin", row.get("kind") or "")

if __name__ == "__main__":
    unittest.main()
