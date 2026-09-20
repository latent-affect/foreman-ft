"""ATLASSN-133 / C12: revocation is a D1-governed write, not an administrative escape hatch.

Four properties, and the battery is arranged around them rather than around the function's code
paths: every clause is enforced on the revoking act too; the revoking reference is spent exactly
as an assert's is (G4); the revoked state is terminal for the assertion but NOT for the edge; and
a revoked-only edge is a reported forward gap until a new valid assertion lands.

Every refusal probe below has a companion that must be ACCEPTED from the same harness, so a probe
cannot pass because the fixture was broken in some unrelated way.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas.registry import (architecture_parse, ddl, reconcile, status, write_audit as wa,
                            write_path as wp)
from atlas.registry.tests import write_path_fixtures as fx

ARCH_TEXT = architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT)
COMPONENTS = architecture_parse.parse_components(ARCH_TEXT)
REACH = architecture_parse.parse_reach(ARCH_TEXT)

ASSERTED_AT = "2026-09-12T19:00:00Z"
REVOKED_AT = "2026-09-12T20:00:00Z"
REVERIFIED_AT = "2026-09-12T21:00:00Z"
READ_AT = "2026-09-12T22:00:00Z"


class RevocationHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="c12-")
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
        # A second, WAREHOUSE-scoped record, for the clause-(a) scope probe. Well-formed and
        # valid -- it simply does not cover the component the revoked assertion names.
        fx.seed_json_file(
            self.records_dir, "d-warehouse", record_id="d-warehouse",
            author_session_id="s-author", named_session_id=fx.REAL_TRANSCRIPT_SESSION,
            role="verifier", project="atlas-sonnet", repo_scope=["warehouse"],
            created_at="2025-12-01T00:00:00Z")
        self.config_path = fx.write_config_and_decision(self.root)
        self.store_path = self.root / "registry.db"
        fx.create_store(self.store_path)
        self.audit_path = self.root / "audit.jsonl"
        self.uid = self.assert_write(fx.DECLARED_EDGE_GATE_CLIENT,
                                     fx.REAL_ANCHOR_TOOL_USE_ID, ASSERTED_AT)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_write(self, edge_id, tool_use_id, verified_at):
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

    def revoke(self, assertion_uid=None, dispatch_record_id="d-1", session=None,
               tool_use_id=None, evidence_class="observed-probe", transcript_path=None,
               required_role="verifier", records_dir=None, sessions_stream_path=None,
               verdict_ledger_path=wp.DEFAULT_VERDICT_LEDGER, revoked_at=REVOKED_AT):
        return wp.revoke_assertion(
            store_path=self.store_path, audit_log_path=self.audit_path,
            assertion_uid=assertion_uid or self.uid,
            dispatch_record_id=dispatch_record_id,
            revoking_session_id=session or fx.REAL_TRANSCRIPT_SESSION,
            edge_repo="atlas-sonnet", project_root=fx.REAL_PROJECT_ROOT,
            required_role=required_role,
            evidence_tool_use_id=tool_use_id or fx.REAL_ANCHOR_SECOND_TOOL_USE_ID,
            evidence_class=evidence_class,
            transcript_path=transcript_path or fx.REAL_TRANSCRIPT_PATH,
            components=COMPONENTS,
            records_dir=records_dir or self.records_dir,
            sessions_stream_path=sessions_stream_path or self.sessions_path,
            verdict_ledger_path=verdict_ledger_path, revoked_at=revoked_at)

    def row(self):
        connection = ddl.connect(self.store_path)
        try:
            return connection.execute(
                "SELECT state, revoked_at, revoked_by_session_id, "
                "revocation_evidence_tool_use_id FROM assertion WHERE assertion_uid = ?",
                (self.uid,)).fetchone()
        finally:
            connection.close()


class ValidRevocationTests(RevocationHarness):
    def test_accepted_and_terminal_state_set(self):
        self.assertEqual(self.revoke(), self.uid)
        state, revoked_at, by_session, evidence = self.row()
        self.assertEqual(state, "revoked")
        self.assertEqual(revoked_at, REVOKED_AT)
        self.assertEqual(by_session, fx.REAL_TRANSCRIPT_SESSION)
        self.assertEqual(evidence, fx.REAL_ANCHOR_SECOND_TOOL_USE_ID)

    def test_the_revoking_reference_is_spent_in_the_same_transaction(self):
        self.revoke()
        connection = ddl.connect(self.store_path)
        try:
            spent = connection.execute(
                "SELECT assertion_uid FROM spent_ref WHERE tool_use_id = ?",
                (fx.REAL_ANCHOR_SECOND_TOOL_USE_ID,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(spent[0], self.uid)

    def test_one_line_appended_per_accepted_revocation(self):
        self.revoke()
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)


class EachClauseBrokenIndividuallyTests(RevocationHarness):
    """C12's "revocation with each clause individually broken rejected". One probe per clause,
    with everything else in the harness left healthy, so the reason names the clause that failed
    rather than whichever one happened to be checked first."""

    def test_clause_a_no_dispatch_record(self):
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(dispatch_record_id="d-nonexistent")
        self.assertEqual(ctx.exception.reason, "no-dispatch-record")
        self.assertEqual(self.row()[0], "live", "a refused revocation must change nothing")

    def test_clause_a_record_out_of_scope_for_the_component(self):
        """The subtler half of clause (a): a real, valid, in-date record that does not cover the
        REVOKED ASSERTION'S component. The component is read from the store, never from the
        caller, so this cannot be talked around by naming a different one."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(dispatch_record_id="d-warehouse")
        self.assertEqual(ctx.exception.reason, "scope-mismatch:component")
        self.assertEqual(self.row()[0], "live")

    def test_clause_b_wrong_session(self):
        """ATLASSN-165: transcript_path here is still the real anchor's own transcript, which
        belongs to fx.REAL_TRANSCRIPT_SESSION, not "some-other-session" -- so the NEW
        transcript/session binding check now catches this before clause (b)'s own ledger check
        is ever reached. TranscriptSessionBindingTests below isolates clause (b)'s OWN check
        using two genuinely different REAL sessions instead of one real session plus a
        fabricated string."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(session="some-other-session")
        self.assertEqual(ctx.exception.reason, "evidence-transcript-session-mismatch")
        self.assertEqual(self.row()[0], "live")

    def test_clause_b_evidence_class(self):
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(evidence_class="self-attested")
        self.assertEqual(ctx.exception.reason, "evidence-class-invalid")

    def test_clause_c_component_mismatch(self):
        """A real act that resolves to `warehouse` cannot revoke a `registry` assertion. Uses
        fx.REAL_DENIED_TRANSCRIPT_PATH -- REAL_DENIED_SESSION's OWN real transcript -- rather
        than the anchor's, both to satisfy ATLASSN-165's new binding check (session and
        transcript must now agree) and because the anchor transcript never actually contains
        REAL_DENIED_TOOL_USE_ID: the old assertIn's second branch (evidence-target-unresolved)
        was silently doing the real work here, not component-mismatch. With the correct
        transcript this is now a single, exact, discriminating assertion."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(tool_use_id=fx.REAL_DENIED_TOOL_USE_ID,
                        session=fx.REAL_DENIED_SESSION,
                        transcript_path=fx.REAL_DENIED_TRANSCRIPT_PATH)
        self.assertEqual(ctx.exception.reason, "component-mismatch")
        self.assertEqual(self.row()[0], "live")

    def test_clause_d_dependency_unreachable_is_retryable(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(wp.AssertionRejected) as ctx:
                self.revoke(records_dir=Path(other) / "no-such-dir")
            self.assertEqual(ctx.exception.reason, "dispatch-record-store-unreachable")
            self.assertTrue(ctx.exception.retryable)
        self.assertEqual(self.row()[0], "live")

    def test_every_refused_clause_left_the_trail_at_one_line(self):
        """The cross-cutting assertion for this class: not one of the refusals above may append.
        Run last as its own probe rather than repeated in each, so the count is unambiguous."""
        for attempt in (lambda: self.revoke(dispatch_record_id="d-nonexistent"),
                        lambda: self.revoke(dispatch_record_id="d-warehouse"),
                        lambda: self.revoke(session="some-other-session"),
                        lambda: self.revoke(evidence_class="self-attested")):
            with self.assertRaises(wp.AssertionRejected):
                attempt()
        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_no_such_assertion(self):
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(assertion_uid="a-NEVERWRITTEN")
        self.assertEqual(ctx.exception.reason, "no-such-assertion")


class TranscriptSessionBindingTests(RevocationHarness):
    """ATLASSN-165, isolated from clause (b)'s own wrong-session check
    (test_clause_b_wrong_session above): a genuine transcript belonging to one REAL session,
    claimed under a DIFFERENT real session's id. Proves the hole closed is "two real sessions
    crossed", not merely "one real session plus a nonsense string" -- the shape Dana's finding
    actually named (a caller supplying a real transcript for the wrong claimed session)."""

    def test_real_transcript_from_a_different_real_session_rejected(self):
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(session=fx.REAL_DENIED_SESSION,
                        transcript_path=fx.REAL_TRANSCRIPT_PATH)
        self.assertEqual(ctx.exception.reason, "evidence-transcript-session-mismatch")
        self.assertEqual(self.row()[0], "live")


class Atlassn167FrozenArchitectureBindingTests(RevocationHarness):
    """ATLASSN-167: revoke_assertion() must not let a caller-fabricated `components` map widen
    which stored assertions a revoking act may touch. Bypasses RevocationHarness.revoke() (which
    always passes the REAL parsed COMPONENTS) and calls wp.revoke_assertion() directly, since
    this probe's whole point is a components map that LIES about what component the revoking
    act's real target belongs to.

    The revoking act is real: clean in the verdict ledger (no deny, no error, a completed
    PostToolUse act -- found by the same corpus-scan methodology write_path_fixtures.py's other
    REAL_* constants use) and its real target, atlas/warehouse/GOALS.json, genuinely resolves to
    `warehouse` under the frozen ARCHITECTURE.md -- not `registry`, the component this harness's
    own assertion (self.uid, from RevocationHarness.setUp) actually stores."""

    REVOKING_TOOL_USE_ID = "toolu_019FifYaaFvYuN6Bt9QzuCf1"
    REVOKING_SESSION = "a2ab3716-51ff-41d0-9ed2-ca27255f502c"
    REVOKING_TRANSCRIPT = (
        f"/Users/m5/.claude/projects/-Users-m5-dev-atlas-sonnet/{REVOKING_SESSION}.jsonl")

    def _revoke_with_components(self, components):
        return wp.revoke_assertion(
            store_path=self.store_path, audit_log_path=self.audit_path,
            assertion_uid=self.uid, dispatch_record_id="d-1",
            revoking_session_id=self.REVOKING_SESSION, edge_repo="atlas-sonnet",
            project_root=fx.REAL_PROJECT_ROOT, required_role="verifier",
            evidence_tool_use_id=self.REVOKING_TOOL_USE_ID,
            evidence_class="observed-probe", transcript_path=self.REVOKING_TRANSCRIPT,
            components=components,
            records_dir=self.records_dir, sessions_stream_path=self.sessions_path,
            revoked_at=REVOKED_AT)

    def test_fabricated_components_map_no_longer_lets_the_wrong_component_revoke(self):
        # Lies that atlas/warehouse/GOALS.json is `registry` -- makes clause (c) pass by
        # matching the stored assertion's component, exactly the "widen which stored
        # assertions it may revoke" attack ATLASSN-167 closes.
        fabricated_components = {"registry": ["atlas/warehouse/GOALS.json"]}
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self._revoke_with_components(fabricated_components)
        self.assertEqual(ctx.exception.reason, "component-not-declared")
        self.assertEqual(self.row()[0], "live", "a refused revocation must change nothing")

    def test_an_honest_components_map_is_rejected_earlier_by_clause_c_not_by_this_check(self):
        """Companion control: an HONEST components map (correctly maps the warehouse file to
        `warehouse`, no lie) is rejected by clause (c) itself (component-mismatch, since
        warehouse != the stored registry), never reaching the new ATLASSN-167 check at all.
        Confirms the fabrication above is what defeats clause (c) specifically -- not a fixture
        mistake that would have rejected either way for an unrelated reason."""
        honest_components = {"warehouse": ["atlas/warehouse/**"],
                              "registry": ["atlas/registry/**"]}
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self._revoke_with_components(honest_components)
        self.assertEqual(ctx.exception.reason, "component-mismatch")
        self.assertEqual(self.row()[0], "live")


class SingleUseTests(RevocationHarness):
    """G4: REVOCATION REFERENCES ARE NOT EXEMPT FROM SINGLE-USE."""

    def test_replayed_revocation_evidence_refused_for_a_later_assert(self):
        self.revoke()
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.assert_write(fx.DECLARED_EDGE_WRITE_GUARD,
                              fx.REAL_ANCHOR_SECOND_TOOL_USE_ID, REVERIFIED_AT)
        self.assertEqual(ctx.exception.reason, "evidence-already-spent")

    def test_replayed_revocation_evidence_refused_for_a_later_revocation(self):
        """The same reference cannot back a second revocation either -- one observed act, one
        revocation, which is the sentence C12 spells out."""
        self.revoke()
        second = self.assert_write(fx.DECLARED_EDGE_WRITE_GUARD,
                                   fx.REAL_ANCHOR_THIRD_TOOL_USE_ID, REVERIFIED_AT)
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(assertion_uid=second)
        self.assertEqual(ctx.exception.reason, "evidence-already-spent")

    def test_an_asserts_reference_cannot_be_reused_to_revoke_it(self):
        """The mirror case: the act that justified the assertion cannot also justify its
        revocation. Without this, every assertion would arrive carrying its own revoker."""
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(tool_use_id=fx.REAL_ANCHOR_TOOL_USE_ID)
        self.assertEqual(ctx.exception.reason, "evidence-already-spent")

    def test_a_fresh_unspent_reference_is_accepted_so_the_probes_discriminate(self):
        self.assertEqual(self.revoke(tool_use_id=fx.REAL_ANCHOR_FOURTH_TOOL_USE_ID), self.uid)

    def test_re_revoking_refuses_and_burns_nothing(self):
        self.revoke()
        with self.assertRaises(wp.AssertionRejected) as ctx:
            self.revoke(tool_use_id=fx.REAL_ANCHOR_THIRD_TOOL_USE_ID)
        self.assertEqual(ctx.exception.reason, "assertion-already-revoked")
        connection = ddl.connect(self.store_path)
        try:
            spent = connection.execute(
                "SELECT COUNT(*) FROM spent_ref WHERE tool_use_id = ?",
                (fx.REAL_ANCHOR_THIRD_TOOL_USE_ID,)).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(spent, 0, "a refused re-revocation must not burn its reference")


class TerminalForTheAssertionNotTheEdgeTests(RevocationHarness):
    def read(self, edge_id, at=READ_AT):
        connection = ddl.connect(self.store_path)
        try:
            return status.read_edge(connection, edge_id, status.parse_timestamp(at))
        finally:
            connection.close()

    def test_post_revocation_read_denies(self):
        self.revoke()
        result = self.read(fx.DECLARED_EDGE_GATE_CLIENT)
        self.assertEqual(result.status, status.REVOKED)
        self.assertEqual(result.decision, status.DENY)

    def test_the_read_allowed_before_the_revocation(self):
        """The control: without it, a DENY above could be an expiry, a malformed row, or a
        never-registered edge rather than the revocation."""
        result = self.read(fx.DECLARED_EDGE_GATE_CLIENT, at="2026-09-12T19:30:00Z")
        self.assertEqual(result.status, status.ACTIVE)
        self.assertEqual(result.decision, status.ALLOW)

    def test_a_new_valid_write_resumes_reads_and_leaves_the_revoked_row_as_history(self):
        self.revoke()
        new_uid = self.assert_write(fx.DECLARED_EDGE_GATE_CLIENT,
                                    fx.REAL_ANCHOR_THIRD_TOOL_USE_ID, REVERIFIED_AT)
        self.assertNotEqual(new_uid, self.uid)
        result = self.read(fx.DECLARED_EDGE_GATE_CLIENT)
        self.assertEqual(result.status, status.ACTIVE)
        self.assertEqual(result.assertion_uid, new_uid)

        connection = ddl.connect(self.store_path)
        try:
            rows = connection.execute(
                "SELECT assertion_uid, state FROM assertion WHERE edge_id = ? "
                "ORDER BY verified_at", (fx.DECLARED_EDGE_GATE_CLIENT,)).fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [(self.uid, "revoked"), (new_uid, "live")],
                         "the revoked row must survive as history, not be rewritten")

    def test_a_later_write_cannot_mutate_the_revoked_row(self):
        """No resurrection, asserted BEHAVIOURALLY rather than by scanning the source for an
        UPDATE string -- the scan version of this probe passed on the revoking UPDATE's own
        `AND state = 'live'` guard, which is the substring-assertion trap: it matched text that
        proves the opposite of what it claimed to check.

        What is actually asserted: after a full re-verification cycle, the revoked row's state and
        its whole revocation triple are byte-identical to what the revocation wrote. A path that
        cleared or rewrote them would have to change one of these four values."""
        self.revoke()
        before = self.row()
        self.assert_write(fx.DECLARED_EDGE_GATE_CLIENT,
                          fx.REAL_ANCHOR_THIRD_TOOL_USE_ID, REVERIFIED_AT)
        with self.assertRaises(wp.AssertionRejected):
            self.revoke(tool_use_id=fx.REAL_ANCHOR_FOURTH_TOOL_USE_ID)
        self.assertEqual(self.row(), before)
        self.assertEqual(before[0], "revoked")


class ReconcilerReportsTheForwardGapTests(RevocationHarness):
    """C12's last clause: a revoked-only edge cannot sit silent between revocation and
    re-verification. Driven through the REAL frozen reach block, not a seeded one."""

    def asserted_now(self, at=READ_AT):
        connection = ddl.connect(self.store_path)
        try:
            return status.live_asserted_edge_ids(connection, status.parse_timestamp(at))
        finally:
            connection.close()

    def gaps(self, at=READ_AT):
        return reconcile.reconcile_declared_vs_asserted(
            REACH, architecture_parse.edge_canonical_id, self.asserted_now(at))["forward_gaps"]

    def test_a_live_assertion_is_not_a_gap(self):
        self.assertNotIn(fx.DECLARED_EDGE_GATE_CLIENT, self.gaps(at="2026-09-12T19:30:00Z"))

    def test_a_revoked_only_edge_is_reported_as_a_forward_gap(self):
        self.revoke()
        self.assertIn(fx.DECLARED_EDGE_GATE_CLIENT, self.gaps())

    def test_exactly_one_finding_per_missing_edge(self):
        """C9's granularity rule holds through revocation too: N missing edges give N entries,
        never one rolled-up summary."""
        self.revoke()
        gaps = self.gaps()
        self.assertEqual(len(gaps), len(set(gaps)))
        self.assertEqual(gaps.count(fx.DECLARED_EDGE_GATE_CLIENT), 1)

    def test_a_new_valid_write_clears_the_gap(self):
        self.revoke()
        self.assertIn(fx.DECLARED_EDGE_GATE_CLIENT, self.gaps())
        self.assert_write(fx.DECLARED_EDGE_GATE_CLIENT,
                          fx.REAL_ANCHOR_THIRD_TOOL_USE_ID, REVERIFIED_AT)
        self.assertNotIn(fx.DECLARED_EDGE_GATE_CLIENT, self.gaps())

    def test_an_expired_assertion_is_also_a_gap(self):
        """Stated as its own probe because it is a decision, not an accident: the asserted set is
        `active`, so a lapsed lease reads as unverified rather than as covered. A reconciler that
        counted expired edges as asserted would go quiet on exactly the gap it exists to find."""
        far_future = status.parse_timestamp("2030-01-01T00:00:00Z")
        connection = ddl.connect(self.store_path)
        try:
            live = status.live_asserted_edge_ids(connection, far_future)
        finally:
            connection.close()
        self.assertNotIn(fx.DECLARED_EDGE_GATE_CLIENT, live)


class RevocationAppearsInTheTrailTests(RevocationHarness):
    def test_the_revoke_line_replays_into_the_same_terminal_store(self):
        """The 132/133 seam, asserted from this side too: a store rebuilt from the trail must
        agree with the live store after a revocation, or the recoverability claim only covers
        asserts."""
        self.revoke()
        replayed, _ = wa.replay_into_store(self.audit_path, self.root / "replayed.db")
        original = ddl.connect(self.store_path)
        try:
            self.assertEqual(wa.compare_store_to_replay(original, replayed), [])
        finally:
            original.close()
            replayed.close()


if __name__ == "__main__":
    unittest.main()
