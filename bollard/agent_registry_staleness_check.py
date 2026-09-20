#!/usr/bin/env python3
"""REQ-23 (Foreman v2.0 PRD): encodes LESSONS-LEARNED.md Lesson 17 ("Claude Code custom
subagent files (`.claude/agents/*.md`) are not hot-reloaded mid-session") as a mechanical
check, rather than relying on every session re-reading the full document.

Real mechanism, cited from the lesson entry directly: an already-running session's agent
registry snapshots each custom subagent type once, at session start, and never re-reads its
`.md` definition file again -- editing `~/.claude/agents/<name>.md` (or a project-local
`.claude/agents/<name>.md`) mid-session has no effect on that session's own later dispatches
of that agent type, with no error or warning surfaced anywhere. The lesson's own worked
example: several rounds of "the fix isn't working" before the real cause (registry
staleness, not the fix) was identified.

Real, live instance that motivated building this rather than treating it as hypothetical:
this exact session edited `~/.claude/agents/priya-desai.md` (REQ-7a's model-pin fix) after
this session's own start, and — before this check existed — a later `Agent` dispatch of
`priya-desai` in the SAME session would have silently used the pre-edit definition with no
warning, the identical trap Lesson 17 describes.

Scope, disclosed: this checks ONE proxy for "session start" -- the birth time (`st_birthtime`
on APFS/macOS) of the session's own transcript JSONL file at `transcript_path`, which is
created once at session start and never recreated for the life of that session. If
`st_birthtime` is unavailable (non-macOS filesystem), falls back to `st_ctime`, which is a
weaker proxy (can be updated by some non-content operations) but still fail-open-safe here:
this hook only ever ASKs, never denies, so a wrong proxy costs an unnecessary confirmation
prompt, not a wrong denial.
"""
from pathlib import Path

import hook_common as hc

AGENT_DIRS = (
    Path.home() / ".claude" / "agents",
)


def _project_agent_dir(cwd):
    if not cwd:
        return None
    return Path(cwd) / ".claude" / "agents"


def _resolve_agent_md(subagent_type, cwd):
    """Returns the Path to the agent .md file that would actually be loaded, or None if no
    custom definition exists for this type (built-in types like 'general-purpose', 'fork',
    'claude' have no .md file and are never stale in this sense)."""
    candidates = list(AGENT_DIRS)
    project_dir = _project_agent_dir(cwd)
    if project_dir:
        candidates = [project_dir] + candidates
    for d in candidates:
        p = d / f"{subagent_type}.md"
        if p.is_file():
            return p
    return None


def _session_start_epoch(transcript_path):
    if not transcript_path:
        return None
    p = Path(transcript_path)
    try:
        st = p.stat()
    except OSError:
        return None
    return getattr(st, "st_birthtime", None) or st.st_ctime


def main(data):
    if data.get("tool_name") != "Agent":
        return
    tool_input = data.get("tool_input") or {}
    subagent_type = tool_input.get("subagent_type")
    if not subagent_type or subagent_type == "fork":
        # 'fork' inherits the live session, not a separate registry snapshot -- not this trap.
        return

    agent_md = _resolve_agent_md(subagent_type, data.get("cwd"))
    if agent_md is None:
        return

    session_start = _session_start_epoch(data.get("transcript_path"))
    if session_start is None:
        return

    try:
        edited_at = agent_md.stat().st_mtime
    except OSError:
        return

    if edited_at <= session_start:
        return

    hc.set_rule("lesson-17-agent-registry-staleness")
    hc.ask(
        f"{agent_md} was edited after this session started (Lesson 17, "
        f"~/.claude/LESSONS-LEARNED.md): Claude Code's agent registry snapshots a custom "
        f"subagent's .md definition once at session start and does not hot-reload it. "
        f"Dispatching '{subagent_type}' now will very likely use the PRE-EDIT definition, "
        f"not the file on disk. If you need the edited version to take effect, start a new "
        f"session (or use a plain CLI invocation instead of a custom agent type, per the "
        f"lesson's own robust alternative). Confirm to proceed anyway with the stale version."
    )


if __name__ == "__main__":
    hc.run(main)
