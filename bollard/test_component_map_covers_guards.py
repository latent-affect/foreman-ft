#!/usr/bin/env python3
"""C9: every file R14/R15 added under bollard/ (the four new guards, the two new lib/ scripts,
and every bollard/test_*.py file) resolves to the 'bollard' component through the real, shipped
component_coupling predicates -- not a reimplementation of the parser -- so goals_freeze_gate
actually gates it. Regression guard: this criterion's own history (bollard/GOALS.json C9) records
the same glob block silently un-gating these paths twice already (a bare-filename glob, then a
mid-string wildcard, DEVH-72), so this test exists to fail loudly the next time a component-map
edit does that, instead of the gap sitting undetected until someone re-measures by hand."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import component_coupling as cc  # noqa: E402

BOLLARD_DIR = Path(__file__).resolve().parent

# Fixed per C9's own verification text -- the four new guards and two new lib/ scripts R14/R15
# added. bollard/test_*.py files are NOT hardcoded here (see below): the criterion's own point is
# to catch a FUTURE component-map edit, and a hardcoded list would stop covering "every new
# bollard/test_*.py file it adds" the moment a new one is added after this test is written.
FIXED_NEW_PATHS = [
    "bollard/guard_allowlist.py",
    "bollard/guard_semantic_resolution.py",
    "bollard/guard_pattern_feed.py",
    "bollard/guard_os_sandbox.py",
    "bollard/lib/deny_capability.sb",
    "bollard/lib/capability_scope.sh",
]


class ComponentMapCoversGuardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = cc.find_project_root(str(BOLLARD_DIR))
        assert cls.project_root is not None, (
            "find_project_root() found no .foreman/ marker above "
            f"{BOLLARD_DIR} -- this test must run inside the real dev-harness-run2 checkout"
        )
        cls.component_map = cc.parse_component_map(cls.project_root)
        assert cls.component_map, (
            "parse_component_map() returned no components -- ARCHITECTURE.md's "
            "'```yaml components' block is missing or empty in this checkout"
        )

    def _assert_resolves_to_bollard(self, rel_path):
        abs_path = self.project_root / rel_path
        self.assertTrue(
            abs_path.exists(), f"{rel_path} does not exist under {self.project_root}"
        )
        comp = cc.component_of(rel_path, self.component_map)
        self.assertEqual(
            comp, "bollard", f"component_of({rel_path!r}) resolved to {comp!r}, not 'bollard'"
        )
        is_impl, impl_comp = cc.is_implementation_path(
            abs_path, self.project_root, self.component_map
        )
        self.assertIs(
            is_impl, True,
            f"is_implementation_path({rel_path!r}) returned ({is_impl!r}, {impl_comp!r}), "
            f"expected (True, ...)",
        )
        self.assertEqual(impl_comp, "bollard")

    def test_fixed_new_r14_r15_paths_resolve_to_bollard(self):
        for rel_path in FIXED_NEW_PATHS:
            with self.subTest(rel_path=rel_path):
                self._assert_resolves_to_bollard(rel_path)

    def test_every_bollard_test_file_resolves_to_bollard(self):
        # Dynamic, not a snapshot list: this is what makes the test a real regression guard for
        # "every new bollard/test_*.py file it adds" rather than only today's set.
        test_files = sorted(BOLLARD_DIR.glob("test_*.py"))
        self.assertGreater(
            len(test_files), 0, "no bollard/test_*.py files found -- glob is broken"
        )
        for abs_path in test_files:
            rel_path = str(abs_path.relative_to(self.project_root))
            with self.subTest(rel_path=rel_path):
                self._assert_resolves_to_bollard(rel_path)

    def test_positive_control_hook_common_resolves_to_bollard(self):
        comp = cc.component_of("bollard/hook_common.py", self.component_map)
        self.assertEqual(comp, "bollard")

    def test_negative_control_unknown_component_resolves_to_none(self):
        # Without this, a component map that resolved EVERY string to 'bollard' would pass
        # every assertion above and this criterion would verify nothing.
        comp = cc.component_of("no-such-component/x.py", self.component_map)
        self.assertIsNone(comp)


if __name__ == "__main__":
    unittest.main()
