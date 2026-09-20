"""Builds the index.json / projects/<PREFIX>.json dicts, ARCHITECTURE.md section 17's
documented shape, from warehouse project metadata and an injected ticket_rollup dict (the real
live TESSERA read is a separate component's job -- see GOALS.json out_of_scope)."""

SCHEMA_VERSION = "atlas-snapshot-2"


class TicketRollupIdentityError(ValueError):
    pass


def check_ticket_rollup_identity(ticket_rollup):
    """Raises TicketRollupIdentityError naming every violating project if
    open_total != open_null_severity + sum(open_by_severity.values()) for any project --
    FATAL-3's fix, enforced here as the pre-publish gate (mirrors
    atlas.warehouse.dq_runner.check_snapshot_ticket_rollup_identity's own logic, kept local so
    this component does not need a live warehouse connection just to validate its own input)."""
    violations = []
    for prefix, data in ticket_rollup.items():
        total = data.get("open_total", 0)
        null_sev = data.get("open_null_severity", 0)
        by_sev_sum = sum((data.get("open_by_severity") or {}).values())
        if total != null_sev + by_sev_sum:
            violations.append(f"{prefix}: open_total={total} != null_sev={null_sev} + Σby_sev={by_sev_sum}")
    if violations:
        raise TicketRollupIdentityError("; ".join(violations))


def _project_roots(dim_projects):
    """dim_projects: list of dicts with keys prefix, codename, source_root, root_state,
    root_is_shared. Returns (roots_map, shared_with) where shared_with[prefix] = [other
    prefixes sharing prefix's source_root]."""
    by_root = {}
    for p in dim_projects:
        if p["source_root"]:
            by_root.setdefault(p["source_root"], []).append(p["prefix"])

    roots_map = {}
    for root, prefixes in by_root.items():
        resolution = "unique" if len(prefixes) == 1 else "ambiguous"
        roots_map[root] = {"prefixes": sorted(prefixes), "resolution": resolution}

    shared_with = {}
    for root, prefixes in by_root.items():
        if len(prefixes) > 1:
            for prefix in prefixes:
                shared_with[prefix] = sorted(p for p in prefixes if p != prefix)
    return roots_map, shared_with


def build_index(dim_projects, ticket_rollup, cursors, unresolved_scopes, rate_uninterpretable_handlers,
                 severity_rubric, refresh_cadence_seconds, warehouse_run_id, verdict_window_days,
                 generated_at):
    check_ticket_rollup_identity(ticket_rollup)
    roots_map, shared_with = _project_roots(dim_projects)

    projects = {}
    for p in dim_projects:
        rollup = ticket_rollup.get(p["prefix"], {})
        projects[p["prefix"]] = {
            "codename": p["codename"],
            "source_root": p["source_root"],
            "root_is_shared": p["prefix"] in shared_with,
            "root_state": p["root_state"],
            "tickets": "present" if rollup else "absent",
            "open_total": rollup.get("open_total", 0),
            "open_by_severity": rollup.get("open_by_severity", {}),
            "open_null_severity": rollup.get("open_null_severity", 0),
            "open_null_tier": rollup.get("open_null_tier", 0),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "refresh_cadence_seconds": refresh_cadence_seconds,
        "max_age_seconds": refresh_cadence_seconds * 3,  # section 5: derived, not independent
        "warehouse_run_id": warehouse_run_id,
        "cursors": cursors,
        "verdict_window_days": verdict_window_days,
        "rate_uninterpretable_handlers": rate_uninterpretable_handlers,
        "severity_rubric": severity_rubric,
        "roots": roots_map,
        "unresolved_scopes": unresolved_scopes,
        "projects": projects,
    }, shared_with


def build_shard(prefix, dim_project, ticket_rollup_entry, verdict_rollup, shared_with, index_generated_at):
    is_shared = prefix in shared_with
    return {
        "schema_version": SCHEMA_VERSION,
        "index_generated_at": index_generated_at,
        "project_prefix": prefix,
        "source_root": dim_project["source_root"],
        "rollup_scope": "<ambiguous>" if is_shared else prefix,
        "rollup_is_shared_with": shared_with.get(prefix, []),
        "verdict_rollup": verdict_rollup,
        "recent_fail_open": [],
    }


UNRESOLVED_SCOPES = ("<ambiguous>", "<unregistered>", "<no-cwd>")


def build_unresolved_shard(scope, verdict_rollup, index_generated_at):
    """The bucket shards ARCHITECTURE.md sections 3 and 17 document as real, published shards
    (ambiguous-UNRESOLVED.json, unregistered-UNRESOLVED.json, no-cwd-UNRESOLVED.json) --
    found missing during integration testing (Check 6): the shard loop only ever iterated
    dim_projects, never these three scope buckets, so a consumer requesting one got a real,
    fail-safe shard-corrupt refusal rather than silent wrong data, but the documented artifact
    was never actually produced. scope must be one of UNRESOLVED_SCOPES exactly."""
    if scope not in UNRESOLVED_SCOPES:
        raise ValueError(f"scope must be one of {UNRESOLVED_SCOPES}, got {scope!r}")
    return {
        "schema_version": SCHEMA_VERSION,
        "index_generated_at": index_generated_at,
        "project_prefix": None,
        "rollup_scope": scope,
        "verdict_rollup": verdict_rollup,
        "recent_fail_open": [],
    }
