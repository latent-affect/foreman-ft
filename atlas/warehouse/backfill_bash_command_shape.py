"""Backfills `bash_command_shape` (ARCHITECTURE.md section 24, ATLASSN-61) over every existing
Bash call in `session_tool_call` and `subagent_tool_call`.

FROZEN AT EXTRACTOR_VERSION 1 (ATLASSN-95, ARCHITECTURE.md section 31.1). This table's entire
purpose per migration 8's own comment is to preserve "evidence of what the old version actually
flagged" -- so this module writes the literal `"1"` below, not `bash_shape.EXTRACTOR_VERSION`
(which now reads `"2"`, since `extract_shape()` is a single function with no v1/v2 split). Importing
the live constant here would let a future incremental run silently tag new rows `extractor_version
= '2'` in the one table whose invariant is "only ever v1." bash_command_shape_v2
(`backfill_bash_command_shape_v2.py`) is the v2 backfill target going forward.

Idempotent: the `(transcript_kind, call_id)` UNIQUE constraint plus `INSERT OR IGNORE` means
re-running this against a warehouse that already has some or all rows backfilled only inserts
the missing ones -- safe to run on a cron alongside ingest, not just once.

One row per Bash call, always -- `parse_failure=1` covers BOTH of two distinct causes,
deliberately conflated for this QA-fork phase and named here rather than left implicit: a
`shlex.split` failure on real command text (bash_shape's own signal), and a missing/malformed
`command` field in `tool_input_json` (this module's `_extract_command` returning None, e.g. an
empty or non-JSON payload). Both mean "no reliable feature vector for this call," and conflating
them keeps a simple row-count invariant -- exactly one `bash_command_shape` row per Bash call --
that is directly checkable with a COUNT(*) comparison. Splitting them into separate columns is a
candidate refinement if the distinction turns out to matter once this runs against real data.
"""

import json

from .bash_shape import extract_shape

_FROZEN_V1_VERSION = "1"

_SOURCE_TABLES = (
    ("session", "session_tool_call"),
    ("subagent", "subagent_tool_call"),
)

_INSERT_SQL = """
INSERT OR IGNORE INTO bash_command_shape (
    transcript_kind, call_id, tool_use_id, session_id, ts, token_count, parse_failure,
    has_chr_paren, has_base64_decode, has_eval_word, has_getattr_dunder_import,
    has_string_concat_import, has_dollar_var_command_position, has_heredoc_into_interpreter,
    has_python_c_os_subprocess, extractor_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_EMPTY_FEATURES = {
    "token_count": None,
    "parse_failure": 1,
    "has_chr_paren": 0,
    "has_base64_decode": 0,
    "has_eval_word": 0,
    "has_getattr_dunder_import": 0,
    "has_string_concat_import": 0,
    "has_dollar_var_command_position": 0,
    "has_heredoc_into_interpreter": 0,
    "has_python_c_os_subprocess": 0,
}


def _extract_command(tool_input_json):
    """Returns the `command` string from a Bash call's `tool_input_json`, or None if it is
    missing, non-JSON, or not a dict with a string `command` field."""
    if not tool_input_json:
        return None
    try:
        obj = json.loads(tool_input_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    command = obj.get("command")
    return command if isinstance(command, str) else None


def run(conn):
    """Backfills every Bash call not yet present in bash_command_shape. Returns a dict of
    per-source-table counts: {"session": {"scanned": n, "inserted": n}, "subagent": {...}}."""
    counts = {}
    for kind, table in _SOURCE_TABLES:
        rows = conn.execute(
            f"SELECT c.call_id, c.tool_use_id, c.session_id, c.ts, c.tool_input_json "
            f"FROM {table} c "
            f"LEFT JOIN bash_command_shape s "
            f"  ON s.transcript_kind = ? AND s.call_id = c.call_id "
            f"WHERE c.tool_name = 'Bash' AND s.shape_id IS NULL",
            (kind,),
        ).fetchall()

        scanned = 0
        inserted = 0
        for call_id, tool_use_id, session_id, ts, tool_input_json in rows:
            scanned += 1
            command = _extract_command(tool_input_json)
            features = extract_shape(command) if command is not None else dict(_EMPTY_FEATURES)
            cur = conn.execute(
                _INSERT_SQL,
                (
                    kind, call_id, tool_use_id, session_id, ts,
                    features["token_count"], features["parse_failure"],
                    features["has_chr_paren"], features["has_base64_decode"],
                    features["has_eval_word"], features["has_getattr_dunder_import"],
                    features["has_string_concat_import"],
                    features["has_dollar_var_command_position"],
                    features["has_heredoc_into_interpreter"],
                    features["has_python_c_os_subprocess"],
                    _FROZEN_V1_VERSION,
                ),
            )
            if cur.rowcount:
                inserted += 1
        counts[kind] = {"scanned": scanned, "inserted": inserted}

    conn.commit()
    return counts
