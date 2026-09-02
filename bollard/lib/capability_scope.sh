#!/bin/bash
# capability_scope.sh
#
# Runs a shell command with the destructive-filesystem-write CAPABILITY removed at the OS
# level, via sandbox-exec + deny_capability.sb. Generalizes neutralize_credentials.sh's pattern
# (a single named subprocess tree, wrapped in a Seatbelt deny-profile) from credential denial to
# command-capability scoping -- PRD.md R13's "capability-scoping ... at the OS level" layer,
# TESSERA DEVH-61 through DEVH-64, PRD-DELTA-R14-R15.md section 3 (option C).
#
# UNLIKE neutralize_credentials.sh, this script does not look up a per-target descriptor --
# there is one declared capability (destructive filesystem writes), not a per-project credential
# config, so there is nothing to resolve beyond the command itself.
#
# WHAT THIS SCRIPT DOES:
#   1. Takes a shell command (a single string, the same shape a Bash tool call already carries).
#   2. Runs it via `/bin/sh -c` inside a sandbox-exec wrapper using deny_capability.sb, so any
#      open/create/unlink/rename syscall the command tries fails at the kernel, regardless of
#      how the command string was assembled.
#   3. Never touches, weakens, or bypasses deny_capability.sb -- if the profile is missing or
#      sandbox-exec is unavailable, this script refuses to run the command at all rather than
#      degrade to running it unprotected.
#
# USAGE:
#   capability_scope.sh -- <shell-command-string>
#
# EXAMPLE:
#   capability_scope.sh -- 'rm -rf /some/path'
#   capability_scope.sh -- 'echo hello'
#
# EXIT CODES:
#   <n>      the wrapped command ran (under the capability-removal sandbox) and exited <n>
#   2        usage error (bad arguments)
#   4        this machine's sandbox-exec is unavailable, or the profile is missing -- REFUSED
#             to run the command unprotected rather than degrade silently.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="$SCRIPT_DIR/deny_capability.sb"

die() {
  echo "capability_scope: $1" >&2
  exit "${2:-2}"
}

if [ "$#" -lt 2 ] || [ "$1" != "--" ]; then
  die "usage: capability_scope.sh -- <shell-command-string>" 2
fi
shift 1

if [ ! -f "$PROFILE" ]; then
  die "Seatbelt profile missing at $PROFILE -- refusing to run unprotected" 4
fi
if ! command -v sandbox-exec >/dev/null 2>&1; then
  die "sandbox-exec is not available on this machine -- refusing to run unprotected" 4
fi

# Remaining args are the shell command, as one string (a Bash tool call's tool_input.command is
# already exactly this shape). Joined with a space rather than requiring the caller to pass a
# single pre-quoted argument, so `capability_scope.sh -- rm -rf /some/path` and
# `capability_scope.sh -- 'rm -rf /some/path'` behave identically.
COMMAND="$*"

exec sandbox-exec -f "$PROFILE" /bin/sh -c "$COMMAND"
