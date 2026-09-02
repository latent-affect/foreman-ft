#!/usr/bin/env python3
"""PatternFeedGuard -- bollard/GOALS.json C17 (PRD-DELTA-R14-R15.md R15b, DEVH-16). Additive-only
detection guard, one of the two-layer split's non-safety-carrying pair (the other is
SemanticResolutionGuard) -- per that pass's own component decomposition (DEVH-62), no component
in {OSSandboxGuard, AllowlistGuard} may treat this guard's presence, absence, or crashing as
evidence of safety, and this guard's own fail-open behavior (hook_common.run()'s repo-wide
default) is therefore left UNCHANGED here, unlike AllowlistGuard's guard-local fail-closed fix
(R14b). Confirmed consistent with the frozen constraint before writing this file, not assumed.

R15b's design decision, made explicitly rather than left implicit (F13's own named failure mode
is exactly "made implicitly, so a pattern retired from one list keeps firing from the other"):

    THIS GUARD IS DISJOINT BY CONSTRUCTION FROM guard_destructive.py's DESTRUCTIVE_PATTERNS.
    It does NOT subsume that list, and guard_destructive.py is NOT retired or modified by this
    file.

Why disjoint rather than subsume, decided here because R15b left it open and named it a real,
bounded technical call, not an escalation:
  1. guard_destructive.py is already shipped, already tested, and its own criterion (C5) is
     frozen under the ORIGINAL C1-C8 freeze, not reopened by this C9-C16 pass. Subsuming its
     pattern list and retiring it would mean editing or deprecating a file this pass has no
     mandate to touch, and would put a currently-green, currently-frozen criterion at risk for
     no requirement that asks for it.
  2. "Disjoint by construction" is the property test_guard_pattern_feed.py can actually prove
     mechanically and cheaply: drive each guard's REAL hook entry point against the other's own
     trap commands and assert empty stdout both directions. "Subsumes and retires" would instead
     require proving a negative about an old file's absence, a strictly harder and riskier claim
     for the same requirement.
  3. Matches the "additive-only" framing this whole layer already carries: this guard ADDS new
     detection coverage for destructive-command shapes guard_destructive.py's existing six
     patterns do not cover, rather than replacing coverage that is already verified and shipped.
     Making PatternFeedGuard's presence load-bearing for coverage guard_destructive.py used to
     provide alone would be exactly the kind of new inter-guard dependency the frozen constraint
     ("no component may treat {SemanticResolutionGuard, PatternFeedGuard}'s presence as
     evidence") argues against creating in the other direction.

Same DENY-or-nothing, quote/backslash-normalization discipline as guard_destructive.py (this
project's own established convention for this exact evasion class, PRD.md R13's su""do shape),
duplicated here rather than imported -- guard_destructive.py has no shared normalization module
to import from, and this project's own established convention (see component_coupling.py,
extract_bash_write_targets's docstring) is self-contained hooks over a cross-hook dependency for
a four-line function.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402

RULE_ID = "GUARD-PATTERN-FEED"

# Each entry: (name, compiled pattern against the NORMALIZED command text). Chosen to be
# disjoint BY CONSTRUCTION from guard_destructive.py's DESTRUCTIVE_PATTERNS: different verbs,
# different subcommands, different domains entirely (secure-delete, disk-erase, git history
# rewrite, SQL schema drop) rather than alternate spellings of rm/git-clean/dd/mkfs. Verified
# disjoint empirically, not just by inspection -- see test_guard_pattern_feed.py's
# PatternDisjointnessTests, which drives both guards' real entry points against each other's
# trap commands.
PATTERN_FEED_PATTERNS = [
    ("secure_delete_shred", re.compile(r"\bshred\s+-[a-zA-Z]*[uz][a-zA-Z]*\b")),
    ("disk_erase_diskutil", re.compile(r"\bdiskutil\s+erase(disk|volume)\b", re.IGNORECASE)),
    ("git_force_push", re.compile(r"\bgit\s+push\b[^\n]*(\s-[a-zA-Z]*f[a-zA-Z]*\b|--force\b)")),
    ("git_force_branch_delete", re.compile(r"\bgit\s+branch\s+-[a-zA-Z]*D[a-zA-Z]*\b")),
    ("sql_drop_table_or_database", re.compile(r"\bdrop\s+(table|database)\b", re.IGNORECASE)),
]


def _normalize(command: str) -> str:
    """Strips shell quote characters and backslashes -- identical normalization to
    guard_destructive.py's own _normalize, same reasoning: a quote-split obfuscation collapses
    to the same text a literal spelling would produce. Nothing here executes or interprets the
    shell."""
    return re.sub(r'''['"\\]''', "", command)


def _matched_pattern(command: str):
    normalized = _normalize(command)
    for name, pattern in PATTERN_FEED_PATTERNS:
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
        f"guard_pattern_feed: this command matches a destructive-operation shape "
        f"({matched}) and is denied. If this is genuinely intended, run it yourself in a "
        f"terminal."
    )


if __name__ == "__main__":
    hc.run(main)
