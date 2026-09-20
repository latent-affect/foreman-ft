"""GOALS.json (query component) C8, C9, C10. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.query.tests.test_git_commit_detail -v

UNEXECUTED AS OF THIS PROPOSAL, same disclosure as ATLASSN-104's test file: Alice has no Bash/
Python execution in this session. Every assertion below is reasoned against the real, currently
-live schema (git_commit/git_commit_file's exact column set and constraints, ARCHITECTURE.md
section 39.1's design in ATLASSN-98-ARCHITECTURE-DDL-HANDOFF.md) and this proposal's own
migrate.py/ddl.py additions, not run.

This class calls migrate.apply_migration24(conn) DIRECTLY on TempWarehouse's setup connection,
because migration 24 is not wired into migrate.connect() yet (same reason migration 22/23 are
not -- see apply_migration24's own docstring). EXPECTED TO FAIL with ddl.DdlExtractionError
until ARCHITECTURE.md section 39.1 exists. That is the correct failure mode for unlanded design,
not a defect in this test.

FIX 2026-09-20 (Bob's real-test finding, TESSERA comment 3800 on ATLASSN-188): the migration's
own SQL comment carries the literal word "session_id" in a NEGATION ("no session_id column or
join, ever (C9)"), so test_view_definition_never_names_session_id's naive
assertNotIn("session_id", sql) against the WHOLE file always failed the moment it was actually
run. C9's own text asks for the view's SQL DEFINITION, not its comments -- the check is now
scoped by stripping full-line SQL comments (every comment in this migration's fenced text is on
its own line; no trailing inline comments exist to worry about) before searching.
"""

import sqlite3
import unittest

from atlas.query.facade import QueryFacade, QueryRefused
from atlas.query.tests._helpers import TempWarehouse, make_clean_run
from atlas.warehouse import ddl, migrate


def _insert_project(conn, prefix):
    conn.execute(
        "INSERT INTO dim_project (project_prefix, project_codename, tessera_project_id, "
        "source_root, root_state, is_git_repo, foreman_bootstrapped, tessguard_wired, "
        "refreshed_at) VALUES (?, ?, NULL, '/repo', 'git-repo', 1, 1, 1, '2026-01-01T00:00:00Z')",
        (prefix, prefix),
    )


def _insert_commit(conn, run_id, prefix, sha, author_hash="a" * 64):
    conn.execute(
        "INSERT INTO git_commit (project_prefix, sha, ingest_run_id, committed_ts, "
        "author_hash, subject, files_changed, insertions, deletions) "
        "VALUES (?, ?, ?, '2026-01-01T00:00:00Z', ?, 'a commit', 1, 1, 1)",
        (prefix, sha, run_id, author_hash),
    )


def _insert_file(conn, prefix, sha, file_path, insertions=1, deletions=1):
    conn.execute(
        "INSERT INTO git_commit_file (project_prefix, sha, file_path, insertions, deletions) "
        "VALUES (?, ?, ?, ?, ?)",
        (prefix, sha, file_path, insertions, deletions),
    )


class GitCommitDetailTests(unittest.TestCase):
    def setUp(self):
        self.wh = TempWarehouse()
        migrate.apply_migration24(self.wh.setup_conn)  # raises pre-landing; expected
        run_id = make_clean_run(self.wh.setup_conn, with_verdicts=False)
        _insert_project(self.wh.setup_conn, "PROJ")
        # (a) a commit with files.
        _insert_commit(self.wh.setup_conn, run_id, "PROJ", "sha_with_files")
        _insert_file(self.wh.setup_conn, "PROJ", "sha_with_files", "a.py", 3, 1)
        # (b) a commit with ZERO git_commit_file rows -- the toy-model-killed defect (F5/C8).
        _insert_commit(self.wh.setup_conn, run_id, "PROJ", "sha_zero_files")
        # (c) a file row with NULL insertions/deletions (a binary file, real on the live corpus).
        _insert_commit(self.wh.setup_conn, run_id, "PROJ", "sha_binary_file")
        self.wh.setup_conn.execute(
            "INSERT INTO git_commit_file (project_prefix, sha, file_path, insertions, deletions) "
            "VALUES ('PROJ', 'sha_binary_file', 'image.png', NULL, NULL)"
        )
        self.wh.setup_conn.commit()
        self.facade = QueryFacade(self.wh.db_path)

    def tearDown(self):
        self.facade.close()
        self.wh.close()

    def test_view_is_allowlisted_and_gated_like_any_other(self):
        """C8's own gating half, same shape as test_fetch_allowlist.py's existing coverage."""
        columns, rows = self.facade.fetch("v_git_commit_detail")
        self.assertIn("commit_sha", columns)
        self.assertIn("author_hash", columns)

    def test_zero_file_commit_appears_with_nulls_not_dropped(self):
        """C8/F5: LEFT JOIN, not INNER -- the toy-modeled correction. The commit must be present
        with file_path/insertions/deletions all NULL, not absent."""
        columns, rows = self.facade.fetch("v_git_commit_detail")
        idx = {c: i for i, c in enumerate(columns)}
        zero_file_rows = [r for r in rows if r[idx["commit_sha"]] == "sha_zero_files"]
        self.assertEqual(len(zero_file_rows), 1, "zero-file commit must appear exactly once")
        row = zero_file_rows[0]
        self.assertIsNone(row[idx["file_path"]])
        self.assertIsNone(row[idx["insertions"]])
        self.assertIsNone(row[idx["deletions"]])

    def test_null_insertions_deletions_are_preserved_not_coerced_to_zero(self):
        """C8/F5: a binary/uncounted file's NULL insertions/deletions must stay NULL."""
        columns, rows = self.facade.fetch("v_git_commit_detail")
        idx = {c: i for i, c in enumerate(columns)}
        binary_rows = [r for r in rows if r[idx["commit_sha"]] == "sha_binary_file"]
        self.assertEqual(len(binary_rows), 1)
        self.assertIsNone(binary_rows[0][idx["insertions"]])
        self.assertIsNone(binary_rows[0][idx["deletions"]])

    def test_commit_with_files_reports_real_values(self):
        """The converse of the two tests above, so they are not passing because the view
        returns nothing at all."""
        columns, rows = self.facade.fetch("v_git_commit_detail")
        idx = {c: i for i, c in enumerate(columns)}
        with_files_rows = [r for r in rows if r[idx["commit_sha"]] == "sha_with_files"]
        self.assertEqual(len(with_files_rows), 1)
        self.assertEqual(with_files_rows[0][idx["file_path"]], "a.py")
        self.assertEqual(with_files_rows[0][idx["insertions"]], 3)

    def test_author_hash_only_never_a_raw_author_column(self):
        """D8. The column is author_hash, shaped like a salted digest, never a free-text name."""
        columns, rows = self.facade.fetch("v_git_commit_detail")
        self.assertNotIn("author", columns)
        self.assertIn("author_hash", columns)
        idx = columns.index("author_hash")
        for row in rows:
            if row[idx] is not None:
                self.assertRegex(row[idx], r"^[0-9a-f]{64}$")

    def test_view_definition_never_names_session_id(self):
        """C9, checked exactly as the criterion's own verification text specifies: grep the
        view's SQL definition for session_id and expect nothing, at implementation time and at
        every subsequent stage close.

        Scoped to the SQL statements, not the migration file's own prose: the file's header
        comment states C9 itself ("no session_id column or join, ever (C9)"), which contains the
        literal word in a negation. Stripping full-line `--` comments before searching is what
        makes this check test the view's real definition rather than tripping on its own
        documentation -- every comment in this migration's fenced SQL is a standalone line, so a
        line-level strip loses no statement text."""
        sql = ddl.extract_migration24_sql()
        sql_without_comments = "\n".join(
            line for line in sql.splitlines() if not line.strip().startswith("--")
        )
        self.assertNotIn("session_id", sql_without_comments)

    def test_git_commit_file_row_with_no_parent_commit_is_rejected_by_the_schema(self):
        """C10 probe 1: an orphaned git_commit_file row. git_commit_file's own FOREIGN KEY
        (project_prefix, sha) REFERENCES git_commit(project_prefix, sha), and this connection
        runs with PRAGMA foreign_keys = ON (migrate.connect()'s own header) -- the 'named, tested
        rule' C10 asks for is exactly this schema-level rejection, not an application check."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.wh.setup_conn.execute(
                "INSERT INTO git_commit_file (project_prefix, sha, file_path, insertions, "
                "deletions) VALUES ('PROJ', 'sha_never_existed', 'x.py', 1, 1)"
            )

    def test_git_commit_file_row_missing_file_path_is_rejected_by_the_schema(self):
        """C10 probe 2: file_path is NOT NULL in git_commit_file's own schema (ARCHITECTURE.md
        section 16), so this is unreachable through the real ingest path and constructed directly
        here, per criterion 3's own precedent for the same class of probe."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.wh.setup_conn.execute(
                "INSERT INTO git_commit_file (project_prefix, sha, file_path, insertions, "
                "deletions) VALUES ('PROJ', 'sha_with_files', NULL, 1, 1)"
            )

    def test_source_freshness_gains_a_git_commit_row(self):
        """C11: git_commit must appear in v_source_freshness with a real staleness_minutes,
        same UNION-ALL pattern migration 17 already used for the two transcript sources."""
        columns, rows = self.facade.fetch("v_source_freshness")
        idx = {c: i for i, c in enumerate(columns)}
        git_rows = [r for r in rows if r[idx["source_name"]] == "git_commit"]
        self.assertEqual(len(git_rows), 1)
        self.assertIsNotNone(git_rows[0][idx["staleness_minutes"]])


if __name__ == "__main__":
    unittest.main()
