"""Asserts every import in tessera/common/*.py (excluding tests/) resolves to a stdlib
module -- run standalone: python3 tessera/common/tests/check_stdlib_only.py
"""
import ast
import sys
import sysconfig
from pathlib import Path

STDLIB_PATH = Path(sysconfig.get_paths()["stdlib"])


def is_stdlib(module_name):
    top = module_name.split(".")[0]
    if top in sys.builtin_module_names:
        return True
    spec = None
    try:
        import importlib.util

        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return False  # unresolvable import -> reported as NOT STDLIB by the caller, not swallowed
    if spec is None or spec.origin is None:
        return False
    return str(STDLIB_PATH) in spec.origin and "site-packages" not in spec.origin


def main():
    common_dir = Path(__file__).resolve().parent.parent
    bad = []
    for path in common_dir.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue  # relative import within tessera itself
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                if not is_stdlib(name):
                    bad.append((path.name, name))
    if bad:
        for filename, name in bad:
            print(f"NOT STDLIB: {filename} imports {name!r}", file=sys.stderr)
        sys.exit(1)
    print("OK: tessera/common imports only stdlib")


if __name__ == "__main__":
    main()
