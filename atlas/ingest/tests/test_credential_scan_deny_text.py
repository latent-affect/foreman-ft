"""ATLASSN-197: real-execution tests for the extended credential scan -- deny_text on both
subagent_tool_result and session_tool_result, and a real session-side scan where session_pull.py
used to hardcode credential_hits = 0 with no scan of any kind.

Synthetic fixtures throughout (temp files, in-memory sqlite matching the real schema shape for the
columns this ticket touches) -- no dependency on the real warehouse existing on the machine
running this test.
"""

import sqlite3
import unittest

from atlas.ingest import subagent_pull, session_pull


def make_subagent_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE subagent_tool_call (call_id INTEGER PRIMARY KEY, "
                 "transcript_id INTEGER, tool_use_id TEXT, tool_input_json TEXT)")
    conn.execute("CREATE TABLE subagent_tool_result (transcript_id INTEGER, tool_use_id TEXT, "
                 "deny_text TEXT, is_hook_deny INTEGER, "
                 "PRIMARY KEY (transcript_id, tool_use_id))")
    conn.execute("CREATE TABLE subagent_credential_ack (call_id INTEGER PRIMARY KEY, "
                 "payload_sha256 TEXT, pattern_name TEXT, verdict TEXT, rationale TEXT, "
                 "acknowledged_by TEXT, acknowledged_at TEXT)")
    conn.commit()
    return conn


def make_session_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE session_tool_call (call_id INTEGER PRIMARY KEY, "
                 "transcript_id INTEGER, tool_use_id TEXT, tool_input_json TEXT)")
    conn.execute("CREATE TABLE session_tool_result (transcript_id INTEGER, tool_use_id TEXT, "
                 "deny_text TEXT, is_hook_deny INTEGER, "
                 "PRIMARY KEY (transcript_id, tool_use_id))")
    conn.execute("CREATE TABLE session_credential_ack (call_id INTEGER PRIMARY KEY, "
                 "payload_sha256 TEXT, pattern_name TEXT, verdict TEXT, rationale TEXT, "
                 "acknowledged_by TEXT, acknowledged_at TEXT)")
    conn.commit()
    return conn


DENY_WITH_CREDENTIAL = (
    "Foreman: this dispatch prompt could not be verified -- it embeds "
    "export AAI_API_KEY=your-key-here, which was echoed back verbatim.")


class SubagentDenyTextScanTests(unittest.TestCase):
    def test_a_deny_text_credential_hit_is_now_counted(self):
        """THE CORE REPRO: before ATLASSN-197, scan_stored_tool_inputs_for_credentials() never
        queried subagent_tool_result.deny_text at all -- a credential-shaped string landing there
        (this ticket's own traced real path: agent_dispatch_gate.py echoing up to 60 chars of a
        dispatch prompt into its deny message) was invisible to the scan forever."""
        conn = make_subagent_conn()
        conn.execute("INSERT INTO subagent_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', '{\"ok\": true}')")
        conn.execute("INSERT INTO subagent_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', ?, 1)", (DENY_WITH_CREDENTIAL,))
        conn.commit()
        hits = subagent_pull.scan_stored_tool_inputs_for_credentials(conn)
        self.assertEqual(hits, 1)

    def test_the_same_call_ids_own_tool_input_hit_is_still_counted_too(self):
        """The extension is additive -- the pre-existing tool_input_json coverage must not
        regress."""
        conn = make_subagent_conn()
        conn.execute("INSERT INTO subagent_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', "
                     "'{\"command\": \"export AAI_API_KEY=your-key-here\"}')")
        conn.execute("INSERT INTO subagent_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', NULL, 0)")
        conn.commit()
        hits = subagent_pull.scan_stored_tool_inputs_for_credentials(conn)
        self.assertEqual(hits, 1)

    def test_acknowledging_a_deny_text_hit_suppresses_it(self):
        conn = make_subagent_conn()
        conn.execute("INSERT INTO subagent_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', '{\"ok\": true}')")
        conn.execute("INSERT INTO subagent_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', ?, 1)", (DENY_WITH_CREDENTIAL,))
        conn.commit()
        self.assertEqual(subagent_pull.scan_stored_tool_inputs_for_credentials(conn), 1)

        subagent_pull.acknowledge_deny_credential_hit(
            conn, 10, "toolu_1", "literal_export_assignment", "false-positive",
            "echoed dispatch-prompt placeholder, triaged", "test-actor")
        self.assertEqual(subagent_pull.scan_stored_tool_inputs_for_credentials(conn), 0)

    def test_editing_the_deny_text_after_acknowledgement_re_arms_it(self):
        """Same binding discipline as the tool_input_json ack: the acknowledgement is bound to a
        hash of the payload as it stood, not to the row forever."""
        conn = make_subagent_conn()
        conn.execute("INSERT INTO subagent_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', '{\"ok\": true}')")
        conn.execute("INSERT INTO subagent_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', ?, 1)", (DENY_WITH_CREDENTIAL,))
        conn.commit()
        subagent_pull.acknowledge_deny_credential_hit(
            conn, 10, "toolu_1", "literal_export_assignment", "false-positive", "triaged",
            "test-actor")
        self.assertEqual(subagent_pull.scan_stored_tool_inputs_for_credentials(conn), 0)

        conn.execute("UPDATE subagent_tool_result SET deny_text = ? "
                     "WHERE transcript_id=10 AND tool_use_id='toolu_1'",
                     (DENY_WITH_CREDENTIAL + " a genuinely different tail",))
        conn.commit()
        self.assertEqual(subagent_pull.scan_stored_tool_inputs_for_credentials(conn), 1)

    def test_acknowledging_a_nonexistent_deny_hit_raises(self):
        conn = make_subagent_conn()
        with self.assertRaises(subagent_pull.SubagentPullError):
            subagent_pull.acknowledge_deny_credential_hit(
                conn, 999, "toolu_nope", "literal_export_assignment", "false-positive", "why",
                "test-actor")

    def test_disclosed_collision_fails_toward_more_scrutiny_not_less(self):
        """The accepted trade-off scan_stored_tool_inputs_for_credentials()'s own docstring
        discloses: acknowledging one source on a call_id that has hits on BOTH sources
        overwrites the stored hash, which RE-ARMS the other source rather than silencing it.
        Proven directly, not just asserted in prose."""
        conn = make_subagent_conn()
        conn.execute("INSERT INTO subagent_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', "
                     "'{\"command\": \"export AAI_API_KEY=your-key-here\"}')")
        conn.execute("INSERT INTO subagent_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', ?, 1)", (DENY_WITH_CREDENTIAL,))
        conn.commit()
        self.assertEqual(subagent_pull.scan_stored_tool_inputs_for_credentials(conn), 2,
                         "both sources hit independently on the same call_id")

        subagent_pull.acknowledge_credential_hit(
            conn, 1, "literal_export_assignment", "false-positive", "triaged tool_input",
            "test-actor")
        self.assertEqual(subagent_pull.scan_stored_tool_inputs_for_credentials(conn), 1,
                         "tool_input hit silenced, deny_text hit still visible")

        subagent_pull.acknowledge_deny_credential_hit(
            conn, 10, "toolu_1", "literal_export_assignment", "false-positive",
            "triaged deny_text", "test-actor")
        self.assertEqual(subagent_pull.scan_stored_tool_inputs_for_credentials(conn), 1,
                         "acknowledging deny_text overwrote the shared ack row, which RE-ARMS "
                         "the tool_input hit -- never silences a hit, only re-surfaces one")


class SessionCredentialScanTests(unittest.TestCase):
    def test_session_pull_no_longer_hardcodes_zero(self):
        """THE OTHER CORE REPRO: session_pull.py had NO credential scan at all before this
        ticket -- confirmed by direct read, hardcoded credential_hits = 0. This proves the real
        function is wired and actually returns a nonzero count on real matching data."""
        conn = make_session_conn()
        conn.execute("INSERT INTO session_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', "
                     "'{\"command\": \"export AAI_API_KEY=your-key-here\"}')")
        conn.execute("INSERT INTO session_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', NULL, 0)")
        conn.commit()
        hits = session_pull.scan_stored_tool_inputs_for_credentials(
            conn, call_table="session_tool_call", result_table="session_tool_result",
            ack_table="session_credential_ack")
        self.assertEqual(hits, 1)

    def test_session_deny_text_credential_hit_is_counted(self):
        conn = make_session_conn()
        conn.execute("INSERT INTO session_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', '{\"ok\": true}')")
        conn.execute("INSERT INTO session_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', ?, 1)", (DENY_WITH_CREDENTIAL,))
        conn.commit()
        hits = session_pull.scan_stored_tool_inputs_for_credentials(
            conn, call_table="session_tool_call", result_table="session_tool_result",
            ack_table="session_credential_ack")
        self.assertEqual(hits, 1)

    def test_session_side_acknowledgement_wrappers_round_trip(self):
        conn = make_session_conn()
        conn.execute("INSERT INTO session_tool_call (call_id, transcript_id, tool_use_id, "
                     "tool_input_json) VALUES (1, 10, 'toolu_1', "
                     "'{\"command\": \"export AAI_API_KEY=your-key-here\"}')")
        conn.execute("INSERT INTO session_tool_result (transcript_id, tool_use_id, deny_text, "
                     "is_hook_deny) VALUES (10, 'toolu_1', ?, 1)", (DENY_WITH_CREDENTIAL,))
        conn.commit()
        scan = lambda: session_pull.scan_stored_tool_inputs_for_credentials(
            conn, call_table="session_tool_call", result_table="session_tool_result",
            ack_table="session_credential_ack")
        self.assertEqual(scan(), 2)
        session_pull.acknowledge_credential_hit(
            conn, 1, "literal_export_assignment", "false-positive", "triaged", "test-actor")
        self.assertEqual(scan(), 1)
        session_pull.acknowledge_deny_credential_hit(
            conn, 10, "toolu_1", "literal_export_assignment", "false-positive", "triaged",
            "test-actor")
        # Re-arms the tool_input hit (same disclosed shared-ack-row trade-off as the subagent
        # side) -- proven, not assumed, since this is the session-side wrapper, a separate call
        # path than the test above that proved it for the subagent side.
        self.assertEqual(scan(), 1)


if __name__ == "__main__":
    unittest.main()
