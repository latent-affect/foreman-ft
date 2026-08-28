import hashlib
import json


def canonical_json(obj):
    """Deterministic JSON serialization: sorted keys, no whitespace.

    Raises TypeError on a non-JSON-serializable value (json.dumps' own behavior) rather
    than silently coercing or dropping it -- callers hashing this output need a failure
    to be loud, since a silently-altered payload would hash to something else with no
    error anywhere in the chain.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data):
    """sha256 hex digest of `data`, a str (utf-8 encoded) or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def event_hash(payload):
    """sha256_hex(canonical_json(payload)) -- the composition `store` uses for
    event_hash and `api` uses for claim/discrepancy hashing. Stable across dict key
    order by construction (canonical_json sorts keys)."""
    return sha256_hex(canonical_json(payload))
