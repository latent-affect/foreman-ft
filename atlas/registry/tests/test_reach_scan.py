"""ATLASSN-135's own contract tests, plus parity checks against architecture_parse.py
(ATLASSN-127) on the SAME real, frozen ARCHITECTURE.md -- proving the two independently
derived parsers agree on real data, which is the actual point of building a second one.

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_reach_scan -v

The git/build_repo/block fixtures below are DUPLICATED from test_architecture_parse.py rather
than imported, and that duplication is deliberate: C9's independence property covers the tests
too, and a shared fixture helper is the quiet way two "independent" derivations start agreeing
because they were handed the same bytes by the same code.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from atlas.registry import architecture_parse
from atlas.registry import reach_scan as rs

REPO_ROOT = Path(__file__).resolve().parents[3]
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


MINIMAL_DOC = (
    block("components", 'core: ["src/core/**"]')
    + block("interfaces", 'a_to_b: {"producer": "a", "consumer": "b", "trust": "internal"}')
)


class TestParityAgainstArchitectureParse(unittest.TestCase):
    """The actual point of a second derivation: agreement on real, frozen data. Reads the real
    repo's ARCHITECTURE.md via each module's OWN independent frozen-read path (not sharing the
    text between them, so this also exercises reach_scan's own git read, not just its parser)."""

    def test_components_agree_on_real_repo(self):
        text_a = architecture_parse.read_frozen_architecture(REPO_ROOT)
        text_b = rs.read_frozen_text(REPO_ROOT)
        self.assertEqual(
            architecture_parse.parse_components(text_a), rs.scan_components(text_b))

    def test_interfaces_agree_on_real_repo(self):
        text_a = architecture_parse.read_frozen_architecture(REPO_ROOT)
        text_b = rs.read_frozen_text(REPO_ROOT)
        self.assertEqual(
            architecture_parse.parse_interfaces(text_a), rs.scan_interfaces(text_b))

    def test_reach_agrees_on_real_repo(self):
        text_a = architecture_parse.read_frozen_architecture(REPO_ROOT)
        text_b = rs.read_frozen_text(REPO_ROOT)
        self.assertEqual(architecture_parse.parse_reach(text_a), rs.scan_reach(text_b))

    def test_scan_frozen_repo_matches_parse_frozen_repo_shape_and_content(self):
        a = architecture_parse.parse_frozen_repo(REPO_ROOT)
        b = rs.scan_frozen_repo(REPO_ROOT)
        self.assertEqual(set(a.keys()), set(b.keys()))
        self.assertEqual(a["components"], b["components"])
        self.assertEqual(a["interfaces"], b["interfaces"])
        self.assertEqual(a["reach"], b["reach"])
        self.assertEqual(a["reach_declared"], b["reach_declared"])


class TestDeliberateDisagreement(unittest.TestCase):
    """34.4's own named requirement: prove the two derivations DISAGREE when one side's INPUT
    is corrupted, and that the disagreement localizes to the corrupted side -- not just that
    they agree on clean data (TestParityAgainstArchitectureParse covers that)."""

    def test_agreement_on_clean_synthetic_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, MINIMAL_DOC)
            text_a = architecture_parse.read_frozen_architecture(repo)
            text_b = rs.read_frozen_text(repo)
            self.assertEqual(
                architecture_parse.parse_components(text_a), rs.scan_components(text_b))
            self.assertEqual(
                architecture_parse.parse_interfaces(text_a), rs.scan_interfaces(text_b))

    def test_corrupting_the_shared_input_denies_both_localizably(self):
        """A corruption in the INPUT TEXT itself (the realistic shape: a bad commit, a hand
        edit) must deny on BOTH derivations independently -- proving neither one silently
        tolerates malformed input the other would catch. If only one parser denied a
        genuinely malformed document, that parser would be the weaker of the two, which is
        exactly what this test is positioned to catch."""
        corrupted = block("components", 'core: not-json-at-all')
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, corrupted)
            text_a = architecture_parse.read_frozen_architecture(repo)
            text_b = rs.read_frozen_text(repo)
            with self.assertRaises(architecture_parse.FrozenArchitectureError):
                architecture_parse.parse_components(text_a)
            with self.assertRaises(rs.ReachScanError):
                rs.scan_components(text_b)

    def test_reach_scan_alone_denies_a_document_architecture_parse_never_sees(self):
        """The independence property from THIS module's own side: a malformed reach entry
        denies here regardless of what architecture_parse.py would do with the same bytes --
        this module does not defer to or need the other one to make its own correct call."""
        corrupted = block("reach", 'bad_edge: {"repo": "x", "direction": missing_quotes}')
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, corrupted)
            text = rs.read_frozen_text(repo)
            with self.assertRaises(rs.BlockCorrupt):
                rs.scan_reach(text)


class TestOwnContract(unittest.TestCase):
    """This module's own correctness, independent of any comparison to architecture_parse.py."""

    def test_every_block_of_a_kind_counts_not_just_the_first(self):
        doc = (block("components", 'a: ["x/**"]') + "\nsome prose in between\n\n"
               + block("components", 'b: ["y/**"]'))
        self.assertEqual(rs.scan_components(doc), {"a": ["x/**"], "b": ["y/**"]})

    def test_missing_block_raises_block_absent(self):
        with self.assertRaises(rs.BlockAbsent):
            rs.scan_reach(block("components", 'a: ["x/**"]'))

    def test_duplicate_name_across_blocks_raises(self):
        doc = block("components", 'a: ["x/**"]') + block("components", 'a: ["y/**"]')
        with self.assertRaises(rs.BlockCorrupt):
            rs.scan_components(doc)

    def test_wrong_shape_value_raises(self):
        # components wants an array; this is an object
        doc = block("components", 'a: {"not": "an-array"}')
        with self.assertRaises(rs.BlockCorrupt):
            rs.scan_components(doc)

    def test_unquoted_bare_yaml_key_form_rejected(self):
        doc = block("interfaces", 'a_to_b: {producer: a, consumer: b}')
        with self.assertRaises(rs.BlockCorrupt):
            rs.scan_interfaces(doc)

    def test_comment_and_blank_lines_inside_block_are_skipped(self):
        doc = block("components", '# a comment', '', 'a: ["x/**"]')
        self.assertEqual(rs.scan_components(doc), {"a": ["x/**"]})

    def test_unknown_block_kind_raises_value_error(self):
        with self.assertRaises(ValueError):
            rs.block_pattern("not-a-real-kind")


class TestFrozenRead(unittest.TestCase):
    """This module's own independently-written git-read path (34.4: committed HEAD, freeze
    marker required, never the working tree)."""

    def test_missing_freeze_marker_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, MINIMAL_DOC, freeze_marker=False)
            with self.assertRaises(rs.FreezeMarkerAbsent):
                rs.read_frozen_text(repo)

    def test_working_tree_edit_is_ignored_reads_committed_head_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, MINIMAL_DOC)
            (repo / "ARCHITECTURE.md").write_text("uncommitted garbage", encoding="utf-8")
            text = rs.read_frozen_text(repo)
            self.assertEqual(text, MINIMAL_DOC)

    def test_reads_correctly_after_a_real_commit_updates_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_repo(tmp, MINIMAL_DOC)
            updated = block("components", 'core: ["src/core/**"]', 'extra: ["src/extra/**"]')
            (repo / "ARCHITECTURE.md").write_text(updated, encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "--quiet", "-m", "update", identity=True)
            self.assertEqual(rs.scan_components(rs.read_frozen_text(repo)),
                             {"core": ["src/core/**"], "extra": ["src/extra/**"]})

    def test_no_git_repo_at_all_raises_freeze_marker_absent_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(rs.FreezeMarkerAbsent):
                rs.read_frozen_text(tmp)


if __name__ == "__main__":
    unittest.main()
