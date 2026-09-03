#!/usr/bin/env python3
"""OSSandboxGuard -- R14a (PRD.md R13/R14, TESSERA DEVH-16, DEVH-61 through DEVH-64,
PRD-DELTA-R14-R15.md). Removes the destructive-filesystem-write CAPABILITY at the OS level for
a Bash command in a declared capability class -- the layer PRD.md R13 requires to still deny
the `su""do` class even when every detection-layer guard is disabled.

SCOPE, STATED ACCURATELY (F18, found by Priya against the shipped guards): the capability is
removed regardless of how the command STRING is obfuscated or spelled -- quote-split, built by
concatenation, or otherwise constructed, since CAPABILITY_CLASS_PATTERNS matches on normalized
text, not a literal spelling. It does NOT cover a destructive operation that never appears in a
Bash command string at all: a script written via Write and executed as `sh <script>` routes
around this guard entirely, because there is no command text here for a command-string matcher
to see, regardless of how well it resists obfuscation. That is a routing gap, not a spelling
gap, and C16's own claim (the capability-removal pair denies when detection is absent) still
holds for the traffic this guard actually sees. A real fix is an architecture-stage scope call
(the REQ-13a parse-then-deny redesign), not something this file patches around on its own.

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
import re
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


def _profile_can_deny(profile_path: Path) -> bool:
    """C19: shallow check that the resolved profile could deny SOMETHING before this guard
    substitutes it for a pre-execution veto. Deliberately shallow -- it parses for the presence
    of a `(deny ...)` rule, it does not and cannot (without running it) prove the profile denies
    the RIGHT capability, which is C14's and C18's job against the real, shipped profile.

    Exists because BOLLARD_DENY_CAPABILITY_SB_OVERRIDE (below) means the resolved profile is not
    always the shipped one, and this guard's rewrite carries a destructive-shaped command past
    Claude Code's own ambient safety layer (measured directly this session -- see DEVH-75):
    substituting a profile that cannot deny anything is strictly worse than not registering this
    guard at all, since the upstream layer that would have blocked the unwrapped command is
    bypassed and nothing replaces it (F17).

    Strips Seatbelt ';' line comments (C19 case (d), Priya's own finding) AND the CONTENTS of
    double-quoted string literals (DEVH-80, cf's own finding, empirically confirmed against real
    sandbox-exec) before matching. Both are the same class of gap: a raw substring/regex search
    over the whole file text cannot tell a real top-level `(deny ...)` rule apart from that same
    text sitting inside a comment or inside a string argument of a harmless `(allow ...)` rule --
    e.g. `(allow file-read* (literal "(deny file-write*)"))` denies nothing but would pass a
    search that doesn't know what a string literal is. String contents are stripped BEFORE
    comment-stripping so a ';' inside a string can't be misread as a real comment start either.
    """
    try:
        text = profile_path.read_text()
    except OSError:
        return False

    without_strings_chars = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            if ch == "\n":
                without_strings_chars.append(ch)
            continue
        if ch == '"':
            in_string = True
            continue
        without_strings_chars.append(ch)
    without_strings = "".join(without_strings_chars)

    active_text = "\n".join(line.split(";", 1)[0] for line in without_strings.splitlines())
    return bool(re.search(r"\(deny\b", active_text))


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

    if not _profile_can_deny(DENY_CAPABILITY_SB):
        hc.deny(
            f"guard_os_sandbox: this command matches a destructive-operation shape "
            f"({matched}), but the capability-removal profile at {DENY_CAPABILITY_SB} cannot "
            f"be shown to deny anything (missing, unreadable, or no deny rule present). "
            f"Refusing to substitute a control that would deny nothing while its rewrite still "
            f"bypasses the operator's own permission layer -- denying outright instead of "
            f"allowing an unprotected rewrite through."
        )
        return

    hc.rewrite(
        _wrapped_command(command),
        reason=(
            f"guard_os_sandbox: this command matches a destructive-operation shape "
            f"({matched}); its filesystem-write capability is removed at the OS level "
            f"regardless of how this command STRING is spelled or obfuscated."
        ),
    )


if __name__ == "__main__":
    hc.run(main)
