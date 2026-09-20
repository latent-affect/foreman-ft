"""ATLASSN-121's reconciliation battery (GOALS.json C9): the corrupt-A/corrupt-B probes and
the import-graph independence check C9's verification names explicitly, plus the honest-seam
property for the transcript cross-check.

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_reconciliation -v

The full C9 battery: N/N pass, delete-one, orphan, corrupt-A, corrupt-B, the import-graph
independence check, plus the two probes C9's 2026-09-12T17:05Z amendment added -- COLLISION
over the real §34.6 shape and MALFORMED-EDGE. The declared-vs-asserted layer joins on the
amended QUAD key `repo:path:direction:name`; its first draft joined on the triple and was held
rather than landed, because on this repo's own reach block the triple is not unique. See
FINDING-CANONICAL-EDGE-ID-COLLISION-ATLASSN-135-20260912.md.

The git/build_repo/block fixtures are DUPLICATED from the sibling test modules rather than
imported, deliberately -- C9's independence property covers the tests too, and a shared
fixture helper is the quiet way two independent derivations start agreeing because one
function handed them both the same bytes.
"""
import ast
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from atlas.registry import architecture_parse
from atlas.registry import reach_scan
from atlas.registry import reconcile

GIT_IDENTITY = ["-c", "user.name=atlas-test", "-c", "user.email=atlas-test@invalid"]


def git(repo, *arguments, identity=False):
    command = ["git", "-C", str(repo)] + (GIT_IDENTITY if identity else []) + list(arguments)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"fixture git failed: {' '.join(command)}\n{result.stderr}")
    return result.stdout


def build_repo(directory, architecture_text):
    repo = Path(directory)
    git(repo, "init", "--quiet")
    (repo / "ARCHITECTURE.md").write_text(architecture_text, encoding="utf-8")
    marker = repo / ".foreman"
    marker.mkdir(exist_ok=True)
    (marker / "frozen.json").write_text(
        '{"edges": [], "frozen_at": "2026-01-01T00:00:00Z"}', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "fixture", identity=True)
    return repo


def block(kind, *lines):
    return "```yaml " + kind + "\n" + "\n".join(lines) + "\n```\n"


REPO_ROOT = Path(__file__).resolve().parents[3]

REACH_DOC = block(
    "reach",
    'edge_one: {"repo": "x", "path": "y/", "direction": "registers", "pinned_at": "not-yet-created"}',
    'edge_two: {"repo": "x", "path": "z/", "direction": "reads", "pinned_at": "sha256:aaa"}',
)

COLLISION_DOC = block(
    "reach",
    'registry_gate_client_wired: {"repo": "claude-hooks-v2", "path": "hooks/", "direction": "registers", "pinned_at": "not-yet-created"}',
    'registry_write_guard_wired: {"repo": "claude-hooks-v2", "path": "hooks/", "direction": "registers", "pinned_at": "not-yet-created"}',
)


class TestNOfNPass(unittest.TestCase):
    def test_agreement_and_no_gaps_when_everything_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_DOC)
            agreement = reconcile.reconcile_derivations(repo)
            self.assertTrue(agreement["agree"], agreement["disagreements"])
            declared = architecture_parse.parse_reach(
                architecture_parse.read_frozen_architecture(repo))
            result = reconcile.reconcile_declared_vs_asserted(
                declared, architecture_parse.edge_canonical_id, {"x:y/:registers:edge_one"})
            self.assertEqual(result["forward_gaps"], [])
            self.assertEqual(result["reverse_orphans"], [])


class TestDeleteOne(unittest.TestCase):
    def test_missing_assertion_is_exactly_one_named_forward_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_DOC)
            declared = architecture_parse.parse_reach(
                architecture_parse.read_frozen_architecture(repo))
            result = reconcile.reconcile_declared_vs_asserted(
                declared, architecture_parse.edge_canonical_id, set())
            self.assertEqual(result["forward_gaps"], ["x:y/:registers:edge_one"])
            self.assertEqual(result["reverse_orphans"], [])


class TestOrphan(unittest.TestCase):
    def test_unknown_asserted_edge_is_reverse_orphan_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_DOC)
            declared = architecture_parse.parse_reach(
                architecture_parse.read_frozen_architecture(repo))
            result = reconcile.reconcile_declared_vs_asserted(
                declared, architecture_parse.edge_canonical_id,
                {"x:y/:registers:edge_one", "phantom:repo:registers:phantom_edge"})
            self.assertEqual(result["forward_gaps"], [])
            self.assertEqual(result["reverse_orphans"], ["phantom:repo:registers:phantom_edge"])

    def test_orphan_and_gap_can_coexist_without_masking_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_DOC)
            declared = architecture_parse.parse_reach(
                architecture_parse.read_frozen_architecture(repo))
            result = reconcile.reconcile_declared_vs_asserted(
                declared, architecture_parse.edge_canonical_id,
                {"phantom:repo:registers:phantom_edge"})
            self.assertEqual(result["forward_gaps"], ["x:y/:registers:edge_one"])
            self.assertEqual(result["reverse_orphans"], ["phantom:repo:registers:phantom_edge"])


class TestCanonicalizationParity(unittest.TestCase):
    """The two independently-written edge_canonical_id functions must still agree on the
    real frozen reach block -- proving the "no shared helper" split didn't quietly introduce
    its own disagreement.

    Set equality is the right instrument for THIS property and the wrong one for uniqueness:
    two implementations that collapse identically would compare equal. Uniqueness is
    TestCollisionProbe's job, and the split is deliberate."""

    def test_both_canonicalizers_agree_on_every_real_registers_edge(self):
        text_a = architecture_parse.read_frozen_architecture(REPO_ROOT)
        text_b = reach_scan.read_frozen_text(REPO_ROOT)
        reach_a = architecture_parse.parse_reach(text_a)
        reach_b = reach_scan.scan_reach(text_b)
        ids_a = {architecture_parse.edge_canonical_id(n, e) for n, e in reach_a.items()
                 if e.get("direction") == "registers"}
        ids_b = {reach_scan.edge_canonical_id(n, e) for n, e in reach_b.items()
                 if e.get("direction") == "registers"}
        self.assertEqual(ids_a, ids_b)
        self.assertTrue(ids_a, "expected at least one real registers edge in the frozen doc")

    def test_real_registers_edges_keep_their_count_through_canonicalization(self):
        """The uniqueness half, on real data: §34.6 declares three `registers` edges and two
        of them share a triple, so a triple-keyed canonicalization yields two ids for three
        claims. This pins the real document rather than a fixture of it."""
        reach = architecture_parse.parse_reach(
            architecture_parse.read_frozen_architecture(REPO_ROOT))
        registers = {n: e for n, e in reach.items() if e.get("direction") == "registers"}
        quad_ids = {architecture_parse.edge_canonical_id(n, e) for n, e in registers.items()}
        triple_ids = {f"{e['repo']}:{e['path']}:{e['direction']}" for e in registers.values()}
        self.assertEqual(len(quad_ids), len(registers))
        self.assertLess(len(triple_ids), len(registers),
                        "the real document no longer contains a colliding triple; if that is "
                        "a deliberate reach-block change, this probe and C9's collision "
                        "rationale both need revisiting rather than relaxing")


class TestCollisionProbe(unittest.TestCase):
    """Real shape from section 34.6: registry_gate_client_wired and registry_write_guard_wired
    share an identical (repo, path, direction) triple and differ only by declaration name.
    Required probe, frozen alongside the quad-id ruling 2026-09-12 17:05Z -- must FAIL against
    any triple-keyed implementation: a triple join collapses both names to one dict key, so
    asserting only one would incorrectly read as satisfying both."""

    def test_two_edges_sharing_a_triple_are_tracked_as_two_distinct_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, COLLISION_DOC)
            declared = architecture_parse.parse_reach(
                architecture_parse.read_frozen_architecture(repo))
            self.assertEqual(len(declared), 2, "fixture must declare two distinct names")

            id_gate = architecture_parse.edge_canonical_id(
                "registry_gate_client_wired", declared["registry_gate_client_wired"])
            id_guard = architecture_parse.edge_canonical_id(
                "registry_write_guard_wired", declared["registry_write_guard_wired"])
            self.assertNotEqual(id_gate, id_guard,
                                "quad ids must differ by name even when the triple is identical")

            # Assert only the gate-client claim. A triple-keyed join would consider the
            # write-guard claim satisfied too (same triple -> same dict key already asserted);
            # the quad-keyed join must still report it as an open forward gap.
            result = reconcile.reconcile_declared_vs_asserted(
                declared, architecture_parse.edge_canonical_id, {id_gate})
            self.assertEqual(result["forward_gaps"], [id_guard])
            self.assertEqual(result["reverse_orphans"], [])


class TestMalformedEdgeProbe(unittest.TestCase):
    """A reach edge missing a field edge_canonical_id needs must refuse by name, not crash
    with a bare KeyError (found live, ATLASSN-135: the corrupt-A fixture had no `path` and
    the original implementation raised KeyError)."""

    def test_architecture_parse_side_raises_named_exception_not_keyerror(self):
        edge = {"repo": "x", "direction": "registers"}  # no "path"
        with self.assertRaises(architecture_parse.MalformedReachEdge):
            architecture_parse.edge_canonical_id("some_edge", edge)

    def test_reach_scan_side_raises_named_exception_not_keyerror(self):
        edge = {"repo": "x", "direction": "registers"}
        with self.assertRaises(reach_scan.MalformedReachEdge):
            reach_scan.edge_canonical_id("some_edge", edge)

    def test_named_error_is_catchable_as_the_modules_own_base(self):
        """Each module documents its base class as the one a caller catches to keep scanning
        and emit a finding. An exception outside that hierarchy would escape such a caller and
        crash it -- the same "a crash is not a refusal" failure, moved up one level."""
        edge = {"repo": "x", "direction": "registers"}
        with self.assertRaises(architecture_parse.FrozenArchitectureError):
            architecture_parse.edge_canonical_id("some_edge", edge)
        with self.assertRaises(reach_scan.ReachScanError):
            reach_scan.edge_canonical_id("some_edge", edge)


class TestCorruptDerivation(unittest.TestCase):
    """Corrupt ONE derivation's own output (never the input text, which both would then
    legitimately reject together) and require the reported disagreement's values to trace
    back to exactly the corrupted side -- proving the reconciler doesn't just say "they
    disagree," it says WHICH one and WHAT it said."""

    def test_corrupt_a_localizes_to_architecture_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_DOC)
            real_reach = reach_scan.scan_reach(reach_scan.read_frozen_text(repo))
            fabricated = {"edge_one": {"repo": "WRONG", "direction": "registers"}}
            with mock.patch.object(architecture_parse, "parse_reach", return_value=fabricated):
                result = reconcile.reconcile_derivations(repo)
            self.assertFalse(result["agree"])
            hit = next(d for d in result["disagreements"] if d["edge"] == "edge_one")
            self.assertEqual(hit["architecture_parse"], fabricated["edge_one"])
            self.assertEqual(hit["reach_scan"], real_reach["edge_one"])
            self.assertNotEqual(hit["architecture_parse"], hit["reach_scan"])

    def test_corrupt_b_localizes_to_reach_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_DOC)
            real_reach = architecture_parse.parse_reach(
                architecture_parse.read_frozen_architecture(repo))
            fabricated = {"edge_one": {"repo": "WRONG", "direction": "registers"}}
            with mock.patch.object(reach_scan, "scan_reach", return_value=fabricated):
                result = reconcile.reconcile_derivations(repo)
            self.assertFalse(result["agree"])
            hit = next(d for d in result["disagreements"] if d["edge"] == "edge_one")
            self.assertEqual(hit["reach_scan"], fabricated["edge_one"])
            self.assertEqual(hit["architecture_parse"], real_reach["edge_one"])
            self.assertNotEqual(hit["architecture_parse"], hit["reach_scan"])

    def test_corrupting_a_name_only_on_one_side_is_also_a_disagreement(self):
        """A name present on only one derivation's output must not silently vanish from the
        comparison (e.g. via a naive shared-keys-only intersection)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, REACH_DOC)
            fabricated_extra = dict(
                reach_scan.scan_reach(reach_scan.read_frozen_text(repo)),
                ghost_edge={"repo": "x", "direction": "registers"})
            with mock.patch.object(reach_scan, "scan_reach", return_value=fabricated_extra):
                result = reconcile.reconcile_derivations(repo)
            self.assertFalse(result["agree"])
            hit = next(d for d in result["disagreements"] if d["edge"] == "ghost_edge")
            self.assertIsNone(hit["architecture_parse"])
            self.assertEqual(hit["reach_scan"], fabricated_extra["ghost_edge"])


class TestImportGraphIndependence(unittest.TestCase):
    """C9's own named mechanical check: assert derivation A and derivation B share no
    import-level dependency on each other. Parses each module's real source with `ast` (same
    tool test_architecture_parse.py already uses for its own AST-based check) rather than
    trusting either module's own docstring claim."""

    def imported_module_names(self, module):
        """Every module name this source imports, BY EITHER FORM.

        The `from X import Y` branch must contribute `X.Y` and `Y`, not just `X`. Recording
        only `node.module` was the original shape of this helper and it made the whole check
        vacuous: `from atlas.registry import reach_scan` would record only "atlas.registry",
        so an assertion looking for "reach_scan" or "*.reach_scan" found nothing and passed.
        That is the exact import form this codebase uses everywhere, so the check would have
        certified two derivations that import each other. Caught by a positive-control mutant
        (a real forbidden import added to architecture_parse.py) that the original helper
        failed to detect -- see the note on test_helper_detects_a_real_import below.
        """
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=module.__file__)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
                names.update(alias.name for alias in node.names)
        return names

    def test_helper_detects_a_real_import(self):
        """The control for the three assertions below. Each of them is a NEGATIVE assertion,
        and a negative assertion over a helper that returns nothing passes for the wrong
        reason. This pins the helper against imports both modules genuinely have, so a
        broken helper fails here instead of silently certifying independence."""
        for module, expected in ((architecture_parse, {"json", "re", "subprocess"}),
                                 (reach_scan, {"json", "re", "subprocess"}),
                                 (reconcile, {"atlas.registry.architecture_parse",
                                              "atlas.registry.reach_scan"})):
            names = self.imported_module_names(module)
            self.assertTrue(expected <= names,
                            f"{module.__name__}: helper missed {sorted(expected - names)}")

    def test_architecture_parse_does_not_import_reach_scan(self):
        names = self.imported_module_names(architecture_parse)
        self.assertNotIn("reach_scan", names)
        self.assertFalse(any(n.endswith(".reach_scan") for n in names))

    def test_reach_scan_does_not_import_architecture_parse(self):
        names = self.imported_module_names(reach_scan)
        self.assertNotIn("architecture_parse", names)
        self.assertFalse(any(n.endswith(".architecture_parse") for n in names))

    def test_neither_derivation_imports_reconcile(self):
        """The reconciler depends on both derivations; neither derivation may depend back on
        the reconciler -- that would be a cycle hiding a shared-helper relationship one level
        removed."""
        for module in (architecture_parse, reach_scan):
            names = self.imported_module_names(module)
            self.assertNotIn("reconcile", names)
            self.assertFalse(any(n.endswith(".reconcile") for n in names))


class TestTranscriptCrossCheckSeam(unittest.TestCase):
    """The honest-seam property: this must FAIL LOUDLY, never silently pass or silently fall
    back to a live-file read or a direct warehouse-table query."""

    def test_seam_raises_named_exception_not_silently_passes(self):
        with self.assertRaises(reconcile.TranscriptCrossCheckUnavailable) as ctx:
            reconcile.cross_check_evidence_against_ingested_transcript("s-verifier", "tu-123")
        self.assertIn("not wired", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
