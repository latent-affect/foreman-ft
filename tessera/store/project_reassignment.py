"""reassign_project()'s implementation -- tessera/GOALS.json C2 (PRD.md R1a). Moved out of
store.py for the same reason replay_handlers.py was: this was store.py's single largest method
by line count, and moving it to its own file reduces store.py's own SLOC and Halstead volume,
which the maintainability index formula weighs directly (this module's own MI is not what C2
grades -- only tessera/store/store.py's is).

Logic is unchanged from the method it replaced -- moved verbatim, not rewritten, with `self`
renamed to an explicit `store` parameter since this is now a free function rather than a Store
method. Store.reassign_project is now a thin wrapper that calls into this module.
"""

import json

from .exceptions import SameProjectError


def reassign_project(store, ticket_id, actor, new_project):
    """Moves a ticket to a different registered project. ticket_ids are
    minted from a per-project counter and their prefix names their project (see
    create_ticket) -- there is no sensible way to keep the SAME ticket_id under a
    different project's prefix, so this mints a new ticket_id in the target project
    and copies the ticket's current type/reporter/assignee/priority/tier/summary/
    description/severity/repro_steps/environment/custom_fields/reference_docs/
    comments onto it, links the old ticket to the new one (relates-to), leaves a
    forwarding comment on the old ticket, and closes the old ticket -- unless it's
    still blocked, in which case the move still happens but the old ticket is left
    open with the forwarding comment rather than raising (a blocked close is a
    pre-existing, unrelated fact about the old ticket; failing the whole move over it
    would be worse than leaving it for a human to close once unblocked).

    Deliberately reuses only EXISTING event types (TicketCreated/FieldSet/
    ReferenceDocsSet/CommentAdded/LinkAdded/StatusChanged) in the same shapes their
    single-purpose counterparts already emit -- so rebuild_projection()'s replay
    needs no new branch to stay correct for this method.

    Does not copy ticket_criteria, watchers, claims, attachments, ticket_commit_links,
    or hotlist membership -- out of this method's stated scope (history/comments/
    reference_docs/events); a caller that needs those preserved too should file a
    follow-up rather than assume this covers them."""

    def attempt():
        with store.write_txn_internal() as conn:
            old = conn.execute(
                "SELECT project_id, type, status, reporter, assignee, priority, tier,"
                " summary, description, parent_id, severity, repro_steps, environment,"
                " reference_docs FROM tickets WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
            if not old:
                raise ValueError(f"no such ticket {ticket_id!r}")
            (from_project_id, ttype, status, reporter, assignee, priority, tier, summary,
             description, parent_id, severity, repro_steps, environment,
             reference_docs_json) = old

            to_project_id = store.resolve_project_id_internal(new_project)
            if to_project_id == from_project_id:
                raise SameProjectError(
                    f"{ticket_id} is already in project {new_project!r}"
                )
            to_prefix = conn.execute(
                "SELECT prefix FROM projects WHERE id=?", (to_project_id,)
            ).fetchone()[0]

            # Mint the new ticket_id -- same counter mechanism as create_ticket.
            row = conn.execute(
                "UPDATE counters SET value = value + 1 WHERE project_id = ?"
                " AND name = 'ticket_id' RETURNING value",
                (to_project_id,),
            ).fetchone()
            new_ticket_id = f"{to_prefix}-{row[0]}"

            create_payload = {
                "ticket_id": new_ticket_id, "project_id": to_project_id, "type": ttype,
                "reporter": reporter, "assignee": assignee, "priority": priority,
                "tier": tier, "summary": summary, "description": description,
                "parent_id": parent_id, "severity": severity, "repro_steps": repro_steps,
                "environment": environment, "custom_fields": {},
                "reassigned_from": ticket_id,
            }
            _, created_at = store.append_event_internal(
                conn, "TicketCreated", actor, create_payload,
                ticket_id=new_ticket_id, project_id=to_project_id,
            )
            conn.execute(
                "INSERT INTO tickets (ticket_id, project_id, type, status, reporter,"
                " assignee, priority, tier, summary, description, parent_id, severity,"
                " repro_steps, environment, reference_docs, archived, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (new_ticket_id, to_project_id, ttype, "open", reporter, assignee, priority,
                 tier, summary, description, parent_id, severity, repro_steps, environment,
                 "[]", created_at, created_at),
            )

            for field_name, field_value_json in conn.execute(
                "SELECT field_name, field_value FROM ticket_fields WHERE ticket_id=?",
                (ticket_id,),
            ).fetchall():
                store.append_event_internal(
                    conn, "FieldSet", actor,
                    {"field_name": field_name, "field_value": json.loads(field_value_json)},
                    ticket_id=new_ticket_id,
                )
                conn.execute(
                    "INSERT INTO ticket_fields (ticket_id, field_name, field_value)"
                    " VALUES (?,?,?)",
                    (new_ticket_id, field_name, field_value_json),
                )

            reference_docs = json.loads(reference_docs_json or "[]")
            if reference_docs:
                _, rd_updated_at = store.append_event_internal(
                    conn, "ReferenceDocsSet", actor, {"paths": reference_docs},
                    ticket_id=new_ticket_id,
                )
                conn.execute(
                    "UPDATE tickets SET reference_docs=?, updated_at=? WHERE ticket_id=?",
                    (json.dumps(reference_docs), rd_updated_at, new_ticket_id),
                )

            for c_actor, body, c_snippet in conn.execute(
                "SELECT actor, body, code_snippet FROM comments WHERE ticket_id=?"
                " ORDER BY id",
                (ticket_id,),
            ).fetchall():
                c_payload = {"body": body}
                if c_snippet is not None:
                    c_payload["code_snippet"] = c_snippet
                _, c_created_at = store.append_event_internal(
                    conn, "CommentAdded", c_actor, c_payload, ticket_id=new_ticket_id,
                )
                conn.execute(
                    "INSERT INTO comments (ticket_id, actor, body, code_snippet, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (new_ticket_id, c_actor, body, c_snippet, c_created_at),
                )

            store.append_event_internal(
                conn, "LinkAdded", actor,
                {"from": ticket_id, "to": new_ticket_id, "link_type": "relates-to"},
                ticket_id=ticket_id,
            )
            conn.execute(
                "INSERT OR IGNORE INTO ticket_links (from_ticket, to_ticket, link_type)"
                " VALUES (?,?,?)",
                (ticket_id, new_ticket_id, "relates-to"),
            )

            forward_body = (
                f"Reassigned to {new_ticket_id} in project {to_prefix!r} "
                f"({store.get_project(to_prefix)['codename']})."
            )
            _, fc_created_at = store.append_event_internal(
                conn, "CommentAdded", actor, {"body": forward_body}, ticket_id=ticket_id,
            )
            conn.execute(
                "INSERT INTO comments (ticket_id, actor, body, created_at) VALUES (?,?,?,?)",
                (ticket_id, actor, forward_body, fc_created_at),
            )

            if status != "closed" and not store.open_blockers_internal(conn, ticket_id):
                _, sc_updated_at = store.append_event_internal(
                    conn, "StatusChanged", actor,
                    {"from": status, "to": "closed"}, ticket_id=ticket_id,
                )
                conn.execute(
                    "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?",
                    ("closed", sc_updated_at, ticket_id),
                )

            return new_ticket_id

    return store.retry_internal(attempt)
