#!/usr/bin/env python3
"""OSSandboxGuard -- R14a (PRD.md R13/R14, TESSERA DEVH-16, DEVH-61 through DEVH-64,
PRD-DELTA-R14-R15.md). Removes the destructive-filesystem-write CAPABILITY at the OS level for
a Bash command in a declared capability class, regardless of how the command string is
obfuscated -- the layer PRD.md R13 requires to still deny the `su""do` class even when every
detection-layer guard is disabled.

STRUCTURALLY NOT A PEER OF guard_destructive.py / guard_prodconfig.py, even though it shares
their input contract and subprocess entry-point convention (DEVH-63). Those two guards decide
BEFORE the tool runs and their failure shape is a hook decision. This guard's enforcement is a
kernel-level sandbox wrapper: on a match it does not veto, it REWRITES the command to run under
capability_scope.sh and allows the (rewritten) call to proceed. The actual denial, if any,
happens later, at execution time, inside the wrapped process, as a nonzero exit with
errno-shaped stderr -- not as a second hook decision.

DECLARED CAPABILITY CLASS, per C15's own requirement that it be a module-level constant rather
than inferred: CAPABILITY_CLASS_PATTERNS below, reused verbatim from
guard_destructive.DESTRUCTIVE_PATTERNS rather than a second, competing matcher for the same
command shapes -- the exact duplication R15b independently exists to avoid for PatternFeedGuard.
Widening or narrowing the class is therefore a diff in guard_destructive.py's own pattern list,
visible to both guards at once, not a silent divergence between two copies.

THE COST THIS DESIGN EXISTS TO BOUND (PRD-DELTA-R14-R15.md section 2.3): `updatedInput` must
travel with permissionDecision "allow", and an "allow" bypasses the operator's own workspace
permission prompt for that call. Rewriting every Bash call (option A) would silently suppress
every permission prompt in exchange for one new control -- rejected. This guard only rewrites
commands inside the declared class (option C): outside it, it is silent, and the operator's
ordinary permission layer is untouched.
"""

import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import guard_destructive  # noqa: E402

RULE_ID = "GUARD-OS-SANDBOX"

LIB_DIR = Path(__file__).resolve().parent / "lib"

# BOLLARD_DENY_CAPABILITY_SB_OVERRIDE exists ONLY so C16's live-harness negative-control arm and
# C18's own negative control can point this guard's real, unmodified code path at a profile with
# the deny rule removed -- proving the trap failure is attributable to the deny rule, not to a
# malformed invocation -- without a second copy of this file. It is never set in normal
# operation; the shipped guard always resolves to lib/deny_capability.sb.
DENY_CAPABILITY_SB = Path(
    os.environ.get("BOLLARD_DENY_CAPABILITY_SB_OVERRIDE") or str(LIB_DIR / "deny_capability.sb")
)
CAPABILITY_SCOPE_SH = LIB_DIR / "capability_scope.sh"

# Re-exported, not redefined -- see the module docstring's DECLARED CAPABILITY CLASS section.
# C15's own test imports this name directly to enumerate the class rather than trusting a
# description of it.
CAPABILITY_CLASS_PATTERNS = guard_destructive.DESTRUCTIVE_PATTERNS


def _in_capability_class(command: str):
    normalized = guard_destructive._normalize(command)
    for name, pattern in CAPABILITY_CLASS_PATTERNS:
        if pattern.search(normalized):
            return name
    return None


def _wrapped_command(original_command: str) -> str:
    """Builds the sandbox-exec invocation that replaces `original_command`.

    Constructed directly (mirroring capability_scope.sh's own exec line) rather than shelling
    out to capability_scope.sh, because this function has to return a STRING for the harness to
    execute later -- there is nothing to run yet. Both this function and capability_scope.sh
    point at the same DENY_CAPABILITY_SB path so they cannot silently diverge on which profile
    is enforced; capability_scope.sh remains the independently-driveable artifact C14 tests
    directly and the one a human or another script would invoke by hand.
    """
    return (
        f"sandbox-exec -f {shlex.quote(str(DENY_CAPABILITY_SB))} "
        f"/bin/sh -c {shlex.quote(original_command)}"
    )


def main(data):
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command:
        return

    matched = _in_capability_class(command)
    if matched is None:
        hc.set_rule(f"{RULE_ID}:no-match")
        return

    hc.set_rule(f"{RULE_ID}:{matched}")
    hc.rewrite(
        _wrapped_command(command),
        reason=(
            f"guard_os_sandbox: this command matches a destructive-operation shape "
            f"({matched}); its filesystem-write capability is removed at the OS level "
            f"regardless of how the command string is obfuscated."
        ),
    )


if __name__ == "__main__":
    hc.run(main)
