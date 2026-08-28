#!/bin/bash
# neutralize_credentials.sh
#
# Runs a command with NO real credential reachable via a target project's
# declared credential-resolution config, before that command is allowed to
# touch the target's real credential-resolution code path.
#
# WHY THIS EXISTS: unsetting a credential environment variable does not
# neutralize a credential. CLI tools commonly resolve an API key from an env
# var FIRST and fall back to the platform credential store when it is absent,
# and that fallback does not consult environment variables at all. A harness
# that unsets the env var and believes it has produced a credential-free
# subprocess can therefore still hand it a live key, which then reaches
# whatever that subprocess writes -- logs, transcripts, artifacts.
#
# So both halves are required, and this script does both: unset the target's
# own declared env var AND deny the subprocess tree access to the Keychain
# (deny_keychain.sb). Either half alone leaves a working path to the key.
#
# WHAT THIS SCRIPT DOES:
#   1. Takes a target project's root path.
#   2. Looks up that root in the credential_targets/ registry to find the
#      target's OWN declared api_key_env / api_key_keychain-equivalent
#      config -- read fresh from the target's own reported config, never
#      hardcoded here.
#   3. Runs the given command with that one declared env var unset AND with
#      macOS Keychain access denied outright for that subprocess (and
#      anything it forks/execs), via sandbox-exec + deny_keychain.sb.
#   4. Never touches, creates, unlocks, or replaces the real user keychain
#      or its search list -- see deny_keychain.sb's header for why a
#      deny-only sandbox was chosen over a scratch keychain.
#   5. Fails loudly (non-zero exit, clear stderr) if step 2 can't determine
#      the target's credential config -- this script never silently runs a
#      command unprotected because it "couldn't figure out" the target.
#
# USAGE:
#   neutralize_credentials.sh <target-root> -- <command> [args...]
#
# EXAMPLE:
#   neutralize_credentials.sh ~/src/your-tool -- your-tool doctor
#   neutralize_credentials.sh ~/src/your-service -- curl -s http://127.0.0.1:8888/health
#
# EXIT CODES:
#   0        the wrapped command ran (under neutralization) and exited 0
#   <n>      the wrapped command ran (under neutralization) and exited <n>
#   2        usage error (bad arguments)
#   3        could not determine the target's credential-resolution config
#             -- REFUSED to run the command. Nothing was executed.
#   4        this machine's sandbox-exec is unavailable or broken -- REFUSED
#             to run the command unprotected rather than degrade silently.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="$SCRIPT_DIR/deny_keychain.sb"
TARGETS_DIR="$SCRIPT_DIR/credential_targets"

die() {
  echo "neutralize_credentials: $1" >&2
  exit "${2:-3}"
}

if [ "$#" -lt 3 ] || [ "$2" != "--" ]; then
  die "usage: neutralize_credentials.sh <target-root> -- <command> [args...]" 2
fi

TARGET_ROOT_RAW="$1"
shift 2
# remaining args ("$@") are the command to run

if [ ! -d "$TARGET_ROOT_RAW" ]; then
  die "target root '$TARGET_ROOT_RAW' is not a directory -- refusing to guess" 2
fi
TARGET_ROOT="$(cd "$TARGET_ROOT_RAW" && pwd -P)"

if [ ! -f "$PROFILE" ]; then
  die "Seatbelt profile missing at $PROFILE -- refusing to run unprotected" 4
fi
if ! command -v sandbox-exec >/dev/null 2>&1; then
  die "sandbox-exec is not available on this machine -- refusing to run unprotected" 4
fi

if [ ! -d "$TARGETS_DIR" ]; then
  die "credential_targets registry missing at $TARGETS_DIR -- refusing to run unprotected" 3
fi

# Walk every descriptor in credential_targets/, ask each whether it claims
# this target root, and stop at the first match. A descriptor is a plain
# shell file defining <name>_target_roots() and
# <name>_discover_credential_fields() -- see
# credential_targets/example.sh.template for the interface and a worked
# skeleton. Real descriptors name real local tools, so they are gitignored
# rather than committed; write your own from the template.
MATCHED_ENV_VAR=""
MATCHED_KEYCHAIN_ITEM=""
MATCHED_DESCRIPTOR=""

for descriptor in "$TARGETS_DIR"/*.sh; do
  [ -f "$descriptor" ] || continue
  base="$(basename "$descriptor" .sh)"

  # shellcheck disable=SC1090
  roots_file="$(mktemp "${TMPDIR:-/tmp}/neutcred_roots.XXXXXX")"
  chmod 600 "$roots_file"
  ( unset -f target_roots discover_credential_fields 2>/dev/null
    . "$descriptor"
    roots_fn="${base}_target_roots"
    if ! declare -f "$roots_fn" >/dev/null 2>&1; then
      exit 9
    fi
    "$roots_fn"
  ) > "$roots_file" 2>/dev/null
  roots_status=$?
  roots_output="$(cat "$roots_file" 2>/dev/null)"
  rm -f "$roots_file"

  if [ "$roots_status" -ne 0 ]; then
    continue
  fi

  matched=0
  while IFS= read -r root_candidate; do
    [ -n "$root_candidate" ] || continue
    root_real="$(cd "$root_candidate" 2>/dev/null && pwd -P)"
    [ -n "$root_real" ] || continue
    if [ "$TARGET_ROOT" = "$root_real" ]; then
      matched=1
      break
    fi
  done <<< "$roots_output"

  if [ "$matched" -eq 1 ]; then
    MATCHED_DESCRIPTOR="$descriptor"
    break
  fi
done

if [ -z "$MATCHED_DESCRIPTOR" ]; then
  die "no credential-resolution descriptor registered for target root '$TARGET_ROOT'. Refusing to run the command unprotected. Add a descriptor under $TARGETS_DIR/ first." 3
fi

# Source the matched descriptor and call its discovery function, in a
# subshell so its function definitions don't leak into this script.
discover_out="$(
  base="$(basename "$MATCHED_DESCRIPTOR" .sh)"
  # shellcheck disable=SC1090
  . "$MATCHED_DESCRIPTOR"
  fn="${base}_discover_credential_fields"
  if ! declare -f "$fn" >/dev/null 2>&1; then
    exit 1
  fi
  "$fn"
)"
discover_status=$?

if [ "$discover_status" -ne 0 ] || [ -z "$discover_out" ]; then
  die "descriptor '$MATCHED_DESCRIPTOR' could not determine target's declared api_key_env/api_key_keychain config (tool not installed, config unreadable, or unexpected shape). Refusing to run the command unprotected." 3
fi

ENV_VAR_NAME="$(printf '%s\n' "$discover_out" | sed -n '1p')"
KEYCHAIN_ITEM_NAME="$(printf '%s\n' "$discover_out" | sed -n '2p')"

if [ -z "$ENV_VAR_NAME" ]; then
  die "descriptor '$MATCHED_DESCRIPTOR' returned an empty env var name. Refusing to run the command unprotected." 3
fi

if [ "$#" -eq 0 ]; then
  die "no command given to run under neutralization" 2
fi

echo "neutralize_credentials: target=$TARGET_ROOT env_var_unset=$ENV_VAR_NAME keychain_item_would_have_been=${KEYCHAIN_ITEM_NAME:-<none declared>} keychain_access=DENIED(sandbox-exec)" >&2

# HOME_KEYCHAINS is passed as a sandbox parameter rather than baked into the profile:
# the profile is committed to a public repo and must not carry a real home directory,
# and a placeholder literal there would match nothing at runtime -- denying access to a
# path that does not exist while still reading as a protective rule.
if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ]; then
  die "HOME is unset or not a directory -- cannot scope the Keychain deny rule, refusing to run unprotected" 4
fi

exec sandbox-exec -f "$PROFILE" -D HOME_KEYCHAINS="$HOME/Library/Keychains" \
  env -u "$ENV_VAR_NAME" "$@"
