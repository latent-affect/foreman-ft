"""ATLASSN-131 items 1 and 3: clause (c) refuses a duplicated evidence id instead of picking one,
and the battery can actually SEE the difference.

Item 1 was the defect: `resolve_component_for_write_edit` kept the last matching tool_use block.
Item 3 was why nobody noticed: the anchor transcript carries zero duplicate ids, so every C2/C3
probe was blind to it. Both are closed here, and the second one is closed the only way it can be
-- every probe below also runs against `last_match_wins_mutant`, which must ACCEPT what production
REFUSES. A probe that both implementations pass is not evidence.

The duplicate is built by copying a REAL record of the REAL anchor transcript forward, which is
the mechanism section 34.1b names (resume/fork copying prior turns). See
write_path_fixtures.transcript_with_duplicated_real_record for why the four real duplicate ids in
the corpus cannot serve here.
"""
import tempfile
import unittest
from pathlib import Path

from atlas.registry import architecture_parse, write_path as wp
from atlas.registry.tests import last_match_wins_mutant as mutant
from atlas.registry.tests import write_path_fixtures as fx

COMPONENTS = architecture_parse.parse_components(
    architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT))

# A real path under a DIFFERENT declared component, for the disagreeing-copy case.
OTHER_COMPONENT_PATH = "/Users/m5/dev/atlas-sonnet/atlas/warehouse/ddl.py"


class AnchorIsUnambiguousControlTests(unittest.TestCase):
    """The control the whole module rests on: untouched, the anchor resolves. If this ever fails,
    every refusal below could be refusing for an unrelated reason."""

    def test_untouched_anchor_resolves(self):
        self.assertEqual(
            wp.resolve_component_for_write_edit(
                fx.REAL_TRANSCRIPT_PATH, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT,
                COMPONENTS, "registry"),
            "registry")

    def test_the_other_declared_component_path_really_is_another_component(self):
        """Pins OTHER_COMPONENT_PATH's meaning against the frozen parser rather than against my
        reading of the glob block -- otherwise the disagreeing-copy probe could silently become a
        same-component probe after an architecture edit."""
        rel = Path(OTHER_COMPONENT_PATH).relative_to(fx.REAL_PROJECT_ROOT).as_posix()
        resolved = architecture_parse.component_of(rel, COMPONENTS)
        self.assertIsNotNone(resolved)
        self.assertNotEqual(resolved, "registry")


class IdenticalCopyTests(unittest.TestCase):
    """Two blocks, one id, SAME target. This is the case that separates a count rule from a
    disagreeing-values rule, and the reason the count rule was chosen: the spend, the grade and
    the provenance all attach to an act, so two acts sharing an id are ambiguous even when they
    name one path."""

    def build(self, root):
        return fx.transcript_with_duplicated_real_record(
            Path(root) / "duplicated.jsonl", fx.REAL_ANCHOR_TOOL_USE_ID)

    def test_production_refuses(self):
        with tempfile.TemporaryDirectory(prefix="amb-same-") as tmp:
            path = self.build(tmp)
            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.resolve_component_for_write_edit(
                    path, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT, COMPONENTS,
                    "registry")
            self.assertEqual(ctx.exception.reason, "evidence-target-ambiguous")
            self.assertFalse(ctx.exception.retryable,
                             "ambiguity is a property of the evidence, not an outage -- a "
                             "retryable refusal would invite a retry loop that can never clear")

    def test_the_mutant_accepts_it_so_this_probe_discriminates(self):
        with tempfile.TemporaryDirectory(prefix="amb-same-mut-") as tmp:
            path = self.build(tmp)
            self.assertEqual(
                mutant.resolve_component_last_match_wins(
                    path, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT, COMPONENTS,
                    "registry"),
                "registry",
                "the pre-fix implementation must PASS this input; if it refuses too, the probe "
                "above proves nothing about ambiguity")


class DisagreeingCopyTests(unittest.TestCase):
    """Two blocks, one id, DIFFERENT targets in different components. Under last-match-wins the
    later block decides, so an attacker appending a block wins the resolution outright."""

    def build(self, root):
        return fx.transcript_with_duplicated_real_record(
            Path(root) / "duplicated.jsonl", fx.REAL_ANCHOR_TOOL_USE_ID,
            second_file_path=OTHER_COMPONENT_PATH)

    def test_production_refuses_as_ambiguous_not_as_mismatch(self):
        """The refusal name matters. `component-mismatch` would mean the path resolved and
        disagreed; `evidence-target-ambiguous` means it was never resolved at all. Landing on the
        first would be the fail-open shape -- it implies a single target was chosen."""
        with tempfile.TemporaryDirectory(prefix="amb-diff-") as tmp:
            path = self.build(tmp)
            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.resolve_component_for_write_edit(
                    path, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT, COMPONENTS,
                    "registry")
            self.assertEqual(ctx.exception.reason, "evidence-target-ambiguous")

    def test_the_mutant_resolves_to_the_appended_block(self):
        """Shows the exact capability the fix removes: the LAST block wins, so appending one
        rewrites what the evidence is taken to have touched."""
        with tempfile.TemporaryDirectory(prefix="amb-diff-mut-") as tmp:
            path = self.build(tmp)
            with self.assertRaises(wp.AssertionRejected) as ctx:
                mutant.resolve_component_last_match_wins(
                    path, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT, COMPONENTS,
                    "registry")
            self.assertEqual(ctx.exception.reason, "component-mismatch")
            self.assertIn("warehouse", str(ctx.exception),
                          "the mutant should have resolved to the APPENDED block's component")

    def test_the_mutant_accepts_an_assertion_naming_the_appended_component(self):
        """The fail-open direction, stated as a passing write rather than as a different refusal:
        under last-match-wins a caller who appends a block can get an accepted resolution for a
        component the original act never touched."""
        with tempfile.TemporaryDirectory(prefix="amb-diff-mut2-") as tmp:
            path = self.build(tmp)
            rel = Path(OTHER_COMPONENT_PATH).relative_to(fx.REAL_PROJECT_ROOT).as_posix()
            appended_component = architecture_parse.component_of(rel, COMPONENTS)
            self.assertEqual(
                mutant.resolve_component_last_match_wins(
                    path, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT, COMPONENTS,
                    appended_component),
                appended_component)


class NoTargetDuplicateTests(unittest.TestCase):
    """The four REAL duplicate ids in the corpus are WebSearch/TaskUpdate blocks with no
    file_path. They must refuse `evidence-target-unresolved`, NOT `evidence-target-ambiguous`:
    zero candidates and two candidates are different facts and the reasons must not blur.

    Run against the real file, so this also records the corpus state the fixture comment cites."""

    REAL_DUPLICATE_TRANSCRIPT = (
        "/Users/m5/.claude/projects/-Users-m5-dev-gif-smith/"
        "5c325c56-2cc4-4190-9aae-fdca99232a8e.jsonl")
    REAL_DUPLICATE_ID = "toolu_01RB49VyWsqWYmdHb3Q75ohE"

    def test_real_duplicate_without_file_path_refuses_unresolved(self):
        if not Path(self.REAL_DUPLICATE_TRANSCRIPT).is_file():
            self.skipTest("the real duplicate-carrying transcript is no longer on this machine")
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_component_for_write_edit(
                self.REAL_DUPLICATE_TRANSCRIPT, self.REAL_DUPLICATE_ID, fx.REAL_PROJECT_ROOT,
                COMPONENTS, "registry")
        self.assertEqual(ctx.exception.reason, "evidence-target-unresolved")


class EndToEndTests(unittest.TestCase):
    """The refusal has to survive the whole write path, not just the resolver: clause (c) runs
    first, so an ambiguous reference must never reach the transaction."""

    def test_write_assertion_refuses_and_writes_nothing(self):
        import sqlite3
        with tempfile.TemporaryDirectory(prefix="amb-e2e-") as tmp:
            root = Path(tmp)
            records_dir = root / "dispatch-records"
            records_dir.mkdir()
            sessions_path = root / "sessions.jsonl"
            fx.write_sessions_stream(
                sessions_path,
                [fx.session_line(fx.REAL_TRANSCRIPT_SESSION, "2026-01-01T00:00:00Z")])
            fx.seed_json_file(
                records_dir, "d-1", record_id="d-1", author_session_id="s-author",
                named_session_id=fx.REAL_TRANSCRIPT_SESSION, role="verifier",
                project="atlas-sonnet", repo_scope=["registry"],
                created_at="2025-12-01T00:00:00Z")
            config_path = fx.write_config_and_decision(root)
            store_path = root / "registry.db"
            fx.create_store(store_path)
            audit_path = root / "audit.jsonl"
            transcript = fx.transcript_with_duplicated_real_record(
                root / "duplicated.jsonl", fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)

            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.write_assertion(
                    store_path=store_path, audit_log_path=audit_path,
                    dispatch_record_id="d-1",
                    verifier_session_id=fx.REAL_TRANSCRIPT_SESSION,
                    edge_id="claude-hooks-v2:hooks/:registers:registry_gate_client_wired",
                    edge_repo="atlas-sonnet", edge_component="registry",
                    project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
                    assertion_class="structural",
                    evidence_tool_use_id=fx.REAL_ANCHOR_SECOND_TOOL_USE_ID,
                    evidence_class="observed-probe", transcript_path=transcript,
                    components=COMPONENTS, reach=fx.reach_for(fx.DECLARED_EDGE_GATE_CLIENT),
                    config_path=config_path,
                    records_dir=records_dir, sessions_stream_path=sessions_path)
            self.assertEqual(ctx.exception.reason, "evidence-target-ambiguous")

            self.assertFalse(audit_path.exists(),
                             "a rejected write must append nothing to the trail (C13)")
            connection = sqlite3.connect(store_path)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM spent_ref").fetchone()[0], 0,
                "an ambiguous reference must not be burned")
            connection.close()


if __name__ == "__main__":
    unittest.main()
