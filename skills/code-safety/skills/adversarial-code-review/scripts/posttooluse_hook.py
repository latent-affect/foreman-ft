#!/usr/bin/env python3
"""
PostToolUse hook for adversarial-code-review.

Fires after every Write/Edit/MultiEdit, scans ONLY the just-touched file with
the fast mechanical half of scan.py (proxy-markers, silent-failures,
undefined-calls), and -- only if it finds something -- feeds a terse nudge
back into Claude's context via additionalContext so the same session sees it
immediately, while the code is still hot, instead of waiting for a separate
review pass to find it later.

This is the FAST TIER only. It does not run checks 4 (security) or 5
(optimization), and it does not run the judgment-heavy sub-patterns in
SKILL.md (sibling-convention consistency, aggregate-disclosure) -- those need
real reasoning, not a per-edit script, and belong to a full run of the
adversarial-code-review skill (subagents per check) before merge/handoff, not
to a hook that has to return in well under a second on every keystroke-level
edit.

Wiring (add to .claude/settings.json in the target project):

    {
      "hooks": {
        "PostToolUse": [
          {
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [
              {
                "type": "command",
                "command": "python3",
                "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/posttooluse_hook.py"]
              }
            ]
          }
        ]
      }
    }

Copy this file to <project>/.claude/hooks/posttooluse_hook.py (or point the
command at wherever the adversarial-code-review skill's scripts/ directory
actually lives -- both work, this file has no import dependency on being
colocated with scan.py, see the inline fallback below).

LIVE-RUN CONFIRMED 2026-08-09 (macOS, Claude Code v2.1.x): registered in a project
.claude/settings.json in exec form with an absolute path to this file, the matcher
matched, the scan ran, and additionalContext landed beside the tool result on the same
turn. Timeout behavior remains unexercised. See SKILL.md "Wiring a live hook" for the
exact registration -- and note you do NOT need to copy this file into the project;
an absolute path to it here keeps scan.py adjacent, which this file requires.

Superseded (kept for provenance): NOT YET VERIFIED against a live Claude Code session -- the JSON-parsing and
scan logic below is tested directly (see the skill's handoff notes), but the
actual hook firing, matcher behavior, and additionalContext delivery need a
real Claude Code run to confirm. Treat this as ready-to-try, not
ready-to-trust blind.
"""

import json
import pathlib
import sys
from pathlib import Path

# scan.py is expected to live next to this file (both under scripts/). If this
# hook gets copied somewhere else on its own, fall back to a same-directory
# import attempt, then fail loud rather than silently skipping scans.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import scan
except ImportError as e:
    # Exit 2, not 0. On PostToolUse, exit 0 sends stderr to the debug log only and
    # Claude never sees it -- a broken scanner import would mean this hook silently
    # does nothing on every edit, forever, while appearing installed and healthy.
    # That is the exact failure class this hook exists to catch, one level up.
    # Exit 2 on PostToolUse doesn't block anything (the tool already ran); it just
    # surfaces stderr to Claude so the breakage is visible.
    print(f"adversarial-code-review hook DISABLED: cannot import scan.py from "
          f"{Path(__file__).parent} ({e}). No scanning is happening on edits until this "
          f"is fixed -- do not read a lack of hook output as a clean scan.", file=sys.stderr)
    sys.exit(2)

FAST_SCAN_EXTENSIONS = {
    ".py", ".go", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".vue", ".svelte",
}
MAX_FILE_SIZE_BYTES = 2_000_000  # skip huge files so the hook stays fast
MAX_HITS_SHOWN = 8


HOOK_LABEL = "posttooluse_hook.py"


# Verdict recording. This hook emits its decision directly rather than through hook_common.run,
# so it never reached run()'s recorder: it fired constantly and wrote nothing, which the
# confusion matrix reads as NEVER INVOKED. That is the exact state the ledger exists to
# eliminate, and it survived because the file imports hook_common -- so a source scan for that
# name reported it instrumented. Only a side-effect test caught it.
# This file lives under skills/, not hooks/, so its own directory does NOT contain
# verdict_ledger. Walk up to the enclosing .claude instead -- the same resolution
# post_html_write_hook.py uses, and for the same reason. Adding the script's own parent was
# the first attempt and it failed open silently-but-for-stderr, which is the correct
# behaviour and is also exactly how this whole class of gap stays invisible.
def _hooks_dir():
    for parent in pathlib.Path(__file__).resolve().parents:
        if parent.name == ".claude":
            return parent / "hooks"
    return pathlib.Path.home() / ".claude" / "hooks"


sys.path.insert(0, str(_hooks_dir()))
try:
    import verdict_ledger
except Exception as _vl_exc:  # noqa: BLE001 -- the instrument must not disable the guard
    verdict_ledger = None
    print(f"[{HOOK_LABEL}] verdict ledger unavailable ({_vl_exc!r}); this hook still ran and "
          f"still made its decision, but nothing will be recorded, so its absence from the "
          f"ledger is not evidence about the guard.", file=sys.stderr)


def note_verdict(data, verdict, kind=None):
    if verdict_ledger is not None:
        verdict_ledger.record(data, verdict, kind=kind, handler_id=HOOK_LABEL)


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        # Malformed input isn't this hook's problem to raise -- exit clean.
        note_verdict({}, "error", "unparseable-stdin")
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        note_verdict(payload, "silent", "tool-not-in-scope")
        sys.exit(0)

    file_path = payload.get("tool_input", {}).get("file_path")
    if not file_path:
        note_verdict(payload, "silent", "no-file-path")
        sys.exit(0)

    target = Path(file_path)
    if not target.exists() or not target.is_file():
        note_verdict(payload, "error", "file-not-on-disk")
        sys.exit(0)
    if target.suffix.lower() not in FAST_SCAN_EXTENSIONS:
        note_verdict(payload, "silent", "extension-not-scannable")
        sys.exit(0)
    try:
        if target.stat().st_size > MAX_FILE_SIZE_BYTES:
            note_verdict(payload, "silent", "over-size-floor")
            sys.exit(0)
    except OSError:
        note_verdict(payload, "error", "stat-failed")
        sys.exit(0)

    findings = []

    try:
        proxy_hits, testPathHits, commentProseHits = scan.proxy_markers(target)
        findings.extend(f"[proxy-marker] {h}" for h in proxy_hits)
        # Comment-prose hits are reported but TAGGED, not merged into the main list
        # and not silently dropped. On the hyphy arm of the rigor pilot these were
        # 14 of 119 unique flagged items and every one was a comment describing the
        # design rather than a proxy in the code -- including one reading "no mocks"
        # flagged for "mock". Tagging lets a reviewer and the compliance judge tell
        # the two apart; dropping them would hide a real hit behind a heuristic.
        findings.extend(f"[proxy-marker-in-comment] {h}" for h in commentProseHits)
    except Exception as e:
        print(f"posttooluse_hook: proxy_markers failed on {target} -- {e}", file=sys.stderr)

    try:
        sf_hits = scan.silent_failures(target)
        findings.extend(f"[silent-failure] {h}" for h in sf_hits)
    except Exception as e:
        print(f"posttooluse_hook: silent_failures failed on {target} -- {e}", file=sys.stderr)

    if target.suffix == ".py":
        try:
            uc_hits = scan.undefined_calls(target)
            findings.extend(f"[undefined-call] {h}" for h in uc_hits)
        except Exception as e:
            print(f"posttooluse_hook: undefined_calls failed on {target} -- {e}", file=sys.stderr)

    if not findings:
        note_verdict(payload, "silent", "clean")
        sys.exit(0)  # clean file -- stay silent, don't nudge on every edit

    shown = findings[:MAX_HITS_SHOWN]
    remainder = len(findings) - len(shown)
    context_lines = [
        f"adversarial-code-review fast-tier scan of {file_path} found {len(findings)} "
        f"candidate(s) (checks 1-3 only, mechanical pass -- each needs a quick judgment call, "
        f"not an automatic finding):",
    ]
    context_lines.extend(f"  - {f}" for f in shown)
    if remainder > 0:
        context_lines.append(f"  ... and {remainder} more, run scripts/scan.py all {file_path} directly to see the rest")
    context_lines.append(
        "Checks 4-5 and the sibling-convention / aggregate-disclosure sub-patterns still need a "
        "full adversarial-code-review pass before this is ready to ship -- this hook only covers "
        "the fast mechanical tier."
    )
    additional_context = "\n".join(context_lines)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        },
        "systemMessage": f"adversarial-code-review: {len(findings)} candidate(s) flagged in {Path(file_path).name}",
    }
    print(json.dumps(output))
    note_verdict(payload, "fire", "candidates")
    sys.exit(0)


if __name__ == "__main__":
    main()
