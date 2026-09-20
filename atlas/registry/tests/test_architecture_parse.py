"""ATLASSN-127's own contract tests. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_architecture_parse -v

This module exists because of the close condition ratified on the ticket (comment
2026-09-12T13:59:11Z): ATLASSN-127 owns no frozen criterion in GOALS.json, so without its own
tests it would close on assertion alone while blocking the very consumers whose batteries would
otherwise have proved it.

Every git test builds a REAL repository and runs real git against it. The behaviour under test
in C3 is the git read itself -- "HEAD, not the working tree" -- and a mocked git would only
assert that the test's own stub returned what the test told it to, which is the shape of
non-discriminating control this whole component exists to remove.
"""

import ast
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from atlas.registry import architecture_parse

REPO_ROOT = Path(__file__).resolve().parents[3]

# A pseudonymous identity for fixture repos, never the machine's git config: these commits are
# throwaway, but pinning an explicit non-personal identity is the habit that matters.
GIT_IDENTITY = ["-c", "user.name=atlas-test", "-c", "user.email=atlas-test@invalid"]


def git(repo, *arguments, identity=False):
    command = ["git", "-C", str(repo)] + (GIT_IDENTITY if identity else []) + list(arguments)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"fixture git failed: {' '.join(command)}\n{result.stderr}")
    return result.stdout


def build_repo(directory, architecture_text, freeze_marker=True):
    repo = Path(directory)
    git(repo, "init", "--quiet")
    (repo / "ARCHITECTURE.md").write_text(architecture_text, encoding="utf-8")
    if freeze_marker:
        marker = repo / ".foreman"
        marker.mkdir(exist_ok=True)
        (marker / "frozen.json").write_text(
            '{"edges": [], "frozen_at": "2026-01-01T00:00:00Z"}', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "fixture", identity=True)
    return repo


def block(kind, *lines):
    return "```yaml " + kind + "\n" + "\n".join(lines) + "\n```\n"


def independent_yaml_load(text, kind):
    """ATLASSN-155's C1 amendment: an independent re-derivation of one fenced block kind via
    stdlib-adjacent yaml.safe_load, sharing no code with architecture_parse.iter_block_lines()/
    parse_block(). Finds EVERY fenced block of `kind`, not just the first -- a re-derivation
    that only looked at the first block would silently agree with a parser that stopped there
    too (FORE-260's own failure mode), which is exactly the regression C1 exists to catch.

    Every declared entry is `name: <JSON value>` on its own line, and JSON's array/object
    syntax with quoted keys is already valid YAML flow-collection syntax -- so yaml.safe_load
    over the concatenated block bodies parses to the same {name: value} shape
    architecture_parse's own regex+json.loads does, without reusing its logic. Returns None if
    no block of this kind exists at all (mirrors BlockMissing's condition without raising, since
    the caller decides what an absence means for its own assertion)."""
    pattern = re.compile(r"^```yaml " + re.escape(kind) + r"\s*$(.*?)^```\s*$",
                         re.MULTILINE | re.DOTALL)
    bodies = pattern.findall(text)
    if not bodies:
        return None
    loaded = yaml.safe_load("\n".join(bodies))
    return loaded or {}


MINIMAL_DOC = (
    block("components", 'core: ["src/core/**"]')
    + block("interfaces", 'a_to_b: {"producer": "a", "consumer": "b", "trust": "internal"}')
)


class TestBlockParsing(unittest.TestCase):
    """C1 -- structural parse of the three fenced blocks, every declared field preserved."""

    def setUp(self):
        self.text = architecture_parse.read_frozen_architecture(REPO_ROOT)

    def test_components_include_every_declared_name_across_both_blocks(self):
        components = architecture_parse.parse_components(self.text)
        independent = independent_yaml_load(self.text, "components")
        self.assertIsNotNone(independent, "independent yaml derivation found no components block")
        self.assertEqual(len(components), len(independent))
        self.assertEqual(components, independent)
        self.assertEqual(components["registry"], ["atlas/registry/**"])

    def test_interfaces_preserve_producer_consumer_and_trust(self):
        interfaces = architecture_parse.parse_interfaces(self.text)
        independent = independent_yaml_load(self.text, "interfaces")
        self.assertIsNotNone(independent, "independent yaml derivation found no interfaces block")
        self.assertEqual(len(interfaces), len(independent))
        self.assertEqual(interfaces, independent)
        self.assertEqual(
            interfaces["query_to_registry"],
            {"producer": "query", "consumer": "registry", "trust": "internal"})
        self.assertEqual(
            interfaces["dispatchrecord_to_registry"],
            {"producer": "dispatch_record_store", "consumer": "registry",
             "trust": "untrusted-input"},
        )
        for name, edge in interfaces.items():
            self.assertIn("trust", edge, f"{name} lost its trust field")

    def test_reach_preserves_direction_and_pin_including_registers_edges(self):
        reach = architecture_parse.parse_reach(self.text)
        independent = independent_yaml_load(self.text, "reach")
        self.assertIsNotNone(independent, "independent yaml derivation found no reach block")
        self.assertEqual(len(reach), len(independent))
        self.assertEqual(reach, independent)
        # The registers-edge count is derived from the SAME independent load rather than a
        # literal, for the identical reason the top-level counts moved off literals (ATLASSN-155):
        # a bare "3" here would re-break the next time a registers edge is added, exactly the
        # hand-edit-on-every-drift pattern this amendment exists to stop.
        registers = [name for name, edge in reach.items() if edge.get("direction") == "registers"]
        registers_independent = [name for name, edge in independent.items()
                                 if edge.get("direction") == "registers"]
        self.assertEqual(len(registers), len(registers_independent))
        self.assertEqual(reach["dispatch_records"]["pinned_at"], "not-yet-created")
        self.assertEqual(reach["verdict_ledger_resolution"]["direction"], "reads")
        self.assertEqual(reach["session_transcripts_resolution"],
                         {"repo": ".claude", "path": "projects/", "direction": "reads",
                          "pinned_at": "live-store-unpinned"})

    def test_absent_block_raises_rather_than_returning_an_empty_map(self):
        # C1's "never an empty mapping": a caller cannot distinguish a document that declares
        # nothing from one whose block this parser simply failed to find.
        with self.assertRaises(architecture_parse.BlockMissing):
            architecture_parse.parse_reach(MINIMAL_DOC)
        with self.assertRaises(architecture_parse.BlockMissing):
            architecture_parse.parse_components(block("interfaces", 'a_to_b: {"producer": "a"}'))

    def test_malformed_line_inside_a_block_raises(self):
        with self.assertRaises(architecture_parse.BlockMalformed):
            architecture_parse.parse_components(block("components", "core: not-a-json-array"))

    def test_broken_json_value_raises(self):
        with self.assertRaises(architecture_parse.BlockMalformed):
            architecture_parse.parse_components(block("components", 'core: ["src/core/**",]'))

    def test_bare_key_yaml_object_is_refused_not_skipped(self):
        # component_coupling.py documents that the bare-key form is "silently skipped by the
        # parser" elsewhere on this machine. A silently skipped interface is a declared edge
        # that stops being checked, so here it is an error.
        with self.assertRaises(architecture_parse.BlockMalformed):
            architecture_parse.parse_interfaces(
                block("interfaces", "a_to_b: {producer: a, consumer: b}"))

    def test_duplicate_declaration_raises_instead_of_letting_the_later_one_win(self):
        with self.assertRaises(architecture_parse.BlockMalformed):
            architecture_parse.parse_components(
                block("components", 'core: ["src/core/**"]')
                + block("components", 'core: ["src/other/**"]')
            )

    def test_comments_and_blank_lines_inside_a_block_are_not_malformed(self):
        components = architecture_parse.parse_components(
            block("components", "# a note", "", 'core: ["src/core/**"]')
        )
        self.assertEqual(components, {"core": ["src/core/**"]})


class TestComponentResolution(unittest.TestCase):
    """C2 -- prefix semantics, path-component-aware, longest match wins."""

    def setUp(self):
        self.components = architecture_parse.parse_components(
            architecture_parse.read_frozen_architecture(REPO_ROOT))

    def test_path_under_a_declared_directory_resolves_to_that_component(self):
        self.assertEqual(
            architecture_parse.component_of(
                "atlas/registry/tests/test_dispatch_record.py", self.components),
            "registry",
        )
        self.assertEqual(
            architecture_parse.component_of("dashboard/atlas_dashboard_data.py", self.components),
            "dashboard")

    def test_undeclared_path_resolves_to_none_not_to_a_nearest_guess(self):
        self.assertIsNone(architecture_parse.component_of("docs/PRD.md", self.components))
        self.assertIsNone(architecture_parse.component_of("ARCHITECTURE.md", self.components))

    def test_longest_declaration_wins_when_two_overlap(self):
        overlapping = {"outer": ["src/**"], "inner": ["src/deep/**"]}
        self.assertEqual(
            architecture_parse.component_of("src/deep/thing.py", overlapping), "inner")
        self.assertEqual(
            architecture_parse.component_of("src/shallow.py", overlapping), "outer")

    def test_prefix_collision_across_a_path_boundary_does_not_match(self):
        # G5, the design-scope falsification's own example: a component declared over "atlas"
        # must not claim a path under "atlas-sonnet". A raw str.startswith() -- what
        # component_coupling.component_of() compares today -- returns "core" for both of these.
        collide = {"core": ["atlas/**"]}
        self.assertEqual(
            architecture_parse.component_of("atlas/ingest/stream.py", collide), "core")
        self.assertIsNone(
            architecture_parse.component_of("atlas-sonnet/ingest/stream.py", collide))

    def test_single_file_declaration_matches_exactly_and_not_as_a_prefix(self):
        single = {"gate": ["hooks/architecture_gate.py"]}
        self.assertEqual(
            architecture_parse.component_of("hooks/architecture_gate.py", single), "gate")
        self.assertIsNone(
            architecture_parse.component_of("hooks/architecture_gate.py.bak", single))

    def test_leading_and_trailing_separators_are_normalized(self):
        self.assertEqual(
            architecture_parse.component_of("./atlas/registry/x.py", self.components), "registry")
        self.assertEqual(
            architecture_parse.component_of("/atlas/registry/x.py", self.components), "registry")

    def test_directory_itself_matches_its_own_declaration(self):
        self.assertEqual(
            architecture_parse.component_of("atlas/registry", self.components), "registry")


class TestFrozenSource(unittest.TestCase):
    """C3 -- committed HEAD with a freeze marker present, never the working tree."""

    def test_parse_reflects_head_not_the_dirty_working_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = build_repo(directory, block("components", 'committed: ["src/**"]'))
            (repo / "ARCHITECTURE.md").write_text(
                block("components", 'uncommitted: ["src/**"]'), encoding="utf-8")

            text = architecture_parse.read_frozen_architecture(repo)
            self.assertEqual(set(architecture_parse.parse_components(text)), {"committed"})

            # Positive control: the working tree really does differ, so the assertion above is
            # about HEAD resolution and not about the fixture having failed to write anything.
            working_tree = (repo / "ARCHITECTURE.md").read_text(encoding="utf-8")
            self.assertEqual(
                set(architecture_parse.parse_components(working_tree)), {"uncommitted"})

    def test_repo_without_a_freeze_marker_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = build_repo(directory, MINIMAL_DOC, freeze_marker=False)
            with self.assertRaises(architecture_parse.FreezeMarkerMissing):
                architecture_parse.read_frozen_architecture(repo)

    def test_repo_without_an_architecture_document_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "--quiet")
            marker = repo / ".foreman"
            marker.mkdir()
            (marker / "frozen.json").write_text("{}", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "--quiet", "-m", "fixture", identity=True)
            with self.assertRaises(architecture_parse.ArchitectureUnreadable):
                architecture_parse.read_frozen_architecture(repo)

    def test_absent_reach_block_is_reported_not_implied_to_be_zero_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = build_repo(directory, MINIMAL_DOC)
            parsed = architecture_parse.parse_frozen_repo(repo)
            self.assertEqual(parsed["reach"], {})
            self.assertFalse(parsed["reach_declared"])

    def test_parse_frozen_repo_on_this_repo_declares_reach(self):
        parsed = architecture_parse.parse_frozen_repo(REPO_ROOT)
        self.assertTrue(parsed["reach_declared"])
        # Nine since the §34 second amendment -- see the note in
        # test_reach_preserves_direction_and_pin_including_registers_edges.
        self.assertEqual(len(parsed["reach"]), 9)
        self.assertIn("session_transcripts_resolution", parsed["reach"])
        self.assertEqual(len(parsed["components"]), 8)


class TestParserUniqueness(unittest.TestCase):
    """C4 -- exactly one parser module serves this component.

    Until a consumer battery exists to assert import identity against this module, uniqueness is
    what can actually be checked: a second module under atlas/registry defining the same entry
    points would make C3, C8 and C9's import-identity assertions satisfiable by two different
    objects, which is the defect this ticket exists to prevent.
    """

    ENTRY_POINTS = {"parse_components", "parse_interfaces", "parse_reach"}

    def test_exactly_one_module_defines_the_parse_entry_points(self):
        definers = []
        for path in sorted((REPO_ROOT / "atlas" / "registry").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            defined = {node.name for node in tree.body
                       if isinstance(node, ast.FunctionDef)} & self.ENTRY_POINTS
            if defined == self.ENTRY_POINTS:
                definers.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(definers, ["atlas/registry/architecture_parse.py"])


if __name__ == "__main__":
    unittest.main()
