#!/usr/bin/env python3
"""REQ-12 (Foreman v2.0 PRD): PreToolUse hard-deny on any direct Write/Edit/NotebookEdit or
Bash write targeting `.foreman/review-events.jsonl` -- the ONLY legitimate writer is
stamp_review_event.py, which computes its own hash independently rather than trusting one
embedded in a reviewer-output artifact's own text.

Same coverage discipline REQ-3's guard_prodconfig.py already established for a hard-denied
path: direct Edit/Write/NotebookEdit/MultiEdit file_path, Bash-extracted write targets
(component_coupling.extract_bash_write_targets, which resolves symlinks), AND
a raw-text backstop that resolves each write-shaped operator's OWN destination against the
ledger path (CHV2-49) -- so a symlink swap or a shell redirect that never resolves to the real
path in extract_bash_write_targets' own filesystem walk still gets caught, matching FORE-147's own
symlink-bypass finding for the exact same reason.

KNOWN, ACCEPTED LIMIT (CHV2-50, decision not oversight): a Bash command that invokes a SCRIPT which writes to the ledger from inside its own runtime -- not as an operator on the command line this guard ever sees -- is not caught by any layer here, and cannot be: neither the filesystem walk nor the text backstop ever has a write operator on the command line to resolve or match in this shape, because it never reaches the command line at all. Filesystem-layer enforcement (an append-only flag on the live ledger) was considered and declined -- not because the technique is unproven: this repository already has a working reference, audit_lib.py, which applies UF_APPEND (`chflags uappnd`) to its own ledger while O_APPEND still honors legitimate appends. The decline is blast radius alone: applying it to a SECOND live ledger across three repos at this hour, with a real risk of locking out the legitimate writer (stamp_review_event.py), is disproportionate to a defect already disclosed and understood. Accepted per PDP 11.1; audit_lib.py is the reference implementation for whoever revisits this.

Dana Okafor's real finding (REQ-12 review round 1, 2026-08-27): the original branch omitted
`MultiEdit`, inherited faithfully from modeling this guard on guard_prodconfig.py's own
pre-fix code -- a real MultiEdit payload targeting this ledger passed through completely
unblocked. Fixed in both files."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import component_coupling as cc  # noqa: E402
import hook_common as hc  # noqa: E402

RULE_ID = "FOREMAN-REVIEW-LEDGER-GUARD"
LEDGER_NAME = "review-events.jsonl"

# CHV2-49: dd has no shared pattern in component_coupling.py (extract_bash_write_targets does
# not cover dd). Captures the of= value so its OWN destination can be checked, not just presence.
BASH_DD_RE = re.compile(r"\bdd\b[^\n]*\bof=(\S+)")


def _is_ledger_path(path_str):
    return bool(path_str) and Path(path_str).name == LEDGER_NAME


# CHV2-94. This guard owns its own cp/mv trigger instead of importing one.
#
# The shared component_coupling.BASH_CPMV_RE is being REMOVED by the FORE-575 cluster (FORE-591),
# because for extract_bash_write_targets -- a SCOPE DETECTOR -- the loose `\b(?:cp|mv)\b` search
# produces false targets on `java -cp`, `git mv`, `docker cp`, `aws s3 cp`, `kubectl cp`. There,
# over-blocking is noise and anchoring to tokens[0] is the right trade.
#
# THIS HOOK'S RISK PROFILE IS THE REVERSE, which is why it must not inherit that trade. It is a
# GUARD on a protected path: over-blocking is a spurious deny, under-blocking is a BYPASS.
# Measured, not argued -- against the anchored trigger:
#
#     git mv notes.txt <protected path>      -> anchored: NO trigger.  A real write, missed.
#     docker cp c:/x <protected path>        -> anchored: NO trigger.  A real write, missed.
#     java -cp lib.jar Main <protected path> -> anchored: correctly no trigger (noise removed).
#
# So adopting the anchored form here would trade two noise cases for two real bypasses on a path
# this hook exists to protect. The loose form is the correct one FOR A GUARD, and keeping it
# local is what lets the shared module fix its own defect without importing that fix into a hook
# where it is not a fix.
#
# Local rather than aliased, deliberately: an alias in component_coupling would keep this hook
# coupled to a constant that module is deleting, and would leave a permanently-deprecated name
# pointing at a regex removed for being wrong. This way the guard works against the current
# shared module AND the post-cluster one, with no dependency either way.
GUARD_CPMV_RE = re.compile(r"\b(?:cp|mv)\b")


def _bash_text_mentions_ledger_write(command):
    """Whether `command` has a write-shaped operator whose OWN destination is the ledger --
    not whether the ledger's name and some write-operator both appear anywhere on the line.
    CHV2-49: the prior version was true whenever both matched anywhere at all, and produced
    three real false positives in its first hour live -- one where both strings appeared only
    inside a quoted -m argument (never live shell syntax), two where the ledger was a READ
    command's own argument while the write operator's real destination was different.

    Reuses component_coupling's quote-awareness (quoted_spans/inside_quotes, promoted public
    for this) and per-operator destination regexes rather than re-deriving parsing this project
    already hardened across FORE-1/14/15/21/23. Same segment split as extract_bash_write_targets,
    duplicated as one line rather than importing the whole function -- this codebase's own
    established convention for a self-contained hook. Does not resolve against the filesystem;
    _is_ledger_path() is a pure basename check, so none of that resolution machinery is needed.
    """
    for seg in re.split(r"[;&|\n]", command):
        spans = cc.quoted_spans(seg)
        for m in cc.BASH_REDIRECT_RE.finditer(seg):
            if not cc.inside_quotes(m.start(), spans) and _is_ledger_path(m.group(1).strip('"').strip("'")):
                return True
        m = cc.BASH_TEE_RE.search(seg)
        if m and not cc.inside_quotes(m.start(), spans) and _is_ledger_path(m.group(1).strip('"').strip("'")):
            return True
        cpmv = GUARD_CPMV_RE.search(seg)
        sed = cc.BASH_SED_INPLACE_RE.search(seg)
        trigger = cpmv or sed
        if trigger and not cc.inside_quotes(trigger.start(), spans):
            tokens = re.findall(r'"[^"]+"|\'[^\']+\'|\S+', seg)
            if tokens and not tokens[-1].startswith("-") and _is_ledger_path(tokens[-1].strip('"').strip("'")):
                return True
        dd = BASH_DD_RE.search(seg)
        if dd and not cc.inside_quotes(dd.start(), spans) and _is_ledger_path(dd.group(1).strip('"').strip("'")):
            return True
    return False


def main(data):
    tool_name = data.get("tool_name")
    sid, cwd = data.get("session_id"), data.get("cwd")

    if tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        fp = hc.target_path(data.get("tool_input"))
        if fp and _is_ledger_path(fp):
            hc.audit("SAFETY_DENY", {"guard": "review_events_ledger", "reason":
                      "direct-write-to-review-ledger", "file_path": fp}, sid, cwd, severity="high")
            hc.set_rule(f"{RULE_ID}:direct-edit-denied")
            hc.deny(
                f"Blocked direct write to {Path(fp).name}: .foreman/review-events.jsonl is an "
                f"append-only ledger, written only by stamp_review_event.py (REQ-12), never "
                f"edited directly. Run stamp_review_event.py if you need to record a real "
                f"review event."
            )
            return
        return

    if tool_name == "Bash":
        command = (data.get("tool_input") or {}).get("command", "") or ""
        if not command:
            return
        for target in cc.extract_bash_write_targets(command, cwd):
            if _is_ledger_path(str(target)):
                hc.audit("SAFETY_DENY", {"guard": "review_events_ledger", "reason":
                          "direct-bash-write-to-review-ledger", "command": command[:500]},
                         sid, cwd, severity="high")
                hc.set_rule(f"{RULE_ID}:bash-write-denied")
                hc.deny(
                    "Blocked Bash write to review-events.jsonl: this append-only ledger is "
                    "written only by stamp_review_event.py (REQ-12), never edited directly."
                )
                return
        if _bash_text_mentions_ledger_write(command):
            hc.audit("SAFETY_DENY", {"guard": "review_events_ledger", "reason":
                      "bash-text-match-review-ledger", "command": command[:500]},
                     sid, cwd, severity="high")
            hc.set_rule(f"{RULE_ID}:bash-text-match-denied")
            hc.deny(
                "Blocked Bash command: an operator in it (redirect/tee/cp/mv/sed -i/dd) "
                "resolves its own write destination to review-events.jsonl. This append-only "
                "ledger is written only by stamp_review_event.py (REQ-12). If this command "
                "targets a different, unrelated file that merely shares this basename, rename "
                "it or run this command yourself in a terminal."
            )
            return


if __name__ == "__main__":
    hc.run(main)
