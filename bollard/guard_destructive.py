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

DESTRUCTIVE_PATTERNS BELOW IS READ BY ANOTHER GUARD, AND WIDENING IT HAS A SECOND EFFECT.
guard_os_sandbox.py imports this list by live reference (not a copy) and uses it to decide WHEN to
wrap a command in sandbox-exec. Inside that wrapped class it returns allow plus updatedInput, which
deliberately bypasses the operator's own permission prompt -- the substituted control there is the
kernel-level sandbox, per PRD-DELTA-R14-R15.md section 3, Option C. So adding a pattern here does
not only add a deny: it also widens the set of commands whose permission prompt is replaced by a
sandbox. The live reference means the two never drift, which is by design and verified, but it also
means the coupling is invisible from this file unless it is written down. Found by a real
security-review pass (DEVH-16 comment 1368) which noted the disclosure ran only one way.
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
    # These two are BELOW the enumeration's own destructiveness threshold, not oversights. The
    # threshold was measured against the shipped guards rather than inferred from the list:
    # `rm <file>` and `rm -f <file>` both PASS today; only the recursive/bulk forms (rm -rf,
    # find -delete, dd to a device, mkfs) are denied. Enumerating a single-file truncate or unlink
    # would make this guard stricter about those than it is about rm itself, which is incoherent
    # rather than safer. If the threshold is ever deliberately lowered to any-single-file
    # destruction, these two come off this list together with plain `rm` -- as one decision, not
    # three separate ones.
    ("truncate_in_place", "truncate -s 0 /tmp/devh92-target/file",
     "destroys one file's contents with no enumerated verb; single-target, so below the same "
     "threshold that lets plain `rm <file>` through today (measured, not assumed)"),
    ("unlink_single_file", "unlink /tmp/devh92-target/file",
     "the single-file sibling of rm; plain `rm <file>` passes today, so denying this one would "
     "be stricter about unlink than about rm"),
    # Both interpreter forms Clint's DEVH-92 verdict named, declared rather than enumerated. The
    # verdict named find-delete, shutil.rmtree AND perl-unlink; find-delete landed as a pattern
    # above, these two did not, and the reasoning is on the ticket (comment 1357). Both are here
    # so a named-but-not-delivered item exists somewhere on disk rather than only in prose -- which
    # is the job this declaration exists to do, applied to its own scope.
    ("interpreter_hosted_delete_python",
     "python3 -c import shutil;shutil.rmtree('/tmp/devh92-target')",
     "the destructive verb lives inside an interpreter argument, so no enumerated shell verb "
     "appears -- and the general form is unbounded, since any interpreter on PATH is another "
     "spelling. Enumerating this exact string would also deny every command that merely mentions "
     "it, including this declaration's own example"),
    ("interpreter_hosted_delete_perl", "perl -e unlink('/tmp/devh92-target/file')",
     "the second interpreter form the DEVH-92 verdict named. Same unbounded class as the python "
     "one; the class spans both single-target spellings like this and bulk ones "
     "(perl -MFile::Path -e rmtree(...)), which is why enumerating any single spelling of it "
     "reads as coverage without being coverage"),
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
