#!/usr/bin/env python3
"""AllowlistGuard -- denies the privilege-escalation capability (`sudo`) at the command-string
layer, PRD.md R13 / R14-R15, bollard/GOALS.json C10, C11 (DEVH-16, DEVH-62).

Matches on the NORMALIZED command text (quote and backslash characters stripped), the same
technique guard_destructive.py uses, so the `su""do` quote-embedding evasion PRD.md R13 names
by that exact spelling, and any shell-level string-concatenation variant of it, collapse to the
same text a literal `sudo` would produce and are caught by the same pattern.

DEVH-61's conclusion (this component's binding design constraint, not background): R13's
capability-removal layers must deny independently of whether any detection guard ran or fired.
PRD.md's own R13 verification clause requires the deny to survive "when every detection-layer
guard is disabled" -- C10 tests this literally, running this hook alone with no detection guard
present.

FAIL-CLOSED, deliberately NOT hook_common.py's fail-open default (bollard/GOALS.json C11 /
DEVH-62's toy-modeled mitigation). hook_common.run()'s fail-open exists so a crashing DETECTION
guard degrades to a miss rather than bricking the agent -- correct for a guard that is additive
by R13's own ordering. AllowlistGuard is the opposite case: it is one of the two components
DEVH-61 identified as the layer the deny actually has to survive on. A capability-scoping guard
that fails open on its own internal error reproduces exactly the "guard that looks alive and
does nothing" failure hook_common.py's docstring names, for the one guard class R13 says must
not do that. So classification here is wrapped in its own try/except that calls hc.deny()
directly, rather than letting the exception reach hook_common.run()'s outer handler.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402

RULE_ID = "GUARD-ALLOWLIST"

# Each entry: (capability name, compiled pattern against the NORMALIZED command text).
#
# DEVH-92 Class C: `sudo` was the only enumerated member of this capability, so any other
# privilege-escalation binary passed the guard untouched -- not an obfuscated spelling of `sudo`,
# a different program achieving the same thing, which no amount of quote-collapse or chr()/hex/
# base64 resolution turns into `sudo`. Measured through the real 5-guard chain before this landed:
# doas, pkexec, sudoedit and run0 all reached the agent unimpeded.
#
# The four added here are each a distinctive multi-character token, so matching them in this same
# shape carries no realistic false-positive cost on ordinary command text. `su` is deliberately
# NOT here -- see KNOWN_NOT_ENUMERATED below, which records why and proves it is still open.
DENIED_CAPABILITIES = [
    ("privilege_escalation_sudo", re.compile(r"\bsudo\b")),
    ("privilege_escalation_doas", re.compile(r"\bdoas\b")),
    ("privilege_escalation_pkexec", re.compile(r"\bpkexec\b")),
    ("privilege_escalation_sudoedit", re.compile(r"\bsudoedit\b")),
    ("privilege_escalation_run0", re.compile(r"\brun0\b")),
]

# THE COVERAGE BOUNDARY, DECLARED RATHER THAN IMPLIED (DEVH-92 Class C).
#
# Clint's architecture verdict on DEVH-92 says this in prose: Class C is NOT closed by landing an
# enumeration, any non-enumerated tool evades identically, and expanding the list on each discovery
# is the correct and only available response until a non-enumeration layer is designed. Prose in a
# ticket comment is a caveat someone has to go and find. This list is the same statement as an
# artifact that fails when the boundary silently moves.
#
# Each entry: (name, an example that is NOT denied today, why it is out).
# test_class_c_coverage_boundary.py asserts every example still passes the real chain. If one
# starts being denied, that test fails -- which is the point: either the enumeration grew and this
# declaration is now stale, or something changed matching behaviour without saying so. Update this
# list deliberately; do not delete the failing case.
KNOWN_NOT_ENUMERATED = [
    ("privilege_escalation_su", "su root -c id",
     "a bare \\bsu\\b in this matching shape denies ordinary text too -- normalization strips "
     "quotes, so `git commit -m \"su\"` collapses to a match. Catching it needs command-position "
     "awareness, which is a change to HOW matching works, not to the list, and therefore out of "
     "scope for this ticket by its own review boundary."),
]


def _normalize(command: str) -> str:
    """Strips shell quote characters and backslashes. su""do and 'su'"do" both collapse to
    sudo; this is pure text normalization, nothing here executes or interprets the shell."""
    return re.sub(r'''['"\\]''', "", command)


def _matched_capability(command: str):
    normalized = _normalize(command)
    for name, pattern in DENIED_CAPABILITIES:
        if pattern.search(normalized):
            return name
    return None


def main(data):
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command:
        return

    try:
        matched = _matched_capability(command)
    except Exception as exc:
        # Guard-local fail-closed backstop -- C11 / DEVH-62. hook_common.run()'s outer
        # fail-open must never be this guard's last word: a capability-scoping layer that
        # goes silent on its own bug is indistinguishable, from outside, from one that was
        # never invoked at all.
        hc.set_rule(f"{RULE_ID}:internal-error")
        hc.deny(
            f"guard_allowlist: internal error evaluating this command "
            f"({type(exc).__name__}: {exc}); denying rather than failing open, because this "
            f"guard is the capability-scoping backstop this project's R13 ordering requires to "
            f"hold even when the guard's own body raises."
        )
        return

    if matched is None:
        hc.set_rule(f"{RULE_ID}:no-match")
        return

    hc.set_rule(f"{RULE_ID}:{matched}")
    hc.deny(
        f"guard_allowlist: this command attempts a denied capability ({matched}) and is "
        f"denied. If this is genuinely intended, run it yourself in a terminal."
    )


if __name__ == "__main__":
    hc.run(main)
