"""ATLASSN-134 -- D3 recency, the COMPUTATION Marcus's gate calls (GOALS.json C8, section
34.3). This module does not decide Go/Hold; it answers "is everything this decision declares
as a registry dependency verified RECENTLY ENOUGH", and the caller renders that into a verdict.

"One parser, two consumers" (34.3, verbatim): required_by_scope() calls architecture_parse.
parse_reach + edge_canonical_id, the SAME functions reconcile.py's reconciler already calls --
proven by import, not by two independently-written functions that happen to agree today.

RECENCY IS NOT LIVENESS, and the first draft of this module conflated them. An assertion's
status is ACTIVE while `now - verified_at <= lease_s` -- its own lease, which for the
structural class is long. The Go recency window is a SEPARATE, shorter bound the decision
imposes: verified_at must be inside `go_recency_window_s` of now. An assertion can be
perfectly ACTIVE under a 24h lease and far outside a 30-minute window, and that is exactly the
"Go against stale" case 34.3 calls FATAL. Checking ACTIVE alone would have returned go for it.
Both conditions are required here: the assertion must be live AND recent.

F3, THE SILENT-PASS TRAP THIS MODULE IS BUILT AGAINST: a malformed reach block must never
silently compute to an empty scope and pass as "nothing to check" -- that is indistinguishable
from a genuinely dependency-free decision, and the difference is exactly what a real bug would
hide behind. BlockMissing (no reach block at all) is the ONLY thing this module reads as
genuinely empty; BlockMalformed (a reach block that exists but will not parse) is reported as
its own FATAL, never silently swallowed into an empty set.

NAMING HAZARD, found live: architecture_parse and reach_scan deliberately use different
exception names for the same concepts (missing block / malformed block), since C9's
no-shared-helper rule forbids one sharing the other's hierarchy. A third module consuming one
must use THAT module's names, not its sibling's -- this file got that wrong once already
(BlockAbsent/BlockCorrupt are reach_scan's names, not architecture_parse's).
"""

import time
from datetime import datetime, timezone

from atlas.registry import architecture_parse, availability, config, status

FATAL = "fatal"
GO = "go"

REASON_SCOPE_MALFORMED = "required-by-scope-malformed"
REASON_NOT_ACTIVE = "not-currently-active"
REASON_STALE = "verified-outside-recency-window"
REASON_NO_TIMESTAMP = "no-verified-at-to-compare"


def required_by_scope(repo_root):
    """Canonical edge_ids (quad form) this repo's frozen ARCHITECTURE.md declares as
    `registers`-direction reach edges -- the same scope 34.4's reconciler tracks. Returns an
    empty set only when the document declares NO reach block at all (BlockMissing); a
    malformed reach block raises architecture_parse.BlockMalformed, which this function does
    NOT catch, so a caller cannot mistake "couldn't parse" for "nothing required"."""
    text = architecture_parse.read_frozen_architecture(repo_root)
    try:
        reach = architecture_parse.parse_reach(text)
    except architecture_parse.BlockMissing:
        return set()
    return {architecture_parse.edge_canonical_id(name, edge)
            for name, edge in reach.items() if edge.get("direction") == "registers"}


def as_utc_datetime(now):
    """Accept an aware datetime, a naive one (treated as UTC), or an epoch float.

    status.compute_status requires an aware datetime and availability.read_guarded converts
    the TypeError from anything else into `registry-unavailable`, which misreports a caller's
    type error as a store outage. Normalising here means this module cannot produce that
    particular lie about the store.
    """
    if isinstance(now, datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(float(now), tz=timezone.utc)


def check_recency(repo_root, store_path, config_path=None, now=None):
    """(GO, None) once every required edge is live AND verified inside the window, else
    (FATAL, reason).

    config.ConfigUnsigned (and ConfigRecordValueMismatch) propagate AS THEMSELVES when the
    window is actually needed and config is invalid -- that is every other config consumer's
    contract (config.py, lease.py), not something this module should wrap or hide.

    ORDER: scope is computed FIRST. A genuinely empty scope means the window is never
    consulted at all -- config.recency_window_seconds() is not called, so an unsigned config
    cannot block a decision that declares no registry dependencies in the first place.
    """
    try:
        scope = required_by_scope(repo_root)
    except architecture_parse.BlockMalformed as exc:
        return FATAL, f"{REASON_SCOPE_MALFORMED}:{exc}"

    if not scope:
        return GO, None

    window_s = config.recency_window_seconds(config_path)
    moment = as_utc_datetime(now if now is not None else time.time())

    for edge_id in sorted(scope):
        result = availability.read_one(store_path, edge_id, moment)
        if result.status != status.ACTIVE:
            return FATAL, f"{REASON_NOT_ACTIVE}:{edge_id}:{result.status}"
        if result.verified_at is None:
            # Active without a parseable timestamp should be unreachable -- compute_status
            # cannot return ACTIVE without one. Refusing rather than trusting it keeps the
            # impossible case fail-closed instead of silently skipping the window check.
            return FATAL, f"{REASON_NO_TIMESTAMP}:{edge_id}"
        age_s = (moment - result.verified_at).total_seconds()
        if age_s > window_s:
            return FATAL, f"{REASON_STALE}:{edge_id}:{int(age_s)}s>{window_s}s"
    return GO, None


__all__ = ["required_by_scope", "check_recency", "as_utc_datetime",
           "FATAL", "GO", "REASON_SCOPE_MALFORMED", "REASON_NOT_ACTIVE", "REASON_STALE",
           "REASON_NO_TIMESTAMP"]
