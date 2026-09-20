"""ATLASSN-132 / C13: every accepted write appends one full-payload line, rejected writes append
nothing, and replaying the trail reconstructs `assertion` and `spent_ref` uid-keyed and
field-complete -- every column except the local rowid.

The replay comparison is done against a REAL rebuilt store, not a dict: C13's claim is about the
tables, and a dict comparison would pass on a half-revoked row the schema's own CHECK forbids.

Alice proposal, 2026-09-16 (write_audit hash-chaining, unfiled): HashChainTests below covers the
new prev_sha256 chain link -- genesis rooting, clean-chain replay, the edit-and-recompute attack
the finding named (now caught as CHAIN_BROKEN even though the edited line's OWN digest was
dishonestly kept self-consistent), and ChainTipUnreadable's fail-closed refusal when a new write
is attempted against an already-corrupted tip.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.registry import architecture_parse, ddl, status, write_audit as wa, write_path as wp
from atlas.registry.tests import write_path_fixtures as fx

COMPONENTS = architecture_parse.parse_components(
    architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT))

EDGE_ONE = "claude-hooks-v2:hooks/:registers:registry_gate_client_wired"
EDGE_TWO = "claude-hooks-v2:hooks/:registers:registry_write_guard_wired"
REACH = fx.reach_for(EDGE_ONE, EDGE_TWO)


class TrailHarness(unittest.TestCase):
    """Builds the four healthy dependencies once per test and drives real writes through them.

    Two real acts from the real anchor session are available, which caps a single harness at two
    accepted writes -- clause (b)'s same-session rule means the suite cannot mint a third, and
    that limit is the design working, not a gap to route around.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="c13-")
        self.root = Path(self.tmp.name)
        self.records_dir = self.root / "dispatch-records"
        self.records_dir.mkdir()
        self.sessions_path = self.root / "sessions.jsonl"
        fx.write_sessions_stream(
            self.sessions_path,
            [fx.session_line(fx.REAL_TRANSCRIPT_SESSION, "2026-01-01T00:00:00Z")])
        fx.seed_json_file(
            self.records_dir, "d-1", record_id="d-1", author_session_id="s-author",
            named_session_id=fx.REAL_TRANSCRIPT_SESSION, role="verifier",
            project="atlas-sonnet", repo_scope=["registry"],
            created_at="2025-12-01T00:00:00Z")
        self.config_path = fx.write_config_and_decision(self.root)
        self.store_path = self.root / "registry.db"
        fx.create_store(self.store_path)
        self.audit_path = self.root / "audit.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, edge_id, tool_use_id, verified_at=None):
        return wp.write_assertion(
            store_path=self.store_path, audit_log_path=self.audit_path,
            dispatch_record_id="d-1", verifier_session_id=fx.REAL_TRANSCRIPT_SESSION,
            edge_id=edge_id, edge_repo="atlas-sonnet", edge_component="registry",
            project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
            assertion_class="structural", evidence_tool_use_id=tool_use_id,
            evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
            components=COMPONENTS, reach=REACH, config_path=self.config_path,
            records_dir=self.records_dir, sessions_stream_path=self.sessions_path,
            verified_at=verified_at)

    def trail(self):
        return [json.loads(line) for line in
                self.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]


class AcceptedWritesAppendOneFullLineTests(TrailHarness):
    def test_n_accepted_writes_produce_n_lines(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        self.write(EDGE_TWO, fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)
        self.assertEqual(len(self.trail()), 2)

    def test_every_line_carries_every_declared_field_and_nothing_else(self):
        """Field-complete in BOTH directions. A missing field breaks replay; an extra one means
        the writer is emitting something the trail's own shape does not account for, which is how
        a second, undeclared source of truth gets in."""
        uid = self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        line = self.trail()[0]
        self.assertEqual(sorted(line), sorted(wa.ASSERT_LINE_FIELDS))
        self.assertEqual(line["op"], wa.OP_ASSERT)
        self.assertEqual(line["assertion_uid"], uid)

    def test_every_not_null_assertion_column_appears_in_the_line(self):
        """Asserted against the LIVE schema rather than against my copy of it, so a column added
        to ddl.py without being added to the trail fails here instead of silently degrading the
        replay a release later. `id` (the rowid) and `state` are the two documented exclusions."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        connection = ddl.connect(self.store_path)
        try:
            info = connection.execute("PRAGMA table_info(assertion)").fetchall()
        finally:
            connection.close()
        not_null = {row[1] for row in info if row[3] == 1} - {"id", "state"}
        self.assertTrue(not_null <= set(wa.ASSERTION_COLUMN_FIELDS),
                        f"NOT NULL columns missing from the trail: "
                        f"{sorted(not_null - set(wa.ASSERTION_COLUMN_FIELDS))}")

    def test_payload_digest_is_recomputable_by_a_reader(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        line = self.trail()[0]
        self.assertEqual(line["payload_sha256"], wa.payload_digest(line))

    def test_a_tampered_line_fails_its_own_digest(self):
        """The control for the probe above: if the digest passed on an edited line too, it would
        be checking nothing."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        line = self.trail()[0]
        line["component"] = "warehouse"
        self.assertNotEqual(line["payload_sha256"], wa.payload_digest(line))


class HashChainTests(TrailHarness):
    """Alice proposal, 2026-09-16 (unfiled): prev_sha256 chaining. Real writes through
    wp.write_assertion/wp.revoke_assertion, same TrailHarness convention as every other class
    here -- nothing about the chain link is tested against a hand-built payload dict alone."""

    def test_first_line_chains_from_genesis(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        line = self.trail()[0]
        self.assertEqual(line["prev_sha256"], wa.GENESIS_PREV_SHA256)

    def test_second_line_chains_onto_the_first_lines_real_digest(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        self.write(EDGE_TWO, fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)
        lines = self.trail()
        self.assertEqual(lines[1]["prev_sha256"], wa.payload_digest(lines[0]))
        self.assertEqual(lines[1]["prev_sha256"], lines[0]["payload_sha256"],
                         "for an untampered first line the stored and recomputed digest "
                         "already agree, so both comparisons should hold")

    def test_clean_two_line_trail_replays_with_no_chain_divergence(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        self.write(EDGE_TWO, fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)
        state = wa.replay(self.audit_path)
        kinds = {d["kind"] for d in state["divergences"]}
        self.assertNotIn(wa.CHAIN_BROKEN, kinds, state["divergences"])

    def test_edit_and_recompute_a_middle_line_breaks_the_chain(self):
        """THE reproduced gap, closed. Before chaining, editing line 0's field and recomputing
        ITS OWN payload_sha256 (the exact same move test_a_tampered_line_fails_its_own_digest's
        CONTROL demonstrates a reader could make) passed DIGEST_MISMATCH cleanly and left no
        other trace. Now line 1's prev_sha256 -- untouched, still pointing at line 0's ORIGINAL
        content -- disagrees with line 0's recomputed (post-edit) digest, and CHAIN_BROKEN
        fires even though the attacker did exactly what would have been undetectable before."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        self.write(EDGE_TWO, fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)
        lines = self.trail()
        lines[0]["component"] = "warehouse"
        lines[0]["payload_sha256"] = wa.payload_digest(lines[0])  # the attacker "covers" line 0
        self.audit_path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

        state = wa.replay(self.audit_path)
        kinds = {d["kind"] for d in state["divergences"]}
        self.assertNotIn(wa.DIGEST_MISMATCH, kinds,
                         "line 0's own digest was dishonestly recomputed to match -- confirming "
                         "the attack this test models really does hide from DIGEST_MISMATCH "
                         "alone, which is the property that makes CHAIN_BROKEN's catch below "
                         "the real point of this test")
        self.assertIn(wa.CHAIN_BROKEN, kinds, state["divergences"])
        broken = [d for d in state["divergences"] if d["kind"] == wa.CHAIN_BROKEN][0]
        self.assertEqual(broken["line"], 2, "the break must be reported at line 1 (the line "
                                            "whose prev_sha256 no longer matches), not at the "
                                            "edited line itself")

    def test_a_trail_not_rooted_at_genesis_is_chain_broken(self):
        """A trail whose first surviving line does not open with GENESIS_PREV_SHA256 -- the
        shape a front-truncated file would have -- is caught at line 1."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        lines = self.trail()
        lines[0]["prev_sha256"] = "f" * 64
        lines[0]["payload_sha256"] = wa.payload_digest(lines[0])
        self.audit_path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        state = wa.replay(self.audit_path)
        broken = [d for d in state["divergences"] if d["kind"] == wa.CHAIN_BROKEN]
        self.assertTrue(broken, state["divergences"])
        self.assertEqual(broken[0]["line"], 1)

    def test_current_chain_tip_is_genesis_for_an_empty_trail(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "never-written.jsonl"
            self.assertEqual(wa.current_chain_tip(empty_path), wa.GENESIS_PREV_SHA256)

    def test_current_chain_tip_matches_the_real_tips_recomputed_digest(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        line = self.trail()[0]
        self.assertEqual(wa.current_chain_tip(self.audit_path), wa.payload_digest(line))

    def test_a_write_against_an_already_corrupted_tip_refuses_audit_append_failed(self):
        """The write-time half of the same fix: current_chain_tip() refuses to chain a new,
        honest line onto a tip that is already inconsistent, which write_path.py's existing
        AuditAppendFailed handling converts into the same clean refusal an ordinary append
        failure already produces -- no new rejection reason, no uncaught exception."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        lines = self.trail()
        lines[0]["component"] = "warehouse"  # payload_sha256 NOT recomputed -- tip is now broken
        self.audit_path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.write(EDGE_TWO, fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)
        self.assertEqual(ctx.exception.reason, "audit-append-failed")
        # And the store must not have advanced past the still-broken trail (C14's own guarantee,
        # now exercised via the NEW failure mode this fix introduces rather than only the old
        # OS-level append failure).
        connection = sqlite3.connect(self.store_path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1, "only the FIRST, legitimate write may have landed a row -- "
                                   "the second write's own rejection must not have added one")

    def test_current_chain_tip_raises_on_a_malformed_last_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            path.write_text('{"not": "json"\n', encoding="utf-8")
            with self.assertRaises(wa.ChainTipUnreadable):
                wa.current_chain_tip(path)

    def test_chain_tip_unreadable_is_an_audit_append_failed_subclass(self):
        """So write_path.py's existing `except write_audit.AuditAppendFailed` clauses catch it
        with no new except clause needed -- the integration test above is the behavioral proof;
        this pins the type relationship directly."""
        self.assertTrue(issubclass(wa.ChainTipUnreadable, wa.AuditAppendFailed))

    def test_revoke_line_also_chains_onto_the_trails_tip(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID, verified_at="2026-09-12T19:00:00Z")
        assert_line = self.trail()[0]
        wp.revoke_assertion(
            store_path=self.store_path, audit_log_path=self.audit_path,
            assertion_uid=json.loads(self.audit_path.read_text().splitlines()[0])["assertion_uid"],
            dispatch_record_id="d-1", revoking_session_id=fx.REAL_TRANSCRIPT_SESSION,
            edge_repo="atlas-sonnet", project_root=fx.REAL_PROJECT_ROOT,
            required_role="verifier", evidence_tool_use_id=fx.REAL_ANCHOR_SECOND_TOOL_USE_ID,
            evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
            components=COMPONENTS, records_dir=self.records_dir,
            sessions_stream_path=self.sessions_path, revoked_at="2026-09-12T20:00:00Z")
        revoke_line = self.trail()[1]
        self.assertEqual(revoke_line["prev_sha256"], wa.payload_digest(assert_line))


class RejectedWritesAppendNothingTests(TrailHarness):
    def test_clause_failure_leaves_no_line(self):
        with self.assertRaises(wp.AssertionRejected):
            wp.write_assertion(
                store_path=self.store_path, audit_log_path=self.audit_path,
                dispatch_record_id="no-such-record",
                verifier_session_id=fx.REAL_TRANSCRIPT_SESSION, edge_id=EDGE_ONE,
                edge_repo="atlas-sonnet", edge_component="registry",
                project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
                assertion_class="structural",
                evidence_tool_use_id=fx.REAL_ANCHOR_TOOL_USE_ID,
                evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
                components=COMPONENTS, reach=REACH, config_path=self.config_path,
                records_dir=self.records_dir, sessions_stream_path=self.sessions_path)
        self.assertFalse(self.audit_path.exists())

    def test_already_spent_leaves_no_second_line(self):
        """The rejection that happens INSIDE the transaction, after BEGIN -- the one a naive
        'append on the way in' implementation would get wrong."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.write(EDGE_TWO, fx.REAL_ANCHOR_TOOL_USE_ID)
        self.assertEqual(ctx.exception.reason, "evidence-already-spent")
        self.assertEqual(len(self.trail()), 1)


class ReplayReconstructsTheStoreTests(TrailHarness):
    def replay_and_compare(self):
        replayed_path = self.root / "replayed.db"
        replayed, state = wa.replay_into_store(self.audit_path, replayed_path)
        original = ddl.connect(self.store_path)
        try:
            divergences = wa.compare_store_to_replay(original, replayed)
        finally:
            original.close()
            replayed.close()
        return divergences, state

    def test_two_asserts_replay_field_for_field(self):
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        self.write(EDGE_TWO, fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)
        divergences, state = self.replay_and_compare()
        self.assertEqual(divergences, [])
        self.assertEqual(state["divergences"], [])
        self.assertEqual(len(state["assertions"]), 2)
        self.assertEqual(len(state["spent_refs"]), 2)

    def test_the_comparison_catches_a_store_the_trail_does_not_explain(self):
        """The control. If compare_store_to_replay returned [] for a store that diverges, the
        probe above would be proving nothing -- so break the store on purpose and require a
        named divergence."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        connection = sqlite3.connect(self.store_path)
        connection.execute("UPDATE assertion SET component = 'warehouse'")
        connection.commit()
        connection.close()
        divergences, _ = self.replay_and_compare()
        self.assertTrue(any(d["kind"] == wa.FIELD_MISMATCH for d in divergences), divergences)

    def test_every_column_except_the_rowid_is_compared(self):
        """C13 says field-complete except the local rowid. Pins that the comparison really does
        read every other column from the live schema rather than a hand-listed subset."""
        self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID)
        connection = ddl.connect(self.store_path)
        try:
            compared = set(wa.assertion_rows_by_uid(connection).popitem()[1])
            live = set(ddl.table_columns(connection, "assertion"))
        finally:
            connection.close()
        self.assertEqual(compared, live - {"id"})


class RevokeLinesReplayTests(TrailHarness):
    """A revoke line replays into the terminal state AND its duplicated assert fields are
    cross-checked, which is the tamper-evidence property the ruled line shape buys."""

    def revoke(self):
        return wp.revoke_assertion(
            store_path=self.store_path, audit_log_path=self.audit_path,
            assertion_uid=self.uid, dispatch_record_id="d-1",
            revoking_session_id=fx.REAL_TRANSCRIPT_SESSION, edge_repo="atlas-sonnet",
            project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
            evidence_tool_use_id=fx.REAL_ANCHOR_SECOND_TOOL_USE_ID,
            evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
            components=COMPONENTS, records_dir=self.records_dir,
            sessions_stream_path=self.sessions_path, revoked_at="2026-09-12T20:00:00Z")

    def setUp(self):
        super().setUp()
        self.uid = self.write(EDGE_ONE, fx.REAL_ANCHOR_TOOL_USE_ID,
                              verified_at="2026-09-12T19:00:00Z")

    def test_revoke_line_carries_the_ruled_shape(self):
        self.revoke()
        line = self.trail()[1]
        self.assertEqual(sorted(line), sorted(wa.REVOKE_LINE_FIELDS))
        self.assertEqual(line["op"], wa.OP_REVOKE)
        self.assertEqual(line["revoking_dispatch_record_id"], "d-1")
        self.assertEqual(line["payload_sha256"], wa.payload_digest(line))

    def test_assert_and_revoke_replay_to_the_terminal_row(self):
        self.revoke()
        replayed_path = self.root / "replayed.db"
        replayed, state = wa.replay_into_store(self.audit_path, replayed_path)
        original = ddl.connect(self.store_path)
        try:
            divergences = wa.compare_store_to_replay(original, replayed)
        finally:
            original.close()
            replayed.close()
        self.assertEqual(divergences, [])
        self.assertEqual(state["assertions"][self.uid]["state"], "revoked")
        self.assertEqual(len(state["spent_refs"]), 2,
                         "both the asserting and the revoking reference must replay as spent")

    def test_an_edited_assert_line_is_caught_by_the_revoke_line_cross_check(self):
        """The whole point of duplicating the assert fields onto the revoke line: an attacker who
        rewrites history has to rewrite it consistently, or replay reports a divergence."""
        self.revoke()
        lines = self.trail()
        lines[0]["component"] = "warehouse"
        lines[0]["payload_sha256"] = wa.payload_digest(lines[0])
        self.audit_path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        state = wa.replay(self.audit_path)
        kinds = {d["kind"] for d in state["divergences"]}
        self.assertIn(wa.REVOKE_FIELD_MISMATCH, kinds, state["divergences"])

    def test_a_revoke_for_an_unknown_assertion_is_a_divergence(self):
        self.revoke()
        lines = self.trail()
        lines[1]["assertion_uid"] = "a-NEVERWRITTEN"
        lines[1]["payload_sha256"] = wa.payload_digest(lines[1])
        self.audit_path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        state = wa.replay(self.audit_path)
        self.assertIn(wa.REVOKE_OF_UNKNOWN_ASSERTION,
                      {d["kind"] for d in state["divergences"]})

    def test_the_replayed_store_reads_the_same_status_as_the_original(self):
        """End of the chain: a store rebuilt from the trail must give a gate the same answer as
        the store it replaced, or recoverability is nominal."""
        self.revoke()
        replayed_path = self.root / "replayed.db"
        replayed, _ = wa.replay_into_store(self.audit_path, replayed_path)
        original = ddl.connect(self.store_path)
        try:
            now = status.parse_timestamp("2026-09-12T20:30:00Z")
            self.assertEqual(status.read_edge(original, EDGE_ONE, now).status, status.REVOKED)
            self.assertEqual(status.read_edge(replayed, EDGE_ONE, now).status, status.REVOKED)
        finally:
            original.close()
            replayed.close()


class MalformedTrailTests(unittest.TestCase):
    """A corrupt line is evidence. Dropping it silently is the one thing this module may not do."""

    def test_unparseable_line_is_reported_not_skipped(self):
        with tempfile.TemporaryDirectory(prefix="c13-malformed-") as tmp:
            path = Path(tmp) / "audit.jsonl"
            path.write_text("{not json\n", encoding="utf-8")
            state = wa.replay(path)
            self.assertEqual([d["kind"] for d in state["divergences"]],
                             [wa.MALFORMED_AUDIT_LINE])

    def test_missing_trail_replays_empty_without_raising(self):
        with tempfile.TemporaryDirectory(prefix="c13-absent-") as tmp:
            state = wa.replay(Path(tmp) / "never-written.jsonl")
            self.assertEqual(state["assertions"], {})
            self.assertEqual(state["divergences"], [])


if __name__ == "__main__":
    unittest.main()
