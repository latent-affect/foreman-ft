#!/usr/bin/env python3
"""Restamp .foreman/SHIP-CHARTER.json's commit_hash to the real current HEAD.

The charter file cannot contain the hash of the commit that adds it. Stamp
against current HEAD, then commit only this file. ship_readiness_gate.py treats a
charter-only child of commit_hash as still current, so do not restamp after that
commit. Restamping after the commit recreates the dirty-tree loop. This script does
not commit anything itself.

A later non-charter commit still stale-denies, same as before.
"""

import json
import subprocess
import sys
from pathlib import Path

CHARTER_RELPATH = ".foreman/SHIP-CHARTER.json"


def current_commit_hash(cwd):
    """Identical logic to ship_readiness_gate.py's own function of the same name -- this
    script exists specifically so its answer always matches what the gate will check."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                              capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def main():
    project_root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    charter_path = project_root / CHARTER_RELPATH

    if not charter_path.is_file():
        print(f"stamp_ship_charter: no charter at {charter_path} -- nothing to stamp.", file=sys.stderr)
        return 1

    real_hash = current_commit_hash(project_root)
    if real_hash is None:
        print(f"stamp_ship_charter: could not resolve current HEAD in {project_root} -- "
              f"failing closed, not writing a guessed value.", file=sys.stderr)
        return 1

    charter = json.loads(charter_path.read_text())
    old_hash = charter.get("commit_hash")
    if old_hash == real_hash:
        print(f"stamp_ship_charter: {charter_path} already matches current HEAD ({real_hash[:12]}). No change.")
        return 0

    charter["commit_hash"] = real_hash
    charter_path.write_text(json.dumps(charter, indent=2) + "\n")
    print(f"stamp_ship_charter: {charter_path} commit_hash {old_hash!r} -> {real_hash} "
          f"(current HEAD). Not committed -- commit the result yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
