"""ATLASSN-152 -- D1g, EDGE_ID FORMAT CONTRACT, ratified against ATLASSN-131's frozen criteria
2026-09-13. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_write_path_edge_id -v

Three controls, per the ratified verification text: a positive (a real declared edge_id,
independently re-derived over a live git-worktree fixture, ACCEPTED), and two negatives, both
REJECTED with edge-id-undeclared -- membership drift (a well-formed quad naming a combination
genuinely absent from the declared reach) and format drift (a differently-shaped string a
canonicalizer would never produce). Format drift is the control this residual exists for: a
membership-only check could pass a wrong-shaped id, and membership-against-the-canonicalized-set
is what actually closes that gap (resolve_edge_id's own docstring explains why no separate shape
check is needed).
"""
import tempfile
import unittest
from pathlib import Path

from atlas.registry import architecture_parse, write_path as wp
from atlas.registry.tests.test_architecture_parse import block, build_repo


class ResolveEdgeIdTests(unittest.TestCase):
    """Direct unit tests against resolve_edge_id(), isolated from D1a-D1f's own dependencies --
    D1g is independent of the other clauses' resolved values, so a unit test is the focused
    battery and test_write_assertion_rejects_an_undeclared_edge_id below is the end-to-end proof
    that it is actually wired into the write path."""

    def test_a_real_declared_edge_id_is_accepted(self):
        """Positive control: never a string the test invents. Builds a real git repo, writes a
        real reach block, reads it back through parse_frozen_repo (the SAME read clause (c)
        would use), and independently re-derives the expected id via edge_canonical_id -- the
        production function, over production-shaped parsed output, not a hand-typed match."""
        with tempfile.TemporaryDirectory(prefix="d1g-positive-") as tmp:
            architecture_text = (
                block("components", 'core: ["src/**"]')
                + block("interfaces", 'a_to_b: {"producer": "a", "consumer": "b", '
                                      '"trust": "internal"}')
                + block("reach", 'core_registers: {"repo": "atlas-sonnet", '
                                 '"path": "src/", "direction": "registers"}')
            )
            repo = build_repo(tmp, architecture_text)
            parsed = architecture_parse.parse_frozen_repo(repo)
            self.assertTrue(parsed["reach_declared"])
            real_edge_id = architecture_parse.edge_canonical_id(
                "core_registers", parsed["reach"]["core_registers"])

            # No exception is the assertion: resolve_edge_id raises on rejection and returns
            # None on acceptance, so simply calling it and reaching the next line IS the proof.
            wp.resolve_edge_id(real_edge_id, parsed["reach"])

    def test_membership_drift_is_rejected(self):
        """Negative control 1: a well-formed quad naming a repo/path/direction/name combination
        genuinely absent from the declared reach."""
        reach = {"core_registers": {"repo": "atlas-sonnet", "path": "src/",
                                    "direction": "registers"}}
        undeclared = "atlas-sonnet:src/:registers:a_completely_different_edge_name"
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_edge_id(undeclared, reach)
        self.assertEqual(ctx.exception.reason, "edge-id-undeclared")
        self.assertEqual(ctx.exception.detail, undeclared)

    def test_format_drift_is_rejected(self):
        """Negative control 2, the one this residual exists for: a differently-SHAPED string a
        canonicalizer would never produce -- the pre-QUAD triple (repo:path:direction, no name)
        -- must also be rejected. A membership-only check that compared against something
        looser than the real canonical set could pass this; membership against
        edge_canonical_id's own QUAD output cannot, because a 3-field string can never equal a
        4-field colon-joined one."""
        reach = {"core_registers": {"repo": "atlas-sonnet", "path": "src/",
                                    "direction": "registers"}}
        pre_quad_triple = "atlas-sonnet:src/:registers"
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_edge_id(pre_quad_triple, reach)
        self.assertEqual(ctx.exception.reason, "edge-id-undeclared")

        # A second format-drift shape: an ad hoc label with no structure at all -- the kind of
        # arbitrary string every write_assertion() test fixture used before this ticket.
        ad_hoc_label = "registry_self"
        with self.assertRaises(wp.AssertionRejected) as ctx2:
            wp.resolve_edge_id(ad_hoc_label, reach)
        self.assertEqual(ctx2.exception.reason, "edge-id-undeclared")

    def test_a_reach_edge_missing_a_required_field_is_excluded_not_a_crash(self):
        """A malformed reach entry (missing a field edge_canonical_id needs) must not make
        resolve_edge_id raise the wrong exception type -- it is excluded from the declared set
        (fail-closed: excluded means any edge_id that would have matched it is rejected, never
        silently admitted) rather than crashing with a bare KeyError-shaped error."""
        reach = {"corrupt": {"repo": "atlas-sonnet", "direction": "registers"}}  # no "path"
        with self.assertRaises(wp.AssertionRejected) as ctx:
            wp.resolve_edge_id("atlas-sonnet:anything:registers:corrupt", reach)
        self.assertEqual(ctx.exception.reason, "edge-id-undeclared")


class WriteAssertionEndToEndTests(unittest.TestCase):
    """D1g wired into the real write path, not just the standalone function -- the whole point
    per this ticket's own framing is that a real write does not land, not merely that a helper
    raises."""

    def test_write_assertion_rejects_an_undeclared_edge_id_before_any_row_lands(self):
        import sqlite3

        from atlas.registry.tests import write_path_fixtures as fx

        with tempfile.TemporaryDirectory(prefix="d1g-e2e-") as tmp:
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
            components = architecture_parse.parse_components(
                architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT))

            with self.assertRaises(wp.AssertionRejected) as ctx:
                wp.write_assertion(
                    store_path=store_path, audit_log_path=audit_path,
                    dispatch_record_id="d-1",
                    verifier_session_id=fx.REAL_TRANSCRIPT_SESSION,
                    edge_id="claude-hooks-v2:hooks/:registers:an_edge_nobody_declared",
                    edge_repo="atlas-sonnet", edge_component="registry",
                    project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
                    assertion_class="structural",
                    evidence_tool_use_id=fx.REAL_ANCHOR_TOOL_USE_ID,
                    evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
                    components=components,
                    reach=fx.reach_for(fx.DECLARED_EDGE_GATE_CLIENT),
                    config_path=config_path, records_dir=records_dir,
                    sessions_stream_path=sessions_path)
            self.assertEqual(ctx.exception.reason, "edge-id-undeclared")

            connection = sqlite3.connect(store_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0], 0)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM spent_ref").fetchone()[0], 0,
                    "an edge-id-undeclared rejection must not burn the evidence reference")
            finally:
                connection.close()
            self.assertFalse(audit_path.exists(),
                             "a refused write appends nothing to the trail (C13)")


if __name__ == "__main__":
    unittest.main()
