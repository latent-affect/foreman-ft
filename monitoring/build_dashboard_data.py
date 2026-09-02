#!/usr/bin/env python3
"""Snapshot generator for the dev-harness Run 2 build-brief dashboard.

Adapted from /Users/m5/dev/foreman-v2/monitoring/foreman_v2_build_dashboard_data.py (same
philosophy: read real sources directly, disclose gaps honestly, never fabricate a percentage).
This build has no single epic ticket -- it's tracked as a flat DEVH project ticket range plus
per-component GOALS.json freezes, so the shape differs from foreman-v2's single-subtree model:

  1. TESSERA (read-only sqlite) -- every real ticket in the DEVH project (DEVH-1 upward), which
     is this build's actual ticket range (dev-harness-run2's PRD/design-and-scope work).
  2. Direct filesystem reads for every GOALS.json in this worktree -- freeze status, criteria
     count, MET/pending results, per component. This IS the design-and-scope stage's real
     progress signal for this build, since there's no single freeze file the way foreman-v2 had.
  3. PDP gate artifacts for the overall Run 2 pipeline (concept/PRD/architecture), same file-exists
     check pattern as the foreman-v2 dashboard.

Only components actually touched by this build's requirements (R0/R23/R8/R24 and the docs
stopgap) are shown in the primary "build components" panel -- the other pre-existing GOALS.json
files in this worktree (tessera/*, atlas/warehouse, atlas/query, etc.) belong to earlier,
unrelated work and are listed separately as "other components in this worktree", not conflated
with this build's own progress.
"""

import json
import subprocess
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

TESSERA_DB = Path("/Users/m5/dev/ticket-system/data/tessera.db")
REPO = Path("/Users/m5/dev/dev-harness-run2")
DEVH_PREFIX = "DEVH"

# Components this build's requirements actually touch, in the order they were worked tonight.
# relpath is where GOALS.json lives; requirement is the brief's own R-number for context.
BUILD_COMPONENTS = [
    ("root (measurement tooling)", "GOALS.json", "R23"),
    ("atlas (credential-scrub write-time discipline)", "atlas/GOALS.json", "R8"),
    ("skills (shipped-guard config)", "skills/GOALS.json", "R24"),
    ("bollard (shipped guards + preflight carve-out)", "bollard/GOALS.json", "R24"),
    ("scripts (install fail-closed)", "scripts/GOALS.json", "R24"),
    ("docs (DDL schema source of record)", "docs/GOALS.json", "R8 (dependency)"),
    ("tessera (store.py refactor + characterization tests + second baseline)", "tessera/GOALS.json", "R1/R2/R3"),
]

# PDP gate stages for Phase 2 -- verified against TESSERA's real, shipped tier schema
# (tessera/store/schema.py) and the PDP's own REQ-15: TIER_FULL_NPI (value 3, "Greenfield path")
# is the heaviest of the three real tiers and is defined as exactly this 8-stage sequence, "all
# six hooks enforced, full PRD -> architecture -> design-and-scope -> implementation ->
# integration-test -> validate -> ship-readiness". This build's Phase 2 (R1-R24) genuinely runs
# that path -- real PRD, real architecture + falsification, real per-component design-and-scope.
# 'none' means no single freeze file for that stage, tracked via TESSERA tickets/components instead.
TIER_LABEL = "Tier 3 -- Full NPI (Greenfield path)"
GATE_STAGES = [
    ("concept", ".foreman/tpm-gate-concept.json", "concept_gate.py"),
    ("prd", "PRD.md", None),
    ("architecture", ("ARCHITECTURE.md", "ARCHITECTURE-REVIEW.md"), "architecture_gate.py"),
    ("design-and-scope", None, None),  # tracked per-component below, not a single file
    ("implementation", None, None),
    ("integration-test", "INTEGRATION-TEST-REPORT.md", None),
    ("validate", "VALIDATION-REPORT.md", None),
    ("ship-readiness", ".foreman/SHIP-CHARTER.json", "ship_readiness_gate.py"),
]

# Real roster this build actually used tonight, updated as roles rotate. Not derivable from any
# live system -- personas are applied by peer sessions reading the .md files directly, per this
# project's own standing "route review to a separate session" rule.
SESSION_ROSTER = [
    {"role": "orchestrator", "model": "Sonnet 5", "note": "agent-remediation-af, cross-session coordination"},
    {"role": "Priya Desai", "model": "Opus 5", "note": "PRD, design-and-scope, all component freezes"},
    {"role": "Clint Eastwood", "model": "Sonnet 5", "note": "architecture origination + falsification"},
    {"role": "muse", "model": "Sonnet 5", "note": "blind-spot pass alongside Clint (rotates to implementation after)"},
    {"role": "independent falsifier", "model": "Sonnet 5", "note": "blind criteria falsification, every component"},
]


def now_utc():
    return datetime.now(timezone.utc)


# Real, verified anchor: Priya's PRD-authorship session (dev-harness-run2-01) started here,
# confirmed against `claude agents --json`'s startedAt and cross-checked against Jon's own
# elapsed-time estimate (2026-09-02, ~09:51 UTC check: matched his "about 3 hours" within a
# few minutes). Not derived from any file mtime -- this is the one fact no on-disk artifact
# records on its own, so it's a fixed constant rather than a guess dressed as a computation.
BUILD_STARTED_AT = datetime(2026, 9, 2, 6, 16, 7, tzinfo=timezone.utc)  # 2026-09-01 23:16:07 PDT


def load_tessera_snapshot():
    con = sqlite3.connect(f"file:{TESSERA_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT id FROM projects WHERE prefix = ?", (DEVH_PREFIX,))
    row = cur.fetchone()
    project_id = row["id"] if row else None

    tickets = []
    if project_id is not None:
        cur.execute(
            "SELECT ticket_id, project_id, type, status, tier, severity, summary, assignee, "
            "parent_id, created_at, updated_at FROM tickets WHERE project_id = ? "
            "ORDER BY CAST(SUBSTR(ticket_id, LENGTH(?) + 2) AS INTEGER)",
            (project_id, DEVH_PREFIX),
        )
        tickets = [dict(r) for r in cur.fetchall()]
    con.close()

    last_activity = None
    for t in tickets:
        ts = t.get("updated_at")
        if ts and (last_activity is None or ts > last_activity):
            last_activity = ts

    open_ct = sum(1 for t in tickets if t["status"] == "open")
    closed_ct = sum(1 for t in tickets if t["status"] == "closed")

    return {
        "project_id": project_id,
        "tickets": tickets,
        "open_count": open_ct,
        "closed_count": closed_ct,
        "last_activity_at": last_activity,
    }


def load_goals_file(relpath):
    path = REPO / relpath
    if not path.is_file():
        return {"exists": False}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"exists": True, "error": f"invalid JSON: {exc}"}

    criteria = data.get("criteria", [])
    results = data.get("results", [])
    met_ids = {r["criterion_id"] for r in results if r.get("status") == "MET"}
    gap_ids = {r["criterion_id"] for r in results if r.get("status") == "MET" and r.get("reproduction_gap")}
    frozen_at = data.get("criteria_frozen_at")
    return {
        "exists": True,
        "component": data.get("component"),
        "frozen_at": frozen_at,
        "criteria_total": len(criteria),
        "criteria_met": len(met_ids),
        "criteria_met_with_gap": len(gap_ids),
        "amendments": len(data.get("amendments", [])),
        "scope_note": data.get("scope_note"),
        "integrity_match": (data.get("integrity") or {}).get("match"),
    }


def load_build_components():
    out = []
    for label, relpath, requirement in BUILD_COMPONENTS:
        g = load_goals_file(relpath)
        g["label"] = label
        g["relpath"] = relpath
        g["requirement"] = requirement
        out.append(g)
    return out


def load_other_components():
    """Every other GOALS.json in this worktree, not part of this build's own requirements --
    pre-existing work (tessera/*, atlas's other subcomponents) shown separately so it's never
    conflated with this build's real progress."""
    build_relpaths = {rp for _, rp, _ in BUILD_COMPONENTS}
    out = []
    for path in sorted(REPO.rglob("GOALS.json")):
        rel = str(path.relative_to(REPO))
        if rel in build_relpaths:
            continue
        g = load_goals_file(rel)
        g["relpath"] = rel
        out.append(g)
    return out


def design_and_scope_status(build_components):
    """design-and-scope has no single freeze file for this build (each component freezes its
    own GOALS.json), so a flat 'no artifact' would read as 'not started' even with real,
    substantial progress. Compute an honest 3-state aggregate instead of a binary present/absent:
    'not-started' (nothing frozen), 'in-progress' (some frozen, not all shipped), 'done' (every
    component in this build's own scope has shipped). A partial state must never render as a
    failure -- 6/6 frozen and 2/6 shipped is real progress, not something gone wrong."""
    frozen = [c for c in build_components if c.get("exists") and c.get("frozen_at")]
    if not frozen:
        return {"stage": "design-and-scope", "artifact": None, "hook": None,
                "status": "not-started", "note": "no components frozen yet"}
    shipped = [c for c in frozen if c["criteria_total"] and c["criteria_met"] == c["criteria_total"]]
    # design-and-scope's own job is freezing criteria, not shipping them -- "done" must be measured
    # against len(frozen), never len(shipped) (that's implementation_status()'s job below). Using
    # the shipped count here was a real bug: it made this stage read "in-progress" for the entire
    # duration implementation runs, no matter how long ago freezing itself actually finished.
    status = "done" if len(frozen) == len(build_components) else "in-progress"
    return {
        "stage": "design-and-scope", "artifact": None, "hook": None, "status": status,
        "note": f"{len(frozen)}/{len(build_components)} components frozen, {len(shipped)} fully shipped",
    }


def implementation_status(build_components):
    """Same shape as design_and_scope_status: no single artifact for 'implementation' as a whole
    in this build (each component implements independently), so aggregate honestly instead of
    reporting a flat not-applicable placeholder once real implementation work has started."""
    frozen = [c for c in build_components if c.get("exists") and c.get("frozen_at")]
    shipped = [c for c in frozen if c["criteria_total"] and c["criteria_met"] == c["criteria_total"]]
    if not shipped:
        status = "not-started"
    elif len(shipped) == len(build_components):
        status = "done"
    else:
        status = "in-progress"
    return {
        "stage": "implementation", "artifact": None, "hook": None, "status": status,
        "note": f"{len(shipped)}/{len(build_components)} components fully implemented and shipped",
    }


def load_phase1_status():
    """Phase 1 (R0) already closed, real Tier 1 (Paper Qual / Hotfix path) -- bypassed
    architecture/design-and-scope entirely per TESSERA's real tier definitions, tracked by one
    ticket, not a gate sequence. Shown as closed context, not part of Phase 2's own gate panel."""
    con = sqlite3.connect(f"file:{TESSERA_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT ticket_id, status, summary, updated_at FROM tickets WHERE ticket_id = 'DEVH-3'")
    row = cur.fetchone()
    con.close()
    if not row:
        return {"available": False, "reason": "DEVH-3 not found"}
    return {"available": True, "ticket": dict(row), "tier_label": "Tier 1 -- Paper Qual (Hotfix path)"}


FREE_FORM_REPORT_STAGES = {"integration-test", "validate"}


def check_gate_artifacts():
    out = []
    for name, relpath, hook in GATE_STAGES:
        if relpath is None:
            out.append({"stage": name, "artifact": None, "hook": hook, "present": None,
                        "status": "not-started"})
            continue
        relpaths = (relpath,) if isinstance(relpath, str) else relpath
        paths = [REPO / rp for rp in relpaths]
        all_present = all(p.is_file() and p.stat().st_size > 0 for p in paths)
        # integration-test/validate write free-form markdown, not a structured GOALS.json-style
        # record -- mechanically parsing prose for a real PASS/FAIL verdict risks the same
        # "aggregate that quietly claims more than it checked" failure this dashboard exists to
        # avoid (see design_and_scope_status()'s own docstring). Report presence honestly, but
        # never claim "done" from a free-form doc existing -- that's exactly the shape of a false
        # green this project's own gate discipline (see INTEGRATION-TEST-REPORT.md's own
        # "I do not think it should pass on the literal reading" finding) warns against.
        if all_present and name in FREE_FORM_REPORT_STAGES:
            status = "in-progress"
            note = f"{relpath} exists -- read it directly, this dashboard does not parse prose reports for a verdict"
        elif all_present and name == "ship-readiness":
            # SHIP-CHARTER.json is real structured JSON (unlike integration-test/validate's
            # free-form prose), so its own verdict CAN be read mechanically -- and MUST be, not
            # just checked for existence. A HOLD charter existing is not "done"; treating artifact
            # presence as done here would be the exact same false-green shape this dashboard
            # already fixed for design-and-scope (implementation_status ratio bug) and
            # integration-test (the empty-interfaces-block false pass) -- Jon caught this one live
            # by reading the dashboard, same as those two.
            try:
                charter = json.loads((REPO / relpath).read_text())
                verdict = charter.get("verdict", "unknown")
                real_head = subprocess.run(
                    ["git", "-C", str(REPO), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                stale = charter.get("commit_hash") and real_head and not real_head.startswith(charter["commit_hash"][:12])
                if verdict == "go":
                    status = "done"
                else:
                    status = "in-progress"  # hold/kill/unknown are all real, non-terminal states
                note = f"verdict: {verdict}" + (" (charter commit_hash is STALE vs. current HEAD -- re-run needed)" if stale else "")
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                status = "in-progress"
                note = f"charter exists but couldn't be read cleanly: {exc}"
        else:
            status = "done" if all_present else "not-started"
            note = None
        entry = {
            "stage": name,
            "artifact": relpath,
            "hook": hook,
            "present": all_present,
            "status": status,
            "mtime": max(
                datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                for p in paths
            ).isoformat() if all_present else None,
        }
        if note:
            entry["note"] = note
        out.append(entry)
    return out


def git_head_info():
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        subject = subprocess.run(
            ["git", "-C", str(REPO), "log", "-1", "--format=%s"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        commit_ct = subprocess.run(
            ["git", "-C", str(REPO), "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return {"sha": sha, "branch": branch, "subject": subject, "dirty": bool(dirty), "commit_count": commit_ct}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _git_commit_events():
    """Every commit on this branch since BUILD_STARTED_AT, with the DEVH ticket IDs its subject
    line mentions (if any) -- always live, no ATLAS dependency (ATLASSN-45: the batch pipeline
    that would otherwise answer this is 6-hourly and was found stale by hours during this build)."""
    import re
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "log", "--since", BUILD_STARTED_AT.isoformat(),
             "--format=%H|%cI|%s"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return []
    events = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        sha, iso_ts, subject = parts
        tickets = re.findall(r"\bDEVH-\d+\b", subject)
        events.append({"ts": iso_ts, "type": "commit", "label": subject[:80], "sha": sha[:8], "tickets": tickets})
    return events


def _ticket_events(tessera_tickets):
    """created/updated timestamps straight from the live TESSERA DB -- same freshness guarantee
    as everything else in this dashboard, deliberately not routed through ATLAS's batch views."""
    events = []
    for t in tessera_tickets:
        events.append({"ts": t["created_at"], "type": "ticket_created", "label": f"{t['ticket_id']} opened", "tickets": [t["ticket_id"]]})
        if t.get("updated_at") and t["updated_at"] != t["created_at"]:
            events.append({"ts": t["updated_at"], "type": "ticket_updated", "label": f"{t['ticket_id']} updated", "tickets": [t["ticket_id"]]})
    return events


def _goals_events(build_components, other_components):
    events = []
    for c in build_components + other_components:
        if not c.get("exists"):
            continue
        if c.get("frozen_at"):
            events.append({"ts": c["frozen_at"], "type": "goals_frozen", "label": f"{c.get('component', c.get('relpath'))} frozen", "tickets": []})
        try:
            raw = json.loads((REPO / c["relpath"]).read_text())
            for a in raw.get("amendments", []):
                ts = a.get("amended_at") or a.get("timestamp")
                if ts:
                    events.append({"ts": ts, "type": "goals_amended", "label": f"{c.get('component', c.get('relpath'))} amended", "tickets": []})
        except Exception:  # noqa: BLE001
            pass
    return events


def build_activity_timeline(tessera_tickets, build_components, other_components, bucket_minutes=15):
    """Real, live-sourced 'is this build actually moving' signal -- an answer to 'it's all open,
    that doesn't show progress' that doesn't wait on ATLAS's 6-hourly batch pipeline (ATLASSN-45).
    Every event here comes straight from TESSERA, git, or GOALS.json on disk; nothing is estimated
    or interpolated. A flat/empty bucket is a real observation (stagnation), not missing data."""
    events = (_git_commit_events() + _ticket_events(tessera_tickets)
              + _goals_events(build_components, other_components))
    parsed = []
    for e in events:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        parsed.append((ts, e))
    parsed.sort(key=lambda p: p[0])

    now = now_utc()
    total_minutes = max((now - BUILD_STARTED_AT).total_seconds() / 60, bucket_minutes)
    n_buckets = max(1, int(total_minutes // bucket_minutes) + 1)
    buckets = []
    for i in range(n_buckets):
        start = BUILD_STARTED_AT.timestamp() + i * bucket_minutes * 60
        end = start + bucket_minutes * 60
        in_bucket = [e for ts, e in parsed if start <= ts.timestamp() < end]
        by_type = {}
        for e in in_bucket:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        buckets.append({
            "start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
            "count": len(in_bucket),
            "by_type": by_type,
        })

    # Per-ticket: does an open DEVH ticket have any real, linked evidence (a commit mentioning
    # its ID) or not -- this is the direct fix for "it's all open, that doesn't indicate
    # progress": status alone can't distinguish "shipped, ticket just not closed yet" from
    # "genuinely untouched", but a linked commit can.
    commit_events = [e for _, e in parsed if e["type"] == "commit"]
    linked = {}
    for e in commit_events:
        for tid in e["tickets"]:
            linked.setdefault(tid, []).append({"sha": e["sha"], "label": e["label"]})
    ticket_activity = []
    for t in tessera_tickets:
        tid = t["ticket_id"]
        ticket_activity.append({
            "ticket_id": tid,
            "status": t["status"],
            "linked_commits": len(linked.get(tid, [])),
            "last_commit": linked.get(tid, [{}])[-1].get("label") if linked.get(tid) else None,
        })

    return {
        "bucket_minutes": bucket_minutes,
        "buckets": buckets,
        "total_events": len(parsed),
        "ticket_activity": ticket_activity,
        "tickets_with_linked_commits": sum(1 for t in ticket_activity if t["linked_commits"] > 0),
        "tickets_open_no_linked_commit": sum(1 for t in ticket_activity if t["status"] == "open" and t["linked_commits"] == 0),
    }


# Real per-session transcript paths for this build's fleet -- hardcoded because there's no
# programmatic way to enumerate "which sessions worked this build tonight" from outside; update
# this list if a new session joins. Reads real per-turn `usage.output_tokens` from the transcript,
# not a char-count estimate -- REQ-51's own measurement started as an estimate (4 chars/token) and
# this upgrades it to the real number now that it's a standing dashboard metric, not a one-off.
REPORT_COST_SESSIONS = {
    "agent-remediation-af (orchestrator)": "/Users/m5/.claude/projects/-Users-m5-agent-remediation/f72156d5-f47e-4f3a-866f-e5f41faeaaac.jsonl",
    "dev-harness-run2-01 (Priya)": "/Users/m5/.claude/projects/-Users-m5-dev-dev-harness-run2/e175b64c-a795-4b21-945e-2c1dfa6d284f.jsonl",
    "dev-harness-run2-73 (Clint)": "/Users/m5/.claude/projects/-Users-m5-dev-dev-harness-run2/73be5b99-042f-4341-bfcf-529e29aa04d7.jsonl",
    "dev-harness-run2-1e (implementer)": "/Users/m5/.claude/projects/-Users-m5-dev-dev-harness-run2/4074b6f5-b471-4f30-8613-3b737989d5f0.jsonl",
    "dev-harness-9b": "/Users/m5/.claude/projects/-Users-m5-dev-dev-harness/8a41f70c-d837-4acb-b92a-aba697c8433c.jsonl",
    "dev-harness-33": "/Users/m5/.claude/projects/-Users-m5-dev-dev-harness/5c92ac28-071d-4518-a3a9-b8eae2ebb1ae.jsonl",
}
REQ51_MIN_REPORT_CHARS = 200  # below this, a message is a routine ack/pointer, not "report volume"
REQ51_PROJECTED_REDUCTION = 0.65  # Priya's own honest self-estimate (60-70%), midpoint, of the
# category-1/2 (duplicative) share of pre-REQ-51 volume -- a target to compare the real, measured
# reduction against, not a claim about what has actually been achieved.


def _send_message_reports(path):
    rows = []
    try:
        with open(path, errors="ignore") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = obj.get("message", {})
                content = msg.get("content")
                usage = msg.get("usage")
                if not isinstance(content, list) or not usage:
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "SendMessage":
                        text = block.get("input", {}).get("message", "")
                        if len(text) < REQ51_MIN_REPORT_CHARS:
                            continue
                        rows.append({
                            "ts": obj.get("timestamp"),
                            "chars": len(text),
                            "output_tokens": usage.get("output_tokens", 0),
                        })
    except OSError:
        return None  # session transcript not readable/rotated -- disclosed as missing, not zero
    return rows


def _find_req51_cutoff():
    path = REPORT_COST_SESSIONS.get("agent-remediation-af (orchestrator)")
    if not path or not Path(path).exists():
        return None
    try:
        with open(path, errors="ignore") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                content = obj.get("message", {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "SendMessage":
                        if "REQ-51" in block.get("input", {}).get("message", ""):
                            return obj.get("timestamp")
    except OSError:
        return None
    return None


def report_cost_metric():
    """Real, measured (not estimated) fleet-wide cost of cross-session status reports, before vs.
    after REQ-51's adoption -- answers 'did the fix actually reduce output-token spend,' not just
    'was a REQ written.' Missing/unreadable session transcripts are disclosed per-session, not
    silently excluded from the total (a session dropping out should never make the number look
    better)."""
    cutoff = _find_req51_cutoff()
    per_session = []
    totals = {"before": {"n": 0, "tokens": 0, "chars": 0}, "after": {"n": 0, "tokens": 0, "chars": 0}}
    for name, path in REPORT_COST_SESSIONS.items():
        rows = _send_message_reports(path)
        if rows is None:
            per_session.append({"session": name, "error": "transcript not readable"})
            continue
        before = [r for r in rows if cutoff is None or r["ts"] < cutoff]
        after = [r for r in rows if cutoff is not None and r["ts"] >= cutoff]
        b = {"n": len(before), "tokens": sum(r["output_tokens"] for r in before), "chars": sum(r["chars"] for r in before)}
        a = {"n": len(after), "tokens": sum(r["output_tokens"] for r in after), "chars": sum(r["chars"] for r in after)}
        for k in ("before", "after"):
            totals[k]["n"] += (b if k == "before" else a)["n"]
            totals[k]["tokens"] += (b if k == "before" else a)["tokens"]
            totals[k]["chars"] += (b if k == "before" else a)["chars"]
        per_session.append({
            "session": name,
            "before": b, "after": a,
            "mean_before": round(b["tokens"] / b["n"]) if b["n"] else None,
            "mean_after": round(a["tokens"] / a["n"]) if a["n"] else None,
        })
    mean_before = totals["before"]["tokens"] / totals["before"]["n"] if totals["before"]["n"] else None
    mean_after = totals["after"]["tokens"] / totals["after"]["n"] if totals["after"]["n"] else None
    pct_change = ((mean_after / mean_before) - 1) * 100 if (mean_before and mean_after) else None
    return {
        "req51_cutoff": cutoff,
        "min_report_chars": REQ51_MIN_REPORT_CHARS,
        "per_session": per_session,
        "fleet": totals,
        "fleet_mean_before_tokens": round(mean_before) if mean_before else None,
        "fleet_mean_after_tokens": round(mean_after) if mean_after else None,
        "fleet_pct_change": round(pct_change, 1) if pct_change is not None else None,
        "projected_reduction_pct": round(REQ51_PROJECTED_REDUCTION * 100, 0),
        "caveat": "'after' sample is small (REQ-51 adopted late in this build) -- early signal, not a settled result. A single long category-3 report (a real disagreement or FAIL finding) can swing a small-n session mean upward by design; REQ-51 targets duplication, not length, so that is not itself a defect.",
    }


def build_snapshot():
    tessera = load_tessera_snapshot()
    build_components = load_build_components()
    gates = check_gate_artifacts()
    # Replace the flat design-and-scope/implementation placeholders with honest 3-state
    # aggregates over this build's own components -- see their docstrings for why a partial
    # state must never render as a failure.
    aggregate_by_stage = {
        "design-and-scope": design_and_scope_status(build_components),
        "implementation": implementation_status(build_components),
    }
    gates = [aggregate_by_stage.get(g["stage"], g) for g in gates]
    # "Current stage" is the FRONTIER -- the furthest stage in real PDP order that has produced
    # real progress (done, or in-progress with a real artifact) -- not simply "the first stage
    # that isn't done." Those two differ, and the difference is a real bug this dashboard shipped
    # with: this build's own R18/security-privacy-review follow-on fixes added NEW criteria to
    # already-shipped components (root, atlas), which drops their shipped ratio and makes
    # implementation_status() read "in-progress" again -- even though integration-test and
    # validate have already produced real PASS/MET verdicts hours ago. A naive "first non-done"
    # read would make current_stage snap backward to "implementation," implying the whole
    # pipeline regressed, which is false -- it's real, disclosed, in-scope rework layered onto a
    # component that already shipped its original scope, not a return to square one.
    done_or_progress_idx = [i for i, g in enumerate(gates) if g.get("status") in ("done", "in-progress")]
    frontier_idx = max(done_or_progress_idx) if done_or_progress_idx else 0
    first_not_done_idx = next((i for i, g in enumerate(gates) if g.get("status") != "done"), len(gates) - 1)
    current_idx = max(frontier_idx, first_not_done_idx)
    current_stage = gates[current_idx]["stage"] if gates else None
    stage_rework_note = None
    if frontier_idx > first_not_done_idx:
        regressed = [g["stage"] for i, g in enumerate(gates) if i < frontier_idx and g.get("status") not in ("done",) and i >= first_not_done_idx]
        if regressed:
            stage_rework_note = (
                f"Real, in-scope rework in progress at an earlier stage ({', '.join(regressed)}) "
                f"while later stages ({gates[frontier_idx]['stage']}) already hold a real verdict -- "
                "not a pipeline regression. New criteria from a downstream finding (e.g. a "
                "security-privacy-review fix) landed on an already-shipped component."
            )
    phase1 = load_phase1_status()
    other_components = load_other_components()
    git_info = git_head_info()

    stall_minutes = None
    stall_parse_error = None
    if tessera["last_activity_at"]:
        try:
            last = datetime.fromisoformat(tessera["last_activity_at"].replace("Z", "+00:00"))
            stall_minutes = round((now_utc() - last).total_seconds() / 60, 1)
        except ValueError as exc:
            stall_parse_error = f"{type(exc).__name__}: {exc}"

    shipped = sum(1 for c in build_components if c.get("exists") and c.get("criteria_total") and c["criteria_met"] == c["criteria_total"])
    in_progress = sum(1 for c in build_components if c.get("exists") and c.get("frozen_at") and not (c.get("criteria_total") and c["criteria_met"] == c["criteria_total"]))

    elapsed_minutes = round((now_utc() - BUILD_STARTED_AT).total_seconds() / 60, 1)
    activity = build_activity_timeline(tessera["tickets"], build_components, other_components)
    report_cost = report_cost_metric()

    return {
        "generated_at": now_utc().isoformat(),
        "build_started_at": BUILD_STARTED_AT.isoformat(),
        "build_elapsed_minutes": elapsed_minutes,
        "repo": str(REPO),
        "git": git_info,
        "tessera": tessera,
        "phase1": phase1,
        "tier_label": TIER_LABEL,
        "current_stage": current_stage,
        "stage_rework_note": stage_rework_note,
        "gate_artifacts": gates,
        "build_components": build_components,
        "other_components": other_components,
        "shipped_count": shipped,
        "in_progress_count": in_progress,
        "total_build_components": len(BUILD_COMPONENTS),
        "stall_minutes": stall_minutes,
        "stall_parse_error": stall_parse_error,
        "session_roster": SESSION_ROSTER,
        "activity": activity,
        "report_cost": report_cost,
    }


if __name__ == "__main__":
    print(json.dumps(build_snapshot(), indent=2, default=str))
