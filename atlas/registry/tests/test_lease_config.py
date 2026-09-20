"""ATLASSN-137 / GOALS.json C11 -- lease derivation, both trail postures, trigger 1.
From the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_lease_config -v

Criteria G1-G5 frozen on the ticket (criteria_hash sha256:efae136a). C11's own verification
names this module by name.

F5 PERMANENCE (ticket clause, restored by backlog verification): these probes are a permanent
suite fixture. F5 is the failure where a clause is satisfied by string presence rather than
resolution -- config validity read from the config's own fields, a lease taken from a payload's
claim. A change that makes any probe here pass by relaxing it is a regression, not a
simplification. Do not delete or loosen them to make a refactor green.
"""

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from atlas.registry import config, consumption, lease, status

# Not AI-3's proposed 1800/3600/86400, and not values a hardcoded default would pick. That is
# what makes the derivation probes discriminating.
STRUCTURAL_S = 271
IN_FLIGHT_S = 577
WINDOW_S = 1597
AI3_PROPOSED = (1800, 3600, 86400)


def write_record(directory, name="PDR-AI3.md"):
    record = Path(directory) / name
    record.write_text(
        f"Structural lease: {STRUCTURAL_S} seconds.\n"
        f"In-flight lease: {IN_FLIGHT_S} seconds.\n"
        f"Go recency window: {WINDOW_S} seconds.\n", encoding="utf-8")
    return record


def write_config(directory, record, name="config.json", **overrides):
    payload = {
        "lease_defaults": {"structural_s": STRUCTURAL_S, "in_flight_s": IN_FLIGHT_S},
        "go_recency_window_s": WINDOW_S,
        "decision_record_path": str(record),
        "decision_record_sha256": hashlib.sha256(record.read_bytes()).hexdigest(),
    }
    payload.update(overrides)
    path = Path(directory) / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def result(edge_id, assertion_uid, computed_status, decision):
    return status.StatusResult(edge_id, computed_status, decision, None, assertion_uid)


class LeaseTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.record = write_record(self.root)
        self.valid = write_config(self.root, self.record)


class TestLeaseDerivation(LeaseTestCase):
    """G1 -- derived from class plus verified config, never supplied, never defaulted."""

    def test_each_class_derives_its_own_configured_lease(self):
        self.assertEqual(
            lease.derive_lease_for_write(lease.CLASS_STRUCTURAL, config_path=self.valid),
            STRUCTURAL_S)
        self.assertEqual(
            lease.derive_lease_for_write(lease.CLASS_IN_FLIGHT, config_path=self.valid),
            IN_FLIGHT_S)

    def test_the_two_classes_do_not_derive_the_same_lease(self):
        # Control: an implementation returning one value for both would pass each assertion
        # above if the fixture happened to use one number for both classes.
        self.assertNotEqual(STRUCTURAL_S, IN_FLIGHT_S)
        self.assertNotEqual(
            lease.derive_lease_for_write(lease.CLASS_STRUCTURAL, config_path=self.valid),
            lease.derive_lease_for_write(lease.CLASS_IN_FLIGHT, config_path=self.valid))

    def test_lease_fields_are_ready_to_merge_into_a_row(self):
        fields = lease.lease_fields_for_write(lease.CLASS_STRUCTURAL, config_path=self.valid)
        self.assertEqual(fields, {"class": "structural", "lease_s": STRUCTURAL_S})

    def test_an_unknown_class_refuses(self):
        with self.assertRaises(config.ConfigUnsigned):
            lease.derive_lease_for_write("provisional", config_path=self.valid)

    def test_no_ai3_proposed_value_is_ever_derived(self):
        for assertion_class in lease.ASSERTION_CLASSES:
            self.assertNotIn(
                lease.derive_lease_for_write(assertion_class, config_path=self.valid),
                AI3_PROPOSED)


class TestPayloadLeaseRejection(LeaseTestCase):
    """G1 -- rejected on presence, before config is consulted."""

    def test_a_payload_lease_is_rejected(self):
        with self.assertRaises(config.PayloadLeaseRejected):
            lease.derive_lease_for_write(
                lease.CLASS_STRUCTURAL, payload={"lease_s": 42}, config_path=self.valid)

    def test_rejected_even_when_it_matches_what_config_would_produce(self):
        # Provenance is the property being defended, not correctness. A payload that happens to
        # be right is still a payload deciding the lease.
        with self.assertRaises(config.PayloadLeaseRejected):
            lease.derive_lease_for_write(
                lease.CLASS_STRUCTURAL, payload={"lease_s": STRUCTURAL_S},
                config_path=self.valid)

    def test_the_payload_check_runs_before_the_config_check(self):
        # Both are wrong here. The caller should be told about the one they control, not have it
        # masked by a dependency failure they cannot fix.
        with self.assertRaises(config.PayloadLeaseRejected):
            lease.derive_lease_for_write(
                lease.CLASS_STRUCTURAL, payload={"lease_s": 42},
                config_path=self.root / "nonexistent.json")

    def test_a_payload_without_a_lease_derives_normally(self):
        self.assertEqual(
            lease.derive_lease_for_write(
                lease.CLASS_STRUCTURAL, payload={"component": "registry"},
                config_path=self.valid),
            STRUCTURAL_S)


class TestRefusalWhileConfigIsInvalid(LeaseTestCase):
    """G1, G5 -- no value escapes an invalid config, and self-attestation rescues nothing."""

    def invalid_configs(self):
        silent = self.root / "silent.md"
        silent.write_text("the operator approves the leases\n", encoding="utf-8")
        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        tampered_record = write_record(self.root, "tampered.md")
        tampered_config = write_config(self.root, tampered_record, name="tampered.json")
        tampered_record.write_text("# changed after signing\n", encoding="utf-8")
        return {
            "absent": self.root / "nope.json",
            "unparseable": broken,
            "record-missing": write_config(self.root, self.record, name="gone.json",
                                           decision_record_path=str(self.root / "gone.md")),
            "hash-mismatch": tampered_config,
            "value-mismatch": write_config(
                self.root, self.record, name="silent-config.json",
                decision_record_path=str(silent),
                decision_record_sha256=hashlib.sha256(silent.read_bytes()).hexdigest()),
        }

    def test_no_lease_is_derived_under_any_invalid_config(self):
        for name, path in self.invalid_configs().items():
            for assertion_class in lease.ASSERTION_CLASSES:
                with self.subTest(state=name, assertion_class=assertion_class):
                    with self.assertRaises(config.ConfigUnsigned):
                        lease.derive_lease_for_write(assertion_class, config_path=path)

    def test_the_value_mismatch_case_carries_its_own_named_reason(self):
        path = self.invalid_configs()["value-mismatch"]
        with self.assertRaises(config.ConfigRecordValueMismatch) as caught:
            lease.derive_lease_for_write(lease.CLASS_STRUCTURAL, config_path=path)
        self.assertEqual(caught.exception.reason, "config-record-value-mismatch")

    def test_self_attestation_fields_rescue_nothing(self):
        # F5's shape: validity read from the artifact's own claims. Adding signed_by/signed_at
        # to a config whose record is missing must change absolutely nothing.
        path = write_config(self.root, self.record, name="attested.json",
                            decision_record_path=str(self.root / "gone.md"),
                            signed_by="the operator", signed_at="2026-09-12T00:00:00Z")
        with self.assertRaises(config.ConfigUnsigned):
            lease.derive_lease_for_write(lease.CLASS_STRUCTURAL, config_path=path)


class TestConsumptionTrailIsFailOpen(LeaseTestCase):
    """G2 -- an analytics channel. Never alters, blocks or delays a read."""

    def results(self):
        return [
            result("e:one", "a-1", status.ACTIVE, status.ALLOW),
            result("e:two", None, status.NEVER_REGISTERED, status.DENY),
        ]

    def test_one_line_per_consulted_edge_with_every_declared_field(self):
        trail = self.root / "consumption.jsonl"
        written = consumption.record_consultation(self.results(), "gate-client", path=trail)
        self.assertEqual(written, 2)

        lines = consumption.read_trail(trail)
        self.assertEqual(len(lines), 2)
        for line in lines:
            for field in consumption.LINE_FIELDS:
                self.assertIn(field, line, f"{field} missing from a consumption line")

    def test_an_unresolved_edge_records_a_null_assertion_uid_rather_than_omitting_it(self):
        trail = self.root / "consumption.jsonl"
        consumption.record_consultation(self.results(), "gate-client", path=trail)
        unresolved = [l for l in consumption.read_trail(trail) if l["edge_id"] == "e:two"][0]
        self.assertIn("assertion_uid", unresolved)
        self.assertIsNone(unresolved["assertion_uid"])

    def test_an_unwritable_trail_never_raises(self):
        blocked = self.root / "blocked"
        blocked.mkdir()
        trail = blocked / "consumption.jsonl"
        os.chmod(blocked, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, blocked, 0o755)

        written = consumption.record_consultation(self.results(), "gate-client", path=trail)
        self.assertEqual(written, 0)
        self.assertFalse(trail.exists())

    def test_a_failed_append_does_not_alter_the_read_result(self):
        # The property that matters: the caller's results are untouched by the trail's failure.
        blocked = self.root / "blocked2"
        blocked.mkdir()
        os.chmod(blocked, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, blocked, 0o755)

        results = self.results()
        before = list(results)
        consumption.record_consultation(results, "gate-client",
                                        path=blocked / "consumption.jsonl")
        self.assertEqual(results, before)
        self.assertEqual(results[0].decision, status.ALLOW)

    def test_the_fail_open_posture_is_the_opposite_of_the_write_audit(self):
        # Stated as a test so a future refactor into one shared append helper has to confront
        # it. The write-audit (ATLASSN-132) must abort its transaction on append failure; this
        # must not. Same codebase, opposite postures, on purpose.
        self.assertIn("FAIL-OPEN", consumption.__doc__)
        self.assertIn("FAIL-CLOSED", consumption.__doc__)


class TestTriggerOneQueryDefinition(LeaseTestCase):
    """G3 -- the definition, validated against a hand-computed rate."""

    def seed(self, name, rows):
        trail = self.root / name
        with open(trail, "w", encoding="utf-8") as handle:
            for uid, computed_status in rows:
                handle.write(json.dumps({
                    "ts": "2026-09-12T15:00:00Z", "edge_id": f"e:{uid or 'none'}",
                    "assertion_uid": uid, "computed_status": computed_status,
                    "consumer": "gate-client", "decision": "deny",
                }) + "\n")
        return trail

    def test_rate_matches_a_rate_computed_by_hand(self):
        # Five resolving lines, two of them expired. By hand: 2/5 = 0.4.
        # Two non-resolving lines are present and must be excluded from BOTH sides -- if they
        # were counted in the denominator the answer would be 2/7 instead.
        trail = self.seed("one.jsonl", [
            ("a-1", "expired"), ("a-2", "active"), ("a-3", "expired"),
            ("a-4", "active"), ("a-5", "revoked"),
            (None, "never-registered"), (None, "never-registered"),
        ])
        self.assertEqual(consumption.rate_from_trail(trail), 0.4)
        self.assertNotEqual(consumption.rate_from_trail(trail), 2 / 7)

    def test_a_second_fixture_with_a_different_hand_computed_rate(self):
        # Four resolving, three expired. By hand: 3/4 = 0.75. Proves the query tracks the data
        # rather than returning a constant that happened to match the first fixture.
        trail = self.seed("two.jsonl", [
            ("a-1", "expired"), ("a-2", "expired"), ("a-3", "expired"), ("a-4", "active"),
        ])
        self.assertEqual(consumption.rate_from_trail(trail), 0.75)

    def test_no_resolving_lines_yields_none_not_zero(self):
        # "No evidence" and "evidence of no problem" are different answers. A too-short-lease
        # trigger that reported 0.0 for an empty window would read as healthy.
        trail = self.seed("three.jsonl", [(None, "never-registered")])
        self.assertIsNone(consumption.rate_from_trail(trail))

    def test_an_empty_trail_yields_none(self):
        trail = self.root / "empty.jsonl"
        trail.write_text("", encoding="utf-8")
        self.assertIsNone(consumption.rate_from_trail(trail))

    def test_a_malformed_line_is_a_missing_sample_not_a_failure(self):
        trail = self.seed("four.jsonl", [("a-1", "expired"), ("a-2", "active")])
        with open(trail, "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        self.assertEqual(consumption.rate_from_trail(trail), 0.5)

    def test_the_definition_is_shipped_in_words(self):
        # The analytics plane reimplements this without reading the Python, so the words are
        # part of the deliverable, not a comment.
        self.assertIn("expiry-before-consumption rate", consumption.TRIGGER_ONE_DEFINITION)
        self.assertIn("assertion_uid is not null", consumption.TRIGGER_ONE_DEFINITION)


class TestTriggerTwoIsNotAQuery(LeaseTestCase):
    """G4 -- procedural, and its absence is intentional rather than an oversight."""

    def test_no_trigger_two_computation_is_exposed(self):
        exposed = [name for name in dir(consumption) if "trigger" in name.lower()]
        self.assertIn("TRIGGER_ONE_DEFINITION", exposed)
        self.assertFalse([name for name in exposed if "two" in name.lower()],
                         "trigger 2 is procedural; shipping it as a computation would dress a "
                         "human review obligation up as a satisfied check")

    def test_the_module_states_why_trigger_two_is_absent(self):
        self.assertIn("TRIGGER 2 IS NOT HERE", consumption.__doc__)


if __name__ == "__main__":
    unittest.main()


class TestTheProductionEntryPointIsCovered(LeaseTestCase):
    """G1's no-number guarantee, asserted against the function the WRITE PATH actually calls.

    THE GAP THIS CLOSES, found by a positive-control mutant and worth stating so it is not
    reintroduced. Every invalid-config assertion above goes through
    `lease.derive_lease_for_write`. `write_path.write_assertion` calls
    `lease.lease_fields_for_write`, a thin wrapper around it -- and before this class existed the
    wrapper was exercised exactly once, on the VALID-config happy path
    (`test_lease_fields_are_ready_to_merge_into_a_row`).

    So the guarantee was true and unguarded. A hardcoded fallback inserted into the wrapper was
    measured to accept a real `write_assertion` under an ABSENT config and store lease_s 86400 --
    AI-3's proposed structural default, the exact number F2 exists to keep unreachable -- with
    `test_lease_config` and `test_config` both still green. That is B2's own failure sentence
    ("a default reachable by any path fails at least one cell") reachable by a path no cell
    enumerated.

    The wrapper being thin today is not the point. Nothing made it stay thin.
    """

    def entry_points(self):
        """Both public derivation entry points, so the cross-product covers the seam rather
        than one side of it."""
        return {
            "derive_lease_for_write": lease.derive_lease_for_write,
            "lease_fields_for_write": lease.lease_fields_for_write,
        }

    def invalid_configs(self):
        broken = self.root / "wrapper-broken.json"
        broken.write_text("{", encoding="utf-8")
        tampered_record = write_record(self.root, "wrapper-tampered.md")
        tampered_config = write_config(self.root, tampered_record, name="wrapper-tampered.json")
        tampered_record.write_text("# changed after signing\n", encoding="utf-8")
        return {
            "absent": self.root / "wrapper-nope.json",
            "unparseable": broken,
            "record-missing": write_config(self.root, self.record, name="wrapper-gone.json",
                                           decision_record_path=str(self.root / "gone.md")),
            "hash-mismatch": tampered_config,
        }

    def test_no_entry_point_yields_a_number_under_any_invalid_config(self):
        for entry_name, fn in self.entry_points().items():
            for state, path in self.invalid_configs().items():
                for assertion_class in lease.ASSERTION_CLASSES:
                    with self.subTest(entry=entry_name, state=state,
                                      assertion_class=assertion_class):
                        with self.assertRaises(config.ConfigUnsigned):
                            fn(assertion_class, config_path=path)

    def test_every_entry_point_serves_the_configured_value_when_config_is_valid(self):
        """The companion positive. Without it the cross-product above could pass against an
        entry point that refuses unconditionally, which is not the property being claimed."""
        self.assertEqual(
            lease.derive_lease_for_write(lease.CLASS_STRUCTURAL, config_path=self.valid),
            STRUCTURAL_S)
        self.assertEqual(
            lease.lease_fields_for_write(lease.CLASS_STRUCTURAL, config_path=self.valid),
            {"class": lease.CLASS_STRUCTURAL, "lease_s": STRUCTURAL_S})

    def test_no_ai3_number_escapes_either_entry_point(self):
        for entry_name, fn in self.entry_points().items():
            for state, path in self.invalid_configs().items():
                with self.subTest(entry=entry_name, state=state):
                    try:
                        served = fn(lease.CLASS_STRUCTURAL, config_path=path)
                    except config.ConfigUnsigned:
                        continue
                    value = served["lease_s"] if isinstance(served, dict) else served
                    self.fail(f"{entry_name} served {value!r} under {state} config"
                              + (" -- an AI-3 proposed value" if value in AI3_PROPOSED else ""))

    def test_the_cross_product_covers_whatever_the_write_path_calls(self):
        """Mechanical, so a rename or a new seam cannot silently recreate the gap.

        Parses `write_path.py` with `ast` and collects every `lease.<name>` it calls, then
        requires each to be in the cross-product above. A future write path that reaches for a
        third lease function fails HERE, at the coverage claim, rather than in production.

        Same shape as ATLASSN-125's cross-repo record-shape check and ATLASSN-135's import-graph
        check: the test reads the real source rather than trusting a docstring.
        """
        import ast

        from atlas.registry import write_path

        source = Path(write_path.__file__).read_text(encoding="utf-8")
        called = set()
        for node in ast.walk(ast.parse(source, filename=write_path.__file__)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "lease"):
                called.add(node.func.attr)

        self.assertTrue(called, "found no lease.* call in write_path -- the parse is wrong, or "
                                "the write path stopped deriving leases through this module")
        uncovered = called - set(self.entry_points())
        self.assertEqual(uncovered, set(),
                         f"write_path calls lease.{sorted(uncovered)} which no invalid-config "
                         f"cell exercises -- the exact shape of the gap this class closes")


class TestWriteAssertionRefusesUnderInvalidConfig(LeaseTestCase):
    """The end-to-end statement of the same guarantee: not "the helper raises" but "a real write
    does not land". This is what the mutant defeated, so this is what has to be pinned."""

    def test_a_real_write_refuses_and_stores_nothing(self):
        import sqlite3

        from atlas.registry import architecture_parse, write_path
        from atlas.registry.tests import write_path_fixtures as fx

        components = architecture_parse.parse_components(
            architecture_parse.read_frozen_architecture(fx.REAL_PROJECT_ROOT))
        records_dir = self.root / "dispatch-records"
        records_dir.mkdir()
        sessions_path = self.root / "sessions.jsonl"
        fx.write_sessions_stream(
            sessions_path,
            [fx.session_line(fx.REAL_TRANSCRIPT_SESSION, "2026-01-01T00:00:00Z")])
        fx.seed_json_file(
            records_dir, "d-1", record_id="d-1", author_session_id="s-author",
            named_session_id=fx.REAL_TRANSCRIPT_SESSION, role="verifier",
            project="atlas-sonnet", repo_scope=["registry"],
            created_at="2025-12-01T00:00:00Z")
        store_path = self.root / "registry.db"
        fx.create_store(store_path)
        audit_path = self.root / "audit.jsonl"

        with self.assertRaises(write_path.AssertionRejected) as caught:
            write_path.write_assertion(
                store_path=store_path, audit_log_path=audit_path, dispatch_record_id="d-1",
                verifier_session_id=fx.REAL_TRANSCRIPT_SESSION,
                edge_id=fx.DECLARED_EDGE_GATE_CLIENT, edge_repo="atlas-sonnet",
                edge_component="registry", project_root=fx.REAL_PROJECT_ROOT,
                required_role="verifier", assertion_class="structural",
                evidence_tool_use_id=fx.REAL_ANCHOR_TOOL_USE_ID,
                evidence_class="observed-probe", transcript_path=fx.REAL_TRANSCRIPT_PATH,
                components=components, reach=fx.reach_for(fx.DECLARED_EDGE_GATE_CLIENT),
                config_path=self.root / "no-such-config.json",
                records_dir=records_dir, sessions_stream_path=sessions_path)
        self.assertEqual(caught.exception.reason, "config-unsigned")

        connection = sqlite3.connect(store_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM assertion").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM spent_ref").fetchone()[0], 0,
                "a config-refused write must not burn the evidence reference")
        finally:
            connection.close()
        self.assertFalse(audit_path.exists(),
                         "a refused write appends nothing to the trail (C13)")
