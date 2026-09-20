#!/usr/bin/env python3
"""REQ-30 clause 2 (Foreman v2.0 PRD): "any addition -- by the operator or by an agent --
updates the linked ticket, not just the document. A gate document that changes without its
ticket being touched is the same 'looks tracked, isn't' failure shape REQ-19's toy-model kill
already found in a different form -- a stale link is functionally equivalent to no link once
the two drift apart."

PostToolUse nudge (ASK, never DENY -- this is a discipline reminder, not a security gate) on a
successful Edit/Write/MultiEdit to a gate-stage document (same GATE_STAGE_DOCUMENTS set
gate_document_ticket_audit.py/gate_document_ticket_gate.py already use): if the linked TESSERA
ticket's own `updated_at` is older than STALE_AFTER_SECONDS relative to right now, reminds the
agent to comment on it. Once per (session, document) -- same dedup convention
postwrite_domain_check.py already uses ("A guard that nags gets disabled, and then nothing is
guarded") -- a real edit stream to one document across a session should get one reminder, not
one per keystroke.

Real limitation, disclosed rather than engineered around: this cannot tell "the ticket was
updated to reflect THIS SPECIFIC change" from "the ticket happened to get commented on for an
unrelated reason around the same time." It is a staleness proxy (has this ticket been touched
recently at all), matching this project's own established discipline for exactly this kind of
mechanically-imperfect-but-real-signal check (REQ-19's own re-consultation-scope-bound has the
same class of limit).
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
from gate_document_ticket_audit import GATE_STAGE_DOCUMENTS, TESSERA_CLI_MODULE  # noqa: E402

STATE_DIR = Path.home() / ".claude" / "audit-plane" / "gate-doc-staleness-seen"
STALE_AFTER_SECONDS = 30 * 60  # 30 minutes -- a real edit just happened; the linked ticket
                                # should get touched around the same time, not by end of session.


def _already_nudged(session_id, doc_relpath):
    if not session_id:
        return False
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker = STATE_DIR / f"{session_id}.txt"
        seen = set()
        if marker.exists():
            seen = {ln.strip() for ln in marker.read_text().splitlines() if ln.strip()}
        if doc_relpath in seen:
            return True
        with marker.open("a") as fh:
            fh.write(doc_relpath + "\n")
        return False
    except OSError:
        return False  # cannot persist -> fire; a duplicate nudge beats a silent guard


def _load_tessera_config(project_root):
    cfg_path = Path(project_root) / ".foreman" / "tessera-project.json"
    if not cfg_path.is_file():
        return None
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not all(k in cfg for k in ("db_path", "repo_root", "prefix")):
        return None
    return cfg


def _find_linked_ticket(cfg, doc_name):
    """Returns the most-recently-updated real ticket whose summary/description mentions
    `doc_name`, or None if none does -- reuses gate_document_ticket_audit.py's own "mentions
    the filename" matching convention so the audit (retrospective) and this nudge
    (prospective) can never silently disagree about what counts as linked."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", TESSERA_CLI_MODULE, "--db", str(cfg["db_path"]),
             "list", "--project", cfg["prefix"]],
            cwd=str(cfg["repo_root"]), capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None

    candidates = [
        t for t in data.get("tickets", [])
        if doc_name.lower() in f"{t.get('summary', '')} {t.get('description', '')}".lower()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t.get("updated_at") or "")


def check_staleness(tool_name, tool_input, cwd, session_id):
    """Returns a nudge message, or None if nothing to say. Pure function, testable without
    the hc I/O layer."""
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return None
    file_path = (tool_input or {}).get("file_path")
    if not file_path or not cwd:
        return None

    try:
        rel = Path(file_path).resolve().relative_to(Path(cwd).resolve())
    except ValueError:
        return None
    rel_str = str(rel)
    if rel_str not in GATE_STAGE_DOCUMENTS:
        return None

    if _already_nudged(session_id, rel_str):
        return None

    cfg = _load_tessera_config(cwd)
    if cfg is None:
        return None  # not opted in -- REQ-30's audit/gate hooks already cover this gap

    ticket = _find_linked_ticket(cfg, Path(rel_str).name)
    if ticket is None:
        return None  # no linked ticket at all is gate_document_ticket_gate.py's job, not this one

    from datetime import datetime, timezone
    try:
        updated_at = datetime.fromisoformat((ticket.get("updated_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    age_s = (datetime.now(timezone.utc) - updated_at).total_seconds()
    if age_s <= STALE_AFTER_SECONDS:
        return None

    age_min = round(age_s / 60)
    return (
        f"REQ-30: {rel_str} was just edited, but its linked ticket {ticket['ticket_id']!r} "
        f"hasn't been updated in {age_min} minutes. \"Any addition updates the linked ticket, "
        f"not just the document\" -- a comment noting this change keeps the tracking link real "
        f"rather than merely present."
    )


def main(data):
    message = check_staleness(
        data.get("tool_name"), data.get("tool_input") or {}, data.get("cwd"), data.get("session_id"),
    )
    if message is None:
        return
    hc.set_rule("req-30-gate-document-ticket-staleness")
    hc.warn(message)


if __name__ == "__main__":
    hc.run(main)
