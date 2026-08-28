import json
import time


def poll_new_events(store, last_seen_rowid):
    """One poll cycle via store's own public API (store.get_events_since), not raw SQL
    against store's internal schema. Correct because every write goes through BEGIN
    IMMEDIATE (WAL single-writer + append-only, no rowid reuse -- see ARCHITECTURE.md's
    corrected note), so this sees writes from ANY process against the same db file, not
    just in-process ones."""
    return store.get_events_since(last_seen_rowid)


def sse_stream(store, poll_interval=0.5, max_iterations=None, start_rowid=None):
    # poll_interval=0.5s: CITED, ARCHITECTURE.md's "api polls events ... on a short
    # interval (500ms)" -- not an arbitrary placeholder.
    """Generator yielding SSE-formatted strings ('id: {n}\\ndata: {...}\\n\\n').
    max_iterations is for tests -- None means run forever (the real HTTP handler's usage).

    start_rowid=None (the default) resolves to store.latest_event_id() -- "start from
    now" -- NOT 0. A hardcoded start_rowid=0 default previously replayed the entire event
    history to every newly-connecting AND reconnecting client (found by Clint
    Eastwood's adversarial review, measured: 81 events replayed on every connect against
    the real db, each firing a full /tickets refetch client-side). Pass an explicit
    start_rowid (typically parsed from the client's Last-Event-ID header on reconnect, see
    http_api.py) when the caller genuinely wants to resume from a specific point,
    including 0 for "replay everything" if that's ever actually wanted."""
    last_seen = start_rowid if start_rowid is not None else store.latest_event_id()
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        for event in poll_new_events(store, last_seen):
            yield f"id: {event['id']}\ndata: {json.dumps(event)}\n\n"
            last_seen = event["id"]
        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            time.sleep(poll_interval)
