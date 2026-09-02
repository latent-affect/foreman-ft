#!/usr/bin/env python3
"""Snapshot generator for the dev-harness Run 2 closure-pass + Tier 3 recycle dashboard.

Scope, deliberately narrower than build_dashboard_data.py (that one tracks the whole R1-R24
build's gate sequence): this one answers one question -- "what is the real, live status of
every DEVH ticket touched by tonight's closure pass and the R14/R15 Tier 3 recycle, grouped by
tier" -- straight from the TESSERA sqlite store (read-only), never hand-typed, never estimated.

Per this project's own standing rule (feedback_use-atlas-facade-for-cross-project-data), a
cross-project join belongs behind ATLAS's read-only facade -- this dashboard queries only ONE
project (DEVH), so a direct read-only sqlite connection is the same pattern build_dashboard_data.py
already uses, not the cross-project case that rule is about.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TESSERA_DB = Path("/Users/m5/dev/ticket-system/data/tessera.db")
DEVH_PREFIX = "DEVH"
GATE_PACKAGE = Path("/Users/m5/dev/dev-harness-run2/.foreman/tpm-gate-architecture-r14r15.json")

# Tickets this closure pass and the R14/R15 recycle actually touched tonight -- the "this pass"
# set shown first and highlighted. Everything else open in DEVH is shown too (grouped by tier),
# so "status of each build, both Tier 3 and Tier 2" isn't limited to only these, but these are
# flagged so they're never lost in the wider backlog list.
THIS_PASS_TICKETS = {
    "DEVH-2", "DEVH-12", "DEVH-16",
    "DEVH-60", "DEVH-61", "DEVH-62", "DEVH-63", "DEVH-64", "DEVH-65",
    "DEVH-66", "DEVH-67", "DEVH-68", "DEVH-69",
}

# R14/R15's real recycle sequence, in dependency order -- shown as an explicit chain, not just
# a flat ticket list, since the point is "what's the next open gate."
RECYCLE_CHAIN = ["DEVH-16", "DEVH-61", "DEVH-62", "DEVH-63", "DEVH-64"]

TIER_LABELS = {
    1: "Tier 1 -- Paper Qual (Hotfix)",
    2: "Tier 2 -- NPI-Light (crosses an existing interface)",
    3: "Tier 3 -- Full NPI (new component/interface, foundational)",
    None: "Untiered (review/triage/process, not a build tier)",
}


def now_utc():
    return datetime.now(timezone.utc)


def load_devh_tickets():
    con = sqlite3.connect(f"file:{TESSERA_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT id FROM projects WHERE prefix = ?", (DEVH_PREFIX,))
    row = cur.fetchone()
    project_id = row["id"] if row else None
    if project_id is None:
        con.close()
        return []
    cur.execute(
        "SELECT ticket_id, type, status, tier, priority, severity, summary, assignee, "
        "parent_id, created_at, updated_at FROM tickets WHERE project_id = ? "
        "ORDER BY CAST(SUBSTR(ticket_id, LENGTH(?) + 2) AS INTEGER)",
        (project_id, DEVH_PREFIX),
    )
    rows = [dict(r) for r in cur.fetchall()]

    ticket_ids = [r["ticket_id"] for r in rows]
    comment_counts = {}
    blocked_by = {}
    assigned_session = {}
    for tid in ticket_ids:
        cur.execute("SELECT COUNT(*) AS n FROM comments WHERE ticket_id = ?", (tid,))
        comment_counts[tid] = cur.fetchone()["n"]
        cur.execute(
            "SELECT to_ticket FROM ticket_links WHERE from_ticket = ? AND link_type = 'blocked-by'",
            (tid,),
        )
        blocked_by[tid] = [r["to_ticket"] for r in cur.fetchall()]
        cur.execute(
            "SELECT field_value FROM ticket_fields WHERE ticket_id = ? AND field_name = 'assigned_session'",
            (tid,),
        )
        fr = cur.fetchone()
        if fr and fr["field_value"]:
            try:
                assigned_session[tid] = json.loads(fr["field_value"])
            except (json.JSONDecodeError, TypeError):
                assigned_session[tid] = fr["field_value"]
    con.close()

    for r in rows:
        r["comment_count"] = comment_counts.get(r["ticket_id"], 0)
        r["blocked_by"] = blocked_by.get(r["ticket_id"], [])
        r["assigned_session"] = assigned_session.get(r["ticket_id"])
        r["this_pass"] = r["ticket_id"] in THIS_PASS_TICKETS
    return rows


def group_by_tier(tickets):
    groups = {}
    for t in tickets:
        tier = t.get("tier")
        groups.setdefault(tier, []).append(t)
    out = []
    for tier in sorted(groups.keys(), key=lambda x: (x is None, x)):
        items = groups[tier]
        out.append({
            "tier": tier,
            "label": TIER_LABELS.get(tier, f"Tier {tier}"),
            "open_count": sum(1 for t in items if t["status"] == "open"),
            "closed_count": sum(1 for t in items if t["status"] == "closed"),
            "total": len(items),
            "tickets": items,
        })
    return out


def load_recycle_chain(by_id):
    chain = []
    for tid in RECYCLE_CHAIN:
        t = by_id.get(tid)
        if not t:
            chain.append({"ticket_id": tid, "status": "not-found"})
            continue
        chain.append({
            "ticket_id": tid,
            "status": t["status"],
            "summary": t["summary"],
            "assigned_session": t.get("assigned_session"),
            "comment_count": t["comment_count"],
        })
    # "current" = first ticket in the SEQUENTIAL sub-chain (DEVH-61..64) that is still open --
    # DEVH-16 itself is excluded from this computation on purpose. It's the parent/umbrella
    # ticket, correctly open for the whole recycle's duration (it closes only once every child
    # does), not a step in the 61->62->63->64 dependency sequence -- treating it as step one
    # made "current gate" read DEVH-16 even after 61-63 closed and 64 was the real next step,
    # the same "flat first-not-done reads wrong" bug build_dashboard_data.py's own
    # current_stage frontier logic already had to fix once.
    sequential = [c for c in chain if c["ticket_id"] != "DEVH-16"]
    current = next((c["ticket_id"] for c in sequential if c.get("status") == "open"), None)
    return {
        "chain": chain,
        "current_gate": current,
        "all_closed": current is None,
        "parent_status": next((c["status"] for c in chain if c["ticket_id"] == "DEVH-16"), None),
    }


def load_gate_package():
    if not GATE_PACKAGE.is_file():
        return {"exists": False}
    try:
        data = json.loads(GATE_PACKAGE.read_text())
        return {"exists": True, **data}
    except json.JSONDecodeError as exc:
        return {"exists": True, "error": f"invalid JSON: {exc}"}


def build_snapshot():
    tickets = load_devh_tickets()
    by_id = {t["ticket_id"]: t for t in tickets}
    this_pass = [t for t in tickets if t["this_pass"]]
    tier_groups = group_by_tier(tickets)
    this_pass_tier_groups = group_by_tier(this_pass)

    return {
        "generated_at": now_utc().isoformat(),
        "source": str(TESSERA_DB),
        "project": DEVH_PREFIX,
        "total_tickets": len(tickets),
        "total_open": sum(1 for t in tickets if t["status"] == "open"),
        "total_closed": sum(1 for t in tickets if t["status"] == "closed"),
        "this_pass_tickets": this_pass,
        "this_pass_tier_groups": this_pass_tier_groups,
        "all_tier_groups": tier_groups,
        "recycle_chain": load_recycle_chain(by_id),
        "gate_package": load_gate_package(),
    }


if __name__ == "__main__":
    print(json.dumps(build_snapshot(), indent=2, default=str))
