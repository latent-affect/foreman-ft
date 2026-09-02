#!/usr/bin/env python3
"""Denies an Edit or Write whose target path is shaped like a production configuration file --
PRD.md R24 (DEVH-2), bollard/GOALS.json C6. Path-shape matching, the same style
component_coupling.py's own glob matching already uses for component ownership, applied here to
a fixed, narrow set of production-config shapes rather than the far broader question of "is this
file sensitive," which is not this guard's job.

DENY-or-nothing, hook_common.py's existing convention: acts only on a real path-shape match,
emits nothing otherwise, fails open on its own internal error (hc.run()).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402

RULE_ID = "GUARD-PRODCONFIG"

# Each entry: (name, compiled pattern against the path's POSIX-normalized string). Matched
# case-insensitively against the whole path, not just the basename, so a directory-scoped shape
# (config/production/db.yml) is caught the same way a filename-scoped one is.
PRODCONFIG_PATTERNS = [
    ("dotenv_production", re.compile(r"(^|/)\.env\.(production|prod)$", re.IGNORECASE)),
    ("production_path_segment", re.compile(r"(^|/)(production|prod)(/|$)", re.IGNORECASE)),
    ("docker_compose_prod", re.compile(r"(^|/)docker-compose\.(prod|production)(\.ya?ml)?$", re.IGNORECASE)),
]


def _matched_pattern(file_path: str):
    posix_path = file_path.replace("\\", "/")
    for name, pattern in PRODCONFIG_PATTERNS:
        if pattern.search(posix_path):
            return name
    return None


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write"):
        return
    file_path = (data.get("tool_input") or {}).get("file_path")
    if not file_path:
        return

    matched = _matched_pattern(file_path)
    if matched is None:
        hc.set_rule(f"{RULE_ID}:no-match")
        return

    hc.set_rule(f"{RULE_ID}:{matched}")
    hc.deny(
        f"guard_prodconfig: {file_path!r} is shaped like a production configuration file "
        f"({matched}) and {tool_name} to it is denied. If this is genuinely intended, edit it "
        f"yourself outside this harness."
    )


if __name__ == "__main__":
    hc.run(main)
