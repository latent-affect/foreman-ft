"""C3: clause (c), resolution-then-exact against the real session transcript, Write/Edit only.
Real anchor from OBSERVED-BEHAVIOUR-WRITE-PATH-ATLASSN-131-20260912.md.

COMPONENTS comes from the real frozen ARCHITECTURE.md through architecture_parse, not from a
hand-written dict: C3 requires the mapping to resolve through the SAME parser module C9's
structural derivation uses, and a literal dict would assert the opposite of that.
"""
import ast
import unittest
from pathlib import Path

from atlas.registry import architecture_parse, write_path as wp
from atlas.registry.tests import write_path_fixtures as fx

COMPONENTS = architecture_parse.parse_components(
    architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT))


class ComponentClauseTests(unittest.TestCase):
    def resolve(self, asserted_component):
        return wp.resolve_component_for_write_edit(
            fx.REAL_TRANSCRIPT_PATH, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT,
            COMPONENTS, asserted_component)

    def test_exact_resolved_match_accepted(self):
        self.assertEqual(self.resolve("registry"), "registry")

    def test_near_miss_battery_rejected(self):
        """'Registry', 'registry ', 'registryX' -- exact match only, no case-folding, no
        stripping, no substring test."""
        for near_miss in ("Registry", "registry ", "registryX"):
            with self.subTest(value=repr(near_miss)):
                with self.assertRaises(wp.AssertionRejected) as ctx:
                    self.resolve(near_miss)
                self.assertEqual(ctx.exception.reason, "component-mismatch")
                # Exact match on the whole detail: a substring check for 'registry ' can pass
                # by matching inside the OTHER operand's text.
                self.assertEqual(ctx.exception.detail,
                                 f"resolved 'registry', asserted {near_miss!r}")

    def test_resolution_probe_real_but_wrong_component_rejected(self):
        """RESOLUTION probe: the real target resolves to `registry`, not `warehouse` --
        rejects even though 'warehouse' is itself a real, declared component name, proving
        this compares RESOLVED output rather than checking the claim is merely plausible."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.resolve("warehouse")
        self.assertEqual(ctx.exception.reason, "component-mismatch")

    def test_unmapped_target_rejected_g3(self):
        """UNMAPPED-TARGET: a real act whose target maps to NO declared component in a
        deliberately narrowed map -- rejects by name, never falling back to the caller's
        claim. The narrowness of this map IS the test, so it stays a literal."""
        restricted = {"warehouse": ["atlas/warehouse/**"]}
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_component_for_write_edit(
                fx.REAL_TRANSCRIPT_PATH, fx.REAL_ANCHOR_TOOL_USE_ID, fx.REAL_PROJECT_ROOT,
                restricted, "registry")
        self.assertEqual(ctx.exception.reason, "evidence-target-unmapped")

    def test_target_outside_project_root_rejected(self):
        """Containment: the real anchor's target is genuinely under REAL_PROJECT_ROOT, so
        pointing project_root somewhere else must reject by name -- proving the check runs
        before relativizing, rather than this target happening to fail to map."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_component_for_write_edit(
                fx.REAL_TRANSCRIPT_PATH, fx.REAL_ANCHOR_TOOL_USE_ID, "/tmp/unrelated-repo-root",
                COMPONENTS, "registry")
        self.assertEqual(ctx.exception.reason, "evidence-target-outside-repo")

    def test_bash_resolution_refuses_rather_than_guessing(self):
        """The Bash seam must fail loudly while its target rule is an open architecture
        question -- see FINDING-BASH-TARGET-RESOLUTION-ATLASSN-131-20260912.md."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_component_for_bash("anything", "at", "all")
        self.assertEqual(ctx.exception.reason, "bash-component-resolution-not-wired")

    def test_parser_identity_uses_the_same_architecture_parse_module(self):
        """C3/C9's parser-identity requirement: write_path resolves components through the
        SAME architecture_parse module the reconciler uses, not a second copy."""
        source = Path(wp.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=wp.__file__)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.update(f"{node.module}.{a.name}" for a in node.names)
                imported.update(a.name for a in node.names)
        self.assertIn("architecture_parse", imported)
        self.assertIs(wp.architecture_parse, architecture_parse)

    def qualifying_real_act(self):
        """Pinned anchor first; on absence, a loud named failure -- NEVER a skip. A skipped
        anti-fixture probe silently re-permits seeded-fixture-only coverage, which is F5's
        exact failure mode and the reason C3 carries this requirement at all."""
        if Path(fx.REAL_TRANSCRIPT_PATH).is_file():
            return fx.REAL_TRANSCRIPT_PATH, fx.REAL_ANCHOR_TOOL_USE_ID, "registry"
        self.fail(
            f"anti-fixture anchor missing: {fx.REAL_TRANSCRIPT_PATH} no longer exists. This "
            f"probe requires a REAL historical transcript row (C3's non-negotiable "
            f"anti-fixture requirement) -- find a replacement qualifying act (a completed "
            f"Write/Edit PostToolUse row resolving to a declared component) and update "
            f"write_path_fixtures.py's anchor constants. Do not skip this test."
        )

    def test_anti_fixture_probe_real_row_end_to_end(self):
        """The non-negotiable requirement itself: at least one REAL historical transcript
        row, resolved through the actual production seam, not a seeded fixture."""
        transcript_path, tool_use_id, expected = self.qualifying_real_act()
        component = wp.resolve_component_for_write_edit(
            transcript_path, tool_use_id, fx.REAL_PROJECT_ROOT, COMPONENTS, expected)
        self.assertEqual(component, expected)

    def test_write_assertion_actually_enforces_clause_c_end_to_end(self):
        """THE REGRESSION GUARD FOR THE FAIL-OPEN.

        Every other probe in this module calls resolve_component_for_write_edit directly, so
        all of them pass even if write_assertion never calls it -- which is exactly the
        defect this file had in review: clause (c) implemented, and the write path not
        invoking it. Verified by mutation: with the call replaced by
        `resolved_component = edge_component`, the entire C1-C4 suite stayed green until this
        probe existed.

        The dispatch record deliberately scopes BOTH components, so clause (a) cannot be the
        thing that rejects. The only reachable rejection is clause (c)'s comparison, which
        means an accepted write here is proof the clause is not being enforced.
        """
        import tempfile
        with tempfile.TemporaryDirectory(prefix="c3-enforced-") as tmp:
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
                project="atlas-sonnet", repo_scope=["registry", "warehouse"],
                created_at="2025-12-01T00:00:00Z")
            config_path = fx.write_config_and_decision(root)
            store_path = root / "registry.db"
            fx.create_store(store_path)

            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.write_assertion(
                    store_path=store_path, audit_log_path=root / "audit.jsonl",
                    dispatch_record_id="d-1",
                    verifier_session_id=fx.REAL_TRANSCRIPT_SESSION,
                    edge_id="atlas-sonnet:atlas/warehouse/:registers:x",
                    edge_repo="atlas-sonnet",
                    # Declared warehouse; the real evidence act targets atlas/registry/.
                    edge_component="warehouse",
                    project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
                    assertion_class="structural",
                    evidence_tool_use_id=fx.REAL_ANCHOR_TOOL_USE_ID,
                    evidence_class="observed-probe",
                    transcript_path=fx.REAL_TRANSCRIPT_PATH, components=COMPONENTS,
                    reach=fx.reach_for("atlas-sonnet:atlas/warehouse/:registers:x"),
                    config_path=config_path, records_dir=records_dir,
                    sessions_stream_path=sessions_path)
            self.assertEqual(ctx.exception.reason, "component-mismatch")
            self.assertEqual(ctx.exception.detail,
                             "resolved 'registry', asserted 'warehouse'")

    def test_anti_fixture_anchor_is_genuinely_absolute_pathed(self):
        """What makes the real row worth having: production transcripts carry an ABSOLUTE
        file_path, and the first implementation handed that straight to component_of, which
        wants a project-relative one -- so it resolved to None and clause (c) could never
        accept anything. A seeded fixture with a relative path passes that broken code
        happily. This pins the property that made the real anchor necessary."""
        import json
        found = None
        for line in Path(fx.REAL_TRANSCRIPT_PATH).read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            for block in ((record.get("message") or {}).get("content") or []):
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("id") == fx.REAL_ANCHOR_TOOL_USE_ID):
                    found = (block.get("input") or {}).get("file_path")
        self.assertIsNotNone(found, "anchor act not present in the transcript")
        self.assertTrue(found.startswith("/"), f"expected an absolute path, got {found!r}")
        self.assertIsNone(architecture_parse.component_of(found, COMPONENTS),
                          "an absolute path must NOT map directly -- if it does, the "
                          "containment-then-relativize step has stopped being load-bearing")


if __name__ == "__main__":
    unittest.main()
