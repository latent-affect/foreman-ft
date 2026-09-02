"""Stdio MCP server over atlas.query.facade.QueryFacade, built on the official mcp SDK.

Wraps QueryFacade unchanged -- this file adds no new safety logic of its own. All read-only
enforcement, view allowlisting, and trust-gate refusal live in facade.py.

Run with the SDK's own venv (requires Python >=3.10, matching mcp's own requirement):
`/path/to/dev-harness/.venv-mcp/bin/python3 /path/to/dev-harness/atlas/mcp/server.py`
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp.server.mcpserver import MCPServer

from atlas.query.facade import ALLOWED_VIEWS, QueryFacade, QueryFacadeUnavailable, QueryRefused

# SECURITY-PRIVACY-REVIEW.md F1 (dev-harness-9b, DEVH-49): this used to be
# ALLOWED_VIEWS minus "v_fail_open_incident" -- a denylist of one name, derived from
# facade.py's own allowlist by subtraction. Every view added to ALLOWED_VIEWS in the
# future would have been published over MCP automatically, with no review step and no
# failure mode that would surface it. Rewritten as an explicit allowlist instead: a
# new view is invisible over MCP until someone deliberately adds its name here. This
# is today's real set (17 of ALLOWED_VIEWS's 18, everything except the payload-bearing
# v_fail_open_incident) -- GOALS.json C7's own regression clause requires it to match
# what was servable before this change, not an arbitrary or empty set.
MCP_VIEWS = frozenset({
    "v_atlas_status", "v_decision_outcome_rate", "v_deny_streak",
    "v_gate_proven_live", "v_handler_denominator", "v_hook_latency_rollup", "v_hook_verdict",
    "v_pipeline_selfcheck", "v_project_resolution_coverage", "v_project_scope",
    "v_queryable_source", "v_snapshot_publishable", "v_source_freshness", "v_source_trust",
    "v_ticket_diff_binding", "v_trapped_agent_candidate", "v_verdict_confusion_matrix",
})

DB_PATH = os.environ.get("ATLAS_DB_PATH", str(_REPO_ROOT / "atlas" / "warehouse" / "atlas.db"))
MAX_ROWS = 1000
DEFAULT_ROWS = 100

server = MCPServer(name="atlas-warehouse", version="0.2.0")

_facade = None


def get_facade():
    global _facade
    if _facade is None:
        _facade = QueryFacade(DB_PATH)
    return _facade


@server.tool()
def atlas_status() -> dict:
    """Report the ATLAS warehouse's current trust-gate state: never-run, failed-run,
    checks-not-evaluated, or clean -- and the detail behind whichever it is. Call this before
    atlas_query_view if you want to know in advance whether a query will be refused."""
    s = get_facade().status()
    return {
        "state": s.state, "run_id": s.run_id, "run_status": s.run_status,
        "checks_evaluated": s.checks_evaluated, "contract_failures": s.contract_failures,
        "detail": s.detail,
    }


@server.tool()
def atlas_list_views() -> dict:
    """List the gated views atlas_query_view is allowed to read."""
    return {"views": sorted(MCP_VIEWS)}


@server.tool()
def atlas_query_view(view_name: str, limit: int = DEFAULT_ROWS) -> dict:
    """Read up to `limit` rows from one of the ATLAS warehouse's gated views (see
    atlas_list_views). Refused if the view is not on that list, or if the warehouse's last
    ingest run is not clean (see atlas_status). No caller-supplied WHERE clause -- this tool
    reads the whole view, capped by limit; filter client-side."""
    limit = max(1, min(int(limit), MAX_ROWS))
    if view_name not in MCP_VIEWS:
        raise ValueError(
            f"{view_name!r} is not served over MCP (secret-bearing or not allowlisted)"
        )
    try:
        # SECURITY-PRIVACY-REVIEW.md F2 (DEVH-50/GOALS.json C8): limit is now bound into
        # the SQL itself via facade.fetch()'s own limit= parameter, not sliced off a
        # full fetchall() after the fact -- the previous version of this call cost a
        # full-view read (363,289 rows on the reference warehouse) even for limit=1.
        # limit+1, not limit, is requested here so the "truncated" flag below can still
        # be computed without a second query: this is the one place that extra row is
        # asked for, never inside fetch() itself.
        columns, rows = get_facade().fetch(view_name, limit=limit + 1)
    except (QueryRefused, QueryFacadeUnavailable) as exc:
        raise ValueError(str(exc)) from exc
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


if __name__ == "__main__":
    server.run(transport="stdio")
