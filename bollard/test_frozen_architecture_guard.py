"""FORE-711. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest hooks.test_frozen_architecture_guard -v

No test file existed for frozen_architecture_guard.py before this proposal (confirmed by a real
file search this session) -- Iris's own ticket text notes only fast-tier adversarial-code-review
ever ran against it. This file covers the identity-check fix directly (the actual defect class)
plus the surrounding predicates it sits beside, real filesystem operations throughout (real
hardlinks via os.link, real symlinks via os.symlink, real files), not mocked.

CHV2-179 (2026-09-20, Nadia Osei's real STRIDE/validate finding, Build 8): FrozenArchitectureGuard
MainDispatchTests below covers main()'s own per-target routing directly -- the layer none of the
tests above it exercised, and the layer the real bypass actually lived in (project_root_for_
target()'s path-based resolution gating whether _is_architecture_doc()'s inode check is ever
reached at all). See main()'s own comment for the fix; see this class's own docstring for the two
concrete bypass shapes it reproduces as permanent regressions.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import frozen_architecture_guard as fag


class SameStatIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_a_hardlink_alias_shares_identity_with_its_target(self):
        target = self.root / "ARCHITECTURE.md"
        target.write_text("real content")
        alias = self.root / "totally-unrelated-notes.md"
        os.link(target, alias)
        self.assertTrue(fag._same_stat_identity(alias, target))

    def test_two_unrelated_real_files_do_not_share_identity(self):
        a = self.root / "a.md"
        b = self.root / "b.md"
        a.write_text("a")
        b.write_text("b")
        self.assertFalse(fag._same_stat_identity(a, b))

    def test_a_nonexistent_path_returns_false_not_an_exception(self):
        target = self.root / "ARCHITECTURE.md"
        target.write_text("real content")
        missing = self.root / "does-not-exist.md"
        self.assertFalse(fag._same_stat_identity(missing, target))


class IsArchitectureDocTests(unittest.TestCase):
    """The actual defect class (FORE-711) and its negative controls -- Iris's real probe shapes,
    reproduced as permanent regression tests rather than left as a one-time finding."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tmpdir.name)
        self.arch = self.project_root / "ARCHITECTURE.md"
        self.arch.write_text("frozen content")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_the_real_path_matches_directly(self):
        self.assertTrue(fag._is_architecture_doc(str(self.arch), self.project_root))

    def test_a_hardlink_alias_is_caught_by_the_identity_test(self):
        """THE DISCRIMINATING CASE (FORE-711). Before this fix, a write through this exact alias
        was silently allowed and the real ARCHITECTURE.md's bytes changed -- reproduced live by
        Iris and independently by the orchestrating session. The identity test must catch this
        regardless of the alias's name or path."""
        alias = self.project_root / "totally-unrelated-notes.md"
        os.link(self.arch, alias)
        self.assertTrue(fag._is_architecture_doc(str(alias), self.project_root))

    def test_a_symlink_alias_is_still_caught_via_resolve(self):
        """The negative control this ticket's own repro held: Path.resolve() DOES follow
        symlinks, so this path was never actually broken -- a regression here would mean the fix
        somehow narrowed existing correct behavior, not just added new coverage."""
        alias = self.project_root / "symlink-notes.md"
        os.symlink(self.arch, alias)
        self.assertTrue(fag._is_architecture_doc(str(alias), self.project_root))

    def test_a_genuinely_unrelated_file_is_not_the_architecture_doc(self):
        other = self.project_root / "notes.md"
        other.write_text("unrelated content, unrelated inode")
        self.assertFalse(fag._is_architecture_doc(str(other), self.project_root))

    def test_a_case_variant_of_an_existing_doc_is_caught_by_identity_on_this_filesystem(self):
        """On THIS machine's case-insensitive filesystem (confirmed live, same measurement
        registry_write_guard.py's own module docstring already made for a different directory),
        a differently-cased path to an EXISTING ARCHITECTURE.md is not a distinct, inode-less
        target at all -- the OS itself treats it as the identical directory entry, so the
        identity test (not the name-fold fallback) is what actually catches it. Confirmed
        directly rather than assumed, since the two branches must not be conflated."""
        candidate = self.project_root / "Architecture.MD"
        self.assertTrue(candidate.exists(), "this filesystem is case-insensitive; if this "
                        "assertion ever fails on a different machine, the case-fold fallback "
                        "below is what would catch it instead, and that path needs its own test")
        self.assertTrue(fag._same_stat_identity(candidate, self.arch))
        self.assertTrue(fag._is_architecture_doc(str(candidate), self.project_root))

    def test_the_name_fold_fallback_matches_a_target_with_no_inode_at_all(self):
        """The fallback's real, load-bearing case: a target that does not exist under ANY name
        or case yet -- e.g. the very first Write that would CREATE the project's ARCHITECTURE.md.
        No inode exists for the identity test to compare against, so the fallback's own
        name-string check is what has to catch it."""
        fresh_project = Path(tempfile.mkdtemp())
        try:
            candidate = fresh_project / "Architecture.MD"
            self.assertFalse(candidate.exists())
            self.assertFalse((fresh_project / "ARCHITECTURE.md").exists())
            self.assertTrue(fag._is_architecture_doc(str(candidate), fresh_project))
        finally:
            fresh_project.rmdir()

    def test_a_relative_path_resolves_against_project_root(self):
        self.assertTrue(fag._is_architecture_doc("ARCHITECTURE.md", self.project_root))


class ArchitectureIsClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_review_file_is_open(self):
        closed, detail = fag._architecture_is_closed(self.project_root)
        self.assertFalse(closed)

    def test_empty_review_file_is_open(self):
        (self.project_root / "ARCHITECTURE-REVIEW.md").write_text("")
        closed, detail = fag._architecture_is_closed(self.project_root)
        self.assertFalse(closed)

    def test_nonempty_review_file_with_no_binding_marker_is_closed(self):
        (self.project_root / "ARCHITECTURE-REVIEW.md").write_text("real review content")
        closed, detail = fag._architecture_is_closed(self.project_root)
        self.assertTrue(closed)
        self.assertIn("recorded and non-empty", detail)


class RecordingHookCommon:
    def __init__(self):
        self.calls = []

    def deny(self, reason):
        self.calls.append(("deny", reason))

    def set_rule(self, rule_id):
        self.calls.append(("set_rule", rule_id))


class FrozenArchitectureGuardMainDispatchTests(unittest.TestCase):
    """CHV2-179 (Nadia Osei's real STRIDE/validate finding, Build 8). main()'s own per-target
    loop is what actually decides whether _is_architecture_doc()'s inode check is ever reached --
    none of the tests above exercise that loop, only the predicates it calls once it gets there.
    Both bypass shapes below are reproduced as permanent regressions, matching this project's own
    established discipline (FORE-711's own class above) rather than left as one-time findings."""

    def setUp(self):
        self.hc = RecordingHookCommon()
        self.patcher = mock.patch.object(fag, "hc", self.hc)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="chv2179-frozenarch-")
        self.addCleanup(self.tmp.cleanup)
        self.project_root = Path(self.tmp.name)
        (self.project_root / ".foreman").mkdir()
        self.arch = self.project_root / "ARCHITECTURE.md"
        self.arch.write_text("frozen content")
        (self.project_root / "ARCHITECTURE-REVIEW.md").write_text("real review content")

    def _denies(self):
        return [c for c in self.hc.calls if c[0] == "deny"]

    def test_a_hardlink_outside_every_project_root_is_now_denied(self):
        """THE FIRST CORE REPRO. Before this fix, a hardlink alias placed at a path with no
        .foreman/ marker anywhere above it made project_root_for_target() return None, and
        main()'s loop `continue`d before _is_architecture_doc() (and therefore the FORE-711
        identity check) was ever reached -- the write proceeded and mutated the real frozen
        ARCHITECTURE.md's bytes via the shared inode, completely silently."""
        outside = Path(tempfile.mkdtemp(prefix="chv2179-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        alias = outside / "totally-unrelated-notes.txt"
        os.link(self.arch, alias)

        fag.main({"tool_name": "Write", "cwd": str(self.project_root),
                  "tool_input": {"file_path": str(alias)}})
        self.assertEqual(len(self._denies()), 1)
        self.assertIn("closed for this build", self._denies()[0][1])

    def test_a_hardlink_inside_an_unrelated_project_is_now_denied(self):
        """THE SECOND CORE REPRO. Before this fix, a hardlink alias placed INSIDE a different
        project that also carries its own .foreman/ marker made project_root_for_target()
        resolve to THAT OTHER project, and _is_architecture_doc() then compared the alias's
        inode against the OTHER project's own ARCHITECTURE.md -- a different file entirely, so
        the comparison correctly returned False and the hook stayed silent while the write still
        mutated the real, original project's frozen document."""
        other_project = Path(tempfile.mkdtemp(prefix="chv2179-other-project-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(other_project, ignore_errors=True))
        (other_project / ".foreman").mkdir()
        alias = other_project / "scratch-notes.txt"
        os.link(self.arch, alias)

        fag.main({"tool_name": "Write", "cwd": str(self.project_root),
                  "tool_input": {"file_path": str(alias)}})
        self.assertEqual(len(self._denies()), 1)
        self.assertIn("closed for this build", self._denies()[0][1])

    def test_a_hardlink_via_bash_redirect_is_also_denied(self):
        """FORE-1 coverage, restated for this exact bypass: a denied Write must not be trivially
        routable around via a shell redirect through the same hardlink alias."""
        outside = Path(tempfile.mkdtemp(prefix="chv2179-outside-bash-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        alias = outside / "notes.txt"
        os.link(self.arch, alias)

        fag.main({"tool_name": "Bash", "cwd": str(self.project_root),
                  "tool_input": {"command": f"printf 'x' > {alias}"}})
        self.assertEqual(len(self._denies()), 1)

    def test_architecture_still_open_hardlink_outside_project_is_not_denied(self):
        """Negative control: the new cwd-anchored identity check must not turn into an
        unconditional deny regardless of state -- when architecture is still OPEN for cwd's own
        project, the SAME hardlink-outside-every-project shape must stay silent, exactly as an
        ordinary in-place write to the still-open ARCHITECTURE.md would."""
        (self.project_root / "ARCHITECTURE-REVIEW.md").write_text("")  # empty -- not closed
        outside = Path(tempfile.mkdtemp(prefix="chv2179-open-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        alias = outside / "notes.txt"
        os.link(self.arch, alias)

        fag.main({"tool_name": "Write", "cwd": str(self.project_root),
                  "tool_input": {"file_path": str(alias)}})
        self.assertEqual(self._denies(), [])

    def test_an_unrelated_file_outside_every_project_stays_silent(self):
        """Negative control: an ordinary, genuinely unrelated file outside every project (no
        shared inode with cwd's own ARCHITECTURE.md) must not be caught by the new check -- it
        is not enough to be outside every project root; the inode must actually match."""
        outside = Path(tempfile.mkdtemp(prefix="chv2179-unrelated-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        unrelated = outside / "notes.txt"
        unrelated.write_text("nothing to do with ARCHITECTURE.md")

        fag.main({"tool_name": "Write", "cwd": str(self.project_root),
                  "tool_input": {"file_path": str(unrelated)}})
        self.assertEqual(self._denies(), [])

    def test_ordinary_in_place_write_is_still_denied_exactly_once(self):
        """Regression check: the ordinary, already-covered case (a direct write to the real
        ARCHITECTURE.md path) must still deny exactly once, not twice, now that the new
        cwd-anchored check and the existing path-based route both examine the same target."""
        fag.main({"tool_name": "Write", "cwd": str(self.project_root),
                  "tool_input": {"file_path": str(self.arch)}})
        self.assertEqual(len(self._denies()), 1)


if __name__ == "__main__":
    unittest.main()
