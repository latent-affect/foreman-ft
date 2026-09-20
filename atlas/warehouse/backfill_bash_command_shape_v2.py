"""Backfills `bash_command_shape_v2` (ARCHITECTURE.md section 31, ATLASSN-95) over every existing
Bash call in `session_tool_call` and `subagent_tool_call`.

Structurally identical to `backfill_bash_command_shape.py` -- same `(transcript_kind, call_id)`
UNIQUE key, same `INSERT OR IGNORE` idempotency, same one-row-per-Bash-call invariant -- against
`bash_command_shape_v2` instead. Never touches `bash_command_shape` (migration 8): that table is
frozen evidence of what extractor_version 1 saw (section 31.1), and this module's first real run
is a FULL re-extraction over the whole Bash-call population, not an incremental one, precisely so
`bash_command_shape_v2` covers the same population `bash_command_shape` does rather than only
calls that arrived after this landed.

Deliberately hardcodes `"2"` rather than importing `bash_shape.EXTRACTOR_VERSION` -- this module's
only job is populating the v2 table, and importing the live constant would silently start writing
version "3" rows here the day a v3 feature lands, into a table whose whole point is being the v2
vector. A v3 gets its own parallel table and its own backfill module, same reasoning as section
31.1.
"""

import json

from .bash_shape import extract_shape

_V2_VERSION = "2"

_SOURCE_TABLES = (
    ("session", "session_tool_call"),
    ("subagent", "subagent_tool_call"),
)

_INSERT_SQL = """
INSERT OR IGNORE INTO bash_command_shape_v2 (
    transcript_kind, call_id, tool_use_id, session_id, ts, token_count, parse_failure,
    has_chr_paren, has_base64_decode, has_eval_word, has_getattr_dunder_import,
    has_string_concat_import, has_dollar_var_command_position, has_heredoc_into_interpreter,
    has_python_c_os_subprocess, has_quote_splice, has_cmd_pos_var_indirection, extractor_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    "has_quote_splice": 0,
    "has_cmd_pos_var_indirection": 0,
}


def _extract_command(tool_input_json):
    """Same rule as backfill_bash_command_shape.py's helper of the same name -- returns the
    `command` string from a Bash call's `tool_input_json`, or None if it is missing, non-JSON, or
    not a dict with a string `command` field."""
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
    """Backfills every Bash call not yet present in bash_command_shape_v2. Returns a dict of
    per-source-table counts: {"session": {"scanned": n, "inserted": n}, "subagent": {...}}."""
    counts = {}
    for kind, table in _SOURCE_TABLES:
        rows = conn.execute(
            f"SELECT c.call_id, c.tool_use_id, c.session_id, c.ts, c.tool_input_json "
            f"FROM {table} c "
            f"LEFT JOIN bash_command_shape_v2 s "
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
                    features["has_quote_splice"],
                    features["has_cmd_pos_var_indirection"],
                    _V2_VERSION,
                ),
            )
            if cur.rowcount:
                inserted += 1
        counts[kind] = {"scanned": scanned, "inserted": inserted}

    conn.commit()
    return counts
