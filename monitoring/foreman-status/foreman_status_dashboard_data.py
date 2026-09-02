#!/usr/bin/env python3
"""Snapshot generator for the Foreman cross-project status dashboard.

One view across every project Foreman/TESSERA has bootstrapped. Per-project dogfood dashboards
are not part of this repo. Ticket and gate-activity numbers come from ATLAS's own
read-only query facade (atlas/query/facade.py) against the live ATLAS warehouse -- the
same trust-gated views ATLAS was built to serve, joining Bollard's verdict ledger to TESSERA's
ticket data -- rather than re-deriving that join by hand against raw tables. Project identity
(codename, source root) comes from TESSERA's own `projects` table directly, since ATLAS's views
carry only the resolved prefix, not a human-readable name or path.

If the ATLAS warehouse is unavailable or not in a clean run state, the dashboard still renders --
project identity always comes from TESSERA directly -- but ticket/gate numbers show as
unavailable with the real reason, never silently as zero.

Usage:
    python3 foreman_status_dashboard_data.py

NOT YET PORTABLE: ATLAS_REPO/TESSERA_DB below are hardcoded to the original author's machine.
Point them at your own ATLAS/TESSERA checkouts before using this elsewhere.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ATLAS_REPO = Path(os.environ.get(
    "ATLAS_REPO", str(Path(__file__).resolve().parents[2])
))
sys.path.insert(0, str(ATLAS_REPO))
from atlas.query.facade import QueryFacade, QueryFacadeUnavailable, QueryRefused  # noqa: E402

WAREHOUSE_DB = ATLAS_REPO / "atlas" / "warehouse" / "atlas.db"
TESSERA_DB = Path(os.environ.get(
    "TESSERA_DB", "/path/to/ticket-system/data/tessera.db"
))

# Scratchpad / throwaway test projects created while exercising TESSERA itself, and one row
# with no source_root at all -- not real bootstrapped projects, excluded from the roster.
EXCLUDED_PREFIXES = {"WIDG", "MIDB", "F9FR", "TCHK", "MACNET"}


def load_tessera_projects():
    con = sqlite3.connect(f"file:{TESSERA_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT prefix, codename, source_root, created_at FROM projects ORDER BY codename")
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def load_atlas_snapshot():
    try:
        facade = QueryFacade(str(WAREHOUSE_DB))
    except QueryFacadeUnavailable as exc:
        return {"available": False, "reason": str(exc)}, None

    status = facade.status()
    warehouse = {
        "available": True,
        "state": status.state,
        "run_id": status.run_id,
        "run_status": status.run_status,
        "checks_evaluated": status.checks_evaluated,
        "contract_failures": status.contract_failures,
        "detail": status.detail,
    }
    if not status.is_clean():
        facade.close()
        return warehouse, None

    # facade.fetch() re-checks status() internally on EVERY call, not just once -- a real race,
    # not a hypothetical one: the warehouse's own state can flip between the status() check
    # above and any of the five fetch() calls below (e.g. a live ingest completing mid-script),
    # and fetch() raises QueryRefused defensively when that happens. A raw sqlite3.Error is
    # possible too (a lock, a schema change). Either must come back "unavailable with the real
    # reason," the same pattern the construction-time QueryFacadeUnavailable case above already
    # uses -- never a silent crash, and never tickets/gates silently reading as zero (this
    # module's own documented promise). facade.close() moved to finally so it runs on every
    # exit path, not just the two that were already reached before this fix (Check 6 finding,
    # monitoring/GOALS.json C1-C3).
    try:
        tickets_by_prefix = {}
        cols, rows = facade.fetch("v_ticket_diff_binding")
        idx = {c: i for i, c in enumerate(cols)}
        for r in rows:
            prefix = r[idx["project_prefix"]]
            bucket = tickets_by_prefix.setdefault(
                prefix, {"total": 0, "closed": 0, "linkable": 0, "claimed": 0}
            )
            bucket["total"] += 1
            if r[idx["is_closed"]]:
                bucket["closed"] += 1
            if r[idx["linkability"]] == "linkable":
                bucket["linkable"] += 1
            if r[idx["claim_count"]]:
                bucket["claimed"] += 1

        gates_by_prefix = {}
        cols, rows = facade.fetch("v_decision_outcome_rate")
        idx = {c: i for i, c in enumerate(cols)}
        for r in rows:
            prefix = r[idx["project_scope"]]
            bucket = gates_by_prefix.setdefault(prefix, {"deny": 0, "ask": 0})
            bucket[r[idx["decision"]]] = bucket.get(r[idx["decision"]], 0) + r[idx["n"]]

        cols, rows = facade.fetch("v_source_freshness")
        freshness = [dict(zip(cols, r)) for r in rows]

        cols, rows = facade.fetch("v_project_resolution_coverage")
        resolution = [dict(zip(cols, r)) for r in rows]

        cols, rows = facade.fetch("v_gate_proven_live")
        gate_health = sorted(
            [dict(zip(cols, r)) for r in rows], key=lambda d: d["fires_total"], reverse=True
        )
    except (QueryRefused, sqlite3.Error) as exc:
        warehouse["fetch_error"] = str(exc)
        return warehouse, None
    finally:
        facade.close()

    return warehouse, {
        "tickets_by_prefix": tickets_by_prefix,
        "gates_by_prefix": gates_by_prefix,
        "source_freshness": freshness,
        "resolution_coverage": resolution,
        "gate_health": gate_health,
    }


def build_snapshot():
    warehouse, atlas_data = load_atlas_snapshot()
    tessera_projects = load_tessera_projects()

    projects = []
    for p in tessera_projects:
        if p["prefix"] in EXCLUDED_PREFIXES:
            continue
        tix = (atlas_data or {}).get("tickets_by_prefix", {}).get(p["prefix"])
        gates = (atlas_data or {}).get("gates_by_prefix", {}).get(p["prefix"])
        projects.append({
            "prefix": p["prefix"],
            "codename": p["codename"],
            "source_root": p["source_root"],
            "created_at": p["created_at"],
            "tickets": tix,
            "gates": gates,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warehouse": warehouse,
        "global": {
            "source_freshness": (atlas_data or {}).get("source_freshness", []),
            "resolution_coverage": (atlas_data or {}).get("resolution_coverage", []),
            "gate_health": (atlas_data or {}).get("gate_health", []),
        },
        "projects": projects,
    }


if __name__ == "__main__":
    print(json.dumps(build_snapshot(), indent=2, default=str))
