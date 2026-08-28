import re
import sqlite3
import threading

# Text-level filter is a USABILITY convenience (a friendly 400 for an obvious mistake),
# NOT the security boundary -- Clint Eastwood's review found the
# original design leaned on it as a backstop and that this was wrong: Python's sqlite3
# already rejects multi-statement text on its own ("You can only execute one statement
# at a time"), and the real boundary is the connection-level lockdown in run_readonly_
# query() below. WITH is allowed alongside SELECT because a recursive CTE is the single
# most valuable thing a human will want to type here (walking the events hash chain),
# and it's safe in SQLite specifically -- unlike PostgreSQL, SQLite has no DML-in-CTE
# (`WITH x AS (INSERT ... RETURNING ...)` is a syntax error there), verified directly.
_LEADING_TRIVIA = re.compile(r"^(\s+|--[^\n]*\n?|/\*.*?\*/)*", re.DOTALL)
_ALLOWED_LEADING_KEYWORDS = ("SELECT", "WITH")

MAX_ROWS = 2000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
QUERY_TIMEOUT_S = 8.0
FETCH_BATCH_SIZE = 200


def _deny_attach_authorizer(action, arg1, arg2, dbname, source):
    """sqlite3.Connection.setlimit(SQLITE_LIMIT_ATTACHED, 0) is what Clint Eastwood's
    review actually measured and recommended, but it requires Python 3.11+
    (Connection.setlimit doesn't exist before that) -- this project runs on the
    system's Python 3.9 (confirmed: hasattr(sqlite3.Connection, 'setlimit') is False
    there). set_authorizer denying SQLITE_ATTACH specifically is available since much
    earlier Python and achieves the identical outcome (ATTACH itself fails), which is
    the one Clint measured as the actual gap in mode=ro; everything else stays
    permitted so ordinary SELECT/WITH queries are unaffected."""
    if action == sqlite3.SQLITE_ATTACH:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _regexp_internal(pattern, value):
    """Backs the SQLite REGEXP operator: SQLite has no built-in REGEXP --
    the operator exists in its grammar but is a no-op that raises 'no such function:
    regexp' unless a function literally named REGEXP is registered on the connection.
    SQLite calls it as regexp(pattern, value) for the expression 'value REGEXP
    pattern' (pattern first, value second -- confirmed against SQLite's own docs,
    the reverse of what the operator's word order suggests). value can be NULL/non-
    text (e.g. an INTEGER column); re.search requires a str, so a non-string value is
    coerced rather than raising and turning an ordinary WHERE clause into a 500."""
    if value is None:
        return False
    return re.search(pattern, str(value)) is not None


class QueryRejected(ValueError):
    """The query text itself was rejected before any connection was opened."""


class QueryTimedOut(Exception):
    """The query ran past QUERY_TIMEOUT_S and was interrupted."""


# Deterministic (no ML) fixups, each tried ONLY after the original query has
# already failed to execute -- never a preemptive rewrite of something that might be
# intentional (a real leading SQL comment already passes validate_query_text and runs
# fine as-is; these two patterns can only ever fire on a query that's already broken).
#
# Two separate sub()s, not one: a naive single regex that strips the comma AND all its
# trailing whitespace before a clause keyword glues the two tokens together --
# "SELECT a, b, FROM t" became "SELECT a, bFROM t" (a NEW syntax error) in first-draft
# testing, caught by direct verification before this ever reached the app. Before a
# closing paren or end-of-string, no separator is needed, so that case strips cleanly;
# before a keyword, the match is replaced with exactly one space instead.
_TRAILING_COMMA_BEFORE_CLOSE_OR_END = re.compile(r",\s*(?=\)|$)")
_TRAILING_COMMA_BEFORE_KEYWORD = re.compile(
    r",\s*(?=\b(?:FROM|WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b)", re.IGNORECASE
)


def fixup_trailing_comma(query):
    fixed = _TRAILING_COMMA_BEFORE_CLOSE_OR_END.sub("", query)
    fixed = _TRAILING_COMMA_BEFORE_KEYWORD.sub(" ", fixed)
    if fixed != query:
        return fixed, "removed a trailing comma"
    return query, None


def fixup_dash_dash_equals(query):
    """A lone ' -- ' (space, dash, dash, space) is a plausible fat-finger for ' = ' --
    deliberately narrow (requires a space on BOTH sides) so a real SQL comment like
    '--note' or a comment that isn't surrounded by spaces on both sides is never touched.
    Only reached after the original query already failed (see module docstring above),
    and only KEPT if the resulting query actually runs (see run_readonly_query_with_
    self_healing) -- so even a wrong guess here can't silently corrupt a query that
    would otherwise have worked.

    Real, disclosed limitation found by direct testing, not assumed: this typo usually
    does NOT raise an error at all. `-- ` starts a genuine SQLite line comment, so
    "WHERE status -- 'open'" just silently becomes "WHERE status" -- a bare truthy check
    that returns wrong/empty results with nothing to react to (self-healing only ever
    fires on an actual execution failure). This fixup can only help the narrower case
    where the comment eats enough of the query to break its own structure and raise
    something, e.g. an unclosed paren: "WHERE (status -- 'open')" -> OperationalError
    'incomplete input', because the closing paren got commented out too."""
    if " -- " in query:
        return query.replace(" -- ", " = "), "replaced ' -- ' with ' = ' (likely a fat-fingered equals sign)"
    return query, None


SELF_HEALING_FIXUPS = (fixup_trailing_comma, fixup_dash_dash_equals)


def run_readonly_query_with_self_healing(db_path, query):
    """Tries the query exactly as given first. Only on a genuine failure does it apply
    SELF_HEALING_FIXUPS (in order, all that match) and retry ONCE with the combined
    result -- if that still fails, the ORIGINAL error is what gets raised, not a
    confusing secondary error from a mangled query. On success (original or healed),
    the result always discloses whether healing happened and exactly what changed --
    never a silent rewrite, so a fix nobody wanted is still fully visible."""
    try:
        result = run_readonly_query(db_path, query)
        result["self_healed"] = False
        result["original_error"] = None
        result["fixes_applied"] = []
        return result
    except (QueryRejected, QueryTimedOut, sqlite3.DatabaseError) as original_exc:
        candidate = query
        fixes_applied = []
        for fixup in SELF_HEALING_FIXUPS:
            candidate, description = fixup(candidate)
            if description:
                fixes_applied.append(description)
        if not fixes_applied:
            raise
        try:
            result = run_readonly_query(db_path, candidate)
        except (QueryRejected, QueryTimedOut, sqlite3.DatabaseError):
            raise original_exc from None
        result["self_healed"] = True
        result["original_error"] = str(original_exc)
        result["fixes_applied"] = fixes_applied
        result["healed_query"] = candidate
        return result


def validate_query_text(query):
    stripped = _LEADING_TRIVIA.sub("", query or "").lstrip()
    upper = stripped[:10].upper()
    if not any(upper.startswith(kw) for kw in _ALLOWED_LEADING_KEYWORDS):
        raise QueryRejected(
            "only SELECT or WITH (recursive CTE) queries are allowed"
        )
    return stripped


def run_readonly_query(db_path, query):
    """The actual safety boundary, per Clint Eastwood's review: mode=ro alone protects
    the FILE, not the CONNECTION -- a mode=ro connection can still ATTACH DATABASE a
    second file read-write and write into it (measured, confirmed real). Both lines
    below close that: the authorizer makes ATTACH itself fail outright (see
    _deny_attach_authorizer's docstring for why that's an authorizer and not
    setlimit(SQLITE_LIMIT_ATTACHED, 0) as Clint's review literally recommended), and
    PRAGMA query_only=ON blocks writes to anything the connection can see, including
    an attach that somehow got through. Neither depends on parsing the query text.

    No BEGIN, no reuse across requests/threads, closed in `finally` -- Clint's WAL/lock
    analysis (a long read does not starve a concurrent writer, verified: 3000 writes
    with a 40M-row recursive CTE open at the same time, identical timing to no reader)
    depends on exactly this pattern. Deviating from it (caching this connection,
    wrapping it in an explicit transaction) invalidates that measurement -- don't."""
    validate_query_text(query)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None)
    try:
        conn.set_authorizer(_deny_attach_authorizer)
        conn.execute("PRAGMA query_only=ON")
        conn.create_function("REGEXP", 2, _regexp_internal)

        timer = threading.Timer(QUERY_TIMEOUT_S, conn.interrupt)
        timer.start()
        try:
            try:
                cur = conn.execute(query)
            except sqlite3.Warning as exc:
                # sqlite3.Warning (raised for multi-statement text like "SELECT 1;
                # SELECT 2") is NOT a subclass of sqlite3.DatabaseError/Error -- it's a
                # direct sibling under Exception (confirmed: sqlite3.Warning.__mro__ has
                # no Error in it). It therefore escaped BOTH this function's own
                # DatabaseError handling further down AND http_api.py's `except
                # sqlite3.DatabaseError` catch entirely uncaught, crashing the request
                # thread and dropping the connection with no response at all -- found by
                # a direct HTTP repro of "SELECT 1; SELECT 2", not by inspection. Folded
                # into QueryRejected here so every caller gets one classified exception
                # type regardless of which sqlite3 exception class the underlying
                # rejection happens to use.
                raise QueryRejected(str(exc)) from exc
            except sqlite3.ProgrammingError as exc:
                # Python 3.12+ raises ProgrammingError instead of the Warning above for
                # the same multi-statement text -- confirmed live on 3.14.6:
                # "You can only execute one statement at a time." ProgrammingError is
                # also raised for other, unrelated caller mistakes, so only the
                # multi-statement message is folded into QueryRejected; anything else
                # re-raises rather than being silently misclassified.
                if "one statement at a time" not in str(exc):
                    raise
                raise QueryRejected(str(exc)) from exc
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = []
            total_bytes = 0
            truncated = False
            while True:
                batch = cur.fetchmany(FETCH_BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    # bytes (a BLOB column, or any query using randomblob()/a binary
                    # function) is not JSON-serializable -- json.dumps in
                    # http_api.py's respond_internal() crashed on it uncaught,
                    # dropping the connection with no response at all (found by a
                    # direct HTTP repro of "SELECT randomblob(10)", not by
                    # inspection). Hex-encode it here, at the one place that already
                    # knows the real column type, rather than leaving every response
                    # consumer to rediscover this.
                    row_list = [v.hex() if isinstance(v, bytes) else v for v in row]
                    # Byte-capped, not just row-capped: a single-row SELECT
                    # randomblob(200000000) is ONE row and balloons memory regardless of
                    # any row LIMIT (measured: 400MB+ RSS) -- Clint's finding.
                    total_bytes += sum(len(str(v)) for v in row_list)
                    if len(rows) >= MAX_ROWS or total_bytes >= MAX_RESPONSE_BYTES:
                        truncated = True
                        break
                    rows.append(row_list)
                if truncated:
                    break
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise QueryTimedOut(
                    f"query exceeded the {QUERY_TIMEOUT_S}s limit and was interrupted"
                ) from exc
            raise
        finally:
            timer.cancel()
    finally:
        conn.close()

    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}


# BigQuery-style display names for the schema panel, NOT a change to the
# actual SQLite declared type or storage -- SQLite's on-disk representation is
# unaffected by any of this (verified directly: typeof() stays 'text' for an ISO8601
# string regardless of whether the column is declared TEXT or DATETIME, since SQLite's
# NUMERIC affinity only coerces text that looks like a well-formed number). Doing this
# as a display-only mapping, rather than an actual ALTER-TABLE-rebuild migration of the
# DDL, was a deliberate choice: SQLite has no ALTER COLUMN TYPE at all (the only way to
# change a declared type is the 12-step create-copy-drop-rename dance), and the
# investigation above found ZERO functional difference between TEXT and DATETIME for
# these columns -- comparison, sorting, and every SQLite date function already work
# correctly against the real stored ISO8601 strings today. Migrating 10+ tables
# (including the hash-chained events table) for a purely cosmetic label was judged not
# worth the risk. See the design notes for the full investigation.
_SQLITE_TYPE_DISPLAY_NAMES = {"TEXT": "STRING", "INTEGER": "INTEGER", "REAL": "FLOAT", "BLOB": "BYTES"}


def display_type_internal(column_name, declared_type):
    declared_type = (declared_type or "TEXT").upper()
    # Name-pattern based, not a DDL change (see module comment above) -- every timestamp
    # column in this schema follows the *_at convention consistently (created_at,
    # updated_at, criteria_frozen_at, added_at, ...), confirmed by inspection of
    # schema.py's actual DDL, not assumed.
    if declared_type == "TEXT" and column_name.endswith("_at"):
        return "DATETIME"
    return _SQLITE_TYPE_DISPLAY_NAMES.get(declared_type, declared_type)


def get_schema(db_path):
    """table name -> [{"name": col_name, "type": display_type}, ...] for every real,
    user-facing table AND view (sqlite_master, type in ('table','view'), excluding
    SQLite's own internal sqlite_% tables) -- backs the SQL tab's schema/type browser. This is
    server-controlled metadata, not the ad hoc user-query surface run_readonly_query()
    guards -- table names come from sqlite_master itself, never from request input, so
    the f-string PRAGMA table_info({table}) below is safe the same way schema.py's own
    PROJECTION_TABLES-driven queries are: the identifier is always a real table this
    process's own catalog just reported, not attacker-controlled text. Still opened
    read-only, same hygiene as the rest of this module."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Views too, not just tables. The analytics layer is entirely views, and
        # filtering to type='table' left them queryable but invisible -- a schema browser
        # that hides half of what you can select from is worse than no browser, because it
        # reads as "this does not exist" rather than "this is not listed". PRAGMA
        # table_info() works on a view exactly as it does on a table, so nothing below
        # needs to know the difference.
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        schema = {}
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk.
            schema[table] = [
                {"name": c[1], "type": display_type_internal(c[1], c[2])} for c in cols
            ]
        return schema
    finally:
        conn.close()
