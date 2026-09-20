#!/usr/bin/env python3
"""PDP marker hook (FORE-170). PostToolUse on ScheduleWakeup: record that this session
really scheduled its own continuation, so pdp_turn_gate.py can check REQ-37 against an
artifact instead of parsing a transcript.

Marker over transcript-parsing on purpose: at Stop time the session transcript is still
being written, is large, and its format is not a contract. A marker file is small, is
written by the harness's own PostToolUse path, and cannot disagree with itself.

Writes .foreman/pdp-wakeup-marker.json in the project root. Never blocks anything; a
failure here degrades to "no marker", which pdp_turn_gate.py reads as "not scheduled" --
the safe direction, since the worst case is one extra reminder.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402


def main(data):
    if data.get("tool_name") != "ScheduleWakeup":
        return
    cwd = data.get("cwd") or ""
    if not cwd:
        return
    root = Path(cwd)
    if not (root / ".foreman").is_dir():
        return
    payload = {
        "session_id": data.get("session_id"),
        "written_at": time.time(),
        "tool_input": (data.get("tool_input") or {}),
    }
    try:
        (root / ".foreman" / "pdp-wakeup-marker.json").write_text(json.dumps(payload, indent=2))
    except OSError:
        return  # no marker is the safe direction


if __name__ == "__main__":
    hc.run(main)
