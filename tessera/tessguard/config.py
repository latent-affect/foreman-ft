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


class DbPathError(Exception):
    """Raised when the configured TESSERA db path can't be resolved to a real, existing
    file. Never let this surface as "zero registered projects" -- that reads as a checked,
    clean result to callers, which it is not."""


def resolve_db_path():
    """Absolute, existence-checked path to the TESSERA db. Raises DbPathError rather than
    returning a path Store() would happily create fresh and empty."""
    raw = os.environ.get("TESSGUARD_DB_PATH", DEFAULT_DB_PATH_INTERNAL)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DbPathError(
            f"TESSGUARD_DB_PATH must be an absolute path, got relative path {raw!r} "
            f"(resolved against cwd would silently create a new db in the wrong place)."
        )
    if not path.is_file():
        raise DbPathError(f"no TESSERA db at {path} -- refusing to let Store() create one.")
    return path


# pre-commit and pre-push share a body today (activity-window gate). commit-msg is the
# T-7 binding and has its own hash. Entries stay independent so a one-file edit is caught.
EXPECTED_SHIM_SHAS = {
    "pre-commit": "195b2db4abe9a93608eaa865f20d7e58d3a5e8d65dbc26d08329454bce7b3c10",
    "pre-push": "195b2db4abe9a93608eaa865f20d7e58d3a5e8d65dbc26d08329454bce7b3c10",
    "commit-msg": "9cf9baeb74d86fa4bfedbbab7e8a413fc19ecb7cb85dc932dfeadd098c3fcf5b",
}
