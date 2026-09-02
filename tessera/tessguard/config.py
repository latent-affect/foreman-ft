"""tessguard configuration: env-var overrides, the hard gate's window, and the frozen
shim-hash mapping the self-check compares against.

DB path resolution is deliberately NOT "just build a path string" -- Store(db_path) silently
creates an empty database and a blobs/ directory if the path doesn't already exist (verified
against tessera/store/store.py's __init__), which would make a wrong or missing DB path read as
"zero registered projects, nothing to flag" instead of a real error. resolve_db_path() below is
the one place that turns a possibly-wrong path into either a validated absolute path or a
DbPathError -- callers must go through it rather than constructing Store(config.TESSGUARD_DB_PATH)
directly.
"""

import os
from pathlib import Path

HARD_GATE_WINDOW_HOURS = int(os.environ.get("TESSGUARD_HARD_GATE_HOURS", "24"))

DEFAULT_DB_PATH_INTERNAL = "/path/to/ticket-system/data/tessera.db"

# DEVH-56: nothing ever persisted TESSGUARD_DB_PATH -- no shell profile, no install-time
# export -- so a genuinely fresh shell's git hook invocation always fell through to the
# unresolved placeholder above and failed closed unconditionally, regardless of ticket
# citation (Marcus Webb's ship-readiness re-verification reproduced this directly). Fixed
# with a per-clone marker file scripts/install-dev-harness.sh writes at install time
# (scripts/GOALS.json C6), read here as a fallback -- same convention
# bollard/tessera_resolver.py's own .foreman/tessera-prefix override file already
# establishes, not a new mechanism, and not an edit to the user's shell profile.
DB_PATH_MARKER_FILENAME = "tessguard-db-path"


def _installed_db_path_internal():
    """Real db path written into <repo_root>/.foreman/tessguard-db-path by the installer,
    or None if that file doesn't exist or is empty -- same "absent means fall through"
    contract tessera_resolver.py's own read_override() already establishes for its file.
    repo_root is derived from this module's own on-disk location (parents[2]: config.py ->
    tessguard/ -> tessera/ -> repo root), which resolves correctly no matter which
    worktree's checked-out copy of this file is actually running -- unlike an env var, a
    module's own __file__ is never ambiguous about which clone it belongs to."""
    repo_root = Path(__file__).resolve().parents[2]
    marker_path = repo_root / ".foreman" / DB_PATH_MARKER_FILENAME
    try:
        text = marker_path.read_text().strip()
    except OSError:
        return None
    return text or None


class DbPathError(Exception):
    """Raised when the configured TESSERA db path can't be resolved to a real, existing
    file. Never let this surface as "zero registered projects" -- that reads as a checked,
    clean result to callers, which it is not."""


def resolve_db_path():
    """Absolute, existence-checked path to the TESSERA db. Raises DbPathError rather than
    returning a path Store() would happily create fresh and empty.

    Resolution order: TESSGUARD_DB_PATH env var (still wins if set, e.g. for local
    testing) -> the installer-written marker file (DEVH-56) -> the unresolved placeholder
    (which then correctly raises DbPathError, unchanged from before this fix)."""
    raw = (
        os.environ.get("TESSGUARD_DB_PATH")
        or _installed_db_path_internal()
        or DEFAULT_DB_PATH_INTERNAL
    )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DbPathError(
            f"TESSGUARD_DB_PATH must be an absolute path, got relative path {raw!r} "
            f"(resolved against cwd would silently create a new db in the wrong place)."
        )
    if not path.is_file():
        raise DbPathError(f"no TESSERA db at {path} -- refusing to let Store() create one.")
    return path


# SHA-256 integrity hashes of the .githooks/ shim files themselves -- not credentials, not
# secrets. detect-secrets' Hex High Entropy String plugin flags long hex strings regardless of
# what they represent, so these two values are recorded as audited false positives in the
# repo-root .secrets.baseline (DEVH-11/GOALS.json C8); this comment is the justification a
# reader of that baseline needs, since the baseline file itself carries no free-text field.
# pre-commit and pre-push share a body today (activity-window gate). commit-msg is the
# T-7 binding and has its own hash. Entries stay independent so a one-file edit is caught.
EXPECTED_SHIM_SHAS = {
    "pre-commit": "195b2db4abe9a93608eaa865f20d7e58d3a5e8d65dbc26d08329454bce7b3c10",
    "pre-push": "195b2db4abe9a93608eaa865f20d7e58d3a5e8d65dbc26d08329454bce7b3c10",
    "commit-msg": "9cf9baeb74d86fa4bfedbbab7e8a413fc19ecb7cb85dc932dfeadd098c3fcf5b",
}
