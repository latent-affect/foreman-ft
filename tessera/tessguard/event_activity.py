"""Real TESSERA-write activity, read through store's own public get_events_since() --
never raw SQL, never a transcript-side proxy. This is the actual production data source both
layers use; the original backtest's transcript-regex classifier was a lower-bound proxy for
this and is retired now that the real thing is wired in (see ARCHITECTURE-REVIEW.md, F4/N4).

Deliberately five event types, not store's full set (24 defined today, 15 seen in real rows) --
declared explicitly so a session that only sets a custom field or a summary does not silently
read as "covered." Swapping in the full union was measured to move zero backtested flags, so
this restriction is a stated intent, not (yet) a load-bearing filter.
"""

REAL_TESSERA_WRITE_EVENT_TYPES = frozenset({
    "TicketCreated",
    "CommentAdded",
    "StatusChanged",
    "TicketCriteriaFrozen",
    "ClaimRecorded",
})


def project_prefix_of_ticket(ticket_id):
    """'PREFIX-N' -> 'PREFIX'. Pure string op -- ticket IDs are always f'{prefix}-{counter}'
    (store.create_ticket), so this needs no store round trip. Guarded: get_events_since()
    can return NULL ticket_id rows (ColumnDescriptionSet, DatasetCreated, etc. -- none of
    them in REAL_TESSERA_WRITE_EVENT_TYPES today, but this guard doesn't rely on that
    holding forever)."""
    if not ticket_id:
        return None
    return ticket_id.rsplit("-", 1)[0]


def real_events_for_projects(store, prefixes, window_start_iso=None, window_end_iso=None):
    """Real (non-read-only) TESSERA-write events attributable to any of `prefixes`
    (any-of aggregation -- a repo hosting multiple registered projects, e.g. AREM+FORE
    sharing one source_root, counts coverage from either). Optionally windowed by
    created_at (ISO 8601 strings, compared as parsed datetimes -- see transcript.py's
    docstring on why this must never be a plain string compare across sources). No window
    args means "ever, all time" -- used by gitgate for its 24h check by passing an explicit
    window, and by audit for its whole-session check by passing the session's own span."""
    from datetime import datetime

    def parse_internal(iso):
        # Both transcript (ms) and store (microsecond) timestamps parse fine here; only a
        # STRING comparison across the two precisions is unsafe, not a datetime one.
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    start_dt = parse_internal(window_start_iso) if window_start_iso else None
    end_dt = parse_internal(window_end_iso) if window_end_iso else None

    prefix_set = set(prefixes)
    matched = []
    for event in store.get_events_since(0):
        if event.get("event_type") not in REAL_TESSERA_WRITE_EVENT_TYPES:
            continue
        prefix = project_prefix_of_ticket(event.get("ticket_id"))
        if prefix not in prefix_set:
            continue
        created_at = event.get("created_at")
        if not created_at:
            continue
        created_dt = parse_internal(created_at)
        if start_dt is not None and created_dt < start_dt:
            continue
        if end_dt is not None and created_dt > end_dt:
            continue
        matched.append(event)
    return matched
