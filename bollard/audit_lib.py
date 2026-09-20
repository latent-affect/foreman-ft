#!/usr/bin/env python3
"""Audit-plane writer + append-only flag integrity, for the misalignment-marker-search
agent-config build.

Two-plane discipline (execution vs. audit): the execution plane is the project working
tree; the audit plane is here, OUTSIDE the tree, under ~/.claude/audit-plane/. Hooks and
skills call this module to record router decisions, compaction firings, provenance
findings, review verdicts, Layer-1 denials, and Stop-blocks -- as evidence, not
self-attestation.

Dataset hygiene (goal: a clean research dataset): only events from sessions running INSIDE
the project tree land in the project ledger (misalignment-marker-search/audit.jsonl).
Machine-wide safety-hook denials fired in some OTHER project are still recorded, but routed
to a segregated global-safety ledger so they never pollute the research set.

Append-only: each ledger file carries the macOS user append-only flag (UF_APPEND,
`chflags uappnd`). This is TAMPER-EVIDENT, not tamper-proof -- the same user can clear the
flag without sudo. See CONFIG.md for the honest limitation and the sudo/off-host upgrade
path. check_flag() actively verifies the flag is still set on every session start and
screams if it is not.
"""

import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "audit-1"

PROJECT_ROOT = Path("/Users/m5/dev/misalignment-marker-search").resolve()
# AUDIT_BASE is overridable via MMS_AUDIT_BASE so the self-tests can write to a throwaway
# ledger and never pollute the real research dataset. Unset in normal operation.
AUDIT_BASE = Path(os.environ.get("MMS_AUDIT_BASE") or (Path.home() / ".claude" / "audit-plane"))
PROJECT_AUDIT_DIR = AUDIT_BASE / "misalignment-marker-search"
PROJECT_LEDGER = PROJECT_AUDIT_DIR / "audit.jsonl"
TASK_ANCHOR_DIR = PROJECT_AUDIT_DIR / "task-anchors"
GLOBAL_SAFETY_DIR = AUDIT_BASE / "global-safety"
GLOBAL_LEDGER = GLOBAL_SAFETY_DIR / "safety.jsonl"
# Runtime pointer (mutable, NOT append-only): the SessionStart hook records the current
# session id here so the model-router skill -- which runs as Bash and has no reliable way to
# learn its own session id -- can write its decision under the SAME id the gate checks.
CURRENT_SESSION = PROJECT_AUDIT_DIR / "current-session.json"

LEDGERS = (PROJECT_LEDGER, GLOBAL_LEDGER)


def now_iso():
    """Real UTC timestamp. Normal process -- real time is available and correct here."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_append_flag(path: Path) -> bool:
    try:
        return bool(os.stat(path).st_flags & stat.UF_APPEND)
    except (FileNotFoundError, AttributeError):
        return False


def _set_append_flag(path: Path):
    try:
        flags = os.stat(path).st_flags
        os.chflags(path, flags | stat.UF_APPEND)
    except (FileNotFoundError, AttributeError, PermissionError) as exc:
        print(f"[audit_lib] could not set append-only flag on {path}: {exc}", file=sys.stderr)


def _append_raw(path: Path, obj: dict):
    """Append one JSON line. O_APPEND is honored even when UF_APPEND is set (the flag blocks
    rewrite/truncate, not append)."""
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def ensure_setup():
    """Idempotent: create audit dirs + ledgers + task-anchor dir. The append-only flag is
    set ONLY when a ledger is first created here -- ensure_setup deliberately does NOT
    re-assert the flag on an already-existing ledger. If it did, it would silently repair a
    cleared flag before check_flag() could ever detect the tampering, defeating the whole
    integrity check. Re-asserting a cleared flag is check_flag()'s job, and only its job."""
    for d in (PROJECT_AUDIT_DIR, TASK_ANCHOR_DIR, GLOBAL_SAFETY_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for ledger in LEDGERS:
        if not ledger.exists():
            ledger.touch(mode=0o644)
            _set_append_flag(ledger)


def _in_project(cwd) -> bool:
    if not cwd:
        return False
    try:
        c = Path(cwd).resolve()
    except (OSError, ValueError):
        return False
    return c == PROJECT_ROOT or PROJECT_ROOT in c.parents


def audit_append(event_type, session_id=None, cwd=None, severity="info", **payload):
    """Route + stamp + append one audit event.

    In-project sessions -> project research ledger. Everything else -> global-safety ledger,
    so the research dataset only ever contains this project's own sessions.
    """
    ensure_setup()
    entry = {
        "schema_version": SCHEMA_VERSION,
        "ts": now_iso(),
        "event_type": event_type,
        "severity": severity,
        "session_id": session_id,
        "cwd": str(cwd) if cwd else None,
    }
    entry.update(payload)
    target = PROJECT_LEDGER if _in_project(cwd) else GLOBAL_LEDGER
    entry["ledger"] = "project" if target is PROJECT_LEDGER else "global-safety"
    _append_raw(target, entry)
    return entry


def check_flag():
    """Verify the append-only flag is still set on both ledgers. If a ledger has lost it:
    (1) scream to stderr, (2) if the ledger is still writable, record a high-severity
    AUDIT_FLAG_UNSET entry, (3) re-assert the flag. Never raises -- a failed check must not
    break a session start. Returns True iff all ledgers were already intact.

    Deliberately does NOT call ensure_setup() first: ensure_setup would (correctly) create a
    missing ledger, but must never be the thing that silently restores a cleared flag ahead
    of this detection. This function inspects the real on-disk flag state itself."""
    for d in (PROJECT_AUDIT_DIR, TASK_ANCHOR_DIR, GLOBAL_SAFETY_DIR):
        d.mkdir(parents=True, exist_ok=True)
    all_intact = True
    for ledger in LEDGERS:
        if not ledger.exists():
            # First run / never-created: this is setup, not tampering.
            ledger.touch(mode=0o644)
            _set_append_flag(ledger)
            continue
        if _has_append_flag(ledger):
            continue
        all_intact = False
        msg = (
            f"[audit_lib] SECURITY: append-only flag MISSING on {ledger} -- the audit log "
            f"is currently rewritable. This is logged as AUDIT_FLAG_UNSET and the flag is "
            f"being re-asserted. If you did not do this deliberately, treat it as tampering."
        )
        print(msg, file=sys.stderr)
        try:
            _append_raw(ledger, {
                "schema_version": SCHEMA_VERSION,
                "ts": now_iso(),
                "event_type": "AUDIT_FLAG_UNSET",
                "severity": "high",
                "ledger_path": str(ledger),
                "note": "append-only flag found cleared at check time; re-asserted",
            })
        except OSError as exc:
            print(f"[audit_lib] could not record AUDIT_FLAG_UNSET on {ledger}: {exc}", file=sys.stderr)
        _set_append_flag(ledger)
    return all_intact


# An approved plan outranks an opening prompt. Higher number wins.
#
# This replaces plain capture-once, which was capture-once across BOTH writers rather than
# within each. capture_prompt.py fires on UserPromptSubmit and a prompt always precedes any
# ExitPlanMode, so the anchor was always already occupied by the time capture_plan.py ran and
# its write was always a no-op. Measured 2026-08-12: 181 of 181 anchors carried
# source=UserPromptSubmit and not one carried ExitPlanMode, across every session on this
# machine. capture_plan.py had never once captured a plan.
#
# Precedence rather than a second anchor slot, deliberately. A separate slot would fix the
# collision and leave the single consumer -- reinject_compact.py -- choosing between two
# anchors with no stated rule. This says what an anchor is FOR: the compaction restore was
# built to restore the approved plan, so the approved plan is what it holds.
ANCHOR_PRECEDENCE = {"ExitPlanMode": 2, "UserPromptSubmit": 1}


def anchor_rank(source) -> int:
    return ANCHOR_PRECEDENCE.get(source, 0)


def write_task_anchor(session_id, obj) -> bool:
    """Write a session's task anchor unless an equal-or-higher-precedence one already exists.

    Returns True if written. A lower-precedence source never overwrites a higher one, so an
    opening prompt cannot displace an approved plan, and a re-plan cannot displace the first
    plan (equal rank does not supersede -- capture-once still holds WITHIN a source).

    A supersession records what it replaced, so the change is visible in the file rather than
    only in the fact that the contents differ from what someone expected.
    """
    ensure_setup()
    if not session_id:
        return False
    path = TASK_ANCHOR_DIR / f"{session_id}.json"
    superseded = None
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            # Unreadable anchor. Treat as rank 0 so a valid write can repair it, rather than
            # letting a corrupt file permanently block the restore path.
            existing = {}
        if anchor_rank(obj.get("source")) <= anchor_rank(existing.get("source")):
            return False
        superseded = {"source": existing.get("source"), "ts": existing.get("ts"),
                      "chars": len(existing.get("plan") or "")}
    payload = dict(obj)
    if superseded:
        payload["superseded"] = superseded
    payload.setdefault("ts", now_iso())
    payload.setdefault("session_id", session_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return True


def read_task_anchor(session_id):
    if not session_id:
        return None
    path = TASK_ANCHOR_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_current_session(session_id, cwd=None):
    """Record the active session id so the router skill (running as Bash) can log under it.
    Overwritten each SessionStart. Single-active-session assumption; concurrent project
    sessions would share this pointer -- documented in CONFIG.md."""
    ensure_setup()
    if not session_id:
        return
    try:
        CURRENT_SESSION.write_text(json.dumps(
            {"session_id": session_id, "cwd": str(cwd) if cwd else None, "ts": now_iso()}))
    except OSError as exc:
        print(f"[audit_lib] could not write current-session pointer: {exc}", file=sys.stderr)


def read_current_session():
    try:
        return json.loads(CURRENT_SESSION.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def session_has_router_entry(session_id) -> bool:
    """True iff a MODEL_ROUTER_DECISION event for this session already exists in the project
    ledger. Used by the router-log enforcement gate."""
    if not session_id or not PROJECT_LEDGER.exists():
        return False
    try:
        with open(PROJECT_LEDGER) as f:
            for line in f:
                if '"MODEL_ROUTER_DECISION"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event_type") == "MODEL_ROUTER_DECISION" and obj.get("session_id") == session_id:
                    return True
    except OSError:
        return False
    return False


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Audit-plane writer / flag-integrity CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ensure-setup")
    sub.add_parser("check-flag")
    ap = sub.add_parser("append")
    ap.add_argument("--type", required=True)
    ap.add_argument("--session")
    ap.add_argument("--cwd")
    ap.add_argument("--severity", default="info")
    ap.add_argument("--payload-stdin", action="store_true",
                    help="read a JSON object from stdin and merge into the entry")
    args = p.parse_args()

    if args.cmd == "ensure-setup":
        ensure_setup()
        print(f"audit plane ready: {PROJECT_LEDGER} (append-only={_has_append_flag(PROJECT_LEDGER)})")
    elif args.cmd == "check-flag":
        ok = check_flag()
        print(f"flag-integrity: {'INTACT' if ok else 'REPAIRED (was cleared)'}")
    elif args.cmd == "append":
        payload = {}
        if args.payload_stdin:
            raw = sys.stdin.read().strip()
            if raw:
                payload = json.loads(raw)
        entry = audit_append(args.type, session_id=args.session, cwd=args.cwd,
                             severity=args.severity, **payload)
        print(json.dumps(entry))


if __name__ == "__main__":
    _cli()
