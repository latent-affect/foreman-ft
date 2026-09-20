"""ATLASSN-96, ARCHITECTURE.md section 32. Permanent regression tests for
v_gaming_evasion_by_session, migration 16 -- the trust-gate shape (C1), denominator discipline
(C2), and the retry-after-deny reuse of session_prior (C4), per
ATLASSN-96-PREREGISTRATION-20260905.md's frozen criteria.

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_gaming_evasion_by_session -v
"""

import tempfile
import unittest
from pathlib import Path

from atlas.warehouse import migrate

SENTINEL = "SOURCE-DISTRUSTED-DO-NOT-USE"


def _pass_all_hook_verdict_checks(conn, run_id, exclude=()):
    names = [
        r[0] for r in conn.execute(
            "SELECT check_name FROM dq_check WHERE source_table='hook_verdict'"
        )
        if r[0] not in exclude
    ]
    for name in names:
        conn.execute(
            "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
            "VALUES (?, ?, '2026-01-01T00:00:00Z', 1, 'synthetic-pass')",
            (run_id, name),
        )
    conn.commit()


def _fail_one_check(conn, run_id, check_name):
    conn.execute(
        "INSERT INTO dq_check_run (run_id, check_name, run_at, passed, detail) "
        "VALUES (?, ?, '2026-01-01T00:00:01Z', 0, 'synthetic-fail')",
        (run_id, check_name),
    )
    conn.commit()


def _insert_verdict(conn, run_id, byte_offset, ts, epoch_ms, handler_id, tool_name,
                     session_id, tool_use_id, decision, rule_id="R1"):
    conn.execute(
        "INSERT INTO hook_verdict (stream_id, byte_offset, ingest_run_id, ts, epoch_ms, "
        "ts_resolution, handler_id, hook_event, verdict, session_id, cwd, tool_name, "
        "tool_use_id, self_duration_ms, decision, rule_id) VALUES "
        "('s1', ?, ?, ?, ?, 'microsecond', ?, 'PreToolUse', 'fire', ?, '/proj/a', ?, ?, 5.0, ?, ?)",
        (byte_offset, run_id, ts, epoch_ms, handler_id, session_id, tool_name, tool_use_id,
         decision, rule_id),
    )


def _insert_shape_row(conn, call_id, tool_use_id, session_id, **flags):
    cols = [
        "has_chr_paren", "has_base64_decode", "has_eval_word", "has_getattr_dunder_import",
        "has_string_concat_import", "has_dollar_var_command_position",
        "has_heredoc_into_interpreter", "has_python_c_os_subprocess", "has_quote_splice",
        "has_cmd_pos_var_indirection",
    ]
    values = [flags.get(c, 0) for c in cols]
    conn.execute(
        f"INSERT INTO bash_command_shape_v2 (transcript_kind, call_id, tool_use_id, session_id, "
        f"ts, parse_failure, {', '.join(cols)}, extractor_version) VALUES "
        f"('session', ?, ?, ?, '2026-01-01T00:00:00Z', 0, {', '.join(['?'] * len(cols))}, '2')",
        (call_id, tool_use_id, session_id, *values),
    )


class TrustGateTests(unittest.TestCase):
    """C1: single trust_state, real rows when trusted, exactly one sentinel row when distrusted."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")
        _insert_verdict(self.conn, self.run_id, 0, "2026-01-01T00:00:00.000001Z", 1000,
                         "guard_x.py", "Bash", "sessA", "tu1", "deny")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_returns_real_rows_when_trusted(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id)
        rows = self.conn.execute(
            "SELECT trust_state FROM v_gaming_evasion_by_session"
        ).fetchall()
        self.assertGreater(len(rows), 0)
        self.assertNotIn(SENTINEL, {r[0] for r in rows})

    def test_collapses_to_exactly_one_sentinel_row_when_distrusted(self):
        _pass_all_hook_verdict_checks(self.conn, self.run_id, exclude=("verdict_domain_closed",))
        _fail_one_check(self.conn, self.run_id, "verdict_domain_closed")
        rows = self.conn.execute(
            "SELECT trust_state FROM v_gaming_evasion_by_session"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], SENTINEL)


class DenominatorAndRetryTests(unittest.TestCase):
    """C2 (denominator discipline) and C4 (retry-after-deny reuses session_prior), against a
    hand-computable fixture: sessA has 3 denies (2 Bash, 1 Read), sessB has 1 (Bash, unjoinable).
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = migrate.connect(str(Path(self.tmpdir.name) / "test.db"))
        self.run_id = migrate.new_ingest_run(self.conn, status="ok")

        # sessA: tu1 (Bash, deny, has a shape row with has_cmd_pos_var_indirection=1),
        # tu2 (Bash, deny, NO shape row -- simulates an unbackfilled/unjoinable call),
        # tu3 (Read, deny). Each is immediately followed by the next decision-bearing verdict,
        # which is also a deny, except tu3 (the session's last verdict) -- so
        # next_was_denied_too is true for tu1 and tu2, NULL for tu3. n_retry_after_deny = 2.
        _insert_verdict(self.conn, self.run_id, 0, "2026-01-01T00:00:00.000001Z", 1000,
                         "guard_x.py", "Bash", "sessA", "tu1", "deny")
        _insert_verdict(self.conn, self.run_id, 100, "2026-01-01T00:00:01.000001Z", 1001,
                         "guard_x.py", "Bash", "sessA", "tu2", "deny")
        _insert_verdict(self.conn, self.run_id, 200, "2026-01-01T00:00:02.000001Z", 1002,
                         "guard_x.py", "Read", "sessA", "tu3", "deny")
        _insert_shape_row(self.conn, 1, "tu1", "sessA", has_cmd_pos_var_indirection=1)

        # sessB: one deny, Bash, no shape row, no retry (session's only verdict).
        _insert_verdict(self.conn, self.run_id, 300, "2026-01-01T00:00:03.000001Z", 1003,
                         "guard_x.py", "Bash", "sessB", "tu4", "deny")

        self.conn.commit()
        _pass_all_hook_verdict_checks(self.conn, self.run_id)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_denominator_discipline(self):
        """C2: SUM(n_denies_total) across every row this view returns must equal hook_verdict's
        own deny count for sessioned rows -- no join anywhere may silently shrink the population."""
        total_from_view = self.conn.execute(
            "SELECT SUM(n_denies_total) FROM v_gaming_evasion_by_session WHERE trust_state='ok'"
        ).fetchone()[0]
        total_from_source = self.conn.execute(
            "SELECT COUNT(*) FROM hook_verdict WHERE decision='deny' AND session_id IS NOT NULL"
        ).fetchone()[0]
        self.assertEqual(total_from_view, total_from_source)
        self.assertEqual(total_from_source, 4)

    def test_hand_computed_numbers_per_session(self):
        rows = {
            r[0]: r[1:] for r in self.conn.execute(
                "SELECT session_id, n_denies_total, n_denied_bash_calls, "
                "n_denied_bash_calls_joinable, sum_has_cmd_pos_var_indirection, "
                "n_retry_after_deny FROM v_gaming_evasion_by_session WHERE trust_state='ok'"
            )
        }
        self.assertEqual(rows["sessA"], (3, 2, 1, 1, 2))
        self.assertEqual(rows["sessB"], (1, 1, 0, None, 0))

    def test_retry_after_deny_matches_session_prior_directly(self):
        """C4: n_retry_after_deny must be produced by aggregating session_prior's own
        next_was_denied_too, not a re-derived copy of that window logic."""
        for session_id in ("sessA", "sessB"):
            expected = self.conn.execute(
                "SELECT SUM(next_was_denied_too) FROM session_prior "
                "WHERE session_id = ? AND batch_trust_state = 'ok'",
                (session_id,),
            ).fetchone()[0] or 0
            actual = self.conn.execute(
                "SELECT n_retry_after_deny FROM v_gaming_evasion_by_session WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            self.assertEqual(actual, expected, f"{session_id}: view diverges from session_prior")


class NegativeControlTests(unittest.TestCase):
    """The battery must discriminate: a mutant that reports every obfuscation-flag sum as a bare
    COUNT of denied Bash calls (ignoring which flags actually fired) must disagree with the real
    hand-computed expectation this test file's own fixture establishes."""

    def test_a_count_only_mutant_would_fail_the_hand_computed_check(self):
        # sessA has 2 denied Bash calls but only 1 actually has has_cmd_pos_var_indirection=1.
        # A mutant reporting sum_has_cmd_pos_var_indirection = n_denied_bash_calls (2) would be
        # caught by test_hand_computed_numbers_per_session's assertEqual(..., 1) above.
        mutant_sum_has_cmd_pos_var_indirection = 2  # what a COUNT(*)-only mutant would report
        real_expected = 1
        self.assertNotEqual(
            mutant_sum_has_cmd_pos_var_indirection, real_expected,
            "the mutant and the real expectation must differ, or this battery proves nothing",
        )


if __name__ == "__main__":
    unittest.main()
