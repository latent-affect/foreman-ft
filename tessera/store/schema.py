GENESIS = "GENESIS"

MIN_SQLITE_VERSION = (3, 35, 0)

DDL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ticket_id TEXT,
    project_id INTEGER,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    idempotency_key TEXT,
    prev_hash TEXT NOT NULL UNIQUE,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_hash ON events(event_hash);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
-- project_id, not idempotency_key alone: an idempotency_key was globally unique across
-- every project sharing one store, so create_ticket(..., idempotency_key="k", project="A")
-- and a later create_ticket(..., idempotency_key="k", project="B") would silently return
-- A's ticket to a caller in B (found by Clint Eastwood's adversarial review,
-- confirmed by direct repro). Idempotency should be scoped the same way the ticket_id
-- counter already is -- per project, not global to the store.
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency
    ON events(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: DELETE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS events_prev_hash_resolves BEFORE INSERT ON events
WHEN NEW.prev_hash != 'GENESIS'
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM events WHERE event_hash = NEW.prev_hash)
        THEN RAISE(ABORT, 'prev_hash does not resolve to an existing event')
    END;
END;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codename TEXT NOT NULL,
    prefix TEXT NOT NULL UNIQUE,
    source_root TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (project_id, name)
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    reporter TEXT NOT NULL,
    assignee TEXT,
    priority INTEGER,
    tier INTEGER,
    summary TEXT,
    description TEXT,
    parent_id TEXT REFERENCES tickets(ticket_id),
    severity INTEGER,
    repro_steps TEXT,
    environment TEXT,
    reference_docs TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_criteria (
    ticket_id TEXT PRIMARY KEY REFERENCES tickets(ticket_id),
    criteria TEXT NOT NULL,
    criteria_frozen_at TEXT,
    criteria_hash_at_freeze TEXT
);

CREATE TABLE IF NOT EXISTS watchers (
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    watcher TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ticket_id, watcher)
);

CREATE TABLE IF NOT EXISTS ticket_fields (
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    field_name TEXT NOT NULL,
    field_value TEXT,
    PRIMARY KEY (ticket_id, field_name)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    actor TEXT NOT NULL,
    body TEXT NOT NULL,
    code_snippet TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_links (
    from_ticket TEXT NOT NULL REFERENCES tickets(ticket_id),
    to_ticket TEXT NOT NULL REFERENCES tickets(ticket_id),
    link_type TEXT NOT NULL,
    PRIMARY KEY (from_ticket, to_ticket, link_type)
);

CREATE TABLE IF NOT EXISTS ticket_commit_links (
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    stage TEXT NOT NULL,
    commit_sha TEXT,
    branch TEXT,
    stale INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ticket_id, stage)
);

CREATE TABLE IF NOT EXISTS stage_heads (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    stage TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, stage)
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    actor TEXT NOT NULL,
    summary TEXT NOT NULL,
    files_touched TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- A named, cross-project, event-sourced worklist -- e.g. a standup list, an
-- ACR-style review batch, a build-phase bugfix batch. `name` is the human-facing handle
-- (CLI/HTTP address hotlists by name, not id), unlike `projects.prefix` there is no
-- domain reason to keep it terse -- "standup-2026-08-16" is a fine name. No project_id
-- column: a hotlist item is a ticket_id reference, which already discloses its project
-- via prefix, so one hotlist can span multiple projects by design (a real standup or ACR
-- batch is not scoped to one project).
CREATE TABLE IF NOT EXISTS hotlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hotlist_items (
    hotlist_id INTEGER NOT NULL REFERENCES hotlists(id),
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    added_at TEXT NOT NULL,
    added_by TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (hotlist_id, ticket_id)
);

-- BigQuery-style dataset selector for the SQL tab. A dataset is a NAMED GROUP
-- OF PROJECTS -- distinct from `projects` itself, event-sourced (real custom datasets are
-- a genuine, audited fact, same reasoning as hotlists). store.list_datasets() merges
-- these real rows with an IMPLICIT one-per-project entry derived live from `projects`
-- (never duplicated here) -- "right now is probably just projects, but wired so custom
-- datasets can be selected too."
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_projects (
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    added_at TEXT NOT NULL,
    added_by TEXT NOT NULL,
    PRIMARY KEY (dataset_id, project_id)
);

CREATE TABLE IF NOT EXISTS column_descriptions (
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    description TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    PRIMARY KEY (table_name, column_name)
);

-- ATLAS ingest (atlas/ingest/tessera_pull.py) SELECTs FROM v_flat.
-- This view is the packaged producer of that contract. Column set is a
-- superset of TESSERA_EVENT_COLUMNS plus event_id. Actor-kind does not
-- special-case a personal name; migration scripts vs everything else.
CREATE VIEW IF NOT EXISTS v_flat AS
SELECT
    e.id AS event_id,
    e.event_type AS event_type,
    e.created_at AS event_ts,
    e.actor AS actor,
    CASE
        WHEN e.actor LIKE 'migration%' THEN 'migration'
        ELSE 'agent'
    END AS actor_kind,
    e.ticket_id AS ticket_id,
    p.prefix AS project_prefix,
    t.type AS ticket_type,
    t.status AS ticket_status,
    CASE WHEN t.status IN ('closed', 'wontfix', 'duplicate') THEN 1 ELSE 0 END
        AS ticket_is_closed,
    t.priority AS ticket_priority,
    t.severity AS ticket_severity,
    CASE WHEN tc.ticket_id IS NOT NULL THEN 1 ELSE 0 END
        AS ticket_has_frozen_criteria,
    CASE
        WHEN tc.criteria_frozen_at IS NOT NULL
             AND tc.criteria_frozen_at <= t.created_at THEN 1
        ELSE 0
    END AS ticket_criteria_frozen_before_work,
    CASE
        WHEN tc.criteria IS NOT NULL THEN json_array_length(tc.criteria)
        ELSE 0
    END AS ticket_criteria_count,
    (SELECT COUNT(*) FROM claims c WHERE c.ticket_id = t.ticket_id)
        AS ticket_claim_count,
    CAST(NULL AS REAL) AS ticket_lead_time_hours,
    CASE
        WHEN e.event_type = 'CommentAdded'
             AND cm.code_snippet IS NOT NULL
             AND length(cm.code_snippet) > 0 THEN 1
        ELSE 0
    END AS comment_has_code_snippet,
    json_extract(e.payload, '$.from') AS status_from,
    json_extract(e.payload, '$.to') AS status_to
FROM events e
LEFT JOIN tickets t ON t.ticket_id = e.ticket_id
LEFT JOIN projects p ON p.id = COALESCE(t.project_id, e.project_id)
LEFT JOIN ticket_criteria tc ON tc.ticket_id = e.ticket_id
LEFT JOIN comments cm
    ON e.event_type = 'CommentAdded'
   AND cm.ticket_id = e.ticket_id
   AND cm.created_at = e.created_at;
"""

PROJECTION_TABLES = (
    "projects",
    "counters",  # previously uncovered; see rebuild_projection()'s derivation
    "tickets",
    "ticket_fields",
    "ticket_criteria",
    "comments",
    "ticket_links",
    "ticket_commit_links",
    "stage_heads",
    "claims",
    "attachments",
    "watchers",
    "hotlists",
    "hotlist_items",
    "datasets",
    "dataset_projects",
    "column_descriptions",
)

# PRQ-style tiers (foreman-design.html section 04/05's coupling-span mechanism, named per
# the operator's own plain-language PRQ cross-reference -- the source document's mechanism
# wins if the two ever differ; this project only implements the ticket-schema side of it,
# not the live coupling-measurement hook, which is tracked separately in FORE).
TIER_PAPER_QUAL = 1   # contained, no interface crossed -- documented rationale is enough
TIER_NPI_LIGHT = 2    # crosses an existing interface / another component depends on it
TIER_FULL_NPI = 3     # new component, new interface, or foundational
TIERS = (TIER_PAPER_QUAL, TIER_NPI_LIGHT, TIER_FULL_NPI)

TIER_NAMES = {
    TIER_PAPER_QUAL: "Paper Qual",
    TIER_NPI_LIGHT: "NPI-Light",
    TIER_FULL_NPI: "Full NPI",
}

# Ordered 0-4 int scale for both severity and priority, Jira-style: 0=Highest,
# 4=Lowest. Displayed as S0-S4 / P0-P4 -- stored as a real, sortable/filterable int, not
# free text (previously entirely unconstrained: no validation existed at all). Shared
# between severity and priority since both use the same 5-level scale, just a different
# display prefix.
LEVEL_HIGHEST = 0
LEVEL_HIGH = 1
LEVEL_MEDIUM = 2
LEVEL_LOW = 3
LEVEL_LOWEST = 4
LEVELS = (LEVEL_HIGHEST, LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW, LEVEL_LOWEST)

LEVEL_NAMES = {
    LEVEL_HIGHEST: "Highest", LEVEL_HIGH: "High", LEVEL_MEDIUM: "Medium",
    LEVEL_LOW: "Low", LEVEL_LOWEST: "Lowest",
}

# One-time historical fact, same pattern as TicketCreated's project_id/tier fallback
# handling: events written before this migration carry severity/priority as free text
# ("high"/"medium"/"low") in their immutable, hash-chained payload. This is the exact,
# confirmed (queried directly, not assumed) set of values that ever existed in the real
# db before the migration -- "critical" and "lowest" never appeared, so replaying an
# event with either of those legacy strings would indicate genuinely unknown historical
# data and should raise, not silently guess.
LEGACY_LEVEL_TEXT = {"high": LEVEL_HIGH, "medium": LEVEL_MEDIUM, "low": LEVEL_LOW}

SUBTASK_PARENT_TYPES = ("Story", "Task", "Bug")
TICKET_TYPES = ("Epic", "Story", "Task", "Sub-task", "Bug")
LINK_TYPES = ("blocks", "blocked-by", "relates-to")

# The single source of truth for "these two link types describe the same edge in
# opposite directions". Used by add_link()'s cycle check, its reciprocal-row insert, and
# replay_event_internal()'s replay of the same event -- a second code-review pass found
# these three encoded the same relationship independently, so a future link-type synonym
# added to LINK_TYPES could update this mapping in one call site and not the others,
# quietly reintroducing the "cycle detection only fires for one spelling" bug this
# constant exists to prevent. Types with no reciprocal (e.g. "relates-to") are absent.
RECIPROCAL_LINK_TYPE = {"blocks": "blocked-by", "blocked-by": "blocks"}

# Legal workflow transitions. Anything not listed as a value of the current status is rejected.
WORKFLOW_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"open", "in_review", "closed"},
    "in_review": {"in_progress", "closed"},
    "closed": {"open"},
}


def parse_sqlite_version(version_string):
    return tuple(int(p) for p in version_string.split(".")[:3])


def init_schema(conn):
    conn.executescript(DDL)
