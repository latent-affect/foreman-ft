import contextlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from ..common.hashing import canonical_json, event_hash as compute_event_hash, sha256_hex
from ..common.timestamps import utc_now_iso
from . import project_reassignment, replay_handlers, schema, ticket_operations
from .exceptions import (
    BlockedError, CriteriaNotFrozenError, CycleError, HierarchyError, SameProjectError,
    UnknownProjectError, UnsupportedSQLiteVersion, WorkflowError,
)

RETRY_ATTEMPTS = 15
BUSY_TIMEOUT_MS = 5000


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
        """Idempotent: if `prefix` is already registered, returns its existing project_id.
        Implementation lives in ticket_operations.py."""
        return ticket_operations.register_project(self, codename, prefix, source_root)

    def get_project(self, prefix):
        row = self.conn_internal().execute(
            "SELECT id, codename, prefix, source_root, created_at FROM projects WHERE prefix=?",
            (prefix,),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "codename": row[1], "prefix": row[2],
                "source_root": row[3], "created_at": row[4]}

    def list_projects(self):
        rows = self.conn_internal().execute(
            "SELECT id, codename, prefix, source_root, created_at FROM projects ORDER BY id"
        ).fetchall()
        return [
            {"id": r[0], "codename": r[1], "prefix": r[2], "source_root": r[3], "created_at": r[4]}
            for r in rows
        ]

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
        """Implementation lives in ticket_operations.py."""
        return ticket_operations.create_ticket(
            self, ticket_type=ticket_type, reporter=reporter, actor=actor, priority=priority,
            assignee=assignee, parent_id=parent_id, severity=severity, repro_steps=repro_steps,
            environment=environment, custom_fields=custom_fields,
            idempotency_key=idempotency_key, project=project, tier=tier, summary=summary,
            description=description,
        )

    def check_hierarchy_internal(self, conn, ticket_type, parent_id):
        return ticket_operations.check_hierarchy_internal(self, conn, ticket_type, parent_id)

    def reassign_project(self, ticket_id, actor, new_project):
        """Moves a ticket to a different registered project. Implementation lives in
        project_reassignment.py -- see that module's docstring for the full behavioral
        contract (this was store.py's single largest method by line count)."""
        return project_reassignment.reassign_project(self, ticket_id, actor, new_project)

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
        directly -- same precedent as summary/description backfill via
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

    def transition_status(self, ticket_id, actor, new_status):
        """Implementation lives in ticket_operations.py."""
        ticket_operations.transition_status(self, ticket_id, actor, new_status)

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

    def record_closed_with_no_claim(self, ticket_id, actor):
        """A ticket closed with zero ClaimRecorded event ever -- the discrepancy check has
        nothing to compare against, and that absence is itself the finding worth a real,
        queryable record (T-2/QUALITY-BAR.md's own closed-without-claim floor), not a silent
        no-op the way discrepancy_for_ticket's `if not claim: return None` treats it."""
        def attempt():
            with self.write_txn_internal() as conn:
                self.append_event_internal(
                    conn, "ClosedWithNoClaim", actor, {}, ticket_id=ticket_id,
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
            # unlink path to recover (found by Clint Eastwood's adversarial
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

    # Every column on `tickets`. A name in here is NOT a custom field, so
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
                    (ticket_id, field_name, canonical_json(field_value)),
                )

        self.retry_internal(attempt)

    # priority and severity are first-class columns that had no setter at all,
    # so the ONLY way to set them was create --priority/--severity. set-field appeared to
    # work -- it returned ok and wrote custom_fields={'priority': '1'} -- while
    # tickets.priority stayed NULL. An earlier review lost a day to exactly that: its thread records
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
                # nosec B608 -- field_name is rejected two lines above this function's start
                # unless it is a KEY of PRIORITY_LIKE_FIELDS (line 457, a fixed two-entry dict
                # literal: "priority"/"severity"). No caller-supplied string reaches this
                # f-string; the only two values that can ever arrive here are the dict's own
                # keys. DEVH-10/GOALS.json C6. Widening risk: PRIORITY_LIKE_FIELDS is 14-ish
                # lines above the guard that reads it, itself 14-ish lines above this
                # interpolation -- a future edit widening that dict widens this identifier
                # source too, with no compiler link between the three. Risk-register row: see
                # tessera/store/tests/test_sql_identifier_safety.py's module docstring.
                conn.execute(
                    f"UPDATE tickets SET {field_name}=?, updated_at=? WHERE ticket_id=?",
                    (value, updated_at, ticket_id),
                )

        self.retry_internal(attempt)

    def set_summary(self, ticket_id, actor, summary):
        """Distinct SummarySet event, same reasoning as set_reference_docs -- a real,
        first-class field, not folded into generic FieldSet."""

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

        Was 'set at create time; not updatable' until the operator's direct call: assignee moves a
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

    # ---- hotlists ------------------------------------------------
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

    # ---- datasets -------------------------------------------------
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
        """Per-column documentation metadata, stored separately from the
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
        organizes columns -- Discovery mode looks up
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
        """Implementation lives in ticket_operations.py."""
        return ticket_operations.list_tickets(
            self, status=status, assignee=assignee, ticket_type=ticket_type,
            include_archived=include_archived, project=project,
            priority_max=priority_max, severity_max=severity_max,
        )

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
        newly-connecting client."""
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
    # meaningful when it's actually a live snapshot (found by Clint Eastwood's
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
        # projects is replayed from its own rows directly (registration isn't an event in
        # the hash chain -- it predates any ticket work and there's nothing to compare a
        # claim against), same as it was read directly before multi-project support.
        for row in self.list_projects():
            shadow.execute(
                "INSERT INTO projects (id, codename, prefix, source_root, created_at)"
                " VALUES (?,?,?,?,?)",
                (row["id"], row["codename"], row["prefix"], row["source_root"], row["created_at"]),
            )
        events = self.conn_internal().execute(
            "SELECT event_type, ticket_id, actor, payload, created_at, event_hash"
            " FROM events ORDER BY id"
        ).fetchall()
        # counters was previously entirely uncovered by rebuild-vs-live verification
        # (found by Clint Eastwood's adversarial review) -- it's deterministic
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
            replay_handlers.replay_event_internal(
                shadow, event_type, ticket_id, actor, payload, created_at, event_hash_value
            )
        for pid, count in counters.items():
            shadow.execute(
                "INSERT INTO counters (project_id, name, value) VALUES (?, 'ticket_id', ?)",
                (pid, count),
            )
        result = {}
        # nosec B608 -- neither identifier is caller-supplied. `table` iterates
        # schema.PROJECTION_TABLES, a fixed tuple constant; `col_list` comes from
        # canonical_columns_internal(), itself a PRAGMA table_info() read over a scratch
        # sqlite3 connection built fresh from schema.init_schema() (== schema.DDL) two calls
        # up the stack -- never from a caller argument, a ticket field, or any external
        # source. DEVH-10/GOALS.json C6.
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
        # nosec B608 -- same identifier sources as rebuild_projection() above: table from
        # schema.PROJECTION_TABLES, col_list from canonical_columns_internal()'s scratch-db
        # PRAGMA read. DEVH-10/GOALS.json C6.
        for table in schema.PROJECTION_TABLES:
            cols = canonical_cols[table]
            col_list = ", ".join(cols)
            rows = conn.execute(f"SELECT {col_list} FROM {table} ORDER BY {col_list}").fetchall()
            result[table] = [tuple(r) for r in rows]
        return result

    def verify_chain(self):
        """Implementation lives in ticket_operations.py."""
        return ticket_operations.verify_chain(self)

