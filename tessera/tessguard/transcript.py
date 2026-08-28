"""Reads Claude Code's own session transcripts, read-only. This is harness-owned filesystem
state, not a tessera component's output -- no interface edge for it (see ARCHITECTURE.md).

Transcripts can reach into the tens of MB (largest seen on this machine: ~51MB) -- every
function here streams line by line, never reads a file whole.

Timestamps in transcripts are millisecond-precision ISO 8601 (`...147Z`); tessera's own
`events.created_at` is microsecond-precision (`...753809Z`). Comparing them as plain strings
sorts backwards inside a shared millisecond (`'8' < 'Z'`) -- every comparison in this module and
in event_activity.py parses to `datetime` first.
"""

import json
import re
from pathlib import Path

WRITE_TOOL_NAMES = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

NON_ALNUM_RE_INTERNAL = re.compile(r"[^A-Za-z0-9]")


class TranscriptParseError(Exception):
    """Raised when a transcript file has content but yields zero parseable JSON lines --
    the same class of risk as an unreachable DB path (config.py's DbPathError): a fully
    unparseable file must not silently read as '0 edits, looks clean,' which is exactly
    the wrong-input-reads-as-a-real-result failure this component exists to catch. A file
    with a FEW malformed lines (e.g. one truncated trailing write) is not this case -- only
    a file with zero successfully-parsed lines raises."""


def hyphenated_project_dir(cwd):
    """Reproduces Claude Code's own transcript-directory naming: every non-alphanumeric
    character (not only '/' and space -- underscore, dot, and '+' collapse too, confirmed
    against 3,176 real transcript directory names with zero exceptions) becomes '-'."""
    hyphenated = NON_ALNUM_RE_INTERNAL.sub("-", str(cwd))
    return Path.home() / ".claude" / "projects" / hyphenated


def tool_input_path_internal(tool_input):
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def scan_internal(jsonl_path, repo_root_path):
    """Single streaming pass over one transcript file: returns a dict with first_cwd,
    in_repo_edits, session_start (earliest record timestamp), session_end (latest record
    timestamp) -- all four consumers (OR-rule validation, edit counting, and the event-
    coverage time window) share this one read rather than re-scanning the file per need.
    Raises TranscriptParseError if the file has content but not one line parses -- a
    handful of malformed lines (a truncated trailing write is normal for an actively-
    written JSONL log) is not this case; only a completely unparseable file is, since that
    would otherwise read as a real, checked '0 edits.'"""
    first_cwd = None
    in_repo_edits = 0
    session_start = None
    session_end = None
    total_lines = 0
    parsed_lines = 0
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            parsed_lines += 1
            if first_cwd is None and record.get("cwd"):
                first_cwd = record["cwd"]
            ts = record.get("timestamp")
            if ts:
                if session_start is None or ts < session_start:
                    session_start = ts
                if session_end is None or ts > session_end:
                    session_end = ts
            if record.get("type") != "assistant":
                continue
            content = (record.get("message") or {}).get("content") or []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in WRITE_TOOL_NAMES:
                    continue
                raw_path = tool_input_path_internal(block.get("input") or {})
                if not raw_path:
                    continue
                try:
                    resolved = Path(raw_path).resolve()
                except OSError:
                    # A file_path Python's own resolver rejects outright (embedded NUL,
                    # OS-level path error) -- excluded from the in-repo count, the same
                    # direction as excluding Bash entirely: undercounts rather than risks
                    # a false in-repo match on a path that couldn't be verified.
                    continue
                if resolved == repo_root_path or repo_root_path in resolved.parents:
                    in_repo_edits += 1
    if total_lines > 0 and parsed_lines == 0:
        raise TranscriptParseError(
            f"{jsonl_path} has {total_lines} non-empty line(s) but zero parsed as JSON -- "
            f"refusing to treat this as a valid, checked '0 in-repo edits' result."
        )
    return {
        "first_cwd": first_cwd,
        "in_repo_edits": in_repo_edits,
        "session_start": session_start,
        "session_end": session_end,
    }


def transcript_summary(jsonl_path, repo_root):
    """Dict with first_cwd, in_repo_edits, session_start, session_end for one transcript,
    edits scoped to repo_root via realpath. repo_root need not be a git repository (see
    project_resolve.py's docstring on GIF). Timestamps are the raw millisecond-precision
    ISO strings as recorded -- parse before comparing against store's microsecond-precision
    timestamps (see module docstring)."""
    repo_root_path = Path(repo_root).resolve()
    return scan_internal(Path(jsonl_path), repo_root_path)


def subagent_transcript_paths(parent_jsonl_path):
    """Subagent transcripts for a session live one directory deeper than the parent:
    <hyphenated-path>/<session-id>/subagents/agent-*.jsonl. Real and in daily use on this
    machine (851 found, 182 containing real Edit/Write/MultiEdit/NotebookEdit calls, 1,341
    such calls total, measured during the pass-6 architecture review) -- not covered by a
    scan of the parent file alone."""
    parent = Path(parent_jsonl_path)
    session_id = parent.stem
    subagent_dir = parent.parent / session_id / "subagents"
    if not subagent_dir.is_dir():
        return []
    return sorted(subagent_dir.glob("agent-*.jsonl"))


def in_repo_edit_count_including_subagents(jsonl_path, repo_root):
    """Total in-repo edit count across the parent transcript AND its subagent transcripts.
    Subagent cwd values are NOT used for the OR-rule validation -- a subagent can run at an
    unrelated cwd (observed: one project's subagent ran at /path/to/home/.claude) -- only the
    PARENT transcript's first cwd participates in that check; subagent transcripts are
    scanned for in-repo edits unconditionally, the same way a Bash-issued write would be if
    it weren't already excluded from this count entirely."""
    repo_root_path = Path(repo_root).resolve()
    total = scan_internal(Path(jsonl_path), repo_root_path)["in_repo_edits"]
    for sub_path in subagent_transcript_paths(jsonl_path):
        total += scan_internal(sub_path, repo_root_path)["in_repo_edits"]
    return total
