"""ATLASSN-128 -- the hash-verified config and decision-record gate (failure signature F2).

AI-3's recency window and lease defaults are OPEN decisions, adopted nowhere. This module is
the mechanism that keeps them unreachable until an operator signs them, and that refuses
cleanly in the meantime.

F2 IS THE FAILURE THIS PREVENTS: a number taking effect with no reproduction path -- through
code, through config carrying no valid decision record, or through a caller's payload. So there
is no default in this module. Not a conservative default, not a documented-as-provisional
default, none: every accessor either serves a value from a config whose decision record checked
out, or refuses. A default is precisely the thing that would let a proposed number take effect
while everyone believes the gate is closed.

DETECTION IS BEHAVIOURAL, NOT A GREP, and the ticket says so explicitly: int('1800') and 60*30
both defeat a source scan. The test module therefore exercises the cross-product of every
accessor against every invalid-config state, and asserts that the valid path returns the
fixture's own unusual values rather than anything a hardcoded fallback would produce.

WHAT "VERIFIED, NOT ASSERTED" MEANS HERE (schema doc, findings 21-23). The config names a
decision record and its sha256. The record must exist and hash-match. Self-attestation fields
like signed_by / signed_at are absent from the schema by design: an artifact's own claim never
authenticates it, which is section 34.1(a)'s principle applied to config.

THE NAMED-VALUE CHECK, AND ITS CEILING. A matching hash binds the config to a document, but
nothing binds that document's CONTENT to the config's numbers -- an operator could sign a record
saying one lease while the config claims another, and the hash would still match. So the record
must also NAME each value the config claims.

This began as an addition beyond the ticket text (proposed here as B5 while implementing
ATLASSN-128) and is now frozen: GOALS.json C11 was amended and re-frozen 2026-09-12 at
criteria_hash ee29ebaf, ratified by priya-desai as design-scope decider, with the refusal reason
named `config-record-value-mismatch` and the VALUE-MISMATCH probe specified. The full C11
battery lands with I12 in atlas.registry.tests.test_lease_config; what this module owes that
battery is the behaviour and the exact reason string.

Stated ceiling, carried into the amended criterion rather than left in a module comment: this
confirms the numbers appear in the signed document, not that they appear there in the ROLE the
config assigns them. A record naming all three values in any arrangement passes, including one
that transposes the structural and in-flight leases. Closing that needs a structured decision
record with named fields, which is AI-3's artifact to define, not this module's to invent.
"""

import hashlib
import json
import re
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".claude" / "foreman" / "registry" / "config.json"

# The single reason string every consumer surfaces, per the schema doc. Sub-reasons distinguish
# WHICH way it failed without changing what the caller reports.
REASON = "config-unsigned"

SUB_ABSENT = "absent"
SUB_UNPARSEABLE = "unparseable"
SUB_MALFORMED = "malformed"
SUB_RECORD_MISSING = "decision-record-missing"
SUB_RECORD_HASH_MISMATCH = "decision-record-hash-mismatch"
SUB_RECORD_SILENT = "decision-record-does-not-name-value"

# GOALS.json C11, as amended 2026-09-12 (amendments[] entry, criteria_hash ee29ebaf, ratified by
# priya-desai after this module first landed). The value-mismatch case carries its OWN named
# reason, distinct from config-unsigned: the criterion names both separately, and an auditor
# reading a refusal should be able to tell "no valid config" from "a signed record that does not
# say what the config claims". It stays a ConfigUnsigned subclass so every behavioural guarantee
# expressed over that type -- above all "no accessor yields a number while config is invalid" --
# continues to hold without a caller having to know about the split.
REASON_VALUE_MISMATCH = "config-record-value-mismatch"

REASON_PAYLOAD_LEASE = "payload-supplied-lease-rejected"

CLASS_STRUCTURAL = "structural"
CLASS_IN_FLIGHT = "in-flight"
LEASE_KEY_FOR_CLASS = {
    CLASS_STRUCTURAL: "structural_s",
    CLASS_IN_FLIGHT: "in_flight_s",
}


class ConfigUnsigned(Exception):
    """Every refusal from this module. Carries the named reason plus a sub-reason, so a caller
    reports one thing and an auditor can still tell the five failure modes apart."""

    reason = REASON

    def __init__(self, sub_reason, detail):
        self.sub_reason = sub_reason
        self.detail = detail
        super().__init__(f"{self.reason}/{sub_reason}: {detail}")


class ConfigRecordValueMismatch(ConfigUnsigned):
    """The record hash-matches but does not contain a value the config claims (C11's
    VALUE-MISMATCH probe). A ConfigUnsigned, so the behavioural guarantees hold; its own reason,
    so an auditor can tell it apart from having no valid config at all."""

    reason = REASON_VALUE_MISMATCH


class PayloadLeaseRejected(Exception):
    """A caller tried to supply lease_s. Lease derives from class plus valid config, always."""

    def __init__(self, detail):
        self.reason = REASON_PAYLOAD_LEASE
        self.detail = detail
        super().__init__(f"{REASON_PAYLOAD_LEASE}: {detail}")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def names_value(text, value):
    """Does `text` name `value` as a standalone number?

    Bounded so that 3600 is not found inside 36000 or 13600, and so that a decimal like 271.5
    does not count as naming 271 -- either would let a record about one number vouch for
    another. A trailing sentence period IS allowed, because "the lease is 271." is a person
    naming the value, not a decimal. Thousands separators are accepted for the same reason: a
    real decision record is prose.
    """
    def bounded(literal):
        # Not preceded by a digit, or by a decimal point that makes this a fraction; not
        # followed by a digit, or by ".<digit>" which would make it the integer part of one.
        return r"(?<![\d.])" + re.escape(literal) + r"(?!\d)(?!\.\d)"

    if re.search(bounded(str(value)), text):
        return True
    return bool(re.search(bounded(f"{int(value):,}"), text))


def load(config_path=None):
    """Read and fully validate the config. Raises ConfigUnsigned on any failure.

    Never returns a partially-validated config: a caller that received one would have no way to
    know which of its fields were checked, which is the same ambiguity F2 exists to remove.
    """
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH

    if not path.is_file():
        raise ConfigUnsigned(SUB_ABSENT, f"no config at {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigUnsigned(SUB_UNPARSEABLE, f"{path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigUnsigned(SUB_MALFORMED, f"{path} is not a JSON object")

    leases = raw.get("lease_defaults")
    if not isinstance(leases, dict):
        raise ConfigUnsigned(SUB_MALFORMED, "lease_defaults is missing or not an object")
    for key in ("structural_s", "in_flight_s"):
        if not isinstance(leases.get(key), int) or isinstance(leases.get(key), bool):
            raise ConfigUnsigned(SUB_MALFORMED, f"lease_defaults.{key} is missing or not an int")
        if leases[key] <= 0:
            raise ConfigUnsigned(SUB_MALFORMED, f"lease_defaults.{key} must be positive")

    window = raw.get("go_recency_window_s")
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ConfigUnsigned(SUB_MALFORMED,
                             "go_recency_window_s is missing or not a positive int")

    record_path = raw.get("decision_record_path")
    expected_hash = raw.get("decision_record_sha256")
    if not isinstance(record_path, str) or not record_path:
        raise ConfigUnsigned(SUB_MALFORMED, "decision_record_path is missing or not a string")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ConfigUnsigned(SUB_MALFORMED,
                             "decision_record_sha256 is missing or not a sha256 hex digest")

    record = Path(record_path).expanduser()
    if not record.is_file():
        raise ConfigUnsigned(SUB_RECORD_MISSING, f"decision record not found at {record}")

    actual_hash = sha256_of(record)
    if actual_hash != expected_hash:
        raise ConfigUnsigned(
            SUB_RECORD_HASH_MISMATCH,
            f"decision record {record} hashes to {actual_hash}, config claims {expected_hash}")

    record_text = record.read_text(encoding="utf-8", errors="replace")
    for label, value in (("lease_defaults.structural_s", leases["structural_s"]),
                         ("lease_defaults.in_flight_s", leases["in_flight_s"]),
                         ("go_recency_window_s", window)):
        if not names_value(record_text, value):
            raise ConfigRecordValueMismatch(
                SUB_RECORD_SILENT,
                f"signed decision record {record} does not name {label}={value}; a matching "
                f"hash binds the config to a document, not to the numbers it claims")

    return {
        "lease_defaults": {"structural_s": leases["structural_s"],
                           "in_flight_s": leases["in_flight_s"]},
        "go_recency_window_s": window,
        "decision_record_path": str(record),
        "decision_record_sha256": expected_hash,
    }


def lease_seconds_for_class(class_name, config_path=None):
    """The lease for an assertion class, or a refusal. No default, by design."""
    key = LEASE_KEY_FOR_CLASS.get(class_name)
    if key is None:
        raise ConfigUnsigned(SUB_MALFORMED,
                             f"unknown assertion class {class_name!r}; expected one of "
                             f"{sorted(LEASE_KEY_FOR_CLASS)}")
    return load(config_path)["lease_defaults"][key]


def recency_window_seconds(config_path=None):
    """The Go-check recency window, or a refusal. No default, by design."""
    return load(config_path)["go_recency_window_s"]


def reject_payload_lease(payload):
    """Refuse any caller-supplied lease_s (schema doc finding 23).

    Rejects on PRESENCE, never on value: a payload whose lease happens to agree with what valid
    config would have produced is still rejected, because accepting it would mean the value's
    provenance was the payload and it only looked right.
    """
    if isinstance(payload, dict) and "lease_s" in payload:
        raise PayloadLeaseRejected(
            "lease_s may not be supplied by a caller; it derives from class plus signed config")
    return payload
