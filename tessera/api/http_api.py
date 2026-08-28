import json
import mimetypes
import os
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ..gitops.exceptions import GitOpsError
from ..store.exceptions import StoreError
from .discrepancy import check_and_record_closure
from . import docs_store
from .cli import compact
from .discrepancy import discrepancy_for_ticket_singlerepo
from .sql_query import (
    QueryRejected, QueryTimedOut, get_schema, run_readonly_query_with_self_healing,
)

STATIC_ROOT = Path(__file__).resolve().parent.parent / "reviewui" / "static"
CSP_HEADER = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'"
MAX_BODY_BYTES = 1_000_000

ROUTES = []


def route(method, pattern):
    compiled = re.compile(pattern)

    def deco(fn):
        ROUTES.append((method, compiled, fn))
        return fn

    return deco


def require_internal(body, *fields):
    missing = [f for f in fields if not body.get(f)]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


def parse_query_internal(path):
    """GET requests carry their parameters in the query string, not a body -- do_GET was
    previously always dispatching with body={}, so a route handler reading e.g. `stage`
    or `status` from `body` silently always saw nothing (code-review finding). One value
    per key (the first, if repeated)."""
    query = urlsplit(path).query
    parsed = parse_qs(query)
    return {key: values[0] for key, values in parsed.items()}


@route("POST", r"^/tickets$")
def create_ticket(ctx, m, body):
    require_internal(body, "ticket_type", "reporter", "actor")
    tid = ctx.store.create_ticket(
        ticket_type=body["ticket_type"], reporter=body["reporter"], actor=body["actor"],
        priority=body.get("priority"), assignee=body.get("assignee"),
        parent_id=body.get("parent_id"), severity=body.get("severity"),
        repro_steps=body.get("repro_steps"), environment=body.get("environment"),
        custom_fields=body.get("custom_fields"), idempotency_key=body.get("idempotency_key"),
        project=body.get("project"), tier=body.get("tier"),
        summary=body.get("summary"), description=body.get("description"),
    )
    return 201, {"ticket_id": tid}


@route("GET", r"^/tickets/(?P<tid>[^/]+)$")
def get_ticket(ctx, m, body):
    ticket = ctx.store.get_ticket(m.group("tid"))
    if ticket is None:
        return 404, {"error": f"no such ticket {m.group('tid')!r}"}
    # ?compact=1 omits null/empty fields -- for context-injection or bulk-reading
    # use, not for anything that checks a field's ABSENCE as a finding.
    if body.get("compact"):
        ticket = compact(ticket)
    return 200, ticket


@route("GET", r"^/tickets$")
def list_tickets(ctx, m, body):
    # priority_max/severity_max are thresholds (0=highest), e.g. severity_max=1
    # for "S0 or S1" -- the real cross-team ask was a set, not a single exact level.
    priority_max = body.get("priority_max")
    severity_max = body.get("severity_max")
    tickets = ctx.store.list_tickets(
        status=body.get("status"),
        assignee=body.get("assignee"),
        ticket_type=body.get("type"),
        project=body.get("project"),
        priority_max=int(priority_max) if priority_max is not None else None,
        severity_max=int(severity_max) if severity_max is not None else None,
    )
    if body.get("compact"):
        tickets = [compact(t) for t in tickets]
    return 200, {"tickets": tickets}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/comments$")
def add_comment(ctx, m, body):
    require_internal(body, "actor", "body")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.add_comment(tid, body["actor"], body["body"], code_snippet=body.get("code_snippet"))
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/transition$")
def transition(ctx, m, body):
    require_internal(body, "actor", "status")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.transition_status(tid, body["actor"], body["status"])
    if body["status"] == "closed":
        check_and_record_closure(ctx.store, tid, body["actor"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/reassign-project$")
def reassign_project(ctx, m, body):
    require_internal(body, "actor", "project")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    new_tid = ctx.store.reassign_project(tid, body["actor"], body["project"])
    return 200, {"ticket_id": new_tid}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/links$")
def add_link(ctx, m, body):
    require_internal(body, "actor", "to_ticket", "link_type")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.add_link(tid, body["to_ticket"], body["link_type"], body["actor"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/fields$")
def set_field(ctx, m, body):
    require_internal(body, "actor", "field_name")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.set_custom_field(tid, body["actor"], body["field_name"], body.get("field_value"))
    return 200, {"ok": True}


# Both first-class columns share one handler because they share one store method
# and one validation rule; splitting them would only duplicate the 404. A body with
# "value": null clears the field, which is why the key is required but its value is not
# rejected for being None -- an absent key and an explicit null mean different things.
@route("POST", r"^/tickets/(?P<tid>[^/]+)/(?P<field>priority|severity)$")
def set_priority_like(ctx, m, body):
    require_internal(body, "actor")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    if "value" not in body:
        return 400, {"error": "missing required field 'value' (use null to clear)"}
    ctx.store.set_priority_like(tid, body["actor"], m.group("field"), body["value"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/reference_docs$")
def set_reference_docs(ctx, m, body):
    require_internal(body, "actor", "paths")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.set_reference_docs(tid, body["actor"], body["paths"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/summary$")
def set_summary(ctx, m, body):
    require_internal(body, "actor", "summary")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.set_summary(tid, body["actor"], body["summary"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/description$")
def set_description(ctx, m, body):
    require_internal(body, "actor", "description")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.set_description(tid, body["actor"], body["description"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/assignee$")
def set_assignee(ctx, m, body):
    require_internal(body, "actor", "assignee")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.set_assignee(tid, body["actor"], body["assignee"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/claims$")
def record_claim(ctx, m, body):
    require_internal(body, "actor", "summary", "files_touched", "commit_sha")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ehash = ctx.store.record_claim(
        tid, body["actor"], body["summary"], body["files_touched"], body["commit_sha"]
    )
    return 200, {"event_hash": ehash}


@route("GET", r"^/tickets/(?P<tid>[^/]+)/discrepancy$")
def get_discrepancy(ctx, m, body):
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    result = discrepancy_for_ticket_singlerepo(ctx.store, tid)
    if result is None:
        return 404, {"error": f"no claim recorded for {tid!r}"}
    return 200, result


@route("POST", r"^/stages/(?P<stage>[^/]+)/promote$")
def promote(ctx, m, body):
    require_internal(body, "actor", "commit_sha")
    # project= passed as an explicit per-call argument, NOT by mutating ctx.gitops.project
    # -- ctx.gitops is one long-lived instance shared across every request on a
    # ThreadingHTTPServer, so writing to its .project attribute per-request raced between
    # concurrent threads and could leak one request's project into another's git
    # operations (found by Clint Eastwood's adversarial review).
    new_head = ctx.gitops.promote_stage(
        m.group("stage"), body["commit_sha"], body["actor"], project=body.get("project"),
    )
    return 200, {"head": new_head}


@route("POST", r"^/stages/(?P<stage>[^/]+)/rollback$")
def rollback(ctx, m, body):
    require_internal(body, "actor", "target")
    new_head = ctx.gitops.rollback_stage(
        m.group("stage"), body["target"], body["actor"], project=body.get("project"),
    )
    return 200, {"head": new_head}


@route("POST", r"^/stages/(?P<stage>[^/]+)/sync$")
def stage_sync(ctx, m, body):
    results = ctx.gitops.reconcile_stage(m.group("stage"), project=(body or {}).get("project"))
    return 200, {"reconciled": results}


@route("GET", r"^/docs/(?P<relpath>.+)$")
def get_doc(ctx, m, body):
    relpath = unquote(m.group("relpath"))
    try:
        content = docs_store.read_doc(ctx.docs_root, relpath)
    except docs_store.PathEscapesDocsRoot as exc:
        return 400, {"error": str(exc)}
    if content is None:
        return 404, {"error": "no such doc"}
    return 200, {"path": relpath, "content": content}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/criteria$")
def freeze_criteria(ctx, m, body):
    require_internal(body, "actor", "criteria")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    chash = ctx.store.freeze_ticket_criteria(tid, body["actor"], body["criteria"])
    return 200, {"criteria_hash": chash}


@route("GET", r"^/tickets/(?P<tid>[^/]+)/criteria$")
def get_criteria(ctx, m, body):
    tid = m.group("tid")
    result = ctx.store.get_ticket_criteria(tid)
    if result is None:
        return 404, {"error": f"no criteria set for {tid!r}"}
    return 200, result


@route("POST", r"^/tickets/(?P<tid>[^/]+)/watch$")
def watch_ticket(ctx, m, body):
    require_internal(body, "watcher", "actor")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.watch_ticket(tid, body["watcher"], body["actor"])
    return 200, {"ok": True}


@route("POST", r"^/tickets/(?P<tid>[^/]+)/unwatch$")
def unwatch_ticket(ctx, m, body):
    require_internal(body, "watcher", "actor")
    tid = m.group("tid")
    if ctx.store.get_ticket(tid) is None:
        return 404, {"error": f"no such ticket {tid!r}"}
    ctx.store.unwatch_ticket(tid, body["watcher"], body["actor"])
    return 200, {"ok": True}


@route("GET", r"^/watched$")
def list_watched(ctx, m, body):
    watcher = body.get("watcher")
    if not watcher:
        return 400, {"error": "missing required query param: watcher"}
    return 200, {"tickets": ctx.store.list_watched_tickets(watcher)}


@route("POST", r"^/projects$")
def register_project(ctx, m, body):
    require_internal(body, "codename", "prefix")
    project_id = ctx.store.register_project(body["codename"], body["prefix"], body.get("source_root"))
    return 201, {"project_id": project_id}


@route("GET", r"^/projects$")
def list_projects(ctx, m, body):
    return 200, {"projects": ctx.store.list_projects()}


@route("POST", r"^/hotlists$")
def create_hotlist(ctx, m, body):
    require_internal(body, "name", "actor")
    hotlist_id = ctx.store.create_hotlist(body["name"], body["actor"])
    return 201, {"hotlist_id": hotlist_id}


@route("GET", r"^/hotlists$")
def list_hotlists(ctx, m, body):
    return 200, {"hotlists": ctx.store.list_hotlists()}


@route("GET", r"^/hotlists/(?P<name>[^/]+)$")
def get_hotlist(ctx, m, body):
    result = ctx.store.get_hotlist(unquote(m.group("name")))
    if result is None:
        return 404, {"error": f"no such hotlist {m.group('name')!r}"}
    return 200, result


@route("POST", r"^/hotlists/(?P<name>[^/]+)/items$")
def add_to_hotlist(ctx, m, body):
    require_internal(body, "ticket_id", "actor")
    ctx.store.add_to_hotlist(
        unquote(m.group("name")), body["ticket_id"], body["actor"], note=body.get("note"),
    )
    return 200, {"ok": True}


@route("POST", r"^/hotlists/(?P<name>[^/]+)/items/(?P<tid>[^/]+)/remove$")
def remove_from_hotlist(ctx, m, body):
    require_internal(body, "actor")
    ctx.store.remove_from_hotlist(unquote(m.group("name")), m.group("tid"), body["actor"])
    return 200, {"ok": True}


@route("POST", r"^/datasets$")
def create_dataset(ctx, m, body):
    require_internal(body, "name", "actor")
    dataset_id = ctx.store.create_dataset(body["name"], body["actor"])
    return 201, {"dataset_id": dataset_id}


@route("POST", r"^/datasets/(?P<name>[^/]+)/projects$")
def add_project_to_dataset(ctx, m, body):
    require_internal(body, "project_prefix", "actor")
    ctx.store.add_project_to_dataset(unquote(m.group("name")), body["project_prefix"], body["actor"])
    return 200, {"ok": True}


@route("GET", r"^/datasets$")
def list_datasets(ctx, m, body):
    return 200, {"datasets": ctx.store.list_datasets()}


@route("POST", r"^/sql$")
def run_sql(ctx, m, body):
    """Read-only ad hoc SQL, per Clint Eastwood's review. Errors are caught and
    classified HERE, not left to escape into dispatch()'s generic sqlite3.OperationalError
    handler -- that handler substring-matches "locked"/"busy" to decide 503-vs-500, and
    every SQL typo a human types in this box IS an OperationalError, so a query against a
    table literally named "locked" would otherwise be misreported as store contention
    (Clint's finding, confirmed: 'SELECT * FROM locked' -> false 503 through the generic
    path). A query timeout is a client-correctable condition (408), not a server error."""
    if os.environ.get("TESSERA_ENABLE_SQL") != "1":
        return 403, {
            "error": "POST /sql is disabled (set TESSERA_ENABLE_SQL=1 to enable this admin surface)"
        }
    require_internal(body, "query")
    try:
        # Self-healing wrapper, not the bare run_readonly_query -- tries the
        # query as given first, only applies deterministic fixups (trailing comma,
        # a fat-fingered ' -- ' for ' = ') on a genuine failure, and only keeps a fix
        # that actually runs successfully. The exception types raised on total failure
        # are identical either way, so the except clauses below are unchanged.
        result = run_readonly_query_with_self_healing(ctx.store.db_path, body["query"])
        return 200, result
    except QueryRejected as exc:
        return 400, {"error": str(exc)}
    except QueryTimedOut as exc:
        return 408, {"error": str(exc)}
    except sqlite3.DatabaseError as exc:
        return 400, {"error": str(exc)}


@route("GET", r"^/sql/schema$")
def sql_schema(ctx, m, body):
    """Server-controlled schema metadata for the SQL tab's schema/type browser
    -- table names and column types, not user query input, so none of run_sql's
    query-safety machinery (timeout/byte-cap/authorizer) applies here. descriptions
    -- descriptions are table_name -> {column_name: description}, merged in here rather than
    a second round-trip since Discovery mode needs both together on every load."""
    return 200, {
        "tables": get_schema(ctx.store.db_path),
        "descriptions": ctx.store.list_column_descriptions(),
    }


@route("POST", r"^/sql/schema/(?P<table>[^/]+)/(?P<column>[^/]+)/description$")
def set_column_description(ctx, m, body):
    require_internal(body, "description", "actor")
    ctx.store.set_column_description(
        unquote(m.group("table")), unquote(m.group("column")), body["description"], body["actor"]
    )
    return 200, {"ok": True}


@route("PUT", r"^/docs/(?P<relpath>.+)$")
def put_doc(ctx, m, body):
    require_internal(body, "content")
    relpath = unquote(m.group("relpath"))
    try:
        docs_store.write_doc(ctx.docs_root, relpath, body["content"])
    except docs_store.PathEscapesDocsRoot as exc:
        return 400, {"error": str(exc)}
    return 200, {"ok": True}


class Context:
    def __init__(self, store, gitops, docs_root):
        self.store = store
        self.gitops = gitops
        self.docs_root = docs_root


def dispatch(ctx, method, path, body):
    for route_method, pattern, fn in ROUTES:
        if route_method != method:
            continue
        m = pattern.match(path)
        if m:
            try:
                return fn(ctx, m, body)
            except ValueError as exc:
                return 400, {"error": str(exc)}
            except (StoreError, GitOpsError) as exc:
                return 400, {"error": str(exc)}
            except sqlite3.IntegrityError as exc:
                # A FK/PK violation from the projection tables (e.g. `link` to a
                # ticket_id that doesn't exist) is not a StoreError subclass and
                # previously escaped uncaught -- BaseHTTPRequestHandler's default error
                # path on an uncaught exception is a broken connection with no JSON body
                # at all (found by Clint Eastwood's adversarial review; an earlier pass
                # gave the CLI the equivalent fix but missed this exception type and this
                # surface). Always a bad-input problem, never transient -- 400, not 503.
                return 400, {"error": str(exc)}
            except sqlite3.OperationalError as exc:
                # store's 15-attempt retry is real but bounded (ARCHITECTURE.md:
                # measured 89 hard SQLITE_BUSY/1000 at the documented worst case) --
                # exhausting it raises sqlite3.OperationalError, which is not a
                # StoreError subclass and would otherwise escape uncaught, hitting
                # BaseHTTPRequestHandler's default error path (a stack trace to stderr
                # and a reset connection with no JSON body).
                #
                # Only classify as "transient, retry it" (503) using the SAME message
                # check retry_internal itself uses to decide what's retryable -- a
                # second review pass found the original blanket catch would also map a
                # genuine, permanent bug (a bad column name, on-disk corruption) to 503,
                # mislabeling it as transient load instead of a real defect. Anything
                # else is a real error (500), not something worth telling a caller to
                # retry.
                msg = str(exc).lower()
                if "locked" in msg or "busy" in msg:
                    return 503, {"error": f"store temporarily unavailable under contention: {exc}"}
                return 500, {"error": f"store error: {exc}"}
    return 404, {"error": f"no route for {method} {path}"}


def make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet by default; a real deployment would route this to a log file

        def read_body_internal(self):
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            if length > MAX_BODY_BYTES:
                raise ValueError(
                    f"request body {length} bytes exceeds cap of {MAX_BODY_BYTES}"
                )
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else {}

        def cross_origin_internal(self):
            """This server has no legitimate cross-origin caller -- app.js is
            always served from, and fetches, the same origin. A cross-origin POST/PUT
            using a CORS-safelisted method + content-type (e.g. Content-Type: text/plain)
            never triggers a preflight, so a malicious webpage the operator's browser has
            open can otherwise write blind, with no CORS headers required on either side.
            Origin is compared against Host (what the browser itself believes it's
            talking to), so this holds regardless of which port the server is bound to --
            no hardcoded origin to fall out of sync with --port."""
            origin = self.headers.get("Origin")
            if origin is None:
                return False  # no Origin header: same-origin browser nav, or a non-browser client
            host = self.headers.get("Host", "")
            return origin not in (f"http://{host}", f"https://{host}")

        def non_json_content_type_internal(self):
            """Reject anything other than application/json on a write -- closes
            the specific CORS-safelisted-content-type gap (text/plain) the exploit used,
            as defense in depth alongside the Origin check above."""
            ctype = self.headers.get("Content-Type", "")
            return ctype.split(";")[0].strip().lower() != "application/json"

        def respond_internal(self, status, obj):
            payload = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/" or self.path == "":
                return self.handle_index_internal()
            if self.path.startswith("/events/stream"):
                return self.handle_sse_internal()
            if self.path.startswith("/static/"):
                return self.handle_static_internal()
            status, obj = dispatch(ctx, "GET", self.path.split("?")[0], parse_query_internal(self.path))
            self.respond_internal(status, obj)

        def handle_index_internal(self):
            index_path = STATIC_ROOT / "index.html"
            if not index_path.is_file():
                return self.respond_internal(404, {"error": "reviewui not built"})
            body = index_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", CSP_HEADER)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.cross_origin_internal():
                return self.respond_internal(403, {"error": "cross-origin request rejected"})
            if self.non_json_content_type_internal():
                return self.respond_internal(415, {"error": "Content-Type must be application/json"})
            try:
                body = self.read_body_internal()
            except json.JSONDecodeError:
                return self.respond_internal(400, {"error": "invalid JSON body"})
            except ValueError as exc:
                return self.respond_internal(413, {"error": str(exc)})
            status, obj = dispatch(ctx, "POST", self.path.split("?")[0], body)
            self.respond_internal(status, obj)

        def do_PUT(self):
            if self.cross_origin_internal():
                return self.respond_internal(403, {"error": "cross-origin request rejected"})
            if self.non_json_content_type_internal():
                return self.respond_internal(415, {"error": "Content-Type must be application/json"})
            try:
                body = self.read_body_internal()
            except json.JSONDecodeError:
                return self.respond_internal(400, {"error": "invalid JSON body"})
            except ValueError as exc:
                return self.respond_internal(413, {"error": str(exc)})
            status, obj = dispatch(ctx, "PUT", self.path.split("?")[0], body)
            self.respond_internal(status, obj)

        def handle_sse_internal(self):
            from .sse import sse_stream

            # A reconnecting EventSource automatically sends back the last "id:" value
            # this handler emitted, via Last-Event-ID -- honor it so a reconnect resumes
            # exactly where it left off instead of either missing events (if we always
            # started from "now") or replaying full history again (the bug this
            # replaces). A brand-new connection has no such header and gets sse_stream's
            # own "now" default.
            last_event_id_header = self.headers.get("Last-Event-ID")
            start_rowid = int(last_event_id_header) if last_event_id_header else None

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for chunk in sse_stream(ctx.store, start_rowid=start_rowid):
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # An ordinary client disconnect mid-stream (tab closed, refresh). The 200
                # response was already sent before this loop started, so there is no
                # response left to attach an error to -- nothing to flag, this ends the
                # handler thread cleanly, which is the correct outcome here.
                pass

        def handle_static_internal(self):
            rel = self.path[len("/static/"):].split("?")[0]
            target = (STATIC_ROOT / rel).resolve()
            if not (target == STATIC_ROOT.resolve() or target.is_relative_to(STATIC_ROOT.resolve())):
                return self.respond_internal(400, {"error": "path escapes static root"})
            if not target.is_file():
                return self.respond_internal(404, {"error": "not found"})
            # No Content-Type at all previously -- survived only because this directory
            # has one app.js/styles.css and browsers sniff (per Clint's review). Set
            # it explicitly now that more static files are being added, plus
            # X-Content-Type-Options so a browser can't be talked into re-sniffing anyway.
            content_type, _ = mimetypes.guess_type(str(target))
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(target.read_bytes())

    return Handler


def build_server(store, gitops, docs_root, host="127.0.0.1", port=0):
    ctx = Context(store, gitops, docs_root)
    server = ThreadingHTTPServer((host, port), make_handler(ctx))
    return server
