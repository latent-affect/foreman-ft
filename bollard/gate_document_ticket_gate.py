#!/usr/bin/env python3
"""REQ-30 (Foreman v2.0 PRD): generation-time enforcement -- a real TESSERA ticket must exist
and be linked BEFORE a gate-stage document is first created, not discovered missing later.

Real, deliberately narrower scope than REQ-30's full text, disclosed rather than silently
assumed complete: this hook covers ONLY the requirement's core, motivating clause -- "when that
document or decision is first generated, the system shall require a real TESSERA ticket to
exist and be linked to it at that moment." It does NOT cover REQ-30's other two clauses:
  - "any addition updates the linked ticket" (an ongoing per-edit sync discipline -- would need
    a PostToolUse hook checking staleness on every later edit, separate future work).
  - "the frozen/final version is explicitly linked, distinct from the ongoing tracking link"
    (a freeze-time marker, separate future work, analogous to REQ-1/REQ-12/REQ-26's existing
    freeze-time evidence conventions).
This hook closes the exact gap REQ-30's own motivating incident describes (PRD.md existed for a
whole night, untracked, before FORE-124 was filed only because the operator asked) and no more.

Reuses gate_document_ticket_audit.py's real TESSERA-query helper rather than re-implementing it
-- same document set (GATE_STAGE_DOCUMENTS), same "ticket mentions the filename" check, so the
audit (retrospective) and this gate (prospective) can never silently disagree about what counts
as "linked."

Configuration: reads TESSERA connection details from .foreman/tessera-project.json at the
project root (schema: {"db_path": "...", "repo_root": "...", "prefix": "FORE"}) -- the same
config shape this project's other TESSERA-calling hooks use. Missing config means this hook
cannot check anything; it fails open (ASK, not DENY) rather than blocking every gate-document
write because of a local config gap unrelated to whether a ticket actually exists (NFR-1 is
about internal hook errors defaulting to DENY for security guards; this is a workflow-discipline
gate, and a config-driven false DENY here would block real, legitimate work for a reason that
has nothing to do with the actual claim being checked).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc
from gate_document_ticket_audit import GATE_STAGE_DOCUMENTS, _run_tessera

import json


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


def _is_gate_stage_document(file_path, project_root):
    try:
        rel = Path(file_path).resolve().relative_to(Path(project_root).resolve())
    except ValueError:
        return None
    rel_str = str(rel)
    return rel_str if rel_str in GATE_STAGE_DOCUMENTS else None


def check_generation_time_link(tool_name, tool_input, cwd):
    """Returns (verdict, message) where verdict is one of "allow", "ask", None (not applicable
    to this call at all). Pure function, no hc dependency, so it's directly testable without
    mocking the hook I/O layer."""
    if tool_name not in ("Write",):
        return None, None  # Edit/MultiEdit imply the file already exists -- not a creation.
    file_path = (tool_input or {}).get("file_path")
    if not file_path or not cwd:
        return None, None

    doc_relpath = _is_gate_stage_document(file_path, cwd)
    if doc_relpath is None:
        return None, None

    if Path(file_path).is_file():
        return None, None  # already exists -- this is an edit-shaped Write, not generation.

    cfg = _load_tessera_config(cwd)
    if cfg is None:
        return "ask", (
            f"{doc_relpath} is a gate-stage document being created for the first time "
            f"(REQ-30), and no .foreman/tessera-project.json config exists to check whether a "
            f"TESSERA ticket already tracks it. This hook cannot verify the requirement from "
            f"here -- confirm a real ticket exists and is linked before proceeding, or add the "
            f"config so this check can run automatically next time."
        )

    ok, data, err = _run_tessera(
        cfg["db_path"], cfg["repo_root"], ["list", "--project", cfg["prefix"]],
    )
    if not ok:
        return "ask", (
            f"{doc_relpath} is being created for the first time (REQ-30), and this hook could "
            f"not query TESSERA to check for a linked ticket ({err}). Confirm a real ticket "
            f"exists before proceeding."
        )

    doc_name = Path(doc_relpath).name.lower()
    all_text = " ".join(
        f"{t.get('summary', '')} {t.get('description', '')}" for t in data.get("tickets", [])
    ).lower()
    if doc_name in all_text:
        return None, None  # a real, linked ticket already exists -- silent allow.

    return "ask", (
        f"{doc_relpath} is a gate-stage document being created for the first time, and no open "
        f"or closed TESSERA ticket in project {cfg['prefix']!r} mentions {doc_name!r} anywhere "
        f"in its summary or description (REQ-30: 'the system shall require a real TESSERA "
        f"ticket to exist and be linked to it at that moment' -- this is the exact gap that "
        f"motivated REQ-30: PRD.md itself existed untracked for a whole night before FORE-124 "
        f"was filed only because the operator asked directly). File and link a ticket before "
        f"creating this document, or confirm one already exists under a name this check "
        f"couldn't match."
    )


def main(data):
    verdict, message = check_generation_time_link(
        data.get("tool_name"), data.get("tool_input") or {}, data.get("cwd"),
    )
    if verdict is None:
        return
    hc.set_rule("req-30-generation-time-ticket-link")
    if verdict == "ask":
        hc.ask(message)


if __name__ == "__main__":
    hc.run(main)
