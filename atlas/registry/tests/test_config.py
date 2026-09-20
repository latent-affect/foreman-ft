"""ATLASSN-128 -- the hash-verified config and decision-record gate. Run from the repo root:

    /Users/m5/.venv/bin/python3 -m unittest atlas.registry.tests.test_config -v

Criteria B1-B6 frozen on the ticket (criteria_hash sha256:63ab2ec5).

THE FIXTURE NUMBERS ARE DELIBERATE. They are 271, 577 and 1597 -- not AI-3's proposed
1800/3600/86400, and not values any plausible hardcoded fallback would pick. That is what makes
B3 discriminating: if a default were reachable anywhere in the path, the accessor would return
something other than the fixture's own number and the test fails. Asserting against 3600 would
have proved nothing, because 3600 is exactly what a wrong implementation would also return.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from atlas.registry import config

STRUCTURAL_S = 271
IN_FLIGHT_S = 577
WINDOW_S = 1597

AI3_PROPOSED_NUMBERS = (1800, 3600, 86400)


def write_record(directory, structural=STRUCTURAL_S, in_flight=IN_FLIGHT_S, window=WINDOW_S):
    """A decision record naming all three values, the way an operator sign-off would."""
    record = Path(directory) / "PDR-AI3-lease-and-window.md"
    record.write_text(
        "# Provisional Decision Record -- AI-3\n\n"
        f"Structural lease: {structural} seconds.\n"
        f"In-flight lease: {in_flight} seconds.\n"
        f"Go recency window: {window} seconds.\n",
        encoding="utf-8")
    return record


def write_config(directory, record, name="config.json", **overrides):
    """Write a config file. `name` matters: several fixtures live in one directory, and a
    helper that always wrote config.json would have them silently overwrite each other. That
    bug was real in the first draft of this module and was caught by
    test_the_state_fixtures_are_themselves_distinct_failures, which is why that control exists.
    """
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


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.record = write_record(self.root)
        self.config_path = write_config(self.root, self.record)

    def assertRefuses(self, sub_reason, config_path, reason=config.REASON):
        """`reason` defaults to config-unsigned. The value-mismatch case carries its own named
        reason per the amended C11, so tests for it pass REASON_VALUE_MISMATCH explicitly --
        asserting the reason string, not just the exception type, is what pins the criterion's
        wording to the code."""
        with self.assertRaises(config.ConfigUnsigned) as caught:
            config.load(config_path)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(caught.exception.sub_reason, sub_reason)
        return caught.exception


class TestValidConfig(ConfigTestCase):
    """B3 -- served values come from the file, not from anything embedded in code."""

    def test_valid_config_loads(self):
        loaded = config.load(self.config_path)
        self.assertEqual(loaded["lease_defaults"]["structural_s"], STRUCTURAL_S)
        self.assertEqual(loaded["lease_defaults"]["in_flight_s"], IN_FLIGHT_S)
        self.assertEqual(loaded["go_recency_window_s"], WINDOW_S)

    def test_accessors_return_the_files_own_values(self):
        self.assertEqual(
            config.lease_seconds_for_class(config.CLASS_STRUCTURAL, self.config_path),
            STRUCTURAL_S)
        self.assertEqual(
            config.lease_seconds_for_class(config.CLASS_IN_FLIGHT, self.config_path),
            IN_FLIGHT_S)
        self.assertEqual(config.recency_window_seconds(self.config_path), WINDOW_S)

    def test_accessors_track_a_different_file_rather_than_caching(self):
        other = Path(self.directory.name) / "other"
        other.mkdir()
        other_record = write_record(other, structural=13, in_flight=17, window=19)
        other_config = write_config(
            other, other_record,
            lease_defaults={"structural_s": 13, "in_flight_s": 17},
            go_recency_window_s=19,
            decision_record_path=str(other_record),
            decision_record_sha256=hashlib.sha256(other_record.read_bytes()).hexdigest())
        self.assertEqual(
            config.lease_seconds_for_class(config.CLASS_STRUCTURAL, other_config), 13)
        self.assertEqual(config.recency_window_seconds(other_config), 19)
        # The first file still answers with its own values: no shared cached state.
        self.assertEqual(config.recency_window_seconds(self.config_path), WINDOW_S)

    def test_an_unknown_assertion_class_refuses(self):
        with self.assertRaises(config.ConfigUnsigned):
            config.lease_seconds_for_class("provisional", self.config_path)


class TestRefusalStates(ConfigTestCase):
    """B1 -- each way validity can fail refuses with its own distinguishable sub-reason."""

    def test_absent_config(self):
        self.assertRefuses(config.SUB_ABSENT, self.root / "nonexistent.json")

    def test_unparseable_config(self):
        broken = self.root / "broken.json"
        broken.write_text("{not json at all", encoding="utf-8")
        self.assertRefuses(config.SUB_UNPARSEABLE, broken)

    def test_config_that_is_not_an_object(self):
        listed = self.root / "listed.json"
        listed.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertRefuses(config.SUB_MALFORMED, listed)

    def test_missing_lease_defaults(self):
        path = write_config(self.root, self.record, name="no-leases.json", lease_defaults=None)
        self.assertRefuses(config.SUB_MALFORMED, path)

    def test_lease_that_is_not_an_integer(self):
        path = write_config(self.root, self.record, name="string-lease.json",
                            lease_defaults={"structural_s": "271", "in_flight_s": IN_FLIGHT_S})
        self.assertRefuses(config.SUB_MALFORMED, path)

    def test_lease_that_is_a_boolean(self):
        # bool is an int subclass in Python; True would otherwise pass an isinstance int check
        # and become a one-second lease.
        path = write_config(self.root, self.record, name="bool-lease.json",
                            lease_defaults={"structural_s": True, "in_flight_s": IN_FLIGHT_S})
        self.assertRefuses(config.SUB_MALFORMED, path)

    def test_non_positive_lease(self):
        path = write_config(self.root, self.record, name="zero-lease.json",
                            lease_defaults={"structural_s": 0, "in_flight_s": IN_FLIGHT_S})
        self.assertRefuses(config.SUB_MALFORMED, path)

    def test_missing_or_invalid_window(self):
        self.assertRefuses(config.SUB_MALFORMED,
                           write_config(self.root, self.record, name="no-window.json", go_recency_window_s=None))
        self.assertRefuses(config.SUB_MALFORMED,
                           write_config(self.root, self.record, name="negative-window.json", go_recency_window_s=-5))

    def test_missing_decision_record_path(self):
        path = write_config(self.root, self.record, name="no-record-path.json", decision_record_path="")
        self.assertRefuses(config.SUB_MALFORMED, path)

    def test_hash_that_is_not_a_sha256_digest(self):
        path = write_config(self.root, self.record, name="bad-digest.json", decision_record_sha256="deadbeef")
        self.assertRefuses(config.SUB_MALFORMED, path)

    def test_decision_record_missing_from_disk(self):
        path = write_config(self.root, self.record, name="record-gone.json",
                            decision_record_path=str(self.root / "gone.md"))
        self.assertRefuses(config.SUB_RECORD_MISSING, path)

    def test_decision_record_hash_mismatch(self):
        self.record.write_text("# Tampered after signing\n", encoding="utf-8")
        exception = self.assertRefuses(config.SUB_RECORD_HASH_MISMATCH, self.config_path)
        self.assertIn("hashes to", str(exception))

    def test_self_attestation_fields_do_not_rescue_an_invalid_config(self):
        # An artifact's own claim never authenticates it. Adding signed_by/signed_at to a config
        # whose record is missing must change nothing.
        path = write_config(self.root, self.record, name="self-attested.json",
                            decision_record_path=str(self.root / "gone.md"),
                            signed_by="the operator", signed_at="2026-09-12T00:00:00Z")
        self.assertRefuses(config.SUB_RECORD_MISSING, path)


class TestRecordMustNameTheValues(ConfigTestCase):
    """B5 -- a matching hash binds the config to a document, not to the numbers it claims."""

    def test_record_that_does_not_name_a_claimed_value_refuses(self):
        silent = self.root / "silent.md"
        silent.write_text("# Decision\n\nThe operator approves the registry leases.\n",
                          encoding="utf-8")
        path = write_config(
            self.root, self.record, name="silent-record.json",
            decision_record_path=str(silent),
            decision_record_sha256=hashlib.sha256(silent.read_bytes()).hexdigest())
        exception = self.assertRefuses(config.SUB_RECORD_SILENT, path,
                                       reason=config.REASON_VALUE_MISMATCH)
        self.assertIn("does not name", str(exception))
        # C11's VALUE-MISMATCH probe names this exact reason string. Asserting the literal, not
        # the constant alone, is what would catch the constant being quietly renamed.
        self.assertEqual(exception.reason, "config-record-value-mismatch")

    def test_record_naming_only_some_values_refuses(self):
        partial = self.root / "partial.md"
        partial.write_text(f"Structural lease: {STRUCTURAL_S}. Window undecided.\n",
                           encoding="utf-8")
        path = write_config(
            self.root, self.record, name="partial-record.json",
            decision_record_path=str(partial),
            decision_record_sha256=hashlib.sha256(partial.read_bytes()).hexdigest())
        self.assertRefuses(config.SUB_RECORD_SILENT, path,
                           reason=config.REASON_VALUE_MISMATCH)

    def test_value_mismatch_is_still_a_config_unsigned_for_behavioural_purposes(self):
        # The split reason must not weaken "no value escapes an invalid config": the
        # value-mismatch exception stays a ConfigUnsigned subclass so every guarantee expressed
        # over that type keeps covering it.
        self.assertTrue(issubclass(config.ConfigRecordValueMismatch, config.ConfigUnsigned))

    def test_a_substring_number_does_not_count_as_naming_it(self):
        # 271 must not be found inside 2710 or 12715, or a record about one number would vouch
        # for another.
        self.assertFalse(config.names_value("the value is 2710", 271))
        self.assertFalse(config.names_value("the value is 12715", 271))
        self.assertFalse(config.names_value("27.1 seconds", 271))
        self.assertTrue(config.names_value("the value is 271.", 271))
        self.assertTrue(config.names_value("set to 271 seconds", 271))

    def test_thousands_separators_are_accepted(self):
        self.assertTrue(config.names_value("a lease of 86,400 seconds", 86400))


class TestNoValueEscapesAnInvalidConfig(ConfigTestCase):
    """B2 -- the cross-product. No accessor yields a number while config is invalid.

    This is the behavioural check the ticket demands in place of a source grep, because
    int('1800') and 60*30 both defeat a grep.
    """

    def invalid_config_paths(self):
        missing_record = write_config(self.root, self.record, name="x-record-gone.json",
                                      decision_record_path=str(self.root / "gone.md"))
        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        silent = self.root / "silent.md"
        silent.write_text("no numbers here\n", encoding="utf-8")
        silent_config = write_config(
            self.root, self.record, name="x-silent.json", decision_record_path=str(silent),
            decision_record_sha256=hashlib.sha256(silent.read_bytes()).hexdigest())
        tampered_root = Path(self.directory.name) / "tampered"
        tampered_root.mkdir()
        tampered_record = write_record(tampered_root)
        tampered_config = write_config(tampered_root, tampered_record)
        tampered_record.write_text("# changed after signing\n", encoding="utf-8")
        return {
            "absent": self.root / "nonexistent.json",
            "unparseable": broken,
            "malformed": write_config(self.root, self.record, name="x-no-window.json",
                                      go_recency_window_s=None),
            "record-missing": missing_record,
            "hash-mismatch": tampered_config,
            "record-silent": silent_config,
        }

    def test_no_accessor_returns_a_value_for_any_invalid_state(self):
        accessors = {
            "structural lease": lambda path: config.lease_seconds_for_class(
                config.CLASS_STRUCTURAL, path),
            "in-flight lease": lambda path: config.lease_seconds_for_class(
                config.CLASS_IN_FLIGHT, path),
            "recency window": config.recency_window_seconds,
            "load": config.load,
        }
        states = self.invalid_config_paths()
        for state_name, path in states.items():
            for accessor_name, accessor in accessors.items():
                with self.subTest(state=state_name, accessor=accessor_name):
                    with self.assertRaises(config.ConfigUnsigned) as caught:
                        accessor(path)
                    self.assertIn(caught.exception.reason,
                                  (config.REASON, config.REASON_VALUE_MISMATCH))

    def test_the_state_fixtures_are_themselves_distinct_failures(self):
        # Negative control on this test's own fixtures: if several "different" invalid states
        # collapsed into the same sub-reason, the cross-product above would be narrower than it
        # looks while still passing.
        observed = set()
        for path in self.invalid_config_paths().values():
            with self.assertRaises(config.ConfigUnsigned) as caught:
                config.load(path)
            observed.add(caught.exception.sub_reason)
        self.assertEqual(len(observed), 6, f"states collapsed into {sorted(observed)}")

    def test_no_ai3_proposed_number_is_ever_served(self):
        for path in self.invalid_config_paths().values():
            for accessor in (config.recency_window_seconds,
                             lambda p: config.lease_seconds_for_class(
                                 config.CLASS_STRUCTURAL, p)):
                try:
                    served = accessor(path)
                except config.ConfigUnsigned:
                    continue
                self.fail(f"a value ({served}) was served under an invalid config")
        # And under a VALID config the served values are the fixture's, never AI-3's proposals.
        for value in (config.recency_window_seconds(self.config_path),
                      config.lease_seconds_for_class(config.CLASS_STRUCTURAL,
                                                     self.config_path)):
            self.assertNotIn(value, AI3_PROPOSED_NUMBERS)


class TestPayloadLease(ConfigTestCase):
    """B4 -- a caller may never supply lease_s."""

    def test_payload_carrying_a_lease_is_rejected(self):
        with self.assertRaises(config.PayloadLeaseRejected):
            config.reject_payload_lease({"component": "registry", "lease_s": 99})

    def test_rejection_is_on_presence_not_on_value(self):
        # Even a payload whose lease agrees with what valid config would produce is rejected:
        # otherwise the value's provenance is the payload and it merely looked right.
        with self.assertRaises(config.PayloadLeaseRejected):
            config.reject_payload_lease({"lease_s": STRUCTURAL_S})
        with self.assertRaises(config.PayloadLeaseRejected):
            config.reject_payload_lease({"lease_s": None})

    def test_payload_without_a_lease_passes_through_unchanged(self):
        payload = {"component": "registry", "class": config.CLASS_STRUCTURAL}
        self.assertIs(config.reject_payload_lease(payload), payload)


if __name__ == "__main__":
    unittest.main()
