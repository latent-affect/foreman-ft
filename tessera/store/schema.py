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
    created_at TEXT NOT NULL,
    actor_ref TEXT
);

-- REQ-6 (Foreman v2.0 PRD): additive, nullable actor_ref alongside the existing `actor`
-- field -- NOT a replacement, and NOT part of the hash-chained payload (this column is
-- outside events_no_update/events_no_delete's own scope only in the sense that the whole
-- ROW stays append-only; actor_ref itself carries no chain-integrity meaning, it is a plain
-- optional identity pointer). New databases get this column from CREATE TABLE directly;
-- an ALREADY-EXISTING events table (this project's own live data/tessera.db) needs the
-- real ALTER TABLE migration in ensure_actor_ref_column() below, since CREATE TABLE IF NOT
-- EXISTS is a no-op against a table that already exists, column differences included.
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_hash ON events(event_hash);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
-- project_id, not idempotency_key alone: an idempotency_key was globally unique across
-- every project sharing one store, so create_ticket(..., idempotency_key="k", project="A")
-- and a later create_ticket(..., idempotency_key="k", project="B") would silently return
-- A's ticket to a caller in B (TESS-23, found by Clint Eastwood's adversarial review,
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

-- TESS-174: `status` makes a dead registration distinguishable from a live one in
-- list-projects output, without deleting the row (this project is append-only: nothing
-- silently disappears). Registration itself still isn't an event, but every LATER change
-- to a mutable project column is -- see ProjectStatusChanged / ProjectSourceRootChanged in
-- store.py, and rebuild_projection()'s seeding, which replays those on top of the row's
-- registration-time baseline rather than trusting the live value. An ALREADY-EXISTING
-- projects table needs ensure_project_status_column() below; CREATE TABLE IF NOT EXISTS is
-- a no-op against a table that already exists, column differences included.
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codename TEXT NOT NULL,
    prefix TEXT NOT NULL UNIQUE,
    source_root TEXT,
    status TEXT NOT NULL DEFAULT 'active',
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

-- A named, cross-project, event-sourced worklist (TESS-36) -- e.g. a standup list, an
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

-- BigQuery-style dataset selector for the SQL tab (TESS-47). A dataset is a NAMED GROUP
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

-- REQ-6 (Foreman v2.0 PRD): a separate, mutable, NOT hash-chained table for real identity
-- data -- deliberately outside event-sourcing/PROJECTION_TABLES (it is not derived from
-- events and rebuild_projection() must never compare it, since it is intended to be edited
-- directly, unlike every projection table above). No OAuth, no DSAR tooling, no directory
-- integration ships until a real second tenant exists (this PRD's own stated scope boundary)
-- -- this table exists as a real, present destination for actor_ref values, nothing more.
CREATE TABLE IF NOT EXISTS identity_registry (
    actor_ref TEXT PRIMARY KEY,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

PROJECTION_TABLES = (
    "projects",
    "counters",  # TESS-32: previously uncovered; see rebuild_projection()'s derivation
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

# Ordered 0-4 int scale for both severity and priority, Jira-style (TESS-44): 0=Highest,
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

# TESS-174. A project registration's lifecycle state. 'archived' means the registration
# is stale -- the directory it names is gone, or the work is over -- and it must stop
# resolving to a real repo (see tessguard/project_resolve.py, which skips archived rows by
# default). Deliberately a two-value set rather than free text: every value here is
# something a caller can branch on, and an unvalidated status column would recreate exactly
# the "silently indistinguishable" problem TESS-174 exists to fix.
PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"
PROJECT_STATUSES = (PROJECT_STATUS_ACTIVE, PROJECT_STATUS_ARCHIVED)

# TESS-178. Event types that are deliberately part of the hash chain but have NO effect on
# any projection table -- pure audit records. Both of these log a FINDING ABOUT the system
# (a claim-vs-diff discrepancy result; a ticket closed without any claim to check against)
# rather than a state change within it, so there is nothing for replay_event_internal() to
# rebuild from them. Verified by execution, not assumed from reading the emitters: emitting
# either against a live store appends the event and changes zero projection tables.
#
# Writing replay clauses for them would be worse than the bug, not better -- it would invent
# projection state the live write path never creates, so rebuild would diverge from live in
# the opposite direction and the check would pass only where both sides were equally wrong.
#
# This is an EXPLICIT ENUMERATION and must stay one. The point of replay_event_internal()
# raising on an unrecognized event type is that an event which really does mutate projected
# state cannot be added without someone deciding how it replays; widening this into a
# blanket `except` or a bare `pass` branch would convert that loud gap into a silent one and
# leave the codebase worse off than the defect this constant was added to fix. Same
# principle as NON_EVENT_SOURCED_COLUMNS on a different axis (a whole event type with no
# projection effect, versus one column not derived from events): an exclusion is fine, an
# undocumented exclusion is not.
#
# Adding a type here is a real claim that it writes nothing. That claim is enforced, not
# trusted -- see test_projection_neutral_events.py, which emits every type on this list
# through its own real store method and asserts the projection is unchanged.
PROJECTION_NEUTRAL_EVENTS = frozenset({
    "ClaimDiscrepancyChecked",
    "ClosedWithNoClaim",
})

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

# TESS-192. The closed vocabulary of reasons a ticket may be closed with no claim behind
# it. Closed, not free text, for the same reason LINK_TYPES is closed: a free-text field
# is unqueryable in aggregate, and the whole point of this mechanism is that someone can
# later ask "how many closes this week rested on nobody having checked anything."
#
# Each of the first three asserts a specific fact a reader could go and falsify:
# this duplicates that one, this will not be done, this was replaced by that. The fourth
# asserts only that a human or agent looked and formed a judgment, which is a legitimate
# thing to do and the one that carries no evidence anyone else can re-derive. That is why
# it, alone, is rate limited below.
NO_CLAIM_REASONS = ("duplicate", "wont-fix", "superseded-by", "disposition-pass")

# TESS-192. Per actor, per rolling 60 seconds, for "disposition-pass" only.
#
# Why 1 and not a friendlier number. TESS-192's acceptance test is that replaying
# ticket-system-bd's real 2026-09-03 burst -- 12 closes in 1.2529 seconds -- must refuse
# AFTER THE FIRST. Any limit above 1 fails that test by construction, so the requirement
# pins this value; it was not chosen for elegance and it should not be raised without
# amending the criterion it was derived from.
#
# It is also defensible on its own terms. A disposition pass is the one close that ships
# no evidence, so its only remaining cost is the closer's attention, and attention is
# exactly what a 12-per-second loop has stopped spending. One per minute makes a
# twenty-ticket sweep take twenty minutes, which is the real cost of this decision and is
# stated here rather than discovered later: the intended response to hitting this limit is
# to record a claim, not to wait. An operator who genuinely wants a fast bulk disposition
# should reach for a dedicated bulk verb that says so, which does not exist yet.
DISPOSITION_PASS_PER_MINUTE = 1

# Legal workflow transitions. Anything not listed as a value of the current status is rejected.
WORKFLOW_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"open", "in_review", "closed"},
    "in_review": {"in_progress", "closed"},
    "closed": {"open"},
}


def parse_sqlite_version(version_string):
    return tuple(int(p) for p in version_string.split(".")[:3])


def ensure_actor_ref_column(conn):
    """REQ-6: adds `events.actor_ref` for a database that already existed before this
    column was added to DDL -- `CREATE TABLE IF NOT EXISTS` is a no-op against an
    already-existing table, column differences included, so a real, separate `ALTER TABLE`
    step is required for any live database created before this change. Idempotent: checks
    `PRAGMA table_info` first, since SQLite's `ALTER TABLE ADD COLUMN` has no `IF NOT
    EXISTS` form and errors on a duplicate column. Nullable, additive, does not touch
    `actor` or any hash-chain trigger -- verified directly against a real copy of this
    project's own live database before this function was written: row count preserved,
    `verify_chain()` reports zero hash_mismatches after the alter, a real INSERT after the
    alter still succeeds (the append-only/hash-chain triggers are untouched by this)."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
    if "actor_ref" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN actor_ref TEXT")


def ensure_project_status_column(conn):
    """TESS-174: adds `projects.status` for a database that already existed before this
    column was added to DDL -- same reasoning and same idempotent PRAGMA check as
    ensure_actor_ref_column() above (SQLite's ALTER TABLE ADD COLUMN has no IF NOT EXISTS
    form and errors on a duplicate column).

    NOT NULL DEFAULT 'active' is applied to existing rows by SQLite itself: a non-null
    constant default backfills every existing row at ALTER time, so a pre-existing project
    is 'active' after migration, which is the correct reading of a registration nobody has
    yet marked stale. Additive, touches no other column and no trigger."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(projects)")]
    if "status" not in cols:
        conn.execute(
            f"ALTER TABLE projects ADD COLUMN status TEXT NOT NULL DEFAULT '{PROJECT_STATUS_ACTIVE}'"
        )


def init_schema(conn):
    conn.executescript(DDL)
    ensure_actor_ref_column(conn)
    ensure_project_status_column(conn)
