"""Resolves a Foreman project_root to its TESSERA (db_path, prefix), and looks up blocking
tickets for a resolved project. Shared by tier_triage_gate (planned) and ticket_status_gate
(planned); built for real as a dependency of preflight_blocking_gate.py.

Not registered as a hook itself -- a library module, imported by the gates that need it.

Single-store assumption, disclosed: DB_PATH defaults to the token
/path/to/ticket-system/data/tessera.db. install-dev-harness.sh replaces that token with the
installing machine's path. TESSERA_DB / TESSERA_CWD env vars override either way.
ARCHITECTURE.md's 'TESSERA resolution' section deferred a config file for multi-store setups.
"""

import json
import os
import subprocess
from pathlib import Path

DB_PATH = os.environ.get("TESSERA_DB", "/path/to/ticket-system/data/tessera.db")
TESSERA_CWD = os.environ.get("TESSERA_CWD", "/path/to/ticket-system")
OVERRIDE_FILENAME = "tessera-prefix"


def _git_worktree_paths(cwd):
    """DEVH-59, mirroring tessguard/gitutil.py's DEVH-54 helper (not imported directly --
    this module stays dependency-free of the tessguard component per its own docstring).
    All worktree paths sharing cwd's repository (`git worktree list`), or [] if cwd isn't
    inside a git working tree or the command fails for any reason -- never raises."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    return [
        line[len("worktree "):].strip()
        for line in proc.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def run_cli(args, timeout=10):
    """Run the tessera CLI, returning (ok, parsed_json_or_None, stderr_text)."""
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", "-m", "tessera.api.cli", "--db", DB_PATH, *args],
            cwd=TESSERA_CWD, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, None, repr(exc)
    if proc.returncode != 0:
        return False, None, proc.stderr.strip()
    try:
        return True, json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return False, None, f"unparseable stdout: {exc}"


def read_override(project_root):
    override_path = Path(project_root) / ".foreman" / OVERRIDE_FILENAME
    try:
        text = override_path.read_text().strip()
    except OSError:
        return None
    return text or None


def resolve(project_root):
    """Returns one of:
    {"status": "ok", "prefix": "FORE", "db_path": DB_PATH}
    {"status": "unregistered"}
    {"status": "ambiguous", "candidates": ["AREM", "FORE"]}
    {"status": "unreachable", "reason": "..."}

    Override file wins outright, but only if the prefix it names is real -- a stale or
    mistyped override must not be silently trusted, it degrades to unreachable so a reader
    can tell 'misconfigured' apart from 'genuinely fine'.
    """
    ok, data, err = run_cli(["list-projects"])
    if not ok:
        return {"status": "unreachable", "reason": err}
    projects = data.get("projects", [])
    known_prefixes = {p["prefix"] for p in projects}

    override = read_override(project_root)
    if override is not None:
        if override in known_prefixes:
            return {"status": "ok", "prefix": override, "db_path": DB_PATH}
        return {"status": "unreachable",
                "reason": f".foreman/{OVERRIDE_FILENAME} names {override!r}, "
                          f"which is not a registered TESSERA prefix"}

    root_str = str(Path(project_root).resolve())
    matches = [p["prefix"] for p in projects if p.get("source_root") == root_str]
    if len(matches) == 0:
        # DEVH-59: project_root itself may be a git worktree distinct from the one a project
        # was registered against. Try every sibling worktree path before giving up -- the
        # fallback must not manufacture a match where none exists (a non-git or
        # zero-sibling root just falls through to unregistered below).
        for sibling in _git_worktree_paths(project_root):
            sibling_str = str(Path(sibling).resolve())
            if sibling_str == root_str:
                continue
            sibling_matches = [p["prefix"] for p in projects if p.get("source_root") == sibling_str]
            if sibling_matches:
                matches = sibling_matches
                break
    if len(matches) == 0:
        return {"status": "unregistered"}
    if len(matches) == 1:
        return {"status": "ok", "prefix": matches[0], "db_path": DB_PATH}
    return {"status": "ambiguous", "candidates": sorted(matches)}


def list_open_tickets(prefix):
    """Returns (ok, tickets_or_None, error_text) -- the raw `list --status open` rows for a
    project. Shared by open_blocking_tickets and preflight_blocking_gate's ratification scan
    so there's one CLI call per prefix per gate invocation, not two."""
    return run_cli(["list", "--status", "open", "--project", prefix])


def open_blocking_tickets(prefix):
    """Returns (ok, tickets_or_None, error_text). Filters client-side on
    custom_fields.blocking == 'true' -- TESSERA's list command has no server-side filter for
    an arbitrary custom field. Each returned ticket also carries whether it's malformed
    (blocking=true with no blocking_criterion) so the caller can treat that as its own case."""
    ok, data, err = list_open_tickets(prefix)
    if not ok:
        return False, None, err
    out = []
    for t in data.get("tickets", []):
        cf = t.get("custom_fields") or {}
        if cf.get("blocking") == "true":
            out.append({
                "ticket_id": t["ticket_id"],
                "blocking_criterion": cf.get("blocking_criterion"),
                "malformed": not cf.get("blocking_criterion"),
            })
    return True, out, ""


def ticket_ratified(prefix, ticket_id, agent_actor_names=("claude",)):
    """True if the ticket has custom_fields.ratified_by set to something other than a known
    agent actor string. Conservative: an unknown/missing field is NOT ratified."""
    ok, data, err = run_cli(["get", ticket_id])
    if not ok:
        return False
    cf = (data or {}).get("custom_fields") or {}
    ratified_by = cf.get("ratified_by")
    return bool(ratified_by) and ratified_by not in agent_actor_names
