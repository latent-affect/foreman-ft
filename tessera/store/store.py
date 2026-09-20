import contextlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..common.git_refs import validate_commit_sha
from ..common.hashing import canonical_json, event_hash as compute_event_hash, sha256_hex
from ..common.timestamps import iso_minus_seconds, utc_now_iso
from . import schema
from .exceptions import (
    BlockedError, ClaimRequiredError, CriteriaNotFrozenError, CycleError, HierarchyError,
    NoClaimRateLimitError, SameProjectError, UnknownProjectError, UnsupportedSQLiteVersion,
    WorkflowError,
)

RETRY_ATTEMPTS = 15
BUSY_TIMEOUT_MS = 5000


def render_current_state_text(hotlist, budget_chars):
    """REQ-25 (Foreman v2.0 PRD) Phase 1: renders a hotlist dict (the real shape
    `Store.get_hotlist()` returns) as compact context text, one line per item, truncated to
    `budget_chars` at ITEM granularity only -- never mid-line. A real bug found and fixed
    during this design's own adversarial self-attack (per FORE-38's design pass): truncating
    at an arbitrary character offset can cut a line in half, producing a malformed, confusing
    fragment in injected context -- worse than omitting the item entirely. This function
    instead renders each item's full line, and stops BEFORE adding a line that would push the
    total past budget_chars, so every included item is always a complete, real line.

    Pure function, no DB dependency -- independently testable against a synthetic hotlist
    dict, not only against a live store.

    Returns a string with a header line, one line per included item
    (`<ticket_id> [<status>] <summary>`), and -- if any items were omitted for budget --
    a trailing line naming how many were dropped, so a reader can tell "nothing else
    mattered" apart from "more existed and was cut for space"."""
    header = f"# current-state: {hotlist['name']} ({len(hotlist['items'])} items)\n"
    lines = []
    for item in hotlist["items"]:
        summary = (item.get("summary") or "").strip()
        lines.append(f"{item['ticket_id']} [{item.get('status', '?')}] {summary}")

    included = []
    used = len(header)
    omitted_count = 0
    for line in lines:
        candidate_len = used + len(line) + 1  # +1 for the newline that will join it
        if candidate_len > budget_chars and included:
            omitted_count = len(lines) - len(included)
            break
        included.append(line)
        used = candidate_len
    else:
        omitted_count = 0

    body = "\n".join(included)
    footer = f"\n... {omitted_count} more item(s) omitted for budget\n" if omitted_count else ""
    return header + body + footer


class Store:
    """A single Store (one db file) can hold MULTIPLE projects -- register_project() adds
    one. For backward compatibility with the original single-project design (and every
    existing call site/test that predates multi-project support), passing codename+prefix
    to __init__ still works exactly as before: it registers that project and remembers it
    as the DEFAULT, so every method that doesn't explicitly name a project (create_ticket's
    `project=` kwarg, stage methods' `project=` kwarg) falls back to it. Nothing that
    already worked single-project needs to change to keep working."""

    def __init__(self, db_path, codename=None, prefix=None, blobs_dir=None):
        self.db_path = str(db_path)
        self.blobs_dir = Path(blobs_dir) if blobs_dir else Path(self.db_path).parent / "blobs"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.local_internal = threading.local()
        self.default_project_prefix = None
        self.check_sqlite_version_internal()
        conn = self.conn_internal()
        schema.init_schema(conn)
        conn.commit()
        if codename or prefix:
            self.register_project(codename, prefix)
            self.default_project_prefix = prefix
        elif self.list_projects():
            # Reopening an existing multi-project db with no codename/prefix given: keep
            # the historical "first ever registered project" as default, for the many
            # existing call sites (CLI, tests) that assume Store(db_path) alone is enough
            # once a db file already has a project in it.
            self.default_project_prefix = self.list_projects()[0]["prefix"]

    # ---- connection / transaction management -----------------------------

    def check_sqlite_version_internal(self):
        actual = schema.parse_sqlite_version(sqlite3.sqlite_version)
        if actual < schema.MIN_SQLITE_VERSION:
            raise UnsupportedSQLiteVersion(
                f"tessera requires SQLite >= {'.'.join(map(str, schema.MIN_SQLITE_VERSION))} "
                f"for UPDATE...RETURNING; host has {sqlite3.sqlite_version}"
            )

    def conn_internal(self):
        conn = getattr(self.local_internal, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
            # PRAGMA journal_mode=WAL briefly needs an exclusive lock to switch modes,
            # and on a brand-new database file with several processes/threads racing to
            # create it for the first time, that specific PRAGMA can raise "database is
            # locked" immediately -- found by direct repro (60 trials, 10 real
            # concurrent Store() constructions): it fired here, before any of store's
            # own write-transaction/retry machinery was even reached, at 0.0004s (not a
            # busy_timeout expiry, an immediate failure setting the mode itself). The
            # PRAGMA connect-time `timeout=` argument does not cover this specific case,
            # so it needs its own short local retry, not delegation to retry_internal
            # (which is itself built on a connection this method is still constructing).
            attempts = 10
            delay = 0.01
            for attempt in range(attempts):
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    msg = str(exc).lower()
                    if "locked" not in msg and "busy" not in msg:
                        raise
                    if attempt == attempts - 1:
                        raise
                    time.sleep(delay)
                    delay = min(delay * 1.5, 0.5)
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys=ON")
            self.local_internal.conn = conn
        return conn

    @contextlib.contextmanager
    def write_txn_internal(self):
        conn = self.conn_internal()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")

    def retry_internal(self, fn, *args, **kwargs):
        delay = 0.01
        last_exc = None
        for _ in range(RETRY_ATTEMPTS):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 1.5, 1.0)
        raise last_exc

    def current_tip_internal(self, conn):
        row = conn.execute("SELECT event_hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else schema.GENESIS

    def append_event_internal(self, conn, event_type, actor, payload, ticket_id=None,
                              idempotency_key=None, project_id=None):
        """project_id is a plain filtering column, deliberately NOT part of hashed_content
        -- it was added after the original hash-chain design shipped with real events
        already hashed under the old formula, and putting project_id in the hash now would
        invalidate every existing event's hash retroactively for no real tamper-evidence
        benefit (ticket_id, which IS hashed, already discloses project via its prefix).
        If not given explicitly and ticket_id is, looked up from the live tickets row --
        every ticket-scoped write already has one row to check, no new query shape."""
        if project_id is None and ticket_id is not None:
            row = conn.execute(
                "SELECT project_id FROM tickets WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            if row:
                project_id = row[0]

        prev_hash = self.current_tip_internal(conn)
        created_at = utc_now_iso()
        hashed_content = {
            "event_type": event_type,
            "ticket_id": ticket_id,
            "actor": actor,
            "payload": payload,
            "prev_hash": prev_hash,
            "created_at": created_at,
        }
        ehash = compute_event_hash(hashed_content)
        conn.execute(
            "INSERT INTO events (event_type, ticket_id, project_id, actor, payload,"
            " idempotency_key, prev_hash, event_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (event_type, ticket_id, project_id, actor, canonical_json(payload),
             idempotency_key, prev_hash, ehash, created_at),
        )
        return ehash, created_at

    # ---- bootstrap / multi-project registration --------------------------

    def register_project(self, codename, prefix, source_root=None):
        """Idempotent: if `prefix` is already registered, returns its existing project_id
        (codename/source_root on the existing row are NOT overwritten by a second call --
        re-registering is a no-op, not a silent rename). Otherwise creates the project and
        its ticket_id counter in the same transaction, and returns the new project_id."""
        existing = self.get_project(prefix)
        if existing is not None:
            return existing["id"]

        if not codename or not prefix:
            raise ValueError("register_project requires both a codename and a prefix")

        def attempt():
            with self.write_txn_internal() as conn:
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
            return self.retry_internal(attempt)
        except sqlite3.IntegrityError:
            # Two processes racing to register the same new prefix for the first time:
            # the loser's INSERT hits projects.prefix's UNIQUE constraint. Not an error --
            # re-read and return the winner's row, same discipline as the old
            # project_metadata race fix this replaces.
            existing = self.get_project(prefix)
            if existing is not None:
                return existing["id"]
            raise

    def get_project(self, prefix):
        row = self.conn_internal().execute(
            "SELECT id, codename, prefix, source_root, status, created_at"
            " FROM projects WHERE prefix=?",
            (prefix,),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "codename": row[1], "prefix": row[2],
                "source_root": row[3], "status": row[4], "created_at": row[5]}

    def list_projects(self, status=None):
        """TESS-174: every row still comes back by default, archived ones included -- what
        changed is that they now carry `status`, so a dead registration is DISTINGUISHABLE
        instead of silently identical to a live one. Filtering is opt-in (`status="active"`)
        rather than the default, because several existing callers (rebuild_projection's own
        seeding, the dashboards, tessguard's audit paths) legitimately need the full
        registry, and silently shrinking what this returns would break them in ways no test
        would name."""
        if status is not None and status not in schema.PROJECT_STATUSES:
            raise ValueError(
                f"unknown project status {status!r}; must be one of {schema.PROJECT_STATUSES}"
            )
        sql = "SELECT id, codename, prefix, source_root, status, created_at FROM projects"
        params = ()
        if status is not None:
            sql += " WHERE status=?"
            params = (status,)
        rows = self.conn_internal().execute(sql + " ORDER BY id", params).fetchall()
        return [
            {"id": r[0], "codename": r[1], "prefix": r[2], "source_root": r[3],
             "status": r[4], "created_at": r[5]}
            for r in rows
        ]

    # ---- project lifecycle (TESS-174) ------------------------------------
    #
    # register_project() was the ONLY write verb a project row had: no way to mark a dead
    # registration stale, and no way to follow a directory that genuinely moved. Both gaps
    # are real and recurring -- four dead registrations (WIDG/MIDB/F9FR/TCHK, all naming
    # scratchpad directories deleted 2026-08-16) sat indistinguishable from live ones, and
    # relocating a live project's checkout had no path at all short of archive-and-
    # re-register, which throws away the project's identity and every ticket_id minted
    # under it.
    #
    # Both verbs emit a real hash-chained event rather than a bare UPDATE, so a change to a
    # project's own state is as attributed and as replayable as any change to a ticket --
    # and rebuild_projection() replays them against the row's registration-time baseline,
    # which means a direct UPDATE that bypassed these verbs shows up as a projection
    # mismatch instead of passing unnoticed.

    def normalize_source_root_internal(self, source_root):
        """None stays None (a real, meaningful value -- MACNET and FOREV2 are registered
        with no source root today). Otherwise the path must be ABSOLUTE: project_resolve.py
        does Path(source_root).resolve(), which silently resolves a relative path against
        whatever the calling PROCESS's cwd happens to be, so a relative source_root would
        make project resolution answer differently per caller. Normalized through Path() to
        drop a trailing slash and collapse redundant separators, but deliberately NOT
        .resolve()d -- resolve() touches the filesystem and rewrites symlinks, and a
        directory being registered before it exists (or on another machine) is legitimate."""
        if source_root is None:
            return None
        text = str(source_root).strip()
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute():
            raise ValueError(
                f"source_root must be an absolute path, got {text!r} -- a relative path "
                "resolves against the calling process's cwd, so project resolution would "
                "answer differently depending on who asked"
            )
        return str(path)

    def set_project_status(self, prefix, actor, status, note=None):
        """Returns the project row as it stands after the call, plus `changed`.

        Setting the status a project already has is a no-op that emits NO event. The
        justification is the event's own name: ProjectStatusChanged asserts that a status
        CHANGED, so emitting one when nothing changed would make the audit log state
        something false. (An earlier version of this docstring reached for
        register_project()'s idempotent-by-prefix behaviour as the precedent. That analogy
        does not hold and was removed on review: register_project is an INSERT made
        idempotent by a UNIQUE key and emits no event in any case, so it says nothing about
        whether an UPDATE should emit one. A ...Set-shaped event would be a defensible
        design here; a ...Changed-shaped one would not.)"""
        if status not in schema.PROJECT_STATUSES:
            raise ValueError(
                f"unknown project status {status!r}; must be one of {schema.PROJECT_STATUSES}"
            )

        def attempt():
            with self.write_txn_internal() as conn:
                row = conn.execute(
                    "SELECT id, status FROM projects WHERE prefix=?", (prefix,)
                ).fetchone()
                if row is None:
                    raise UnknownProjectError(f"no project registered with prefix {prefix!r}")
                project_id, old_status = row
                if old_status == status:
                    return False
                self.append_event_internal(
                    conn, "ProjectStatusChanged", actor,
                    {"project": prefix, "old_status": old_status, "new_status": status,
                     "note": note},
                    project_id=project_id,
                )
                conn.execute("UPDATE projects SET status=? WHERE id=?", (status, project_id))
                return True

        changed = self.retry_internal(attempt)
        result = self.get_project(prefix)
        result["changed"] = changed
        return result

    def archive_project(self, prefix, actor, note=None):
        """Marks a registration stale. Does NOT delete it and does not touch its tickets --
        an archived project's history stays queryable and its tickets stay addressable by
        their existing ids.

        It DOES have real consequences for work done in that project's directory, and this
        docstring previously claimed the opposite ("does not lock the project against
        further writes"), which was flatly wrong -- caught by ticket-system-ed's review and
        confirmed by direct repro, not by re-reading this code. Because project_resolve.py
        skips archived rows, an archived registration stops claiming its filesystem root,
        and everything downstream of resolution changes with it: gitgate's commit gates
        refuse commits in that repo, and cli.assignee_provenance_error refuses a comment on
        a ticket assigned to that prefix even when run from the project's own root. That is
        deliberate fail-closed behaviour and archiving would be fairly toothless without it.
        Archive a project you still intend to commit in and you will be blocked; the way
        back is unarchive_project(), which is what those gates' messages now say."""
        return self.set_project_status(prefix, actor, schema.PROJECT_STATUS_ARCHIVED, note=note)

    def unarchive_project(self, prefix, actor, note=None):
        return self.set_project_status(prefix, actor, schema.PROJECT_STATUS_ACTIVE, note=note)

    def set_project_source_root(self, prefix, actor, source_root, note=None):
        """Point an existing registration at a directory that moved, KEEPING the project's
        id, prefix, codename, ticket_id counter and every ticket already minted under it.
        The alternative available before this existed -- archive the old row and register a
        new one -- loses all of that: a new project id, a restarted counter, and a ticket
        history split across two prefixes for what is one project that changed address.

        Returns the row after the call plus `changed`; setting the source_root a project
        already has emits no event, same reasoning as set_project_status."""
        normalized = self.normalize_source_root_internal(source_root)

        def attempt():
            with self.write_txn_internal() as conn:
                row = conn.execute(
                    "SELECT id, source_root FROM projects WHERE prefix=?", (prefix,)
                ).fetchone()
                if row is None:
                    raise UnknownProjectError(f"no project registered with prefix {prefix!r}")
                project_id, old_source_root = row
                if old_source_root == normalized:
                    return False
                self.append_event_internal(
                    conn, "ProjectSourceRootChanged", actor,
                    {"project": prefix, "old_source_root": old_source_root,
                     "new_source_root": normalized, "note": note},
                    project_id=project_id,
                )
                conn.execute(
                    "UPDATE projects SET source_root=? WHERE id=?", (normalized, project_id)
                )
                return True

        changed = self.retry_internal(attempt)
        result = self.get_project(prefix)
        result["changed"] = changed
        return result

    def resolve_project_id_internal(self, project):
        """`project` is a prefix string, or None to use the store's default. Raises
        UnknownProjectError rather than silently falling through to some other project --
        a caller that got the prefix wrong should see that clearly, not have its ticket
        filed under the wrong project."""
        prefix = project or self.default_project_prefix
        if not prefix:
            raise UnknownProjectError(
                "no project specified and this store has no default project -- pass "
                "project=<prefix>, or register one and reopen with codename/prefix"
            )
        row = self.get_project(prefix)
        if row is None:
            raise UnknownProjectError(f"no project registered with prefix {prefix!r}")
        return row["id"]

    def project_metadata(self):
        """Backward-compatible single-project accessor -- the default project's metadata,
        same shape as the original single-project design returned. New code with more than
        one project should use list_projects()/get_project(prefix) instead."""
        if not self.default_project_prefix:
            raise UnknownProjectError("this store has no default project registered")
        row = self.get_project(self.default_project_prefix)
        return {"codename": row["codename"], "prefix": row["prefix"], "created_at": row["created_at"]}

    # ---- ticket creation ------------------------------------------------

    def create_ticket(self, *, ticket_type, reporter, actor, priority=None, assignee=None,
                       parent_id=None, severity=None, repro_steps=None, environment=None,
                       custom_fields=None, idempotency_key=None, project=None, tier=None,
                       summary=None, description=None):
        if ticket_type not in schema.TICKET_TYPES:
            raise ValueError(f"unknown ticket type {ticket_type!r}")
        if tier is not None and tier not in schema.TIERS:
            raise ValueError(f"unknown tier {tier!r}; must be one of {schema.TIERS}")
        # TESS-44: severity/priority are a real, ordered 0-4 int scale now (previously
        # entirely unconstrained free text) -- validated the same way tier already is.
        if severity is not None and severity not in schema.LEVELS:
            raise ValueError(f"unknown severity {severity!r}; must be one of {schema.LEVELS}")
        if priority is not None and priority not in schema.LEVELS:
            raise ValueError(f"unknown priority {priority!r}; must be one of {schema.LEVELS}")
        # TESS-98. create_ticket writes custom_fields straight into ticket_fields, so
        # custom_fields={"priority": 1} at create is the same shadow-write as set-field is
        # after it: the dedicated `priority=` argument sits right there and would have been
        # ignored in favour of a value no triage view reads.
        shadowed = sorted(set(custom_fields or {}) & set(self.FIRST_CLASS_TICKET_FIELDS))
        if shadowed:
            raise ValueError(
                f"custom_fields may not shadow first-class ticket column(s) {shadowed} -- "
                f"pass them as their own arguments instead"
            )
        project_id = self.resolve_project_id_internal(project)

        def attempt():
            try:
                with self.write_txn_internal() as conn:
                    self.check_hierarchy_internal(conn, ticket_type, parent_id)

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
                    _, created_at = self.append_event_internal(
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
                        # shape as ticket_criteria's already-fixed sibling bug (TESS-17):
                        # a dict-valued custom field's replay path reconstitutes it from
                        # this event's own canonical_json payload (sorted keys), so a
                        # plain json.dumps here on the live path preserves the caller's
                        # original insertion order instead -- same data, different JSON
                        # text, false rebuild/live divergence (TESS-20, found by Clint
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
                    # project_id scoped (TESS-23) -- matches idx_events_idempotency's
                    # (project_id, idempotency_key) constraint, so a duplicate-key hit in
                    # ANOTHER project doesn't get mistaken for this project's own replay.
                    existing = self.conn_internal().execute(
                        "SELECT ticket_id FROM events WHERE idempotency_key = ? AND project_id = ?",
                        (idempotency_key, project_id),
                    ).fetchone()
                    if existing:
                        return existing[0]
                raise

        return self.retry_internal(attempt)

    def check_hierarchy_internal(self, conn, ticket_type, parent_id):
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

    def reassign_project(self, ticket_id, actor, new_project):
        """Moves a ticket to a different registered project (TESS-70). ticket_ids are
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
        or hotlist membership -- out of TESS-70's stated scope (history/comments/
        reference_docs/events); a caller that needs those preserved too should file a
        follow-up rather than assume this covers them."""

        def attempt():
            with self.write_txn_internal() as conn:
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

                to_project_id = self.resolve_project_id_internal(new_project)
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
                _, created_at = self.append_event_internal(
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
                    self.append_event_internal(
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
                    _, rd_updated_at = self.append_event_internal(
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
                    _, c_created_at = self.append_event_internal(
                        conn, "CommentAdded", c_actor, c_payload, ticket_id=new_ticket_id,
                    )
                    conn.execute(
                        "INSERT INTO comments (ticket_id, actor, body, code_snippet, created_at)"
                        " VALUES (?,?,?,?,?)",
                        (new_ticket_id, c_actor, body, c_snippet, c_created_at),
                    )

                self.append_event_internal(
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
                    f"({self.get_project(to_prefix)['codename']})."
                )
                _, fc_created_at = self.append_event_internal(
                    conn, "CommentAdded", actor, {"body": forward_body}, ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO comments (ticket_id, actor, body, created_at) VALUES (?,?,?,?)",
                    (ticket_id, actor, forward_body, fc_created_at),
                )

                if status != "closed" and not self.open_blockers_internal(conn, ticket_id):
                    _, sc_updated_at = self.append_event_internal(
                        conn, "StatusChanged", actor,
                        {"from": status, "to": "closed"}, ticket_id=ticket_id,
                    )
                    conn.execute(
                        "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?",
                        ("closed", sc_updated_at, ticket_id),
                    )

                return new_ticket_id

        return self.retry_internal(attempt)

    # ---- comments / links / workflow ------------------------------------

    def add_comment(self, ticket_id, actor, body, code_snippet=None):
        def attempt():
            with self.write_txn_internal() as conn:
                payload = {"body": body}
                if code_snippet is not None:
                    payload["code_snippet"] = code_snippet
                _, created_at = self.append_event_internal(
                    conn, "CommentAdded", actor, payload, ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO comments (ticket_id, actor, body, code_snippet, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (ticket_id, actor, body, code_snippet, created_at),
                )

        self.retry_internal(attempt)

    def set_comment_code_snippet(self, ticket_id, comment_id, actor, code_snippet):
        """Backfills a historical comment's code_snippet by appending a genuine new
        event, never by mutating the CommentAdded event or writing the projection
        directly -- same precedent as TESS-42's summary/description backfill via
        set_summary()/set_description(), not a payload-replay fallback. The original
        CommentAdded event and its body are untouched; this is an additive fact about
        that comment, layered on top."""
        def attempt():
            with self.write_txn_internal() as conn:
                row = conn.execute(
                    "SELECT id FROM comments WHERE id=? AND ticket_id=?",
                    (comment_id, ticket_id),
                ).fetchone()
                if not row:
                    raise ValueError(f"no comment id={comment_id} on ticket {ticket_id!r}")
                self.append_event_internal(
                    conn, "CommentCodeSnippetSet", actor,
                    {"comment_id": comment_id, "code_snippet": code_snippet},
                    ticket_id=ticket_id,
                )
                conn.execute(
                    "UPDATE comments SET code_snippet=? WHERE id=?",
                    (code_snippet, comment_id),
                )

        self.retry_internal(attempt)

    def no_claim_guard_internal(self, conn, ticket_id, actor, no_claim_reason):
        """TESS-192. Decides whether a close with no claim behind it may proceed, and
        records why when it may. Runs inside the caller's write transaction so the check
        and the record it counts against cannot be separated by a concurrent close.

        Returns the reason actually recorded, or None when the ticket has a real claim and
        this guard has nothing to say. Raises rather than returning a verdict nobody
        reads -- the whole defect this closes was a correct finding with no consumer.

        KNOWN GAP, disclosed rather than fixed (TESS-194). This is NOT the only path by
        which a ticket reaches 'closed'. reassign_project() closes the source ticket by
        appending StatusChanged and updating the row directly, never calling
        transition_status(), so it does not pass through here: no claim requirement, no
        reason, no rate limit, and no ClosedWithNoClaim event, which also makes those
        closes invisible to v_no_claim_close_bursts. Found in review, measured at 6 of 6
        unclaimed tickets closed in one loop. It is pre-existing and arguably legitimate
        bookkeeping, since a reassign moves the work to a linked new ticket rather than
        abandoning it. Stated here because the honest claim is "the chokepoint for
        transition-driven closes", not "the chokepoint for all closes", and a later reader
        who assumes the stronger one will be wrong about their coverage.
        """
        claim = conn.execute(
            "SELECT 1 FROM claims WHERE ticket_id=? LIMIT 1", (ticket_id,)
        ).fetchone()
        if claim:
            if no_claim_reason is not None:
                raise ClaimRequiredError(
                    f"{ticket_id} has a recorded claim, so --no-claim-reason does not apply "
                    f"(got {no_claim_reason!r}). Close it without one."
                )
            return None

        if no_claim_reason is None:
            raise ClaimRequiredError(
                f"{ticket_id} cannot close: no claim has ever been recorded against it. "
                f"Either record one (tessera claim {ticket_id} --file/--commit ...), or "
                f"close it with an explicit no-claim reason from "
                f"{list(schema.NO_CLAIM_REASONS)}."
            )
        if no_claim_reason not in schema.NO_CLAIM_REASONS:
            raise ClaimRequiredError(
                f"{ticket_id} cannot close: {no_claim_reason!r} is not a recognised "
                f"no-claim reason. Legal values are {list(schema.NO_CLAIM_REASONS)} -- this "
                f"vocabulary is closed on purpose so the reasons stay countable."
            )

        if no_claim_reason == "disposition-pass":
            now = utc_now_iso()
            window_start = iso_minus_seconds(now, 60)
            recent = conn.execute(
                "SELECT COUNT(*) FROM events"
                " WHERE event_type='ClosedWithNoClaim' AND actor=?"
                " AND created_at > ?"
                " AND json_extract(payload, '$.reason') = 'disposition-pass'",
                (actor, window_start),
            ).fetchone()[0]
            if recent >= schema.DISPOSITION_PASS_PER_MINUTE:
                raise NoClaimRateLimitError(
                    f"{ticket_id} cannot close: {actor} has already recorded {recent} "
                    f"disposition-pass close(s) in the last 60 seconds and the limit is "
                    f"{schema.DISPOSITION_PASS_PER_MINUTE}. This is the guard working, not a "
                    f"bug: a disposition pass ships no evidence, so its only cost is the "
                    f"attention of whoever runs it. Record a claim on this ticket, or wait."
                )

        self.append_event_internal(
            conn, "ClosedWithNoClaim", actor, {"reason": no_claim_reason},
            ticket_id=ticket_id,
        )
        return no_claim_reason

    def transition_status(self, ticket_id, actor, new_status, no_claim_reason=None):
        def attempt():
            with self.write_txn_internal() as conn:
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
                    open_blockers = self.open_blockers_internal(conn, ticket_id)
                    if open_blockers:
                        raise BlockedError(
                            f"{ticket_id} cannot close: blocked by "
                            f"{', '.join(b['ticket_id'] for b in open_blockers)} "
                            f"(not yet closed: {open_blockers})"
                        )
                    # TESS-192. Runs before StatusChanged is appended, so a refusal leaves
                    # the ticket in its prior state rather than closing it and complaining
                    # afterwards -- which is precisely what the old advisory check did.
                    self.no_claim_guard_internal(conn, ticket_id, actor, no_claim_reason)
                elif no_claim_reason is not None:
                    raise ClaimRequiredError(
                        f"no_claim_reason is only meaningful when closing; got "
                        f"{no_claim_reason!r} for a transition to {new_status!r}."
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
                _, updated_at = self.append_event_internal(
                    conn, "StatusChanged", actor,
                    {"from": current, "to": new_status}, ticket_id=ticket_id,
                )
                conn.execute(
                    "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?",
                    (new_status, updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    def record_diff_check(self, ticket_id, actor, result):
        """Logs the real result of a claim-vs-diff discrepancy check as its own typed event.
        Pure recording -- the caller (tessera.api.discrepancy.check_and_record_closure)
        computes the check; this method never runs git itself and never blocks a transition.
        `result` is compute_discrepancy()'s own dict shape (matches/touched_but_not_claimed/
        claimed_but_not_touched), not re-validated here."""
        def attempt():
            with self.write_txn_internal() as conn:
                self.append_event_internal(
                    conn, "ClaimDiscrepancyChecked", actor, result, ticket_id=ticket_id,
                )

        self.retry_internal(attempt)

    def record_closed_with_no_claim(self, ticket_id, actor, reason=None):
        """A ticket closed with zero ClaimRecorded event ever -- the discrepancy check has
        nothing to compare against, and that absence is itself the finding worth a real,
        queryable record (T-2/QUALITY-BAR.md's own closed-without-claim floor), not a silent
        no-op the way discrepancy_for_ticket's `if not claim: return None` treats it.

        TESS-192: as of the no-claim guard, a close that reaches this state through
        transition_status has ALREADY emitted this event, with a reason, inside the same
        transaction that let the close proceed. This method survives for the paths that
        write the event directly (replay, migrations, and any caller that transitions
        through some other route), and now carries the same `reason` field so those events
        are shaped identically to guarded ones. `reason=None` records the pre-TESS-192
        shape and is what a historical event replays as."""
        payload = {} if reason is None else {"reason": reason}

        def attempt():
            with self.write_txn_internal() as conn:
                self.append_event_internal(
                    conn, "ClosedWithNoClaim", actor, payload, ticket_id=ticket_id,
                )

        self.retry_internal(attempt)

    def open_blockers_internal(self, conn, ticket_id):
        """Tickets that block `ticket_id` (its blocked_by set) and are not yet closed.
        Only gates the close transition (see BlockedError's docstring) -- editing a
        blocked ticket is never restricted by this."""
        rows = conn.execute(
            "SELECT t.ticket_id, t.status FROM ticket_links tl"
            " JOIN tickets t ON t.ticket_id = tl.to_ticket"
            " WHERE tl.from_ticket = ? AND tl.link_type = 'blocked-by' AND t.status != 'closed'",
            (ticket_id,),
        ).fetchall()
        return [{"ticket_id": r[0], "status": r[1]} for r in rows]

    def add_link(self, from_ticket, to_ticket, link_type, actor):
        if link_type not in schema.LINK_TYPES:
            raise ValueError(
                f"unknown link_type {link_type!r}; must be one of {schema.LINK_TYPES}"
            )
        if from_ticket == to_ticket and link_type in schema.RECIPROCAL_LINK_TYPE:
            # would_create_cycle_internal's reachability CTE starts its walk FROM the
            # would-be edge's target, so a single self-referencing edge (A blocks A) is
            # never reachable from itself in zero steps and the existing cycle check
            # can't see it -- the ticket becomes permanently blocked by itself with no
            # unlink path to recover (TESS-25, found by Clint Eastwood's adversarial
            # review, confirmed by direct repro: add_link(A, A, "blocks", ...) succeeded).
            # relates-to has no reciprocal and isn't part of the block graph, so it's not
            # restricted here.
            raise CycleError(f"{from_ticket} cannot {link_type} itself")

        # Cycle detection must fire for EITHER spelling of the dependency relation, not
        # only when the caller happens to say "blocks" -- a direct add_link(A, B,
        # "blocked-by", actor) describes exactly the same edge as add_link(B, A,
        # "blocks", actor) and has to be checked the same way (code-review finding: the
        # original version only checked link_type == "blocks", so a caller could add a
        # cycle-forming edge just by phrasing it as "blocked-by" instead).
        if link_type == "blocked-by":
            check_from, check_to = to_ticket, from_ticket
        else:
            check_from, check_to = from_ticket, to_ticket

        def attempt():
            with self.write_txn_internal() as conn:
                if link_type in schema.RECIPROCAL_LINK_TYPE and self.would_create_cycle_internal(
                    conn, check_from, check_to
                ):
                    raise CycleError(
                        f"linking {from_ticket} {link_type} {to_ticket} would create a cycle"
                    )
                self.append_event_internal(
                    conn, "LinkAdded", actor,
                    {"from": from_ticket, "to": to_ticket, "link_type": link_type},
                    ticket_id=from_ticket,
                )
                conn.execute(
                    "INSERT INTO ticket_links (from_ticket, to_ticket, link_type) VALUES (?,?,?)",
                    (from_ticket, to_ticket, link_type),
                )
                reciprocal = schema.RECIPROCAL_LINK_TYPE.get(link_type)
                if reciprocal:
                    conn.execute(
                        "INSERT OR IGNORE INTO ticket_links (from_ticket, to_ticket, link_type)"
                        " VALUES (?,?,?)",
                        (to_ticket, from_ticket, reciprocal),
                    )

        self.retry_internal(attempt)

    def would_create_cycle_internal(self, conn, from_ticket, to_ticket):
        row = conn.execute(
            """
            WITH RECURSIVE reachable(id) AS (
                SELECT to_ticket FROM ticket_links WHERE from_ticket = ? AND link_type = 'blocks'
                UNION
                SELECT tl.to_ticket FROM ticket_links tl
                JOIN reachable r ON tl.from_ticket = r.id
                WHERE tl.link_type = 'blocks'
            )
            SELECT 1 FROM reachable WHERE id = ? LIMIT 1
            """,
            (to_ticket, from_ticket),
        ).fetchone()
        return row is not None

    # ---- fields / reference_docs / archival ------------------------------

    # TESS-98. Every column on `tickets`. A name in here is NOT a custom field, so
    # set_custom_field refuses it instead of quietly writing a shadow row into
    # ticket_fields that no triage view, filter or report will ever read. The value is the
    # guidance shown in the error: the right way to set it, or an honest statement that
    # there is no way. Kept as a literal (rather than read from PRAGMA table_info at call
    # time) so the guidance can be per-field; test_first_class_field_list_matches_schema
    # asserts it stays in step with the real table, so a future column cannot silently
    # reopen the divert path.
    FIRST_CLASS_TICKET_FIELDS = {
        "ticket_id": "immutable; use reassign-project to move a ticket between projects",
        "project_id": "use reassign-project",
        "type": "set at create time; not updatable",
        "status": "use transition",
        "reporter": "set at create time; not updatable",
        "assignee": "use set-assignee",
        "priority": "use set-priority",
        "tier": "set at create time; not updatable",
        "summary": "use set-summary",
        "description": "use set-description",
        "parent_id": "set at create time; not updatable",
        "severity": "use set-severity",
        "repro_steps": "set at create time; not updatable",
        "environment": "set at create time; not updatable",
        "reference_docs": "use set-reference-docs",
        "archived": "use the archive/unarchive path",
        "created_at": "managed by the store; not settable",
        "updated_at": "managed by the store; not settable",
    }

    def set_custom_field(self, ticket_id, actor, field_name, field_value):
        # Fail loud rather than divert. This call used to accept 'priority', report ok, and
        # leave tickets.priority NULL -- 22 shadow rows across summary/severity/priority
        # were already on disk when this guard went in, every one of them a write someone
        # believed had landed.
        if field_name in self.FIRST_CLASS_TICKET_FIELDS:
            raise ValueError(
                f"{field_name!r} is a first-class ticket column, not a custom field -- "
                f"setting it here would write a shadow value nothing reads. "
                f"{self.FIRST_CLASS_TICKET_FIELDS[field_name]}."
            )

        def attempt():
            with self.write_txn_internal() as conn:
                self.append_event_internal(
                    conn, "FieldSet", actor,
                    {"field_name": field_name, "field_value": field_value},
                    ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO ticket_fields (ticket_id, field_name, field_value) VALUES (?,?,?)"
                    " ON CONFLICT(ticket_id, field_name) DO UPDATE SET field_value=excluded.field_value",
                    (ticket_id, field_name, canonical_json(field_value)),  # TESS-20
                )

        self.retry_internal(attempt)

    # TESS-98. priority and severity are first-class columns that had no setter at all,
    # so the ONLY way to set them was create --priority/--severity. set-field appeared to
    # work -- it returned ok and wrote custom_fields={'priority': '1'} -- while
    # tickets.priority stayed NULL. FORE-23 lost a day to exactly that: its thread records
    # "Marked P1" and every triage view still read None. Same treatment the other
    # first-class fields already get: own event type, own column update, own replay handler.
    PRIORITY_LIKE_FIELDS = {"priority": "PrioritySet", "severity": "SeveritySet"}

    def set_priority_like(self, ticket_id, actor, field_name, value):
        """Set the first-class `priority` or `severity` column. value may be None to clear."""
        if field_name not in self.PRIORITY_LIKE_FIELDS:
            raise ValueError(f"{field_name!r} is not a priority-like field")
        # Same rule create_ticket validates against, from the same constant. An update path
        # that accepted what create rejects (or the reverse) would be a second ruler for
        # one column -- and int() coercion here would have been exactly that, quietly
        # turning the string "1" into 1 where create_ticket refuses it.
        if value is not None and value not in schema.LEVELS:
            raise ValueError(f"unknown {field_name} {value!r}; must be one of {schema.LEVELS}")
        event_type = self.PRIORITY_LIKE_FIELDS[field_name]

        def attempt():
            with self.write_txn_internal() as conn:
                if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, updated_at = self.append_event_internal(
                    conn, event_type, actor, {field_name: value}, ticket_id=ticket_id,
                )
                conn.execute(
                    f"UPDATE tickets SET {field_name}=?, updated_at=? WHERE ticket_id=?",
                    (value, updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    def set_summary(self, ticket_id, actor, summary):
        """Distinct SummarySet event, same reasoning as set_reference_docs -- a real,
        first-class field (TESS-42), not folded into generic FieldSet."""

        def attempt():
            with self.write_txn_internal() as conn:
                if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, updated_at = self.append_event_internal(
                    conn, "SummarySet", actor, {"summary": summary}, ticket_id=ticket_id,
                )
                conn.execute(
                    "UPDATE tickets SET summary=?, updated_at=? WHERE ticket_id=?",
                    (summary, updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    def set_description(self, ticket_id, actor, description):
        """Distinct DescriptionSet event, same reasoning as set_summary/set_reference_docs."""

        def attempt():
            with self.write_txn_internal() as conn:
                if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, updated_at = self.append_event_internal(
                    conn, "DescriptionSet", actor, {"description": description}, ticket_id=ticket_id,
                )
                conn.execute(
                    "UPDATE tickets SET description=?, updated_at=? WHERE ticket_id=?",
                    (description, updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    def set_assignee(self, ticket_id, actor, assignee):
        """Distinct AssigneeSet event, same reasoning as set_summary/set_description.

        Was 'set at create time; not updatable' until Jon's direct call: assignee moves a
        ticket's real working ownership between teams (a ticket can originate in FORE and
        get handed to TESS to actually execute), so it needs a real update path the same
        way status/priority/severity do. Store-level, this is just a value -- callers that
        want the team-handoff semantics (assignee as a real project prefix, checked against
        the actor's own resolved cwd) enforce that at the CLI layer
        (project_resolve.resolve_projects_for_cwd), not here; this method accepts any string,
        same as set_summary accepts any string."""

        def attempt():
            with self.write_txn_internal() as conn:
                if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, updated_at = self.append_event_internal(
                    conn, "AssigneeSet", actor, {"assignee": assignee}, ticket_id=ticket_id,
                )
                conn.execute(
                    "UPDATE tickets SET assignee=?, updated_at=? WHERE ticket_id=?",
                    (assignee, updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    def set_reference_docs(self, ticket_id, actor, paths):
        """Writes a distinct ReferenceDocsSet event -- using this field is a recorded,
        visible decision (SCOPE.md), not folded silently into a generic field update."""

        def attempt():
            with self.write_txn_internal() as conn:
                if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, updated_at = self.append_event_internal(
                    conn, "ReferenceDocsSet", actor, {"paths": list(paths)}, ticket_id=ticket_id,
                )
                conn.execute(
                    "UPDATE tickets SET reference_docs=?, updated_at=? WHERE ticket_id=?",
                    (json.dumps(list(paths)), updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    def archive_ticket(self, ticket_id, actor):
        self.set_archived_internal(ticket_id, actor, True, "TicketArchived")

    def unarchive_ticket(self, ticket_id, actor):
        self.set_archived_internal(ticket_id, actor, False, "TicketUnarchived")

    def set_archived_internal(self, ticket_id, actor, archived, event_type):
        def attempt():
            with self.write_txn_internal() as conn:
                if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, updated_at = self.append_event_internal(conn, event_type, actor, {}, ticket_id=ticket_id)
                conn.execute(
                    "UPDATE tickets SET archived=?, updated_at=? WHERE ticket_id=?",
                    (1 if archived else 0, updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    # ---- claims -----------------------------------------------------------

    def record_claim(self, ticket_id, actor, summary, files_touched, commit_sha):
        # TESS-159: store boundary is the primary gate -- a commit_sha this rejects can
        # never reach the git call sites in discrepancy.py/gitops.py at all, regardless
        # of which of the two currently reads claims.
        validate_commit_sha(commit_sha)

        def attempt():
            with self.write_txn_internal() as conn:
                payload = {
                    "summary": summary, "files_touched": list(files_touched),
                    "commit_sha": commit_sha,
                }
                ehash, created_at = self.append_event_internal(
                    conn, "ClaimRecorded", actor, payload, ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO claims (ticket_id, actor, summary, files_touched,"
                    " commit_sha, event_hash, created_at) VALUES (?,?,?,?,?,?,?)",
                    (ticket_id, actor, summary, json.dumps(list(files_touched)),
                     commit_sha, ehash, created_at),
                )
                return ehash

        return self.retry_internal(attempt)

    # ---- per-ticket frozen criteria (GOALS.json's pattern, one grain finer) ------------

    def criteria_hash_internal(self, criteria):
        """Same algorithm as goals_freeze_gate.py's criteria_hash(): sha256 of
        json.dumps(criteria, sort_keys=True, separators=(",",":")). canonical_json() from
        tessera.common already serializes with exactly those parameters, so this is the
        same function, not a parallel reimplementation that could drift from it."""
        return "sha256:" + sha256_hex(canonical_json(criteria))

    def freeze_ticket_criteria(self, ticket_id, actor, criteria):
        """Extends GOALS.json's criteria+verification+freeze+hash pattern to ticket grain:
        `criteria` is a list of {"id", "statement", "verification", "verifiable"} dicts,
        same shape as a component GOALS.json's criteria[]. A ticket cannot move to
        "in_progress" (see transition_status) until this has been called at least once --
        the mechanical form of "document before building" applied per ticket, not only at
        initial component scope."""
        chash = self.criteria_hash_internal(criteria)

        def attempt():
            with self.write_txn_internal() as conn:
                row = conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
                if not row:
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, frozen_at = self.append_event_internal(
                    conn, "TicketCriteriaFrozen", actor,
                    {"criteria": criteria, "criteria_hash": chash}, ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO ticket_criteria (ticket_id, criteria, criteria_frozen_at,"
                    " criteria_hash_at_freeze) VALUES (?,?,?,?)"
                    " ON CONFLICT(ticket_id) DO UPDATE SET criteria=excluded.criteria,"
                    " criteria_frozen_at=excluded.criteria_frozen_at,"
                    " criteria_hash_at_freeze=excluded.criteria_hash_at_freeze",
                    # canonical_json (sorted keys), not json.dumps -- must match what
                    # replay_event_internal reconstitutes from the event's own canonical
                    # payload, or rebuild_projection()/live_projection() diverge on JSON
                    # key order alone despite identical data (found by direct test run,
                    # not by inspection: test_rebuild_equals_live_across_all_projection_
                    # tables failed on exactly this before this fix).
                    (ticket_id, canonical_json(criteria), frozen_at, chash),
                )
                return chash

        return self.retry_internal(attempt)

    def get_ticket_criteria(self, ticket_id):
        row = self.conn_internal().execute(
            "SELECT criteria, criteria_frozen_at, criteria_hash_at_freeze FROM ticket_criteria"
            " WHERE ticket_id=?",
            (ticket_id,),
        ).fetchone()
        if not row:
            return None
        criteria, frozen_at, stored_hash = row
        criteria = json.loads(criteria)
        return {
            "criteria": criteria, "criteria_frozen_at": frozen_at,
            "criteria_hash_at_freeze": stored_hash,
            "hash_matches": self.criteria_hash_internal(criteria) == stored_hash,
        }

    # ---- watching -------------------------------------------------------
    # A session/agent may not have direct access to fix a ticket it cares about (a
    # cross-project blocker, a shared tool under someone else's active work) but still
    # wants to know when it resolves -- the same reason humans watch tickets to unblock
    # their own work. watch/unwatch are event-sourced like everything else that's a real
    # fact about the system, not a UI preference.

    def watch_ticket(self, ticket_id, watcher, actor):
        def attempt():
            with self.write_txn_internal() as conn:
                row = conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
                if not row:
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, created_at = self.append_event_internal(
                    conn, "TicketWatched", actor, {"watcher": watcher}, ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT OR IGNORE INTO watchers (ticket_id, watcher, created_at) VALUES (?,?,?)",
                    (ticket_id, watcher, created_at),
                )

        self.retry_internal(attempt)

    def unwatch_ticket(self, ticket_id, watcher, actor):
        def attempt():
            with self.write_txn_internal() as conn:
                if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                self.append_event_internal(
                    conn, "TicketUnwatched", actor, {"watcher": watcher}, ticket_id=ticket_id,
                )
                conn.execute(
                    "DELETE FROM watchers WHERE ticket_id=? AND watcher=?", (ticket_id, watcher),
                )

        self.retry_internal(attempt)

    def get_watchers(self, ticket_id):
        rows = self.conn_internal().execute(
            "SELECT watcher, created_at FROM watchers WHERE ticket_id=? ORDER BY created_at",
            (ticket_id,),
        ).fetchall()
        return [{"watcher": w, "since": c} for w, c in rows]

    def list_watched_tickets(self, watcher):
        """Every ticket `watcher` is watching, with CURRENT ticket state -- what a session
        polls (or a future notification mechanism reads) to learn a ticket it cares about
        but can't directly act on has changed, closed, or needs attention."""
        rows = self.conn_internal().execute(
            "SELECT ticket_id FROM watchers WHERE watcher=?", (watcher,)
        ).fetchall()
        return [self.get_ticket(r[0], with_context=False) for r in rows]

    # ---- hotlists (TESS-36) ------------------------------------------------
    # A named, cross-project, ad hoc worklist -- standups, an ACR-style review batch, a
    # build-phase bugfix batch. Unlike project registration (deliberately NOT
    # event-sourced, see rebuild_projection()'s docstring), a hotlist entry IS a real
    # fact worth auditing -- "this ticket was pulled into the next bugfix build on this
    # date by this actor" is exactly the kind of claim this system exists to make
    # checkable, so every hotlist mutation goes through the normal event path.

    def create_hotlist(self, name, actor):
        def attempt():
            with self.write_txn_internal() as conn:
                _, created_at = self.append_event_internal(
                    conn, "HotlistCreated", actor, {"name": name},
                )
                row = conn.execute(
                    "INSERT INTO hotlists (name, created_at, created_by) VALUES (?,?,?)"
                    " RETURNING id",
                    (name, created_at, actor),
                ).fetchone()
                return row[0]

        try:
            return self.retry_internal(attempt)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) and "hotlists.name" in str(exc):
                raise ValueError(f"a hotlist named {name!r} already exists") from exc
            raise

    def get_hotlist_id_internal(self, conn, name):
        row = conn.execute("SELECT id FROM hotlists WHERE name=?", (name,)).fetchone()
        if not row:
            raise ValueError(f"no such hotlist {name!r}")
        return row[0]

    def add_to_hotlist(self, name, ticket_id, actor, note=None):
        def attempt():
            with self.write_txn_internal() as conn:
                hotlist_id = self.get_hotlist_id_internal(conn, name)
                if not conn.execute(
                    "SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)
                ).fetchone():
                    raise ValueError(f"no such ticket {ticket_id!r}")
                _, added_at = self.append_event_internal(
                    conn, "TicketAddedToHotlist", actor,
                    {"hotlist": name, "ticket_id": ticket_id, "note": note},
                    ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO hotlist_items (hotlist_id, ticket_id, added_at, added_by, note)"
                    " VALUES (?,?,?,?,?)"
                    " ON CONFLICT(hotlist_id, ticket_id) DO UPDATE SET note=excluded.note,"
                    " added_at=excluded.added_at, added_by=excluded.added_by",
                    (hotlist_id, ticket_id, added_at, actor, note),
                )

        self.retry_internal(attempt)

    def remove_from_hotlist(self, name, ticket_id, actor):
        def attempt():
            with self.write_txn_internal() as conn:
                hotlist_id = self.get_hotlist_id_internal(conn, name)
                self.append_event_internal(
                    conn, "TicketRemovedFromHotlist", actor,
                    {"hotlist": name, "ticket_id": ticket_id}, ticket_id=ticket_id,
                )
                conn.execute(
                    "DELETE FROM hotlist_items WHERE hotlist_id=? AND ticket_id=?",
                    (hotlist_id, ticket_id),
                )

        self.retry_internal(attempt)

    def get_hotlist(self, name):
        row = self.conn_internal().execute(
            "SELECT id, created_at, created_by FROM hotlists WHERE name=?", (name,)
        ).fetchone()
        if not row:
            return None
        hotlist_id, created_at, created_by = row
        items = self.conn_internal().execute(
            "SELECT ticket_id, added_at, added_by, note FROM hotlist_items"
            " WHERE hotlist_id=? ORDER BY added_at",
            (hotlist_id,),
        ).fetchall()
        return {
            "name": name, "created_at": created_at, "created_by": created_by,
            "items": [
                {**self.get_ticket(ticket_id, with_context=False),
                 "added_at": added_at, "added_by": added_by, "note": note}
                for ticket_id, added_at, added_by, note in items
            ],
        }

    def render_current_state(self, name, budget_chars):
        """REQ-25 (Foreman v2.0 PRD) Phase 1: renders a hotlist as compact context text,
        item-granularity truncation only, never mid-line. Status/summary are read fresh from
        `get_hotlist()` (which itself calls `get_ticket()` per item) at call time -- this is
        the "render-at-read-time" half of REQ-25's design (FORE-38's real architecture pass):
        membership in the hotlist is curated, sticky, judgment; each item's live status/
        summary cannot go stale, because it is read fresh on every call, never cached.

        Returns None if the hotlist doesn't exist -- same "not found" signal `get_hotlist`
        already uses, not a raised exception a caller has to guess the type of."""
        hotlist = self.get_hotlist(name)
        if hotlist is None:
            return None
        return render_current_state_text(hotlist, budget_chars)

    def list_hotlists(self):
        rows = self.conn_internal().execute(
            "SELECT h.name, h.created_at, h.created_by, COUNT(hi.ticket_id)"
            " FROM hotlists h LEFT JOIN hotlist_items hi ON hi.hotlist_id = h.id"
            " GROUP BY h.id ORDER BY h.created_at",
        ).fetchall()
        return [
            {"name": name, "created_at": created_at, "created_by": created_by, "item_count": count}
            for name, created_at, created_by, count in rows
        ]

    # ---- datasets (TESS-47) -------------------------------------------------
    # BigQuery-style dataset selector for the SQL tab. A dataset is a named GROUP OF
    # PROJECTS, event-sourced like hotlists -- distinct from `projects` itself.
    # list_datasets() merges these real rows with an implicit one-per-project entry
    # derived live from `projects`, never duplicated in storage.

    def create_dataset(self, name, actor):
        def attempt():
            with self.write_txn_internal() as conn:
                _, created_at = self.append_event_internal(
                    conn, "DatasetCreated", actor, {"name": name},
                )
                row = conn.execute(
                    "INSERT INTO datasets (name, created_at, created_by) VALUES (?,?,?)"
                    " RETURNING id",
                    (name, created_at, actor),
                ).fetchone()
                return row[0]

        try:
            return self.retry_internal(attempt)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) and "datasets.name" in str(exc):
                raise ValueError(f"a dataset named {name!r} already exists") from exc
            raise

    def get_dataset_id_internal(self, conn, name):
        row = conn.execute("SELECT id FROM datasets WHERE name=?", (name,)).fetchone()
        if not row:
            raise ValueError(f"no such dataset {name!r}")
        return row[0]

    def add_project_to_dataset(self, dataset_name, project_prefix, actor):
        def attempt():
            with self.write_txn_internal() as conn:
                dataset_id = self.get_dataset_id_internal(conn, dataset_name)
                project_row = conn.execute(
                    "SELECT id FROM projects WHERE prefix=?", (project_prefix,)
                ).fetchone()
                if not project_row:
                    raise ValueError(f"no such project {project_prefix!r}")
                project_id = project_row[0]
                _, added_at = self.append_event_internal(
                    conn, "ProjectAddedToDataset", actor,
                    {"dataset": dataset_name, "project": project_prefix},
                )
                conn.execute(
                    "INSERT OR IGNORE INTO dataset_projects"
                    " (dataset_id, project_id, added_at, added_by) VALUES (?,?,?,?)",
                    (dataset_id, project_id, added_at, actor),
                )

        self.retry_internal(attempt)

    def list_datasets(self):
        """Every dataset selectable in the SQL tab: one IMPLICIT entry per registered
        project (kind='project', derived live -- never duplicated storage) plus every
        REAL custom dataset (kind='custom', with its member projects). "Right now is
        probably just projects, but wired so custom datasets can be selected too.\""""
        result = [
            {"name": p["prefix"], "kind": "project",
             "projects": [{"prefix": p["prefix"], "id": p["id"]}]}
            for p in self.list_projects()
        ]
        rows = self.conn_internal().execute(
            "SELECT id, name FROM datasets ORDER BY name"
        ).fetchall()
        for dataset_id, name in rows:
            members = self.conn_internal().execute(
                "SELECT p.prefix, p.id FROM dataset_projects dp"
                " JOIN projects p ON p.id = dp.project_id"
                " WHERE dp.dataset_id=? ORDER BY p.prefix",
                (dataset_id,),
            ).fetchall()
            result.append({
                "name": name, "kind": "custom",
                "projects": [{"prefix": prefix, "id": pid} for prefix, pid in members],
            })
        return result

    def set_column_description(self, table_name, column_name, description, actor):
        """TESS-49: per-column documentation metadata, stored separately from the
        actual data tables (its own row per table.column) so it's editable
        independently of any data row and survives even if the described table is
        later dropped/renamed. One current description per column -- re-setting
        overwrites, same UPSERT pattern as set_summary/set_description on tickets."""
        def attempt():
            with self.write_txn_internal() as conn:
                _, updated_at = self.append_event_internal(
                    conn, "ColumnDescriptionSet", actor,
                    {"table": table_name, "column": column_name, "description": description},
                )
                conn.execute(
                    "INSERT INTO column_descriptions"
                    " (table_name, column_name, description, updated_at, updated_by)"
                    " VALUES (?,?,?,?,?)"
                    " ON CONFLICT(table_name, column_name) DO UPDATE SET"
                    " description=excluded.description, updated_at=excluded.updated_at,"
                    " updated_by=excluded.updated_by",
                    (table_name, column_name, description, updated_at, actor),
                )

        self.retry_internal(attempt)

    def list_column_descriptions(self):
        """table_name -> {column_name: description}, for every column that has ever
        had one set. Grouped by table since that's how the schema panel already
        organizes columns (TESS-43) -- Discovery mode (TESS-50) looks up
        result.get(table, {}).get(column) rather than a flat table.column key."""
        rows = self.conn_internal().execute(
            "SELECT table_name, column_name, description FROM column_descriptions"
        ).fetchall()
        result = {}
        for table_name, column_name, description in rows:
            result.setdefault(table_name, {})[column_name] = description
        return result

    # ---- attachments ------------------------------------------------------

    def add_attachment(self, ticket_id, actor, filename, data):
        """temp-write + fsync + rename to a sha256 content-addressed path, THEN the
        referencing row insert in the same transaction as any other write (ARCHITECTURE.md).
        If the transaction then rolls back, the result is an orphaned but collectable blob
        file, never a row pointing at a blob that doesn't exist."""
        digest = sha256_hex(data)
        final_path = self.blobs_dir / digest
        if not final_path.exists():
            fd, tmp_path = None, None
            try:
                tmp_path = self.blobs_dir / f".tmp-{digest}-{os.getpid()}-{threading.get_ident()}"
                fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                fd = None
                os.rename(tmp_path, final_path)
                tmp_path = None
            finally:
                if fd is not None:
                    os.close(fd)
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.unlink()

        def attempt():
            with self.write_txn_internal() as conn:
                _, created_at = self.append_event_internal(
                    conn, "AttachmentAdded", actor,
                    {"filename": filename, "sha256": digest, "size_bytes": len(data)},
                    ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO attachments (ticket_id, filename, sha256, size_bytes,"
                    " created_at) VALUES (?,?,?,?,?)",
                    (ticket_id, filename, digest, len(data), created_at),
                )
                return digest

        return self.retry_internal(attempt)

    # ---- stage heads (written only via events, per ARCHITECTURE.md) -------

    def record_stage_promotion(self, stage, commit_sha, actor, project=None):
        self.record_stage_event_internal(stage, commit_sha, actor, "StagePromoted", project)

    def record_stage_rollback(self, stage, commit_sha, actor, project=None):
        self.record_stage_event_internal(stage, commit_sha, actor, "StageRolledBack", project)

    def record_stage_event_internal(self, stage, commit_sha, actor, event_type, project=None):
        project_id = self.resolve_project_id_internal(project)

        def attempt():
            with self.write_txn_internal() as conn:
                _, updated_at = self.append_event_internal(
                    conn, event_type, actor,
                    {"stage": stage, "commit_sha": commit_sha, "project_id": project_id},
                    project_id=project_id,
                )
                conn.execute(
                    "INSERT INTO stage_heads (project_id, stage, commit_sha, updated_at)"
                    " VALUES (?,?,?,?)"
                    " ON CONFLICT(project_id, stage) DO UPDATE SET commit_sha=excluded.commit_sha,"
                    " updated_at=excluded.updated_at",
                    (project_id, stage, commit_sha, updated_at),
                )

        self.retry_internal(attempt)

    def get_stage_head(self, stage, project=None):
        project_id = self.resolve_project_id_internal(project)
        row = self.conn_internal().execute(
            "SELECT commit_sha, updated_at FROM stage_heads WHERE project_id=? AND stage=?",
            (project_id, stage),
        ).fetchone()
        return {"commit_sha": row[0], "updated_at": row[1]} if row else None

    def get_commit_links_for_stage(self, stage, project=None):
        """Every ticket_commit_links row for a stage with a real commit SHA (branch-only
        links are excluded, per ARCHITECTURE.md -- ancestry/staleness only applies to
        commit links). Used by gitops's reconcile_stage() so it reads through store's own
        public API instead of a raw query against store's internal schema. Joins through
        tickets.project_id -- ticket_commit_links itself has no project_id column (ticket_id
        already disambiguates project), but "stage" alone (e.g. "dev") is NOT
        project-unique, so a bare stage-name query would mix two projects' commit links
        together if both happen to use the same stage name. Filtered by project here so
        that can't happen."""
        project_id = self.resolve_project_id_internal(project)
        rows = self.conn_internal().execute(
            "SELECT tcl.ticket_id, tcl.commit_sha, tcl.stale FROM ticket_commit_links tcl"
            " JOIN tickets t ON t.ticket_id = tcl.ticket_id"
            " WHERE tcl.stage=? AND tcl.commit_sha IS NOT NULL AND t.project_id=?",
            (stage, project_id),
        ).fetchall()
        return [
            {"ticket_id": ticket_id, "commit_sha": commit_sha, "stale": bool(stale)}
            for ticket_id, commit_sha, stale in rows
        ]

    def set_ticket_stale(self, ticket_id, stage, stale):
        """Called by gitops after an on-read ancestry check. Recomputed from ancestry,
        not latched -- gitops calls this with stale=False the moment ancestry is restored,
        same as it calls stale=True when it diverges."""

        def attempt():
            with self.write_txn_internal() as conn:
                conn.execute(
                    "UPDATE ticket_commit_links SET stale=? WHERE ticket_id=? AND stage=?",
                    (1 if stale else 0, ticket_id, stage),
                )

        self.retry_internal(attempt)

    def link_ticket_commit(self, ticket_id, stage, actor, commit_sha=None, branch=None):
        def attempt():
            with self.write_txn_internal() as conn:
                self.append_event_internal(
                    conn, "TicketCommitLinked", actor,
                    {"stage": stage, "commit_sha": commit_sha, "branch": branch},
                    ticket_id=ticket_id,
                )
                conn.execute(
                    "INSERT INTO ticket_commit_links (ticket_id, stage, commit_sha, branch, stale)"
                    " VALUES (?,?,?,?,0)"
                    " ON CONFLICT(ticket_id, stage) DO UPDATE SET commit_sha=excluded.commit_sha,"
                    " branch=excluded.branch, stale=0",
                    (ticket_id, stage, commit_sha, branch),
                )

        self.retry_internal(attempt)

    # ---- reads --------------------------------------------------------

    def get_ticket(self, ticket_id, with_context=True):
        row = self.conn_internal().execute(
            "SELECT t.ticket_id, t.project_id, p.prefix, p.codename, t.type, t.status,"
            " t.reporter, t.assignee, t.priority, t.tier, t.summary, t.description,"
            " t.parent_id, t.severity, t.repro_steps, t.environment, t.reference_docs,"
            " t.archived, t.created_at, t.updated_at"
            " FROM tickets t JOIN projects p ON p.id = t.project_id"
            " WHERE t.ticket_id=?",
            (ticket_id,),
        ).fetchone()
        if not row:
            return None
        cols = ["ticket_id", "project_id", "project_prefix", "project_codename", "type",
                "status", "reporter", "assignee", "priority", "tier", "summary", "description",
                "parent_id", "severity", "repro_steps", "environment", "reference_docs",
                "archived", "created_at", "updated_at"]
        ticket = dict(zip(cols, row))
        ticket["reference_docs"] = json.loads(ticket["reference_docs"] or "[]")
        ticket["archived"] = bool(ticket["archived"])
        fields = self.conn_internal().execute(
            "SELECT field_name, field_value FROM ticket_fields WHERE ticket_id=?",
            (ticket_id,),
        ).fetchall()
        ticket["custom_fields"] = {name: json.loads(value) for name, value in fields}

        if with_context:
            # Read-back extension: opening/resuming work on a ticket surfaces its
            # immediate block-graph neighborhood too, not just its own text -- the same
            # context-restoration problem the diff-vs-claim read-back already solves,
            # applied one hop into "why is this stuck / what depends on it" (per the
            # Foreman bootstrap brief). One extra query each way, cheap at realistic
            # ticket-graph sizes; with_context=False (used by list_tickets) skips it so a
            # list view doesn't pay 2N queries for N tickets.
            ticket["blocked_by"] = self.linked_neighborhood_internal(ticket_id, "blocked-by")
            ticket["blocks"] = self.linked_neighborhood_internal(ticket_id, "blocks")
            ticket["watchers"] = self.get_watchers(ticket_id)
            ticket["comments"] = self.get_comments_internal(ticket_id)

        return ticket

    def get_comments_internal(self, ticket_id):
        rows = self.conn_internal().execute(
            "SELECT id, actor, body, code_snippet, created_at FROM comments"
            " WHERE ticket_id=? ORDER BY created_at",
            (ticket_id,),
        ).fetchall()
        return [
            {"id": cid, "actor": actor, "body": body, "code_snippet": code_snippet,
             "created_at": created_at}
            for cid, actor, body, code_snippet, created_at in rows
        ]

    def linked_neighborhood_internal(self, ticket_id, link_type):
        rows = self.conn_internal().execute(
            "SELECT t.ticket_id, t.status, t.type FROM ticket_links tl"
            " JOIN tickets t ON t.ticket_id = tl.to_ticket"
            " WHERE tl.from_ticket = ? AND tl.link_type = ?",
            (ticket_id, link_type),
        ).fetchall()
        result = []
        for tid, status, ttype in rows:
            summary_row = self.conn_internal().execute(
                "SELECT field_value FROM ticket_fields WHERE ticket_id=? AND field_name='summary'",
                (tid,),
            ).fetchone()
            summary = json.loads(summary_row[0]) if summary_row else None
            result.append({"ticket_id": tid, "status": status, "type": ttype, "summary": summary})
        return result

    def list_tickets(self, status=None, assignee=None, ticket_type=None,
                      include_archived=False, project=None,
                      priority_max=None, severity_max=None):
        """TESS-120 (asked by Foreman/ATLAS): priority_max/severity_max are threshold filters,
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
        rows = self.conn_internal().execute(query, params).fetchall()
        return [self.get_ticket(r[0], with_context=False) for r in rows]

    def get_idempotent_ticket(self, idempotency_key, project=None):
        project_id = self.resolve_project_id_internal(project)
        row = self.conn_internal().execute(
            "SELECT ticket_id FROM events WHERE idempotency_key = ? AND project_id = ?",
            (idempotency_key, project_id),
        ).fetchone()
        return row[0] if row else None

    def latest_event_id(self):
        """Current max events.id, or 0 if the store has no events yet. Used by the SSE
        stream to resolve "start from now" without replaying the entire history to every
        newly-connecting client (TESS-31)."""
        row = self.conn_internal().execute("SELECT MAX(id) FROM events").fetchone()
        return row[0] or 0

    def get_events_since(self, last_seen_rowid):
        """Events with id > last_seen_rowid, in order. Used by api's SSE poll loop so it
        reads through store's own public API instead of raw SQL against store's internal
        schema. Correct across processes because every write goes through BEGIN IMMEDIATE
        (WAL single-writer + append-only, no rowid reuse)."""
        rows = self.conn_internal().execute(
            "SELECT id, event_type, ticket_id, actor, created_at FROM events"
            " WHERE id > ? ORDER BY id",
            (last_seen_rowid,),
        ).fetchall()
        return [
            {"id": row_id, "event_type": event_type, "ticket_id": ticket_id,
             "actor": actor, "created_at": created_at}
            for row_id, event_type, ticket_id, actor, created_at in rows
        ]

    def get_latest_claim(self, ticket_id):
        """Most recent claim for a ticket, or None. Used by api's discrepancy computation
        so it reads through store's own public API instead of raw SQL against the claims
        table's internal schema."""
        row = self.conn_internal().execute(
            "SELECT summary, files_touched, commit_sha, event_hash, created_at, actor"
            " FROM claims WHERE ticket_id=? ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if not row:
            return None
        summary, files_touched_json, commit_sha, event_hash_value, created_at, actor = row
        return {
            "summary": summary, "files_touched": json.loads(files_touched_json),
            "commit_sha": commit_sha, "event_hash": event_hash_value, "created_at": created_at,
            "actor": actor,
        }

    # ---- backup ---------------------------------------------------------

    def backup(self, dest_path):
        """VACUUM INTO a consistent snapshot -- safe against a live WAL database with
        concurrent writers (a plain file copy is not)."""
        self.conn_internal().execute("VACUUM INTO ?", (str(dest_path),))

    # ---- rebuild / verify --------------------------------------------------

    # ticket_commit_links.stale is deliberately NOT event-sourced -- reconcile_stage()
    # (ARCHITECTURE.md) recomputes it from the stage repo's REAL, current git HEAD on
    # every read, which is live external state the event log has no record of at
    # arbitrary past points. set_ticket_stale() therefore writes this column directly,
    # with no backing event, by design. Comparing it in rebuild_projection() vs
    # live_projection() would either (a) always show a false divergence the instant any
    # ticket is flagged stale (since replay has nothing to derive it from and always
    # produces 0), or (b) require inventing a StaleFlagChanged event whose replay would
    # just be re-deriving a stale git-ancestry snapshot from a point in time, which is not
    # what event sourcing is for here and would misrepresent stale as historically
    # meaningful when it's actually a live snapshot (TESS-21, found by Clint Eastwood's
    # adversarial review). Excluded by name, not by dropping the whole table -- every
    # OTHER column of ticket_commit_links (ticket_id/stage/commit_sha/branch) IS written
    # via TicketCommitLinked and stays fully verified.
    NON_EVENT_SOURCED_COLUMNS = {"ticket_commit_links": {"stale"}}

    def canonical_columns_internal(self):
        """Column order per PROJECTION_TABLES table, taken from a scratch db built fresh
        from schema.DDL -- NOT from PRAGMA table_info() on self.conn_internal(), because a
        db that has lived through an ALTER TABLE ADD COLUMN migration (e.g. tickets.
        project_id/tier, added after the original table existed) has those columns
        physically appended at the end, wherever schema.py declares them logically.
        rebuild_projection()'s shadow db is always freshly CREATEd from the current DDL, so
        its physical order matches the DDL; the live db's physical order can permanently
        differ after any additive migration. Two SELECT * queries using each side's own
        PRAGMA table_info() therefore compare tuples under two different column orders --
        a false-positive mismatch found by direct comparison against the real, migrated
        data/tessera.db, not a hypothetical. Using ONE canonical order (by explicit column
        list, not SELECT *) for both sides fixes this for good, not just for today's schema."""
        scratch = sqlite3.connect(":memory:")
        schema.init_schema(scratch)
        cols = {
            table: [
                r[1] for r in scratch.execute(f"PRAGMA table_info({table})")
                if r[1] not in self.NON_EVENT_SOURCED_COLUMNS.get(table, set())
            ]
            for table in schema.PROJECTION_TABLES
        }
        scratch.close()
        return cols

    def rebuild_projection(self):
        """Replay events from scratch into an in-memory database and return every
        projection table's rows as a comparable structure. Used by the test suite to
        assert equality against the live projection -- the actual correctness check
        event sourcing is supposed to buy."""
        canonical_cols = self.canonical_columns_internal()
        shadow = sqlite3.connect(":memory:")
        shadow.execute("PRAGMA foreign_keys=OFF")
        schema.init_schema(shadow)
        events = self.conn_internal().execute(
            "SELECT event_type, ticket_id, actor, payload, created_at, event_hash"
            " FROM events ORDER BY id"
        ).fetchall()
        # projects is seeded from its own live rows: registration isn't an event in the
        # hash chain -- it predates any ticket work and there's nothing to compare a claim
        # against -- so a project's identity columns (id, codename, prefix, created_at) are
        # taken as given here, exactly as they were before multi-project support.
        #
        # Its MUTABLE columns (source_root, status, TESS-174) are seeded the same way but
        # are NOT taken as given, because the ProjectStatusChanged / ProjectSourceRootChanged
        # replay below overwrites whatever was seeded: a project with at least one lifecycle
        # event ends this rebuild holding that event chain's final value, so a direct
        # `UPDATE projects SET ...` that bypassed the verbs diverges from the live row and
        # is caught. (An earlier version of this seeded those two columns from the first
        # event's recorded OLD value instead, on the theory that seeding from live made the
        # check vacuous. It doesn't: replay-forward lands on the same final value either
        # way, so that elaboration bought no detection at all. Deleted rather than kept as
        # decoration -- confirmed by sabotaging the seed and watching the negative control
        # in test_project_lifecycle.py still pass, then sabotaging the replay handlers and
        # watching it fail.)
        #
        # The honest limit, and it is PER-COLUMN, not per-project (ticket-system-ed's review
        # caught the original wording understating this; confirmed by direct repro): a
        # column is only verified if THAT column has an event to replay. A project with a
        # ProjectSourceRootChanged event but no ProjectStatusChanged event has a verified
        # source_root and an unverified status -- its status can be hand-flipped between
        # active and archived and this rebuild still reconciles clean. That is the
        # resolution-gating column, so it is the half with teeth. Pinned by
        # test_the_blind_spot_is_per_column_not_per_project.
        for row in self.list_projects():
            shadow.execute(
                "INSERT INTO projects (id, codename, prefix, source_root, status, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (row["id"], row["codename"], row["prefix"], row["source_root"],
                 row["status"], row["created_at"]),
            )
        # counters was previously entirely uncovered by rebuild-vs-live verification
        # (TESS-32, found by Clint Eastwood's adversarial review) -- it's deterministic
        # from replayed events, though: tickets are never deleted, and each TicketCreated
        # event corresponds to exactly one atomic counter increment in the SAME write
        # transaction (create_ticket), so a per-project count of TicketCreated events
        # always equals that project's live counter value. No new event type needed.
        #
        # Seeded at 0 for EVERY registered project first, not just ones with a
        # TicketCreated event -- register_project() itself does
        # "INSERT OR IGNORE INTO counters (...) VALUES (?, 'ticket_id', 0)" at
        # registration time, so a project with zero tickets still has a real live
        # counters row. The first version of this derivation only created an entry when
        # it saw a TicketCreated event, so a just-registered, still-empty project (found
        # by direct repro against the real db: AREM, which has zero tickets) produced no
        # rebuilt row at all -- a real mismatch, not a hypothetical.
        counters = {row["id"]: 0 for row in self.list_projects()}
        for event_type, ticket_id, actor, payload_json, created_at, event_hash_value in events:
            payload = json.loads(payload_json)
            if event_type == "TicketCreated":
                pid = payload.get("project_id", 1)
                counters[pid] = counters.get(pid, 0) + 1
            replay_event_internal(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value)
        for pid, count in counters.items():
            shadow.execute(
                "INSERT INTO counters (project_id, name, value) VALUES (?, 'ticket_id', ?)",
                (pid, count),
            )
        result = {}
        for table in schema.PROJECTION_TABLES:
            cols = canonical_cols[table]
            col_list = ", ".join(cols)
            rows = shadow.execute(f"SELECT {col_list} FROM {table} ORDER BY {col_list}").fetchall()
            result[table] = [tuple(r) for r in rows]
        shadow.close()
        return result

    def live_projection(self):
        canonical_cols = self.canonical_columns_internal()
        result = {}
        conn = self.conn_internal()
        for table in schema.PROJECTION_TABLES:
            cols = canonical_cols[table]
            col_list = ", ".join(cols)
            rows = conn.execute(f"SELECT {col_list} FROM {table} ORDER BY {col_list}").fetchall()
            result[table] = [tuple(r) for r in rows]
        return result

    def verify_chain(self):
        """Returns {'roots': n, 'tips': n, 'orphans': n, 'hash_mismatches': n}. A healthy
        chain has exactly one root and one tip and zero of the other two."""
        conn = self.conn_internal()
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


def normalize_legacy_level_internal(value):
    """TESS-44: severity/priority became a real 0-4 int scale, replacing entirely
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


def replay_event_internal(shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value):
    if event_type == "TicketCreated":
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
            # summary/description (TESS-42): events written before this feature existed
            # carry neither key at all -- payload.get() naturally returns None for them,
            # same precedent as tier's own historical handling (no active backfill
            # needed here; the real historical summary text for TESS-2..TESS-8 is instead
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
                (ticket_id, name, canonical_json(value)),  # TESS-20
            )
    elif event_type == "CommentAdded":
        shadow.execute(
            "INSERT INTO comments (ticket_id, actor, body, code_snippet, created_at)"
            " VALUES (?,?,?,?,?)",
            (ticket_id, actor, payload["body"], payload.get("code_snippet"), created_at),
        )
    elif event_type == "CommentCodeSnippetSet":
        shadow.execute(
            "UPDATE comments SET code_snippet=? WHERE id=?",
            (payload["code_snippet"], payload["comment_id"]),
        )
    elif event_type == "StatusChanged":
        shadow.execute(
            "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?",
            (payload["to"], created_at, ticket_id),
        )
    elif event_type == "LinkAdded":
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
    elif event_type == "FieldSet":
        shadow.execute(
            "INSERT INTO ticket_fields (ticket_id, field_name, field_value) VALUES (?,?,?)"
            " ON CONFLICT(ticket_id, field_name) DO UPDATE SET field_value=excluded.field_value",
            (ticket_id, payload["field_name"], canonical_json(payload["field_value"])),  # TESS-20
        )
    elif event_type == "ReferenceDocsSet":
        shadow.execute(
            "UPDATE tickets SET reference_docs=?, updated_at=? WHERE ticket_id=?",
            (json.dumps(payload["paths"]), created_at, ticket_id),
        )
    elif event_type == "SummarySet":
        shadow.execute(
            "UPDATE tickets SET summary=?, updated_at=? WHERE ticket_id=?",
            (payload["summary"], created_at, ticket_id),
        )
    elif event_type == "DescriptionSet":
        shadow.execute(
            "UPDATE tickets SET description=?, updated_at=? WHERE ticket_id=?",
            (payload["description"], created_at, ticket_id),
        )
    elif event_type == "AssigneeSet":
        shadow.execute(
            "UPDATE tickets SET assignee=?, updated_at=? WHERE ticket_id=?",
            (payload["assignee"], created_at, ticket_id),
        )
    # TESS-98. Column name comes from the event type, never from the payload key, so a
    # malformed payload cannot steer this into an arbitrary column.
    elif event_type in ("PrioritySet", "SeveritySet"):
        column = "priority" if event_type == "PrioritySet" else "severity"
        shadow.execute(
            f"UPDATE tickets SET {column}=?, updated_at=? WHERE ticket_id=?",
            (payload.get(column), created_at, ticket_id),
        )
    elif event_type in ("TicketArchived", "TicketUnarchived"):
        shadow.execute(
            "UPDATE tickets SET archived=?, updated_at=? WHERE ticket_id=?",
            (1 if event_type == "TicketArchived" else 0, created_at, ticket_id),
        )
    elif event_type == "ClaimRecorded":
        shadow.execute(
            "INSERT INTO claims (ticket_id, actor, summary, files_touched, commit_sha,"
            " event_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (ticket_id, actor, payload["summary"], json.dumps(payload["files_touched"]),
             payload["commit_sha"], event_hash_value, created_at),
        )
    elif event_type in ("StagePromoted", "StageRolledBack"):
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
    elif event_type == "TicketCriteriaFrozen":
        shadow.execute(
            "INSERT INTO ticket_criteria (ticket_id, criteria, criteria_frozen_at,"
            " criteria_hash_at_freeze) VALUES (?,?,?,?)"
            " ON CONFLICT(ticket_id) DO UPDATE SET criteria=excluded.criteria,"
            " criteria_frozen_at=excluded.criteria_frozen_at,"
            " criteria_hash_at_freeze=excluded.criteria_hash_at_freeze",
            (ticket_id, canonical_json(payload["criteria"]), created_at, payload["criteria_hash"]),
        )
    elif event_type == "TicketWatched":
        shadow.execute(
            "INSERT OR IGNORE INTO watchers (ticket_id, watcher, created_at) VALUES (?,?,?)",
            (ticket_id, payload["watcher"], created_at),
        )
    elif event_type == "TicketUnwatched":
        shadow.execute(
            "DELETE FROM watchers WHERE ticket_id=? AND watcher=?",
            (ticket_id, payload["watcher"]),
        )
    elif event_type == "TicketCommitLinked":
        shadow.execute(
            "INSERT INTO ticket_commit_links (ticket_id, stage, commit_sha, branch, stale)"
            " VALUES (?,?,?,?,0)"
            " ON CONFLICT(ticket_id, stage) DO UPDATE SET commit_sha=excluded.commit_sha,"
            " branch=excluded.branch, stale=0",
            (ticket_id, payload["stage"], payload.get("commit_sha"), payload.get("branch")),
        )
    elif event_type == "AttachmentAdded":
        shadow.execute(
            "INSERT INTO attachments (ticket_id, filename, sha256, size_bytes, created_at)"
            " VALUES (?,?,?,?,?)",
            (ticket_id, payload["filename"], payload["sha256"], payload["size_bytes"], created_at),
        )
    elif event_type == "HotlistCreated":
        # hotlists.id is AUTOINCREMENT and hotlists are never deleted, so replaying
        # HotlistCreated events in event order reproduces the exact same id sequence the
        # live INSERT did -- same guarantee tickets' ticket_id counter already relies on.
        shadow.execute(
            "INSERT INTO hotlists (name, created_at, created_by) VALUES (?,?,?)",
            (payload["name"], created_at, actor),
        )
    elif event_type == "TicketAddedToHotlist":
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
    elif event_type == "TicketRemovedFromHotlist":
        hotlist_id = shadow.execute(
            "SELECT id FROM hotlists WHERE name=?", (payload["hotlist"],)
        ).fetchone()[0]
        shadow.execute(
            "DELETE FROM hotlist_items WHERE hotlist_id=? AND ticket_id=?",
            (hotlist_id, ticket_id),
        )
    elif event_type == "DatasetCreated":
        shadow.execute(
            "INSERT INTO datasets (name, created_at, created_by) VALUES (?,?,?)",
            (payload["name"], created_at, actor),
        )
    elif event_type == "ProjectAddedToDataset":
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
    elif event_type == "ProjectStatusChanged":
        shadow.execute(
            "UPDATE projects SET status=? WHERE prefix=?",
            (payload["new_status"], payload["project"]),
        )
    elif event_type == "ProjectSourceRootChanged":
        shadow.execute(
            "UPDATE projects SET source_root=? WHERE prefix=?",
            (payload["new_source_root"], payload["project"]),
        )
    elif event_type == "ColumnDescriptionSet":
        shadow.execute(
            "INSERT INTO column_descriptions"
            " (table_name, column_name, description, updated_at, updated_by)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(table_name, column_name) DO UPDATE SET"
            " description=excluded.description, updated_at=excluded.updated_at,"
            " updated_by=excluded.updated_by",
            (payload["table"], payload["column"], payload["description"], created_at, actor),
        )
    elif event_type in schema.PROJECTION_NEUTRAL_EVENTS:
        # TESS-178: a pure audit event, deliberately projecting nothing. Named explicitly
        # rather than falling through to a bare `pass`, so this branch cannot be mistaken
        # for a type someone forgot -- which is the exact confusion that let two of these
        # sit unhandled until rebuild_projection() turned out to be unrunnable against the
        # live database. schema.PROJECTION_NEUTRAL_EVENTS documents the reasoning; the
        # neutrality claim itself is enforced by test_projection_neutral_events.py.
        pass
    else:
        raise ValueError(f"rebuild_projection: unknown event_type {event_type!r}")
