"""Shared fixtures for the registry test modules.

Named `helpers.py` rather than the `_helpers.py` the older atlas components use: this project's
convention is no leading underscore on new filenames or identifiers.

The lease and window values here are arbitrary and deliberately NOT AI-3's proposed
1800/3600/86400 (failure signature F2: no proposed number reachable in code, fixtures included).
"""

from datetime import datetime, timedelta, timezone

from atlas.registry import migrate

# Arbitrary, and chosen so arithmetic on it is obvious when a probe fails.
LEASE_S = 600

BASE_NOW = datetime(2026, 9, 12, 15, 0, 0, tzinfo=timezone.utc)


def iso(moment):
    """The stored form: UTC ISO-8601 with a trailing Z, as the schema doc specifies."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_store(path):
    return migrate.create_store(path)


def insert(connection, edge_id, verified_at, assertion_uid=None, state="live",
           lease_s=LEASE_S, component="registry", evidence_tool_use_id=None):
    """Insert one assertion. Only the fields any probe varies are parameters; the rest are
    fixed valid values, so a probe that fails does so for the reason it names."""
    if assertion_uid is None:
        assertion_uid = f"a-{abs(hash((edge_id, iso(verified_at), state))) % (10 ** 24):024d}"
    if evidence_tool_use_id is None:
        evidence_tool_use_id = f"toolu_{abs(hash(assertion_uid)) % (10 ** 20):020d}"
    revocation = ("2026-09-12T14:00:00Z", "s-revoker", "toolu_revocationevidence00000") \
        if state == "revoked" else (None, None, None)
    connection.execute(
        """
        INSERT INTO assertion (
            assertion_uid, edge_id, component, class, lease_s, verified_at,
            verifier_session_id, dispatch_record_id, evidence_tool_use_id, evidence_class,
            state, revoked_at, revoked_by_session_id, revocation_evidence_tool_use_id
        ) VALUES (?, ?, ?, 'structural', ?, ?, 's-verifier', 'd-record', ?, 'observed-probe',
                  ?, ?, ?, ?)
        """,
        (assertion_uid, edge_id, component, lease_s, iso(verified_at), evidence_tool_use_id,
         state) + revocation)
    return assertion_uid


def at_delta(seconds):
    """A verified_at that sits `seconds` BEFORE BASE_NOW, so status probes read as a delta."""
    return BASE_NOW - timedelta(seconds=seconds)
