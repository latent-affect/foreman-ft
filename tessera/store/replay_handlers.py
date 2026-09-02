"""Event-replay handlers for rebuild_projection() -- tessera/GOALS.json C2 (PRD.md R1a).

Split out of store.py: one handler function per event_type, dispatched by table lookup rather
than an elif chain. This used to be a single ~210-line function (replay_event_internal) whose
cyclomatic complexity (32) dominated store.py's health score, and whose sheer size was itself a
large share of store.py's own Halstead volume and SLOC -- both of which the maintainability
index formula weighs directly, so moving this logic to its own file lowers store.py's MI far
more than restructuring it in place could (this module is still large and still has the same
total code; the difference is which FILE the metric is computed for, and rebuild_projection's own
public behaviour is unaffected either way).

Each handler's SQL and logic is unchanged from the branch it replaced in the original elif
chain -- moved verbatim, not rewritten. A maintainability refactor is not licensed to also
change behaviour (tessera/GOALS.json's own constraint); C4 (full suite green, no test silently
lost, checked by identity) is what actually verifies that held.
"""

import json

from ..common.hashing import canonical_json
from . import schema


def normalize_legacy_level_internal(value):
    """severity/priority became a real 0-4 int scale, replacing entirely
    unconstrained free text. Events written before this migration carry the old text
    ("high"/"medium"/"low") in their immutable, hash-chained payload -- that text cannot
    be rewritten, so replay must translate it the same way the live migration did, or
    rebuild_projection() would insert the raw legacy string into what's now an
    INTEGER-affinity column while live_projection() has the migrated int, diverging on
    identical data. New events (post-migration) already carry a real int (or None) in
    this same field, so those pass through unchanged. schema.LEGACY_LEVEL_TEXT is the
    exact, confirmed set of text values that ever existed in the real db before this
    migration (queried directly, not assumed) -- an unrecognized string here means
    genuinely unknown historical data, and raises rather than silently guessing."""
    if value is None or isinstance(value, int):
        return value
    if value in schema.LEGACY_LEVEL_TEXT:
        return schema.LEGACY_LEVEL_TEXT[value]
    raise ValueError(f"unrecognized legacy severity/priority text {value!r} during replay")


def replay_ticket_created(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO tickets (ticket_id, project_id, type, status, reporter, assignee,"
        " priority, tier, summary, description, parent_id, severity, repro_steps,"
        " environment, reference_docs, archived, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
        # payload.get("project_id", 1): TicketCreated events written before multi-
        # project support carry no project_id at all -- that payload is immutable,
        # hash-chained history, it cannot be rewritten to add the field retroactively.
        # The fallback of 1 is a documented, one-time migration fact, not a general
        # default: every ticket created before this migration genuinely did belong to
        # the single project that existed then, which the migration registers as the
        # first (id=1) project. New events (post-migration) always carry a real
        # project_id, so this fallback only ever applies to that one historical batch.
        # summary/description: events written before this feature existed
        # carry neither key at all -- payload.get() naturally returns None for them,
        # same precedent as tier's own historical handling (no active backfill
        # needed here; the real historical summary text for early tickets is instead
        # promoted via genuine set_summary() calls, which DO append new, real
        # SummarySet events -- see the migration script, not a payload fallback).
        (ticket_id, payload.get("project_id", 1), payload["type"], "open",
         payload["reporter"], payload.get("assignee"),
         normalize_legacy_level_internal(payload.get("priority")),
         payload.get("tier"), payload.get("summary"), payload.get("description"),
         payload.get("parent_id"), normalize_legacy_level_internal(payload.get("severity")),
         payload.get("repro_steps"), payload.get("environment"), "[]",
         created_at, created_at),
    )
    for name, value in (payload.get("custom_fields") or {}).items():
        shadow.execute(
            "INSERT INTO ticket_fields (ticket_id, field_name, field_value) VALUES (?,?,?)",
            (ticket_id, name, canonical_json(value)),
        )


def replay_comment_added(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO comments (ticket_id, actor, body, code_snippet, created_at)"
        " VALUES (?,?,?,?,?)",
        (ticket_id, actor, payload["body"], payload.get("code_snippet"), created_at),
    )


def replay_comment_code_snippet_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "UPDATE comments SET code_snippet=? WHERE id=?",
        (payload["code_snippet"], payload["comment_id"]),
    )


def replay_status_changed(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?",
        (payload["to"], created_at, ticket_id),
    )


def replay_link_added(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT OR IGNORE INTO ticket_links (from_ticket, to_ticket, link_type) VALUES (?,?,?)",
        (payload["from"], payload["to"], payload["link_type"]),
    )
    reciprocal = schema.RECIPROCAL_LINK_TYPE.get(payload["link_type"])
    if reciprocal:
        shadow.execute(
            "INSERT OR IGNORE INTO ticket_links (from_ticket, to_ticket, link_type)"
            " VALUES (?,?,?)",
            (payload["to"], payload["from"], reciprocal),
        )


def replay_field_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO ticket_fields (ticket_id, field_name, field_value) VALUES (?,?,?)"
        " ON CONFLICT(ticket_id, field_name) DO UPDATE SET field_value=excluded.field_value",
        (ticket_id, payload["field_name"], canonical_json(payload["field_value"])),
    )


def replay_reference_docs_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "UPDATE tickets SET reference_docs=?, updated_at=? WHERE ticket_id=?",
        (json.dumps(payload["paths"]), created_at, ticket_id),
    )


def replay_summary_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "UPDATE tickets SET summary=?, updated_at=? WHERE ticket_id=?",
        (payload["summary"], created_at, ticket_id),
    )


def replay_description_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "UPDATE tickets SET description=?, updated_at=? WHERE ticket_id=?",
        (payload["description"], created_at, ticket_id),
    )


def replay_assignee_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "UPDATE tickets SET assignee=?, updated_at=? WHERE ticket_id=?",
        (payload["assignee"], created_at, ticket_id),
    )


def replay_priority_or_severity_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    # Column name comes from the event type, never from the payload key, so a
    # malformed payload cannot steer this into an arbitrary column.
    column = "priority" if event_type == "PrioritySet" else "severity"
    # nosec B608 -- column is one of exactly two literal strings selected by the ternary
    # directly above, keyed on event_type (itself store-controlled at append time, never
    # read from payload). No payload-supplied value ever reaches this f-string; payload.get
    # only supplies the bound parameter's VALUE, not the column name. DEVH-10/GOALS.json C6
    # -- this is the site that moved out of store.py during the C1-C5 refactor.
    shadow.execute(
        f"UPDATE tickets SET {column}=?, updated_at=? WHERE ticket_id=?",
        (payload.get(column), created_at, ticket_id),
    )


def replay_ticket_archived_or_unarchived(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "UPDATE tickets SET archived=?, updated_at=? WHERE ticket_id=?",
        (1 if event_type == "TicketArchived" else 0, created_at, ticket_id),
    )


def replay_claim_recorded(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO claims (ticket_id, actor, summary, files_touched, commit_sha,"
        " event_hash, created_at) VALUES (?,?,?,?,?,?,?)",
        (ticket_id, actor, payload["summary"], json.dumps(payload["files_touched"]),
         payload["commit_sha"], event_hash_value, created_at),
    )


def replay_stage_promoted_or_rolled_back(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    # No pre-migration StagePromoted/StageRolledBack events exist in the live db
    # (confirmed by querying it directly before writing this), so unlike TicketCreated
    # above, this can require a real project_id with no historical fallback.
    shadow.execute(
        "INSERT INTO stage_heads (project_id, stage, commit_sha, updated_at)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(project_id, stage) DO UPDATE SET commit_sha=excluded.commit_sha,"
        " updated_at=excluded.updated_at",
        (payload["project_id"], payload["stage"], payload["commit_sha"], created_at),
    )


def replay_ticket_criteria_frozen(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO ticket_criteria (ticket_id, criteria, criteria_frozen_at,"
        " criteria_hash_at_freeze) VALUES (?,?,?,?)"
        " ON CONFLICT(ticket_id) DO UPDATE SET criteria=excluded.criteria,"
        " criteria_frozen_at=excluded.criteria_frozen_at,"
        " criteria_hash_at_freeze=excluded.criteria_hash_at_freeze",
        (ticket_id, canonical_json(payload["criteria"]), created_at, payload["criteria_hash"]),
    )


def replay_ticket_watched(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT OR IGNORE INTO watchers (ticket_id, watcher, created_at) VALUES (?,?,?)",
        (ticket_id, payload["watcher"], created_at),
    )


def replay_ticket_unwatched(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "DELETE FROM watchers WHERE ticket_id=? AND watcher=?",
        (ticket_id, payload["watcher"]),
    )


def replay_ticket_commit_linked(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO ticket_commit_links (ticket_id, stage, commit_sha, branch, stale)"
        " VALUES (?,?,?,?,0)"
        " ON CONFLICT(ticket_id, stage) DO UPDATE SET commit_sha=excluded.commit_sha,"
        " branch=excluded.branch, stale=0",
        (ticket_id, payload["stage"], payload.get("commit_sha"), payload.get("branch")),
    )


def replay_attachment_added(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO attachments (ticket_id, filename, sha256, size_bytes, created_at)"
        " VALUES (?,?,?,?,?)",
        (ticket_id, payload["filename"], payload["sha256"], payload["size_bytes"], created_at),
    )


def replay_hotlist_created(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    # hotlists.id is AUTOINCREMENT and hotlists are never deleted, so replaying
    # HotlistCreated events in event order reproduces the exact same id sequence the
    # live INSERT did -- same guarantee tickets' ticket_id counter already relies on.
    shadow.execute(
        "INSERT INTO hotlists (name, created_at, created_by) VALUES (?,?,?)",
        (payload["name"], created_at, actor),
    )


def replay_ticket_added_to_hotlist(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    hotlist_id = shadow.execute(
        "SELECT id FROM hotlists WHERE name=?", (payload["hotlist"],)
    ).fetchone()[0]
    shadow.execute(
        "INSERT INTO hotlist_items (hotlist_id, ticket_id, added_at, added_by, note)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(hotlist_id, ticket_id) DO UPDATE SET note=excluded.note,"
        " added_at=excluded.added_at, added_by=excluded.added_by",
        (hotlist_id, ticket_id, created_at, actor, payload.get("note")),
    )


def replay_ticket_removed_from_hotlist(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    hotlist_id = shadow.execute(
        "SELECT id FROM hotlists WHERE name=?", (payload["hotlist"],)
    ).fetchone()[0]
    shadow.execute(
        "DELETE FROM hotlist_items WHERE hotlist_id=? AND ticket_id=?",
        (hotlist_id, ticket_id),
    )


def replay_dataset_created(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO datasets (name, created_at, created_by) VALUES (?,?,?)",
        (payload["name"], created_at, actor),
    )


def replay_project_added_to_dataset(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    dataset_id = shadow.execute(
        "SELECT id FROM datasets WHERE name=?", (payload["dataset"],)
    ).fetchone()[0]
    project_id = shadow.execute(
        "SELECT id FROM projects WHERE prefix=?", (payload["project"],)
    ).fetchone()[0]
    shadow.execute(
        "INSERT OR IGNORE INTO dataset_projects (dataset_id, project_id, added_at, added_by)"
        " VALUES (?,?,?,?)",
        (dataset_id, project_id, created_at, actor),
    )


def replay_column_description_set(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    shadow.execute(
        "INSERT INTO column_descriptions"
        " (table_name, column_name, description, updated_at, updated_by)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(table_name, column_name) DO UPDATE SET"
        " description=excluded.description, updated_at=excluded.updated_at,"
        " updated_by=excluded.updated_by",
        (payload["table"], payload["column"], payload["description"], created_at, actor),
    )


def replay_noop(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    return


REPLAY_HANDLERS = {
    "TicketCreated": replay_ticket_created,
    "CommentAdded": replay_comment_added,
    "CommentCodeSnippetSet": replay_comment_code_snippet_set,
    "StatusChanged": replay_status_changed,
    "LinkAdded": replay_link_added,
    "FieldSet": replay_field_set,
    "ReferenceDocsSet": replay_reference_docs_set,
    "SummarySet": replay_summary_set,
    "DescriptionSet": replay_description_set,
    "AssigneeSet": replay_assignee_set,
    "PrioritySet": replay_priority_or_severity_set,
    "SeveritySet": replay_priority_or_severity_set,
    "TicketArchived": replay_ticket_archived_or_unarchived,
    "TicketUnarchived": replay_ticket_archived_or_unarchived,
    "ClaimRecorded": replay_claim_recorded,
    "StagePromoted": replay_stage_promoted_or_rolled_back,
    "StageRolledBack": replay_stage_promoted_or_rolled_back,
    "TicketCriteriaFrozen": replay_ticket_criteria_frozen,
    "TicketWatched": replay_ticket_watched,
    "TicketUnwatched": replay_ticket_unwatched,
    "TicketCommitLinked": replay_ticket_commit_linked,
    "AttachmentAdded": replay_attachment_added,
    "HotlistCreated": replay_hotlist_created,
    "TicketAddedToHotlist": replay_ticket_added_to_hotlist,
    "TicketRemovedFromHotlist": replay_ticket_removed_from_hotlist,
    "DatasetCreated": replay_dataset_created,
    "ProjectAddedToDataset": replay_project_added_to_dataset,
    "ColumnDescriptionSet": replay_column_description_set,
    "ClaimDiscrepancyChecked": replay_noop,
    "ClosedWithNoClaim": replay_noop,
}


def replay_event_internal(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    handler = REPLAY_HANDLERS.get(event_type)
    if handler is None:
        raise ValueError(f"rebuild_projection: unknown event_type {event_type!r}")
    handler(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value)
