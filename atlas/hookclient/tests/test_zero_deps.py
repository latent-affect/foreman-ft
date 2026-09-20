"""GOALS.json C7. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.hookclient.tests.test_zero_deps -v
"""

import ast
import sys
import unittest
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parent.parent
SHIPPED_MODULES = ["matching.py", "reader.py", "__init__.py"]

STDLIB_MODULE_NAMES = set(sys.stdlib_module_names)


class ZeroDependencyTests(unittest.TestCase):
    def _imported_top_level_names(self, path):
        tree = ast.parse(path.read_text(), filename=str(path))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # relative import within this component (e.g. "from . import matching")
                if node.module:
                    names.add(node.module.split(".")[0])
        return names

    def test_no_import_outside_stdlib_or_this_component(self):
        for filename in SHIPPED_MODULES:
            path = COMPONENT_DIR / filename
            with self.subTest(file=filename):
                names = self._imported_top_level_names(path)
                for name in names:
                    self.assertIn(
                        name, STDLIB_MODULE_NAMES,
                        f"{filename} imports {name!r}, not in the Python stdlib",
                    )

    def test_no_import_of_any_sibling_atlas_component(self):
        for filename in SHIPPED_MODULES:
            path = COMPONENT_DIR / filename
            with self.subTest(file=filename):
                names = self._imported_top_level_names(path)
                self.assertNotIn("atlas", names, f"{filename} imports the atlas package directly")


if __name__ == "__main__":
    unittest.main()
