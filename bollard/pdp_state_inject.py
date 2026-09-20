#!/usr/bin/env python3
"""CHV2-7: Class B per-turn state injector -- SessionStart + UserPromptSubmit.

HOOKS-VS-PDP-AND-SUMMARY-BLOAT-20260904.md Part 1 section 1.2's operationalization item 1:
a `UserPromptSubmit` re-injection is "never more than one turn old," which is the only
delivery class immune to "forgets CLAUDE.md halfway through" for a rule with no tool-call
trigger of its own. `SessionStart` gets the same block so turn 1 isn't blind.

Regenerated from files that already exist on every call -- NOT from a ledger, because
`.foreman/ledger.jsonl` does not exist in any project yet (PDP.md section 15). Every line
below is either read fresh from disk/TESSERA on this call, or a static pointer; nothing here
is cached or hand-maintained. That is also why this file is deliberately thin: it is a
freshness ping, matching PDP.md section 14's own description of the injected block ("Short
by design... A session that needs a specific rule reads this document").

Capped at MAX_LINES (15, PDP.md section 14's own budget). Every helper below returns exactly
one line so the cap is enforced structurally by `build_block`'s own assertion, not by hoping
each helper stayed short.

Honesty boundary, stated rather than silently worked around: `.foreman/pipeline.json` and
the ledger do not exist in this project (or any project) yet, so this hook cannot report a
real "closed concept, architecture (check) open design-scope" stage line the way PDP.md
section 14's own example block does -- that data does not exist to read. What it reports
instead is the concrete, file-verifiable state that DOES exist today: whether ARCHITECTURE.md
and a bound ARCHITECTURE-REVIEW.md exist, how many declared components have frozen
GOALS.json, the orchestrator claim, stop-declared.json's age, open Provisional Decision
Records, TESSERA ticket counts, and git status. This is a real, honest, narrower substitute
for the pipeline-driven design in PDP.md section 6/14 -- not a stand-in that pretends to be
that design.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tessera_resolver"))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402
import foreman_evidence as fe  # noqa: E402
import tessera_resolver as tr  # noqa: E402

RULE_ID = "PDP-STATE-INJECT"
MAX_LINES = 15

# Same convention escalation_drift_gate.py's ALREADY_CHECKED uses (PDP.md section 11.4's own
# fix: a literal E1-E4 token, not a mention of a requirement number) -- redefined locally
# rather than imported so this component doesn't take a dependency on an unrelated gate's
# internals for one regex.
ESCALATION_TOKEN = re.compile(r"\bE[1-4]\b")


def _age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def _architecture_line(root):
    arch = root / "ARCHITECTURE.md"
    review = root / "ARCHITECTURE-REVIEW.md"
    if not arch.is_file():
        return "architecture   ARCHITECTURE.md missing"
    review_state = "missing"
    try:
        if review.is_file() and review.stat().st_size > 0:
            verdict, _ = fe.review_binds_architecture(review, arch)
            review_state = verdict
    except OSError:
        review_state = "unreadable"
    return f"architecture   ARCHITECTURE.md exists; review={review_state}"


def _components_line(root):
    components, malformed = cc.parse_component_map(root)
    if not components and not malformed:
        return "components     none declared"
    frozen = 0
    unreadable = 0
    for name in components:
        comp_root = cc.component_root(name, components) or ""
        goals_path = root / comp_root / "GOALS.json"
        if not goals_path.is_file():
            continue  # normal pre-freeze state, not an error -- nothing to disclose
        try:
            goals = json.loads(goals_path.read_text())
        except (OSError, json.JSONDecodeError):
            unreadable += 1  # exists but corrupt/unreadable -- a real problem, disclosed below
            continue
        integrity = goals.get("integrity") or {}
        if goals.get("criteria_frozen_at") and integrity.get("criteria_hash_at_freeze"):
            frozen += 1
    suffix = f", {len(malformed)} malformed" if malformed else ""
    if unreadable:
        suffix += f", {unreadable} GOALS.json unreadable"
    return f"components     {len(components)} declared, {frozen} GOALS.json frozen{suffix}"


def _orchestrator_line(root, session_id):
    orch_path = root / ".foreman" / "pdp-orchestrator.json"
    if not orch_path.is_file():
        return "orchestrator   unclaimed"
    try:
        blob = json.loads(orch_path.read_text())
    except (OSError, json.JSONDecodeError):
        return "orchestrator   claim file unreadable"
    held_by = blob.get("session_id") if isinstance(blob, dict) else None
    if not held_by:
        return "orchestrator   claim file has no session_id"
    if session_id and held_by == session_id:
        return f"orchestrator   held by this session ({held_by})"
    return f"orchestrator   held by {held_by} (not this session)"


def _stop_declared_line(root):
    path = root / ".foreman" / "stop-declared.json"
    if not path.is_file():
        return "stop-declared  none on record"
    try:
        blob = json.loads(path.read_text())
        age_s = time.time() - path.stat().st_mtime
    except (OSError, json.JSONDecodeError):
        return "stop-declared  file present but unreadable"
    condition = blob.get("condition") if isinstance(blob, dict) else None
    return f"stop-declared  condition={condition!r}, {_age(age_s)} old"


def _decisions_line(root):
    files = sorted(root.glob("DECISION-*.md"))
    if not files:
        return "decisions      0 open Provisional Decision Records"
    escalations = 0
    unreadable = 0
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            unreadable += 1  # disclosed below, not silently dropped from the count
            continue
        if ESCALATION_TOKEN.search(text):
            escalations += 1
    suffix = f", {unreadable} unreadable" if unreadable else ""
    return f"decisions      {len(files)} open ({escalations} naming E1-E4 -- don't re-raise){suffix}"


def _tickets_line(root):
    """FORE-570: this used to call tr.list_open_tickets(prefix) -- rows already server-side
    filtered to status=='open' -- and then count how many of THOSE rows had
    status=='in_progress'. That predicate can never match its own source list, so the banner's
    in_progress figure was structurally always 0 regardless of the real distribution (measured
    live: FORE 316 open / 252 closed / 1 in_progress, banner read "0 in_progress").

    Fixed by fetching the statuses this line actually reports, with no server-side status
    filter, and counting both open and in_progress client-side -- one CLI call, matching
    list_open_tickets' own stated reason for existing ("one call per prefix, not two"), not
    two. Calls tr.run_cli() directly rather than adding a second wrapper to
    tessera_resolver.py: that module (hooks/tessera_resolver/) has no GOALS.json of its own yet
    (a real, pre-existing gap this ticket did not create and is not scoped to fix), and
    list_open_tickets() itself must stay exactly as it is -- it is shared by
    open_blocking_tickets and preflight_blocking_gate.py's ratification scan, both of which
    specifically need the server-side open-only filter.
    """
    result = tr.resolve(root)
    status = result.get("status")
    if status != "ok":
        detail = f" ({result['reason']})" if result.get("reason") else ""
        if status == "ambiguous":
            detail = f" ({', '.join(result.get('candidates', []))})"
        return f"tickets        TESSERA {status}{detail}"
    prefix = result["prefix"]
    ok, data, err = tr.run_cli(["list", "--project", prefix])
    if not ok:
        return f"tickets        {prefix}: unreachable ({err})"
    tickets = data.get("tickets", []) if isinstance(data, dict) else []
    open_count = sum(1 for t in tickets if t.get("status") == "open")
    in_progress = sum(1 for t in tickets if t.get("status") == "in_progress")
    return f"tickets        {prefix}: {open_count} open, {in_progress} in_progress"


VERDICT_NAME_RE = re.compile(r"verdict", re.IGNORECASE)
SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".foreman", ".venv"}
MAX_FILES_WALKED = 5000


def _verdicts_line(root):
    """FORE-448: a rendered stage/persona verdict file (filename contains "verdict",
    case-insensitive) can sit on disk with no visible link into ticket tracking -- the exact
    gap FORE-448 names (a design-scope HOLD on shipped components existed only as a file for
    hours, discovered only by someone checking rather than by anything surfacing it). Read-only
    disclosure, same as every other line here; this does not block or gate anything.

    Scope, disclosed: only checks files under this project root (the same root every other
    helper uses), and only a verdict whose filename embeds this project's own ticket prefix
    plus a number can be checked automatically, by querying that one ticket's comments -- a
    verdict with no ticket reference in its name is counted, not checked, since there is
    nothing to look it up by.
    """
    result = tr.resolve(root)
    if result.get("status") != "ok":
        return "verdicts       cannot check (ticket project not resolved)"
    prefix = result["prefix"]
    ref_re = re.compile(rf"{re.escape(prefix)}-?(\d+)", re.IGNORECASE)

    candidates = []
    walked = 0
    truncated = False
    try:
        for p in root.rglob("*"):
            walked += 1
            if walked > MAX_FILES_WALKED:
                truncated = True
                break
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".md", ".json"):
                continue
            if VERDICT_NAME_RE.search(p.name):
                candidates.append(p)
    except OSError:
        return "verdicts       cannot check (directory walk failed)"

    if not candidates:
        return "verdicts       none found" + (" (scan truncated)" if truncated else "")

    unattributable = 0
    unposted = 0
    checked = {}
    for p in candidates:
        m = ref_re.search(p.name)
        if not m:
            unattributable += 1
            continue
        ticket_id = f"{prefix}-{m.group(1)}"
        if ticket_id not in checked:
            ok, data, _err = tr.run_cli(["get", ticket_id])
            checked[ticket_id] = data if ok and isinstance(data, dict) else None
        data = checked[ticket_id]
        if data is None:
            unposted += 1
            continue
        haystack = " ".join([
            str(data.get("summary") or ""), str(data.get("description") or ""),
            *[str(c.get("body") or "") for c in (data.get("comments") or [])
              if isinstance(c, dict)],
        ]).lower()
        if p.name.lower() not in haystack:
            unposted += 1

    suffix_bits = []
    if unattributable:
        suffix_bits.append(f"{unattributable} no ticket ref in name")
    if truncated:
        suffix_bits.append("scan truncated")
    suffix = f" ({', '.join(suffix_bits)})" if suffix_bits else ""
    return f"verdicts       {len(candidates)} found, {unposted} not in any ticket comment{suffix}"


def _git_line(root):
    try:
        proc = subprocess.run(["git", "status", "--short"], cwd=str(root),
                               capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return "git            status unavailable"
    if proc.returncode != 0:
        return "git            not a git repo"
    rows = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not rows:
        return "git            clean"
    untracked = sum(1 for ln in rows if ln.startswith("??"))
    modified = len(rows) - untracked
    return f"git            {modified} modified/staged, {untracked} untracked"


# CHV2-136: where a local PDP copy actually turns up, most specific first. reviewer-context/
# is this repo's own vendored reviewer bundle; docs/ and the root are the other two shapes in
# use across the Foreman repos.
LOCAL_PDP_CANDIDATES = (
    "reviewer-context/docs/PDP.md",
    "docs/PDP.md",
    "PDP.md",
)


def _local_pdp_path(root):
    """The repo-relative path of a local PDP.md copy, or None.

    CHV2-136: this line used to be a hardcoded string asserting "no local copy in this repo"
    unconditionally, checking nothing. It was true for alice-bob-rebuild and false for
    claude-hooks-v2, whose reviewer-context/docs/PDP.md exists -- and it was re-asserted into
    every prompt in both. A banner that states a fact about the filesystem should read the
    filesystem."""
    for candidate in LOCAL_PDP_CANDIDATES:
        try:
            if (root / candidate).is_file():
                return candidate
        except OSError:
            continue
    return None


def _process_pointer_line(root):
    local = _local_pdp_path(root)
    if local:
        return (f"Full process: PDP.md (local copy: {local} -- confirm it is current before "
                f"relying on it). Final message: pointers only, no manifest/tables here.")
    return ("Full process: PDP.md (no local copy in this repo -- ask the orchestrating session "
            "for the current path). Final message: pointers only, no manifest/tables here.")


def build_block(root, session_id):
    lines = [
        f"[PDP state: {root.name}]",
        _orchestrator_line(root, session_id),
        _architecture_line(root),
        _components_line(root),
        _stop_declared_line(root),
        _decisions_line(root),
        _tickets_line(root),
        _verdicts_line(root),
        _git_line(root),
        "Escalate only for E1-E4 (PDP.md section 11.3). Kill and Recycle are verdicts, not "
        "escalations. Marcus owns both terminal Gos.",
        _process_pointer_line(root),
    ]
    if len(lines) > MAX_LINES:
        # Structural guarantee, not a hope: if a future edit adds a line past the budget,
        # trim from the end (the two static pointer lines) rather than silently overflow --
        # PDP.md section 14's own "short by design" constraint applies to this file too.
        lines = lines[:MAX_LINES]
    return "\n".join(lines)


def main(data):
    event = data.get("hook_event_name")
    if event not in ("SessionStart", "UserPromptSubmit"):
        return

    # FORE-562: origin cwd, not the live one -- this asks "which project is this SESSION
    # about," with no write target to anchor on, so a mid-session `cd` must not move the answer.
    root = cc.find_project_root(hc.origin_cwd(data))
    if root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    context = build_block(root, data.get("session_id"))
    hc.set_rule(f"{RULE_ID}:injected")
    hc.inject(event, context)


if __name__ == "__main__":
    hc.run(main)
