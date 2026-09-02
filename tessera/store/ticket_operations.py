"""create_ticket, register_project, transition_status, list_tickets, verify_chain and their
shared hierarchy check -- tessera/GOALS.json C2 (PRD.md R1a). Moved out of store.py for the same
reason replay_handlers.py and project_reassignment.py were: store.py's own SLOC and total
cyclomatic complexity (summed across every method) both feed the maintainability index formula
directly, and these were among the largest remaining contributors.

Logic is unchanged from the methods they replace -- moved verbatim, not rewritten, with `self`
renamed to an explicit `store` parameter since these are now free functions rather than Store
methods. The corresponding Store methods are now thin wrappers that call into this module.
"""

import json
import re
import sqlite3

from ..common.hashing import canonical_json, event_hash as compute_event_hash
from ..common.timestamps import utc_now_iso
from . import schema
from .exceptions import BlockedError, CriteriaNotFrozenError, HierarchyError, WorkflowError


def check_hierarchy_internal(store, conn, ticket_type, parent_id):
    if ticket_type == "Sub-task":
        if not parent_id:
            raise HierarchyError("Sub-task requires a parent_id")
        parent = conn.execute(
            "SELECT type FROM tickets WHERE ticket_id = ?", (parent_id,)
        ).fetchone()
        if not parent or parent[0] not in schema.SUBTASK_PARENT_TYPES:
            raise HierarchyError(
                f"Sub-task's parent_id {parent_id!r} must be a "
                f"{'/'.join(schema.SUBTASK_PARENT_TYPES)}, got {parent[0] if parent else None!r}"
            )


def create_ticket(store, *, ticket_type, reporter, actor, priority=None, assignee=None,
                   parent_id=None, severity=None, repro_steps=None, environment=None,
                   custom_fields=None, idempotency_key=None, project=None, tier=None,
                   summary=None, description=None):
    if ticket_type not in schema.TICKET_TYPES:
        raise ValueError(f"unknown ticket type {ticket_type!r}")
    if tier is not None and tier not in schema.TIERS:
        raise ValueError(f"unknown tier {tier!r}; must be one of {schema.TIERS}")
    # severity/priority are a real, ordered 0-4 int scale now (previously
    # entirely unconstrained free text) -- validated the same way tier already is.
    if severity is not None and severity not in schema.LEVELS:
        raise ValueError(f"unknown severity {severity!r}; must be one of {schema.LEVELS}")
    if priority is not None and priority not in schema.LEVELS:
        raise ValueError(f"unknown priority {priority!r}; must be one of {schema.LEVELS}")
    # create_ticket writes custom_fields straight into ticket_fields, so
    # custom_fields={"priority": 1} at create is the same shadow-write as set-field is
    # after it: the dedicated `priority=` argument sits right there and would have been
    # ignored in favour of a value no triage view reads.
    shadowed = sorted(set(custom_fields or {}) & set(store.FIRST_CLASS_TICKET_FIELDS))
    if shadowed:
        raise ValueError(
            f"custom_fields may not shadow first-class ticket column(s) {shadowed} -- "
            f"pass them as their own arguments instead"
        )
    project_id = store.resolve_project_id_internal(project)

    def attempt():
        try:
            with store.write_txn_internal() as conn:
                check_hierarchy_internal(store, conn, ticket_type, parent_id)

                row = conn.execute(
                    "UPDATE counters SET value = value + 1 WHERE project_id = ?"
                    " AND name = 'ticket_id' RETURNING value",
                    (project_id,),
                ).fetchone()
                prefix = conn.execute(
                    "SELECT prefix FROM projects WHERE id=?", (project_id,)
                ).fetchone()[0]
                ticket_id = f"{prefix}-{row[0]}"

                payload = {
                    "ticket_id": ticket_id, "project_id": project_id, "type": ticket_type,
                    "reporter": reporter, "assignee": assignee, "priority": priority,
                    "tier": tier, "summary": summary, "description": description,
                    "parent_id": parent_id,
                    "severity": severity, "repro_steps": repro_steps,
                    "environment": environment, "custom_fields": custom_fields or {},
                }
                _, created_at = store.append_event_internal(
                    conn, "TicketCreated", actor, payload,
                    ticket_id=ticket_id, idempotency_key=idempotency_key,
                    project_id=project_id,
                )
                conn.execute(
                    "INSERT INTO tickets (ticket_id, project_id, type, status, reporter,"
                    " assignee, priority, tier, summary, description, parent_id, severity,"
                    " repro_steps, environment, reference_docs, archived, created_at,"
                    " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                    (ticket_id, project_id, ticket_type, "open", reporter, assignee,
                     priority, tier, summary, description, parent_id, severity, repro_steps,
                     environment, "[]", created_at, created_at),
                )
                for name, value in (custom_fields or {}).items():
                    # canonical_json, not json.dumps -- same reasoning and same fix
                    # shape as ticket_criteria's already-fixed sibling bug:
                    # a dict-valued custom field's replay path reconstitutes it from
                    # this event's own canonical_json payload (sorted keys), so a
                    # plain json.dumps here on the live path preserves the caller's
                    # original insertion order instead -- same data, different JSON
                    # text, false rebuild/live divergence (found by Clint
                    # Eastwood's adversarial review, confirmed by direct repro).
                    conn.execute(
                        "INSERT INTO ticket_fields (ticket_id, field_name, field_value)"
                        " VALUES (?,?,?)",
                        (ticket_id, name, canonical_json(value)),
                    )
                return ticket_id
        except sqlite3.IntegrityError as exc:
            msg = str(exc)
            if idempotency_key and "idempotency_key" in msg:
                # project_id scoped -- matches idx_events_idempotency's
                # (project_id, idempotency_key) constraint, so a duplicate-key hit in
                # ANOTHER project doesn't get mistaken for this project's own replay.
                existing = store.conn_internal().execute(
                    "SELECT ticket_id FROM events WHERE idempotency_key = ? AND project_id = ?",
                    (idempotency_key, project_id),
                ).fetchone()
                if existing:
                    return existing[0]
            raise

    return store.retry_internal(attempt)


def register_project(store, codename, prefix, source_root=None):
    """Idempotent: if `prefix` is already registered, returns its existing project_id
    (codename/source_root on the existing row are NOT overwritten by a second call --
    re-registering is a no-op, not a silent rename). Otherwise creates the project and
    its ticket_id counter in the same transaction, and returns the new project_id."""
    existing = store.get_project(prefix)
    if existing is not None:
        return existing["id"]

    if not codename or not prefix:
        raise ValueError("register_project requires both a codename and a prefix")
    if ".." in prefix or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}", prefix):
        raise ValueError(
            f"register_project prefix {prefix!r} must match "
            f"[A-Za-z][A-Za-z0-9_-]{{0,15}} and must not contain '..'"
        )

    def attempt():
        with store.write_txn_internal() as conn:
            row = conn.execute(
                "INSERT INTO projects (codename, prefix, source_root, created_at)"
                " VALUES (?,?,?,?) RETURNING id",
                (codename, prefix, str(source_root) if source_root else None, utc_now_iso()),
            ).fetchone()
            project_id = row[0]
            conn.execute(
                "INSERT OR IGNORE INTO counters (project_id, name, value) VALUES (?, 'ticket_id', 0)",
                (project_id,),
            )
            return project_id

    try:
        return store.retry_internal(attempt)
    except sqlite3.IntegrityError:
        # Two processes racing to register the same new prefix for the first time:
        # the loser's INSERT hits projects.prefix's UNIQUE constraint. Not an error --
        # re-read and return the winner's row, same discipline as the old
        # project_metadata race fix this replaces.
        existing = store.get_project(prefix)
        if existing is not None:
            return existing["id"]
        raise


def transition_status(store, ticket_id, actor, new_status):
    def attempt():
        with store.write_txn_internal() as conn:
            row = conn.execute(
                "SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"no such ticket {ticket_id!r}")
            current = row[0]
            legal = schema.WORKFLOW_TRANSITIONS.get(current, set())
            if new_status not in legal:
                raise WorkflowError(
                    f"illegal transition for {ticket_id}: {current!r} -> {new_status!r} "
                    f"(legal targets from {current!r}: {sorted(legal)})"
                )
            if new_status == "closed":
                open_blockers = store.open_blockers_internal(conn, ticket_id)
                if open_blockers:
                    raise BlockedError(
                        f"{ticket_id} cannot close: blocked by "
                        f"{', '.join(b['ticket_id'] for b in open_blockers)} "
                        f"(not yet closed: {open_blockers})"
                    )
            if new_status == "in_progress":
                criteria_row = conn.execute(
                    "SELECT criteria_frozen_at FROM ticket_criteria WHERE ticket_id=?",
                    (ticket_id,),
                ).fetchone()
                if not criteria_row or not criteria_row[0]:
                    raise CriteriaNotFrozenError(
                        f"{ticket_id} cannot move to in_progress: no frozen criteria "
                        f"(call freeze_ticket_criteria first -- document before building)"
                    )
            _, updated_at = store.append_event_internal(
                conn, "StatusChanged", actor,
                {"from": current, "to": new_status}, ticket_id=ticket_id,
            )
            conn.execute(
                "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?",
                (new_status, updated_at, ticket_id),
            )

    store.retry_internal(attempt)


def list_tickets(store, status=None, assignee=None, ticket_type=None,
                  include_archived=False, project=None,
                  priority_max=None, severity_max=None):
    """Requested by Foreman/ATLAS: priority_max/severity_max are threshold filters,
    not exact-match, because the real need named was "all open S0/S1", a set, not a single
    level -- 0 is the highest priority/severity, so *_max=1 means "P0 or P1"/"S0 or S1". A
    ticket with a NULL priority/severity never matches a threshold filter (deliberate, same
    as every other filter here: an unset value is not treated as satisfying a request for a
    specific value)."""
    query = ("SELECT t.ticket_id FROM tickets t JOIN projects p ON p.id = t.project_id"
              " WHERE 1=1")
    params = []
    if status:
        query += " AND t.status=?"
        params.append(status)
    if assignee:
        query += " AND t.assignee=?"
        params.append(assignee)
    if ticket_type:
        query += " AND t.type=?"
        params.append(ticket_type)
    if project:
        query += " AND p.prefix=?"
        params.append(project)
    if priority_max is not None:
        query += " AND t.priority IS NOT NULL AND t.priority<=?"
        params.append(priority_max)
    if severity_max is not None:
        query += " AND t.severity IS NOT NULL AND t.severity<=?"
        params.append(severity_max)
    if not include_archived:
        query += " AND t.archived=0"
    rows = store.conn_internal().execute(query, params).fetchall()
    return [store.get_ticket(r[0], with_context=False) for r in rows]


def verify_chain(store):
    """Returns {'roots': n, 'tips': n, 'orphans': n, 'hash_mismatches': n}. A healthy
    chain has exactly one root and one tip and zero of the other two."""
    conn = store.conn_internal()
    rows = conn.execute(
        "SELECT id, event_type, ticket_id, actor, payload, idempotency_key,"
        " prev_hash, event_hash, created_at FROM events ORDER BY id"
    ).fetchall()
    hashes = {r[7] for r in rows}
    roots = sum(1 for r in rows if r[6] == schema.GENESIS)
    parents_used = {r[6] for r in rows}
    tips = sum(1 for r in rows if r[7] not in parents_used)
    orphans = sum(1 for r in rows if r[6] != schema.GENESIS and r[6] not in hashes)
    mismatches = 0
    for r in rows:
        (row_id, event_type, ticket_id, actor, payload_json, idempotency_key_unused,
         prev_hash, stored_hash, created_at) = r
        recomputed = compute_event_hash({
            "event_type": event_type, "ticket_id": ticket_id, "actor": actor,
            "payload": json.loads(payload_json), "prev_hash": prev_hash,
            "created_at": created_at,
        })
        if recomputed != stored_hash:
            mismatches += 1
    return {"roots": roots, "tips": tips, "orphans": orphans, "hash_mismatches": mismatches}
