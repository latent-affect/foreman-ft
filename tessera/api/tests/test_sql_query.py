import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from ..sql_query import (
    QueryRejected, QueryTimedOut, fixup_dash_dash_equals, fixup_trailing_comma,
    get_schema, run_readonly_query, run_readonly_query_with_self_healing,
)
from ...store.store import Store


class SqlQueryTests(unittest.TestCase):
    # TESS-38, per Clint Eastwood's adversarial review of the original design (TESS-35):
    # mode=ro alone does NOT make a connection read-only -- it can still ATTACH DATABASE
    # a second file read-write and write into it. These tests exercise the actual
    # boundary (the authorizer + PRAGMA query_only), not just the text-level filter,
    # since the text filter was demonstrated NOT to be the real defense.

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.store = Store(self.db_path, codename="TESTPROJ", prefix="TP")
        self.store.create_ticket(ticket_type="Task", reporter="me", actor="agent")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_select_works(self):
        result = run_readonly_query(self.db_path, "SELECT ticket_id FROM tickets")
        self.assertEqual(result["rows"], [["TP-1"]])
        self.assertFalse(result["truncated"])

    def test_regexp_operator_registered_and_filters_correctly(self):
        # TESS-51: SQLite has no built-in REGEXP -- the operator exists in the grammar
        # but raises "no such function: regexp" unless one is registered per-connection.
        self.store.create_ticket(ticket_type="Bug", reporter="me", actor="agent")
        result = run_readonly_query(
            self.db_path, "SELECT ticket_id FROM tickets WHERE ticket_id REGEXP 'TP-[0-9]+'"
        )
        self.assertEqual(result["rows"], [["TP-1"], ["TP-2"]])
        result = run_readonly_query(
            self.db_path, "SELECT ticket_id FROM tickets WHERE ticket_id REGEXP '^ZZ'"
        )
        self.assertEqual(result["rows"], [])

    def test_regexp_operator_against_null_column_does_not_crash(self):
        # assignee is NULL for a freshly-created ticket -- regexp(pattern, NULL) must
        # come back False, not raise, or an ordinary WHERE clause over a nullable
        # column would 500 instead of just excluding the NULL rows.
        result = run_readonly_query(
            self.db_path, "SELECT ticket_id FROM tickets WHERE assignee REGEXP 'x'"
        )
        self.assertEqual(result["rows"], [])

    def test_with_recursive_works(self):
        result = run_readonly_query(
            self.db_path,
            "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n<5) SELECT n FROM r",
        )
        self.assertEqual([r[0] for r in result["rows"]], [1, 2, 3, 4, 5])

    def test_insert_rejected_at_text_filter(self):
        with self.assertRaises(QueryRejected):
            run_readonly_query(self.db_path, "INSERT INTO tickets (ticket_id) VALUES ('x')")

    def test_multi_statement_rejected_as_query_rejected_not_a_bare_sqlite_warning(self):
        # sqlite3.Warning is NOT a subclass of sqlite3.DatabaseError/Error -- a direct
        # sibling under Exception. Letting it escape as a bare sqlite3.Warning meant
        # http_api.py's `except sqlite3.DatabaseError` didn't catch it, crashing the
        # request thread with no response at all (found by direct HTTP repro, not by
        # inspection). run_readonly_query must translate it into QueryRejected so every
        # caller gets one classified, catchable exception type.
        with self.assertRaises(QueryRejected):
            run_readonly_query(self.db_path, "SELECT 1; SELECT 2")

    def test_attach_actually_blocked_at_connection_level_not_just_text_filter(self):
        # The REAL boundary, tested directly (bypassing run_readonly_query's own text
        # filter) so a future change to the filter can't silently remove the actual
        # protection without this test catching it.
        from ..sql_query import _deny_attach_authorizer

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.set_authorizer(_deny_attach_authorizer)
        conn.execute("PRAGMA query_only=ON")
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute(f"ATTACH DATABASE '{self.tmp_dir.name}/evil.db' AS evil")
        finally:
            conn.close()

    def test_write_actually_blocked_at_connection_level(self):
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE tickets SET status='closed' WHERE ticket_id='TP-1'")
        finally:
            conn.close()

    def test_timeout_interrupts_a_spinning_query(self):
        import tessera.api.sql_query as sql_query_module

        original_timeout = sql_query_module.QUERY_TIMEOUT_S
        sql_query_module.QUERY_TIMEOUT_S = 0.5
        try:
            start = time.time()
            with self.assertRaises(QueryTimedOut):
                run_readonly_query(
                    self.db_path,
                    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT count(*) FROM r",
                )
            self.assertLess(time.time() - start, 3.0, "timeout took far longer than configured")
        finally:
            sql_query_module.QUERY_TIMEOUT_S = original_timeout

    def test_byte_cap_truncates_a_single_huge_row(self):
        import tessera.api.sql_query as sql_query_module

        original_cap = sql_query_module.MAX_RESPONSE_BYTES
        sql_query_module.MAX_RESPONSE_BYTES = 1000
        try:
            result = run_readonly_query(self.db_path, "SELECT randomblob(50000)")
            self.assertTrue(result["truncated"])
            self.assertEqual(result["rows"], [])
        finally:
            sql_query_module.MAX_RESPONSE_BYTES = original_cap

    def test_row_cap_truncates_many_small_rows(self):
        import tessera.api.sql_query as sql_query_module

        original_cap = sql_query_module.MAX_ROWS
        sql_query_module.MAX_ROWS = 3
        try:
            result = run_readonly_query(
                self.db_path,
                "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n<10) SELECT n FROM r",
            )
            self.assertEqual(result["row_count"], 3)
            self.assertTrue(result["truncated"])
        finally:
            sql_query_module.MAX_ROWS = original_cap

    def test_blob_values_are_hex_encoded_not_left_as_raw_bytes(self):
        # bytes is not JSON-serializable -- left as raw bytes, this crashed
        # http_api.py's json.dumps() downstream with no response at all (found by a
        # direct HTTP repro of "SELECT randomblob(10)", not by inspection).
        result = run_readonly_query(self.db_path, "SELECT randomblob(4)")
        value = result["rows"][0][0]
        self.assertIsInstance(value, str)
        bytes.fromhex(value)  # raises ValueError if it isn't valid hex

    def test_bad_sql_gives_clean_database_error_not_a_crash(self):
        with self.assertRaises(sqlite3.OperationalError):
            run_readonly_query(self.db_path, "SELECT * FROM this_table_does_not_exist")

    def test_get_schema_lists_views_alongside_tables(self):
        # TESS-100: the analytics layer is entirely views. Filtering the catalog to
        # type='table' left them queryable but invisible in the schema browser, which
        # reads as "this does not exist" rather than "this is not listed".
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE VIEW v_probe AS SELECT ticket_id, status FROM tickets")
        conn.commit()
        conn.close()

        schema = get_schema(self.db_path)
        self.assertIn("v_probe", schema)
        self.assertEqual([c["name"] for c in schema["v_probe"]], ["ticket_id", "status"])
        # Tables must not have been displaced by adding views.
        self.assertIn("tickets", schema)
        # And the view is genuinely selectable through the guarded read-only path, not
        # merely listed.
        result = run_readonly_query(self.db_path, "SELECT ticket_id FROM v_probe")
        self.assertEqual(result["rows"], [["TP-1"]])

    def test_get_schema_lists_real_tables_and_declared_types(self):
        # TESS-44: BigQuery-style display names -- TEXT shows as STRING, a *_at column
        # shows as DATETIME (display-only; see display_type_internal's docstring for why
        # this isn't an actual DDL change).
        schema = get_schema(self.db_path)
        self.assertIn("tickets", schema)
        col_names = {c["name"] for c in schema["tickets"]}
        self.assertIn("ticket_id", col_names)
        self.assertIn("summary", col_names)
        by_name = {c["name"]: c["type"] for c in schema["tickets"]}
        self.assertEqual(by_name["ticket_id"], "STRING")
        self.assertEqual(by_name["created_at"], "DATETIME")
        self.assertEqual(by_name["project_id"], "INTEGER")
        # No SQLite internal tables leaked into the schema browser.
        self.assertFalse(any(t.startswith("sqlite_") for t in schema))

    # ---- TESS-48: deterministic self-healing --------------------------------

    def test_fixup_trailing_comma_before_keyword_and_end_and_paren(self):
        self.assertEqual(
            fixup_trailing_comma("SELECT a, b, FROM tickets"),
            ("SELECT a, b FROM tickets", "removed a trailing comma"),
        )
        self.assertEqual(
            fixup_trailing_comma("SELECT a, b,"),
            ("SELECT a, b", "removed a trailing comma"),
        )
        self.assertEqual(
            fixup_trailing_comma("SELECT COUNT(a,) FROM tickets"),
            ("SELECT COUNT(a) FROM tickets", "removed a trailing comma"),
        )
        # Real, legitimate commas (inside a string literal, or a normal separator not
        # followed by a clause keyword) must be left alone.
        self.assertEqual(fixup_trailing_comma("SELECT 'a,' FROM tickets"), ("SELECT 'a,' FROM tickets", None))
        self.assertEqual(fixup_trailing_comma("SELECT a, b FROM tickets"), ("SELECT a, b FROM tickets", None))

    def test_fixup_dash_dash_equals(self):
        self.assertEqual(
            fixup_dash_dash_equals("SELECT * FROM tickets WHERE status -- 'open'"),
            ("SELECT * FROM tickets WHERE status = 'open'",
             "replaced ' -- ' with ' = ' (likely a fat-fingered equals sign)"),
        )
        # A real comment with no space before the dashes, or only one space, is untouched.
        self.assertEqual(fixup_dash_dash_equals("SELECT 1 --comment"), ("SELECT 1 --comment", None))
        self.assertEqual(fixup_dash_dash_equals("SELECT 1"), ("SELECT 1", None))

    def test_self_healing_fixes_trailing_comma_and_discloses_it(self):
        result = run_readonly_query_with_self_healing(self.db_path, "SELECT ticket_id, FROM tickets")
        self.assertTrue(result["self_healed"])
        self.assertIn("removed a trailing comma", result["fixes_applied"])
        self.assertIn("syntax error", result["original_error"])
        self.assertEqual(result["rows"], [["TP-1"]])

    def test_self_healing_fixes_dash_dash_equals_and_discloses_it(self):
        # Real finding from direct testing: a fat-fingered ' -- ' usually fails SILENTLY
        # (SQLite treats the rest of the line as a comment and the query still parses --
        # e.g. "WHERE status -- 'open'" just becomes "WHERE status", a bare truthy check
        # that returns wrong/empty results with NO raised error at all) -- reactive
        # self-healing, which only ever fires on an actual failure, cannot catch that
        # common case. It only helps when the comment-eaten text also breaks the SQL's
        # own structure enough to raise, e.g. an unclosed paren -- confirmed directly:
        # "WHERE (status -- 'open')" raises OperationalError('incomplete input') because
        # the closing paren got commented out too.
        result = run_readonly_query_with_self_healing(
            self.db_path, "SELECT ticket_id FROM tickets WHERE (ticket_id -- 'TP-1')"
        )
        self.assertTrue(result["self_healed"])
        self.assertTrue(any("fat-fingered" in f for f in result["fixes_applied"]))
        self.assertEqual(result["rows"], [["TP-1"]])

    def test_self_healing_does_not_fire_when_query_already_works(self):
        result = run_readonly_query_with_self_healing(self.db_path, "SELECT ticket_id FROM tickets")
        self.assertFalse(result["self_healed"])
        self.assertIsNone(result["original_error"])
        self.assertEqual(result["fixes_applied"], [])

    def test_self_healing_raises_the_original_error_when_nothing_actually_fixes_it(self):
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            run_readonly_query_with_self_healing(self.db_path, "SELECT * FROM this_table_does_not_exist")
        self.assertIn("this_table_does_not_exist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
