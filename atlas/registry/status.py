"""ATLASSN-129 -- read status computation and per-assertion granularity (GOALS.json C5, C7).

STATUS IS COMPUTED, NEVER STORED. The `state` column is a FACT written by a D1-governed act
(live, or revoked). STATUS is what a read returns, and it is derived from that fact plus the
current time, every time, and persisted nowhere. The schema doc calls this out as finding 3's
distinction and it is worth keeping sharp: a stored status would be a second source of truth
that goes stale silently between the moment a lease expires and the moment anything notices.
Nothing here writes.

THE FIVE OUTCOMES. `active`, `expired`, `revoked`, `never-registered` are computed here.
`registry-unavailable` is the store-level family -- absent, locked, corrupt, recovery-needed,
wrong schema_version, read-error -- and belongs to C6's availability matrix in ATLASSN-130, not
to this module. It is named in the vocabulary below so that the two can never be conflated by a
caller, and so D6's control has something to compare against.

ORDER OF DETERMINATION, and why revoked comes first. Revocation is terminal for that assertion:
it is a deliberate act saying this verification no longer holds, and no passage of time and no
clock reading can undo it. So it is decided before any time arithmetic. A revoked row whose
verified_at also happens to be in the future reads as revoked, not as a clock anomaly -- the
stronger, act-based fact wins over the weaker, inferred one.

SUPERSESSION IS PER EDGE, NOT PER ROW. C5: "revoked reads DENY terminally while a later valid
write on the same edge serves from the new assertion." So an edge read selects the LATEST
assertion for that edge and computes status from it; the revoked row stays in the table as
history and is never rewritten or deleted. Latest is ordered by verified_at then by id, both
descending -- the id tiebreak is not decoration, it makes the answer deterministic when two
assertions share a timestamp, which a same-second double write would otherwise leave to
whatever order SQLite happened to return.

BUDGET (ticket constraint). These reads stay OFF the per-tool-call blocking path. Measured
headroom at ticket authorship: cold open+select+close against a 200-row store, median 0.051ms /
p95 0.065ms. Any future design that moves them onto the per-tool-call path must re-measure
against that path's own standard rather than inheriting this one.
"""

from collections import namedtuple
from datetime import datetime, timezone

ACTIVE = "active"
EXPIRED = "expired"
REVOKED = "revoked"
NEVER_REGISTERED = "never-registered"
REGISTRY_UNAVAILABLE = "registry-unavailable"

COMPUTED_STATUSES = (ACTIVE, EXPIRED, REVOKED, NEVER_REGISTERED)

ALLOW = "allow"
DENY = "deny"

SUB_CLOCK_ANOMALY = "clock-anomaly"
SUB_MALFORMED = "malformed"

# One result per edge asked about. `sub_reason` is None unless the denial has a finer cause the
# gate's audit event should carry.
# `verified_at` is the parsed timestamp of the row this result came from, or None when there
# was no row (never-registered) or it could not be parsed. It is a SIXTH field with a default
# rather than a parallel query, added for ATLASSN-134: D3 recency has to compare verified_at
# against the Go window, and SELECT_LATEST_FOR_EDGE already selects it -- compute_status was
# simply consuming it into the status decision and dropping it. A second query surface over
# the same table is how two readers start disagreeing about what the table says.
#
# The default keeps every existing five-positional construction valid, so no landed caller
# changes. It carries the LEASE-based verdict in `status`; a caller wanting WINDOW-based
# recency must compare this field itself, because "not expired" and "recent enough for this
# decision" are different questions and conflating them was 134's original defect.
StatusResult = namedtuple("StatusResult",
                          "edge_id status decision sub_reason assertion_uid verified_at",
                          defaults=(None,))

SELECT_LATEST_FOR_EDGE = """
SELECT assertion_uid, state, verified_at, lease_s
FROM assertion
WHERE edge_id = ?
ORDER BY verified_at DESC, id DESC
LIMIT 1
"""


def parse_timestamp(text):
    """Parse a stored UTC ISO-8601 'Z' timestamp, or return None if it is unparseable.

    None is a real answer here rather than an exception: an unparseable verified_at is a
    per-assertion malformed denial in C6's matrix, which must deny THAT assertion while leaving
    its siblings readable. Raising would take the whole read pass down with it, which is exactly
    the granularity failure C7 exists to prevent.
    """
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_status(row, now, edge_id=None):
    """Status for one stored row at one instant. `row` is None when the edge has no assertion.

    `now` is a timezone-aware datetime on the same single-host UTC clock that wrote verified_at
    (schema doc, finding 17). A naive datetime is treated as UTC rather than guessed at.
    """
    if row is None:
        return StatusResult(edge_id, NEVER_REGISTERED, DENY, None, None)

    assertion_uid, state, verified_at_text, lease_s = row

    if state == REVOKED:
        return StatusResult(edge_id, REVOKED, DENY, None, assertion_uid)

    verified_at = parse_timestamp(verified_at_text)
    if verified_at is None:
        return StatusResult(edge_id, EXPIRED, DENY, SUB_MALFORMED, assertion_uid)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta_seconds = (now - verified_at).total_seconds()

    if delta_seconds < 0:
        # Never active. An NTP step backwards must not silently re-activate a stale assertion,
        # so this is a denial with its own named sub-reason rather than a quietly clamped zero.
        return StatusResult(edge_id, EXPIRED, DENY, SUB_CLOCK_ANOMALY, assertion_uid,
                            verified_at)

    if not isinstance(lease_s, int) or lease_s <= 0:
        return StatusResult(edge_id, EXPIRED, DENY, SUB_MALFORMED, assertion_uid, verified_at)

    # Closed interval at BOTH ends, per section 34.0's own `<=`: a delta of exactly lease_s is
    # still active. The one-second-past case is the first expired one.
    if delta_seconds <= lease_s:
        return StatusResult(edge_id, ACTIVE, ALLOW, None, assertion_uid, verified_at)

    return StatusResult(edge_id, EXPIRED, DENY, None, assertion_uid, verified_at)


def read_edge(connection, edge_id, now):
    """Status for one edge, selecting its latest assertion. Performs no write."""
    row = connection.execute(SELECT_LATEST_FOR_EDGE, (edge_id,)).fetchone()
    return compute_status(row, now, edge_id=edge_id)


def read_edges(connection, edge_ids, now):
    """One read pass over several edges: {edge_id: StatusResult}.

    THE POINT OF THIS FUNCTION IS C7. Each edge is resolved independently, so an expired or
    revoked assertion denies on its own edge and a healthy sibling in the SAME pass is still
    served ALLOW. There is deliberately no early return and no shared failure flag -- either
    would recreate the ATLASSN-103 defect, where one unrelated source's failure took down every
    gated view.

    Returns exactly one result per requested edge, including duplicates collapsed by identity,
    so a caller can always tell a denial apart from an edge that was silently dropped.
    """
    return {edge_id: read_edge(connection, edge_id, now) for edge_id in edge_ids}


SELECT_DISTINCT_EDGE_IDS = "SELECT DISTINCT edge_id FROM assertion"


def live_asserted_edge_ids(connection, now):
    """The set of edge_ids whose LATEST assertion currently computes to `active`.

    ATLASSN-133 / C12 needs this, and it belongs here rather than in reconcile.py for one
    reason: "the latest assertion for this edge" is already this module's rule (verified_at
    DESC, id DESC), and a second implementation of that selection inside the reconciler is how
    a read and a reconciliation start disagreeing about which row is current -- the same
    two-readers-one-table failure the StatusResult.verified_at note above records.

    WHY `active` AND NOT "not revoked". C12 requires a revoked-only edge to be reported as a
    FORWARD GAP until a new valid assertion lands, so a revoked edge must drop out of the
    asserted set. Expired drops out too, which is the fail-closed direction and matches
    reconcile.py's own wording ("a declared registers edge with no LIVE assertion"): an edge
    whose lease ran out is exactly as unverified as one that was never registered, and a
    reconciler that counted it as covered would go quiet on the gap it exists to find.

    Reads only. Passing `now` rather than calling the clock here keeps a reconciliation run
    evaluable at a single instant instead of drifting across its own loop.
    """
    edge_ids = [row[0] for row in connection.execute(SELECT_DISTINCT_EDGE_IDS).fetchall()]
    results = read_edges(connection, edge_ids, now)
    return {edge_id for edge_id, result in results.items() if result.status == ACTIVE}
