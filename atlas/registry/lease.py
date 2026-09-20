"""ATLASSN-137 -- lease derivation at write time (GOALS.json C11, as amended at ee29ebaf).

THIS MODULE SHIPS A DERIVATION AND A REFUSAL. IT NEVER SHIPS A VALUE. AI-3's lease defaults are
open decisions (proposed 24h structural / 1h in-flight, unsigned, adopted nowhere), and the
whole point of the config gate is that they stay unreachable until an operator signs them. So
there is no fallback here: a write either gets a lease derived from a valid, record-verified
config, or it is refused.

TWO REJECTIONS THAT LOOK SIMILAR AND ARE NOT.

  - A payload carrying lease_s is rejected on PRESENCE, before config is even consulted. Not on
    value: a payload whose lease happens to equal what valid config would produce is still
    rejected, because otherwise the value's provenance was the payload and it merely looked
    right. Provenance is the property being defended, not correctness.
  - A write needing a lease while config is invalid refuses with config-unsigned (or
    config-record-value-mismatch when the signed record does not name the claimed values).
    That is a dependency failure, not a caller error.

Both are refusals, and keeping them distinguishable is what lets an auditor tell "someone tried
to set their own lease" from "nobody has signed the defaults yet".

The seam exists so ATLASSN-131's write path calls one function rather than reimplementing the
rule. The write path is blocked on ATLASSN-125 at the time of writing; this is deliberately the
piece that can land first, so that when the write path is built the lease rule is already here
to be called rather than invented alongside it.
"""

from atlas.registry import config

# Re-exported so a caller does not have to import two modules to handle one refusal.
ConfigUnsigned = config.ConfigUnsigned
ConfigRecordValueMismatch = config.ConfigRecordValueMismatch
PayloadLeaseRejected = config.PayloadLeaseRejected

CLASS_STRUCTURAL = config.CLASS_STRUCTURAL
CLASS_IN_FLIGHT = config.CLASS_IN_FLIGHT
ASSERTION_CLASSES = (CLASS_STRUCTURAL, CLASS_IN_FLIGHT)


def derive_lease_for_write(assertion_class, payload=None, config_path=None):
    """The lease an accepted write should carry, or a refusal. Never a default.

    Order matters and is deliberate: the payload check runs FIRST, so a caller trying to supply
    its own lease is told so plainly even when config is also invalid. If config were checked
    first, a self-supplied lease would be masked by a config-unsigned refusal and the caller
    would fix the wrong thing.
    """
    if payload is not None:
        config.reject_payload_lease(payload)

    if assertion_class not in ASSERTION_CLASSES:
        raise config.ConfigUnsigned(
            config.SUB_MALFORMED,
            f"unknown assertion class {assertion_class!r}; expected one of "
            f"{sorted(ASSERTION_CLASSES)}")

    return config.lease_seconds_for_class(assertion_class, config_path)


def lease_fields_for_write(assertion_class, payload=None, config_path=None):
    """The class/lease pair a write stores, as a dict ready to merge into an assertion row."""
    return {
        "class": assertion_class,
        "lease_s": derive_lease_for_write(assertion_class, payload, config_path),
    }


__all__ = [
    "ConfigUnsigned", "ConfigRecordValueMismatch", "PayloadLeaseRejected",
    "CLASS_STRUCTURAL", "CLASS_IN_FLIGHT", "ASSERTION_CLASSES",
    "derive_lease_for_write", "lease_fields_for_write",
]
