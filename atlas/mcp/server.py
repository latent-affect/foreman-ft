"""Stdio MCP server over atlas.query.facade.QueryFacade, built on the official mcp SDK.

Wraps QueryFacade unchanged -- this file adds no new safety logic of its own. All read-only
enforcement, view allowlisting, and trust-gate refusal live in facade.py; see ATLASSN-26.

Run with the SDK's own venv (requires Python >=3.10, matching mcp's own requirement):
`/Users/m5/dev/atlas-sonnet/.venv-mcp/bin/python3 /Users/m5/dev/atlas-sonnet/atlas/mcp/server.py`
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp.server.mcpserver import MCPServer

from atlas.query.facade import (
    QueryFacade,
    QueryFacadeUnavailable,
    QueryRefused,
)

# ATLASSN-48 (ported from dev-harness DEVH-49 / SECURITY-PRIVACY-REVIEW.md F1). These lists used
# to be facade.ALLOWED_VIEWS and facade.ALLOWED_LIVE_VIEWS served straight through, so every view
# ever added to either set was published over MCP automatically, with no review step and no
# failure mode that would surface it. dev-harness at least carried a denylist of one name; this
# copy had neither, and consequently served v_fail_open_incident -- observed live, not inferred,
# by importing this module and calling atlas_list_views().
#
# That view is the reason this matters rather than being tidiness: ARCHITECTURE.md section 1
# records that it "serves this same payload text verbatim through the query facade," the payload
# being SAFETY_DENY rows carrying verbatim shell command text (largest single line 682,196
# bytes), through the exact channel this project's CLAUDE.md records a real API-key exposure
# through. It is excluded here and nowhere else, so removing it from MCP does not remove it from
# the facade for callers that legitimately need it.
#
# Written as explicit literals rather than derived by subtraction: a new view stays invisible over
# MCP until someone deliberately adds its name. Regression clause -- this must equal what was
# servable before, minus v_fail_open_incident, not an arbitrary or empty set. Enforced by
# atlas/mcp/tests/test_mcp_views_allowlist.py rather than by this comment.
# ATLASSN-80's session_prior is added here as a deliberate decision, not an oversight: it carries
# the same shape of data as v_deny_streak/v_trapped_agent_candidate (session_id, handler_id,
# rule_id, counts, timestamps), both already published, and no verbatim payload text -- the
# distinguishing property that keeps v_fail_open_incident excluded does not apply to it.
MCP_VIEWS = frozenset({
    "v_atlas_status", "v_decision_outcome_rate", "v_deny_streak",
    "v_gate_proven_live", "v_handler_denominator", "v_handler_freshness",
    "v_hook_latency_rollup", "v_hook_verdict", "v_pipeline_selfcheck",
    "v_project_resolution_coverage", "v_project_scope", "v_queryable_source",
    "v_snapshot_publishable", "v_source_freshness", "v_source_trust",
    "v_ticket_diff_binding", "v_trapped_agent_candidate", "v_verdict_confusion_matrix",
    "session_prior",
})

# The live plane's equivalent. No name is excluded today -- v_subagent_tool_call does carry
# verbatim tool_input_json, but that is ATLASSN-27's stated purpose and it is gated twice over
# (freshness, plus subagent_pull.py's own credential scan flipping live_state to
# live-credential-hit). The point of listing it explicitly is the same as above: the next live
# view added should require a decision, not inherit publication.
MCP_LIVE_VIEWS = frozenset({
    "v_subagent_tool_call", "v_subagent_activity", "v_subagent_deny_join",
    "v_transcript_deny_join", "v_ledger_join_coverage", "v_pip_coverage",
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
    """List the gated views atlas_query_view is allowed to read, and separately the live-plane
    views atlas_query_live is allowed to read. The two lists have different gates: the first is
    refused when the 6-hourly batch pipeline's data-quality checks are not clean, the second when
    the 60-second subagent pull is stale, failed, or found a credential."""
    return {"views": sorted(MCP_VIEWS), "live_views": sorted(MCP_LIVE_VIEWS)}


@server.tool()
def atlas_live_status() -> dict:
    """Report the live plane's freshness gate (ATLASSN-27): live, never-run, live-pull-failed,
    live-pull-stale, live-credential-hit, or live-plane-not-migrated -- plus how many seconds ago
    the last subagent pull finished. This is a DIFFERENT gate from atlas_status, which reports the
    6-hourly batch pipeline. A parent session checking on its own dispatched subagent wants this
    one."""
    state, detail = get_facade().live_status()
    return {"live_state": state, **{k: v for k, v in detail.items() if k != "live_state"}}


@server.tool()
def atlas_query_live(view_name: str, limit: int = DEFAULT_ROWS) -> dict:
    """Read the live plane: what a dispatched subagent actually did, within about a minute of it
    doing so. `v_subagent_activity` is one row per subagent -- agent id, agent type, the parent's
    own dispatch tool_use id, tool-call and Bash counts, first and last call timestamps, how many
    of its calls a hook denied. `v_subagent_tool_call` is one row per individual tool call with
    the verbatim tool input.

    Refused if the last subagent pull is stale, failed, or matched a credential pattern -- see
    atlas_live_status. Independent of atlas_status: a batch data-quality failure does not blind
    this, which is the whole point of it being a separate gate.

    This reads; it does not refresh. For zero staleness rather than up to 60 seconds, run
    `/Users/m5/.venv/bin/python3 -m atlas.ingest.subagent_pull` first, then call this."""
    limit = max(1, min(int(limit), MAX_ROWS))
    if view_name not in MCP_LIVE_VIEWS:
        raise ValueError(f"{view_name!r} is not served over MCP (not allowlisted)")
    try:
        # limit+1, not limit: the extra row is what makes "truncated" computable without a
        # second query. Asked for HERE, by the one caller that wants it, never inside
        # fetch_live() -- so no other caller silently over-fetches a row nobody wanted.
        columns, rows = get_facade().fetch_live(view_name, limit=limit + 1)
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


@server.tool()
def atlas_query_view(view_name: str, limit: int = DEFAULT_ROWS) -> dict:
    """Read up to `limit` rows from one of the ATLAS warehouse's gated views (see
    atlas_list_views). Refused if the view is not on that list, or if the warehouse's last
    ingest run is not clean (see atlas_status). No caller-supplied WHERE clause -- this tool
    reads the whole view, capped by limit; filter client-side."""
    limit = max(1, min(int(limit), MAX_ROWS))
    if view_name not in MCP_VIEWS:
        raise ValueError(
            f"{view_name!r} is not served over MCP (payload-bearing or not allowlisted)"
        )
    try:
        # ATLASSN-48/F2: limit is bound into the SQL by facade.fetch() now, not sliced off a
        # full fetchall() afterwards -- the previous version cost a whole-view read even for
        # limit=1. limit+1 for the same truncation reason as atlas_query_live above.
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
