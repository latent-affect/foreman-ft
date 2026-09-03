#!/usr/bin/env python3
"""Denies a Bash call whose command matches a destructive-operation shape: a recursive force
delete, a force-clean, or a device-level overwrite -- PRD.md R24 (DEVH-2), bollard/GOALS.json
C5. Capability removal at the command-string layer: this repo's own guard against the exact
detection-instead-of-prevention gap R24 exists to close (docs, tests and code comments already
treated this file as shipped before it existed -- see hook_common.py's own corruption-incident
docstring, which names it by this filename).

DENY-or-nothing, hook_common.py's existing convention: acts only on a real match, emits nothing
otherwise, fails open on its own internal error (hc.run()).

Matches on the NORMALIZED command text (quote and backslash characters stripped), not the raw
string, so a quote-split obfuscation (rm -r""f, r'm' -rf) collapses to the same text a literal
spelling would produce and is caught by the same pattern. This is the same evasion class this
project's own PRD.md R13 names as the su""do class -- pattern-match the class, not each spelling.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402

RULE_ID = "GUARD-DESTRUCTIVE"

# Each entry: (name, compiled pattern against the NORMALIZED command text).
DESTRUCTIVE_PATTERNS = [
    ("recursive_force_delete", re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\b")),
    ("recursive_force_delete_flag_order", re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\b")),
    ("git_force_clean", re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f[a-zA-Z]*d[a-zA-Z]*\b")),
    ("git_force_clean_flag_order", re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*d[a-zA-Z]*f[a-zA-Z]*\b")),
    ("device_level_overwrite_dd", re.compile(r"\bdd\b[^|;&\n]*\bof=/dev/")),
    ("device_level_overwrite_mkfs", re.compile(r"\bmkfs(\.\w+)?\s+.*?/dev/")),
    # DEVH-92 Class C, pattern 7: a different tool reaching the same effect through its own flag.
    # `find <dir> -delete` deletes recursively without containing any enumerated destructive verb,
    # and the text is already fully plain, so no normalization or resolution layer reaches it.
    # Measured passing the whole 5-guard chain before this landed. Scoped to the same command
    # segment as the `find` that starts it, and `\s-delete` rather than `-delete` so `--delete`
    # (git's spelling, a different and non-destructive thing) does not collide.
    ("recursive_delete_find_flag", re.compile(r"\bfind\b[^|;&\n]*\s-delete\b")),
]

# THE COVERAGE BOUNDARY, DECLARED RATHER THAN IMPLIED (DEVH-92 Class C).
#
# Adding the entry above does not close Class C and must not be read as closing it. Clint's
# architecture verdict on DEVH-92 is explicit that any non-enumerated tool evades identically and
# that expanding this list on each discovery is the correct and only available response until a
# non-enumeration layer is designed. That is true of the list below right now.
#
# Each entry: (name, an example that is NOT denied today, why it is out). Every example is verified
# passing the real chain by test_class_c_coverage_boundary.py, so this is an executable statement
# of the boundary rather than a comment that quietly goes stale. If one of them starts being
# denied, that test fails on purpose -- update the declaration deliberately, do not delete the case.
KNOWN_NOT_ENUMERATED = [
    ("truncate_in_place", "truncate -s 0 /tmp/devh92-target/file",
     "destroys file contents without any enumerated verb"),
    ("unlink_single_file", "unlink /tmp/devh92-target/file",
     "the single-file sibling of rm, never enumerated"),
    ("interpreter_hosted_delete", "python3 -c import shutil;shutil.rmtree('/tmp/devh92-target')",
     "the destructive verb lives inside an interpreter argument, so the enumerated shell verbs "
     "never appear -- the general form of this is unbounded, since any interpreter on PATH is "
     "another spelling"),
]


def _normalize(command: str) -> str:
    """Strips shell quote characters and backslashes. rm -r""f and r'm' -rf both collapse to
    rm -rf; this is pure text normalization, nothing here executes or interprets the shell."""
    return re.sub(r'''['"\\]''', "", command)


def _matched_pattern(command: str):
    normalized = _normalize(command)
    for name, pattern in DESTRUCTIVE_PATTERNS:
        if pattern.search(normalized):
            return name
    return None


def main(data):
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command:
        return

    matched = _matched_pattern(command)
    if matched is None:
        hc.set_rule(f"{RULE_ID}:no-match")
        return

    hc.set_rule(f"{RULE_ID}:{matched}")
    hc.deny(
        f"guard_destructive: this command matches a destructive-operation shape "
        f"({matched}) and is denied. If this is genuinely intended, run it yourself in a "
        f"terminal."
    )


if __name__ == "__main__":
    hc.run(main)
