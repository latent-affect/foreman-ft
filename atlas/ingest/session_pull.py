"""ATLASSN-33: main-session transcript ingest -- the half of the corpus ATLAS never read.

ARCHITECTURE.md section 20 is the design. The short version: section 19's deny-join works but its
denominator is a floor, because section 18 ingested subagent transcripts and nothing else. Of
29,539 distinct tool_use_id values in hook_verdict, only 9,736 -- 33.0% -- joined to a transcript
ATLAS held. The rest were not missing from disk; they were in main-session transcripts at
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl, which was not a source at all.

Measured before this was written: 2,075 files, 1.17 GB, parsed in 3.1 seconds, 58,629 tool_use
blocks, and recovering 19,594 of the 19,803 unjoinable ids -- taking ledger joinability from
33.0% to 99.3%.

Every line of parsing is shared with the subagent pull via transcript_parse, because the record
formats are identical -- checked rather than assumed: sessionId, timestamp, cwd, gitBranch, uuid
and message are present in both, and only agentId is absent here. What differs is discovery (a
flat glob at the project-directory level, not a recursive one under subagents/) and identity (a
session has no dispatching parent, no sidecar and no agent type). Section 20.1 records why that
justified four parallel tables rather than widening subagent_transcript.

Runnable standalone, and also invoked by the same 60-second launchd job as the subagent pull:

    /Users/m5/.venv/bin/python3 -m atlas.ingest.session_pull
    /Users/m5/.venv/bin/python3 -m atlas.ingest.session_pull --rebuild

FIX 2026-09-20 (ATLASSN-193, Iris Chen's real, executed adversarial probe). Two real defects,
found together because the second is a consequence of the first going uncaught:

  (1) A single adversarial-but-JSON-valid transcript line crashed map_transcript_line() with an
      uncaught AttributeError -- a truthy non-dict `message` ((obj.get('message') or {}).get(
      'content') assumes dict-shaped once truthy) or a truthy non-string `text` field (raw =
      block.get('text') or '' then raw.encode('utf-8') assumes str once truthy). This call had NO
      surrounding try/except, unlike the json.loads() call three lines above it, which already
      catches JSONDecodeError/UnicodeDecodeError and counts a malformed line rather than crashing.
      Fixed the same way: wrapped in its own try/except, counted as malformed, skipped.

  (2) The crash from (1) propagated out of pull_session(), out of run()'s per-file loop, and into
      run()'s OWN except block -- whose commit() (there only to persist the error status onto
      subagent_pull_run) also durably committed whatever partial INSERTs had happened in the SAME
      uncommitted transaction before the crash, while session_transcript.byte_offset was NEVER
      advanced (that UPDATE only runs after pull_session's per-line loop completes, which the
      crash prevented) -- so the next run re-reads from the same old watermark, re-encounters the
      SAME now-already-committed rows, and _is_replay()'s own watermark-replay alarm fires
      (SessionPullError), masking the original crash on every subsequent run. Worse: run()'s ONE
      try/except wrapped the ENTIRE for-path-in-files loop, so ONE poisoned file's crash aborted
      every OTHER file in the same run too -- a self-reinforcing, permanent whole-corpus outage
      from one bad line in one transcript, confirmed across 3 consecutive simulated pulls in
      Iris's own end-to-end repro. Fixed by moving the try/except INSIDE the per-file loop, one
      per file rather than one for the whole loop, with an explicit conn.rollback() on that file's
      exception before moving on -- discarding exactly that file's own uncommitted partial work
      (every prior file's work is already committed via the existing `if c or r or t:
      conn.commit()` line, so nothing else is touched) and letting every OTHER file still get
      ingested in the same run, the fix (1) does not structurally guarantee for failure modes it
      does not anticipate, so this is real defense-in-depth, not redundant with it.
"""

import argparse
import datetime
import hashlib
from pathlib import Path

from atlas.warehouse import migrate

from .subagent_pull import (
    acknowledge_credential_hit as _ack_credential_hit,
    acknowledge_deny_credential_hit as _ack_deny_credential_hit,
    ledger_deny_ids, reconcile_deny_text, scan_stored_tool_inputs_for_credentials,
)
from .transcript_parse import map_transcript_line, split_complete_lines, stream_id_of_path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAREHOUSE_DB = REPO_ROOT / "atlas" / "warehouse" / "atlas.db"
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# A main-session transcript is <project_dir>/<session-id>.jsonl -- one level down, flat. A
# subagent transcript is <project_dir>/<session-id>/subagents/**/agent-*.jsonl, which this glob
# structurally cannot reach, so the two sources cannot double-ingest the same file.
SESSION_GLOB = "*.jsonl"


class SessionPullError(RuntimeError):
    pass


# ATLASSN-104: session_block_sequence added alongside the two existing dedup targets. Same
# (transcript_id, record_uuid, block_index) idempotency key, same reason -- a watermark that
# re-read committed bytes must raise, not silently deduplicate.
_REPLAY_DEDUP_TABLES = ("session_tool_call", "session_assistant_text", "session_block_sequence")


def _is_replay(conn, table, transcript_id, record_uuid, block_index, byte_offset):
    """True iff this row is a legitimate re-append of an already-stored record and should be
    silently skipped. Raises SessionPullError iff it is a same-offset watermark replay.

    A single `INSERT ... ON CONFLICT (transcript_id, record_uuid, block_index) DO NOTHING`
    cannot distinguish these two cases: a true watermark replay (re-reading bytes already
    committed) produces the identical transcript_id, byte_offset, block_index AND record_uuid
    as the existing row, so it violates the dedup target's constraint AND the pre-existing
    (transcript_id, byte_offset, block_index) UNIQUE constraint at once -- and measured against
    sqlite3 3.53.4, DO NOTHING resolves via whichever named conflict target matches and never
    reaches the second constraint, silently swallowing the exact case
    `pull_session`'s docstring requires to fail loudly. Checking here first, before either
    INSERT is attempted, keeps that alarm live."""
    assert table in _REPLAY_DEDUP_TABLES, f"unexpected table {table!r}"
    if record_uuid is None:
        return False
    existing = conn.execute(
        f"SELECT byte_offset FROM {table} WHERE transcript_id = ? AND record_uuid = ? "
        "AND block_index = ?",
        (transcript_id, record_uuid, block_index),
    ).fetchone()
    if existing is None:
        return False
    if existing[0] == byte_offset:
        raise SessionPullError(
            f"{table}: transcript_id={transcript_id} record_uuid={record_uuid!r} "
            f"block_index={block_index} re-read at byte_offset {byte_offset}, already "
            "committed there -- the watermark re-read bytes it had already consumed."
        )
    return True


def nowIso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def discover_sessions(projects_root=DEFAULT_PROJECTS_ROOT):
    """Every main-session transcript. Sorted for a deterministic run order, and a missing
    projects_root is an empty list rather than a crash -- the same F3 discipline the subagent
    pull follows for a source that could legitimately be absent."""
    root = Path(projects_root)
    if not root.is_dir():
        return []
    hits = []
    for project_dir in root.iterdir():
        if project_dir.is_dir():
            hits.extend(project_dir.glob(SESSION_GLOB))
    return sorted(hits)


def parse_session_path(path, projects_root=DEFAULT_PROJECTS_ROOT):
    """The identity a main-session transcript's location encodes: the encoded project directory
    and the session id. Returns None if the path is not shaped like one -- which is what keeps a
    subagent transcript out even if one were ever passed in, since those sit two levels deeper."""
    try:
        rel = Path(path).resolve().relative_to(Path(projects_root).resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) != 2 or not parts[1].endswith(".jsonl"):
        return None
    return {"project_dir": parts[0], "session_id": parts[1][: -len(".jsonl")]}


def upsert_session(conn, path, identity, stream_id):
    """Returns (transcript_id, resume_byte_offset). Same rotation rule as the subagent pull: a
    transcript whose identity changed is a new stream and is re-read from zero."""
    now = nowIso()
    row = conn.execute(
        "SELECT transcript_id, stream_id, byte_offset FROM session_transcript "
        "WHERE source_path = ?", (str(path),)
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO session_transcript (source_path, stream_id, project_dir, session_id, "
            "byte_offset, calls_ingested, first_seen_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
            (str(path), stream_id, identity["project_dir"], identity["session_id"], now, now),
        )
        return cur.lastrowid, 0
    transcript_id, prior_stream_id, prior_offset = row
    resume_offset = 0 if prior_stream_id != stream_id else prior_offset
    conn.execute(
        "UPDATE session_transcript SET stream_id = ?, updated_at = ? WHERE transcript_id = ?",
        (stream_id, now, transcript_id),
    )
    return transcript_id, resume_offset


def pull_session(conn, path, pull_run_id, projects_root=DEFAULT_PROJECTS_ROOT,
                 deny_ids=frozenset()):
    """Reads one main-session transcript from its watermark forward. Returns
    (calls, results, texts, malformed), or None if the path is not a session transcript or has no
    stable identity yet.

    Same failure discipline as the subagent pull: a malformed line is counted and skipped, a
    partial trailing line is not consumed, and a duplicate (transcript_id, byte_offset,
    block_index) RAISES rather than being deduplicated -- that means the watermark re-read bytes
    it had already committed, which is a bug that should fail loudly.

    ATLASSN-193: a line that parses as JSON but whose shape crashes map_transcript_line() (a
    truthy non-dict `message`, a truthy non-string `text` field) is now counted as malformed and
    skipped too, same discipline as an actually-unparseable line -- see this module's own top
    docstring for the full incident this closes.

    ATLASSN-104: also writes session_block_sequence, one row per block at every position of every
    record parsed here, regardless of type. Not reflected in this function's own return tuple --
    the block count is not currently consumed by any caller, and adding it would change run()'s
    unpacking of this function's result for no criterion that needs it."""
    identity = parse_session_path(path, projects_root)
    if identity is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    stream_id = stream_id_of_path(p)
    if stream_id is None:
        return None

    transcript_id, start_offset = upsert_session(conn, p, identity, stream_id)
    size = p.stat().st_size
    if size <= start_offset:
        conn.execute(
            "UPDATE session_transcript SET updated_at = ? WHERE transcript_id = ?",
            (nowIso(), transcript_id),
        )
        return 0, 0, 0, 0

    with open(p, "rb") as fh:
        fh.seek(start_offset)
        new_data = fh.read()

    complete_lines, consumed = split_complete_lines(new_data)

    calls_inserted = results_inserted = texts_inserted = malformed = 0
    offset = start_offset
    import json as _json
    for line in complete_lines:
        this_offset = offset
        offset += len(line) + 1
        if not line.strip():
            continue
        try:
            obj = _json.loads(line)
        except (_json.JSONDecodeError, UnicodeDecodeError):
            malformed += 1
            continue
        if not isinstance(obj, dict):
            malformed += 1
            continue
        # ATLASSN-193: the record is valid JSON and a dict at the TOP level, but map_transcript_
        # line() makes further shape assumptions one level down (message is dict-shaped once
        # truthy; a text block's own text field is str-shaped once truthy) that adversarial-but-
        # JSON-valid content can violate -- Iris Chen's real, executed probe found both. Counted
        # as malformed and skipped, same as an actually-unparseable line, rather than crashing the
        # whole file (and see this module's own top docstring for why a crash here used to take
        # down every OTHER file in the same run too).
        try:
            calls, results, texts, blocks = map_transcript_line(obj, this_offset, deny_ids)
        except (AttributeError, TypeError):
            malformed += 1
            continue
        for text in texts:
            if _is_replay(conn, "session_assistant_text", transcript_id, text["record_uuid"],
                          text["block_index"], text["byte_offset"]):
                continue
            conn.execute(
                "INSERT INTO session_assistant_text (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, text, text_bytes, truncated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, text["byte_offset"], text["block_index"], pull_run_id,
                 text["ts"], text["record_uuid"], text["text"], text["text_bytes"],
                 text["truncated"]),
            )
            texts_inserted += 1
        for call in calls:
            if _is_replay(conn, "session_tool_call", transcript_id, call["record_uuid"],
                          call["block_index"], call["byte_offset"]):
                continue
            conn.execute(
                "INSERT INTO session_tool_call (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, session_id, cwd, git_branch, tool_use_id, "
                "tool_name, tool_input_json, tool_input_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, call["byte_offset"], call["block_index"], pull_run_id,
                 call["ts"], call["record_uuid"], call["session_id"], call["cwd"],
                 call["git_branch"], call["tool_use_id"], call["tool_name"],
                 call["tool_input_json"], call["tool_input_bytes"]),
            )
            calls_inserted += 1
        for result in results:
            cur = conn.execute(
                "INSERT OR IGNORE INTO session_tool_result (transcript_id, tool_use_id, "
                "byte_offset, pull_run_id, ts, is_error, result_bytes, is_hook_deny, deny_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, result["tool_use_id"], result["byte_offset"], pull_run_id,
                 result["ts"], result["is_error"], result["result_bytes"],
                 result["is_hook_deny"], result["deny_text"]),
            )
            results_inserted += cur.rowcount
        # ATLASSN-104/REQ-64, ARCHITECTURE.md section 40. Structural only, no content column --
        # written for every block regardless of type, including one this loop's own calls/texts
        # branches above did not otherwise capture (thinking, fallback, an unrecognised type, or a
        # malformed non-dict block, which map_transcript_line already turned into a
        # block_type=NULL row rather than dropping).
        for blk in blocks:
            if _is_replay(conn, "session_block_sequence", transcript_id, blk["record_uuid"],
                          blk["block_index"], blk["byte_offset"]):
                continue
            conn.execute(
                "INSERT INTO session_block_sequence (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, block_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, blk["byte_offset"], blk["block_index"], pull_run_id,
                 blk["ts"], blk["record_uuid"], blk["block_type"]),
            )

    conn.execute(
        "UPDATE session_transcript SET byte_offset = ?, calls_ingested = calls_ingested + ?, "
        "updated_at = ? WHERE transcript_id = ?",
        (start_offset + consumed, calls_inserted, nowIso(), transcript_id),
    )
    return calls_inserted, results_inserted, texts_inserted, malformed


# ATLASSN-102, ARCHITECTURE.md section 33.6. dim_session is declared in section 16 and had
# never held a row, because nothing had ever written to it. It needs no schema change, only a
# writer, so this is a builder rather than a migration.
#
# Timestamps come from the UNION of session_tool_call and session_assistant_text, and that is the
# whole reason the dimension is worth having. Measured before this was written: session_tool_call
# alone covers 725 of 2,361 sessions (31%), because most transcripts carry assistant text and no
# tool calls; unioned with session_assistant_text it covers 2,336 (99.0%).
#
# cwd and git_branch come from the earliest tool call in the session that carries a cwd, so they
# reach only the ~31% of sessions that made a tool call. They are left NULL for the rest rather
# than reversed out of session_transcript.project_dir: Claude Code's path encoding maps both a
# literal hyphen and a path separator onto '-', so the reversal is ambiguous and would sometimes
# name a directory that does not exist. A NULL meaning "not ingested" beats a string that might
# be wrong.
#
# claude_version stays NULL for every row. The transcript records carry a `version` field but no
# ingested table stores it, and capturing it means a schema change to a migration-4 table, which
# this project does by adding a parallel table rather than by ALTER. Filed as follow-on.
REFRESH_DIM_SESSION_SQL = """
INSERT OR REPLACE INTO dim_session
    (session_id, started_ts, ended_ts, claude_version, git_branch, start_cwd)
SELECT s.session_id,
       span.started_ts,
       span.ended_ts,
       NULL,
       origin.git_branch,
       origin.cwd
FROM (SELECT DISTINCT session_id FROM session_transcript) s
LEFT JOIN (
    SELECT t.session_id, MIN(e.ts) AS started_ts, MAX(e.ts) AS ended_ts
    FROM session_transcript t
    JOIN (
        SELECT transcript_id, ts FROM session_tool_call WHERE ts IS NOT NULL
        UNION ALL
        SELECT transcript_id, ts FROM session_assistant_text WHERE ts IS NOT NULL
    ) e ON e.transcript_id = t.transcript_id
    GROUP BY t.session_id
) span ON span.session_id = s.session_id
LEFT JOIN (
    SELECT session_id, cwd, git_branch FROM (
        SELECT t.session_id, c.cwd, c.git_branch,
               ROW_NUMBER() OVER (PARTITION BY t.session_id ORDER BY c.ts, c.call_id) AS rn
        FROM session_transcript t
        JOIN session_tool_call c ON c.transcript_id = t.transcript_id
        WHERE c.cwd IS NOT NULL
    ) WHERE rn = 1
) origin ON origin.session_id = s.session_id
"""


REAP_DIM_SESSION_SQL = """
DELETE FROM dim_session
WHERE session_id NOT IN (SELECT session_id FROM session_transcript)
"""


def refresh_dim_session(conn):
    """Rebuilds dim_session from the already-ingested session tables. Returns the row count.

    The DELETE is not optional. INSERT OR REPLACE never removes a row, so without it a rebuild
    would be "always correct" only if session_transcript were append-only -- and it is not:
    rebuild() below does DELETE FROM session_transcript. One --rebuild would otherwise orphan
    every dim_session row for a session no longer on disk, permanently, with no reaper.

    Both statements share one transaction so a concurrent reader sees the table before or after,
    never mid-rebuild with the deletes applied and the inserts still pending. A full rebuild each
    tick rather than an incremental update because the table is ~2,400 rows: simpler, at a cost
    that does not matter."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(REAP_DIM_SESSION_SQL)
        conn.execute(REFRESH_DIM_SESSION_SQL)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM dim_session").fetchone()[0]


def acknowledge_credential_hit(conn, call_id, pattern_name, verdict, rationale, acknowledged_by):
    """ATLASSN-197. Session-side ergonomic wrapper over subagent_pull.acknowledge_credential_hit,
    pre-bound to session_tool_call/session_credential_ack -- triages a hit on a session tool
    call's OWN tool_input_json. See that function's own docstring for the acknowledgement
    discipline (binds to a hash of the exact payload, so an edited payload re-arms it)."""
    return _ack_credential_hit(
        conn, call_id, pattern_name, verdict, rationale, acknowledged_by,
        call_table="session_tool_call", ack_table="session_credential_ack")


def acknowledge_deny_credential_hit(conn, transcript_id, tool_use_id, pattern_name, verdict,
                                    rationale, acknowledged_by):
    """ATLASSN-197. Session-side ergonomic wrapper over
    subagent_pull.acknowledge_deny_credential_hit, pre-bound to session_tool_call/
    session_tool_result/session_credential_ack -- triages a hit on a session tool result's
    deny_text rather than its call's tool_input_json."""
    return _ack_deny_credential_hit(
        conn, transcript_id, tool_use_id, pattern_name, verdict, rationale, acknowledged_by,
        call_table="session_tool_call", result_table="session_tool_result",
        ack_table="session_credential_ack")


def run(warehouse_db_path=DEFAULT_WAREHOUSE_DB, projects_root=DEFAULT_PROJECTS_ROOT,
        pull_run_id=None):
    """One pass over the main-session corpus.

    Shares subagent_pull_run rather than opening a second run table: both pulls are the same live
    plane on the same 60-second cadence, and v_subagent_live_status's freshness gate should cover
    both or neither. A caller that already opened a run row passes its id in; a standalone run
    opens its own.

    ATLASSN-193: the per-file try/except below, and its conn.rollback() on that file's own
    exception, is the fix for the second half of the incident this module's own top docstring
    describes -- ONE file's crash (from map_transcript_line() or anything else pull_session() can
    raise) must not (a) durably commit that file's own partial, uncommitted INSERTs via a LATER,
    unrelated commit() call, or (b) abort every OTHER file in the same run. rollback() discards
    only the crashed file's own uncommitted work; every prior file's work is already committed via
    the existing `if c or r or t: conn.commit()` line below, so nothing else is touched."""
    conn = migrate.connect(str(warehouse_db_path))
    own_run = pull_run_id is None
    if own_run:
        cur = conn.execute(
            "INSERT INTO subagent_pull_run (started_at, status, atlas_version, host_id) "
            "VALUES (?, 'running', ?, ?)",
            (nowIso(), migrate.ATLAS_VERSION, migrate.hostId()),
        )
        pull_run_id = cur.lastrowid
        conn.commit()

    deny_ids = ledger_deny_ids(conn)
    files = discover_sessions(projects_root)
    files_read = files_skipped = files_errored = 0
    calls = results = texts = malformed = 0
    errored_paths = []
    try:
        for path in files:
            try:
                outcome = pull_session(conn, path, pull_run_id, projects_root, deny_ids)
            except Exception as exc:
                # ATLASSN-193: catch and roll back HERE, per file, rather than letting this
                # propagate to the outer except below -- that block's own commit() (for the
                # error-status UPDATE) would otherwise durably commit this file's partial,
                # uncommitted INSERTs, and the outer except aborts the whole loop, which is
                # exactly the self-reinforcing whole-corpus outage Iris's repro demonstrated
                # across 3 consecutive runs.
                conn.rollback()
                files_errored += 1
                if len(errored_paths) < 5:
                    errored_paths.append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            if outcome is None:
                files_skipped += 1
                continue
            c, r, t, bad = outcome
            files_read += 1
            calls += c
            results += r
            texts += t
            malformed += bad
            if c or r or t:
                conn.commit()
        conn.commit()
        sessions_dimensioned = refresh_dim_session(conn)
        # ATLASSN-194: same self-healing reconciliation subagent_pull.run() now does, for
        # session_tool_result specifically -- see reconcile_deny_text()'s own docstring
        # (subagent_pull.py) for the full incident. session_tool_result showed the identical
        # gap (757 affected rows measured) even though the ticket's own repro named only the
        # subagent side.
        reconciled = reconcile_deny_text(conn, "session_tool_result", "session_transcript")
        # ATLASSN-197 (Nadia Osei's real STRIDE/validate finding, Build 8). This used to be
        # hardcoded to 0 -- session_pull had NO credential scan of any kind, confirmed by direct
        # read, even though this source stores the exact same class of verbatim tool input as the
        # subagent side (ARCHITECTURE.md section 20's own point: identical record shape, only
        # discovery/identity differ). Real scan now, same shared implementation the subagent side
        # uses, over session_tool_call.tool_input_json AND session_tool_result.deny_text.
        credential_hits = scan_stored_tool_inputs_for_credentials(
            conn, call_table="session_tool_call", result_table="session_tool_result",
            ack_table="session_credential_ack")
    except Exception as exc:
        if own_run:
            conn.execute(
                "UPDATE subagent_pull_run SET finished_at = ?, status = 'error', detail = ? "
                "WHERE pull_run_id = ?",
                (nowIso(), f"session_pull {type(exc).__name__}: {exc}", pull_run_id),
            )
            conn.commit()
        conn.close()
        raise

    if own_run:
        detail = (f"session_pull: {files_read} of {len(files)} sessions, {calls} calls, "
                  f"{reconciled['rows_updated']} deny_text rows reconciled "
                  f"({reconciled['candidates_checked']} candidates checked, "
                  f"{reconciled['lines_unreadable']} source lines unreadable), "
                  f"{credential_hits} credential hits")
        if files_errored:
            # ATLASSN-193: a per-file crash no longer aborts the run (files_errored counts it and
            # the loop continues), but it must still be VISIBLE -- a run that silently swallowed
            # every per-file exception would manufacture exactly the "correct silence" this
            # project's own standing discipline (verdict_ledger.py's module docstring, a different
            # component, same principle) warns against for a guard, and the same logic applies to
            # a pull. No new column: subagent_pull_run's schema is unchanged, so this rides in the
            # existing free-text detail field rather than adding one for a first occurrence.
            detail += (f"; {files_errored} file(s) errored and were skipped (rolled back, not "
                      f"committed): {'; '.join(errored_paths)}"
                      + ("; ..." if files_errored > len(errored_paths) else ""))
        conn.execute(
            "UPDATE subagent_pull_run SET finished_at = ?, status = 'ok', files_seen = ?, "
            "files_read = ?, calls_inserted = ?, results_inserted = ?, lines_malformed = ?, "
            "credential_hits = ?, detail = ? WHERE pull_run_id = ?",
            (nowIso(), len(files), files_read, calls, results, malformed, credential_hits,
             detail, pull_run_id),
        )
        conn.commit()
    conn.close()
    return {
        "pull_run_id": pull_run_id,
        "sessions_dimensioned": sessions_dimensioned,
        "files_seen": len(files),
        "files_read": files_read,
        "files_skipped": files_skipped,
        "files_errored": files_errored,
        "calls_inserted": calls,
        "results_inserted": results,
        "texts_inserted": texts,
        "lines_malformed": malformed,
        "credential_hits": credential_hits,
    }


def rebuild(warehouse_db_path=DEFAULT_WAREHOUSE_DB):
    """Clears ingested main-session rows and resets every watermark. Same rationale as the
    subagent pull's: this data is cheap to re-derive, and a schema change that adds a column
    leaves existing rows holding a default rather than the real value.

    ATLASSN-104: session_block_sequence added to the clear list, children-before-parents, same
    position as the other three tables keyed on transcript_id -- it references
    session_transcript(transcript_id), so it must go before that DELETE.

    ATLASSN-197: session_credential_ack added the same way subagent_pull.rebuild() already
    carries subagent_credential_ack -- children before parents (it references
    session_tool_call(call_id), so it must go before that DELETE), and its acknowledgements are
    SAVED here by payload_sha256 for restore_credential_acks() to re-attach after the pull, same
    mechanism and same reason: call_id is an autoincrement rowid that does not survive a rebuild."""
    conn = migrate.connect(str(warehouse_db_path))
    before = conn.execute("SELECT COUNT(*) FROM session_tool_call").fetchone()[0]
    saved_acks = conn.execute(
        "SELECT payload_sha256, pattern_name, verdict, rationale, acknowledged_by, "
        "acknowledged_at FROM session_credential_ack"
    ).fetchall()
    conn.execute("DELETE FROM session_credential_ack")
    conn.execute("DELETE FROM session_tool_result")
    conn.execute("DELETE FROM session_assistant_text")
    conn.execute("DELETE FROM session_tool_call")
    conn.execute("DELETE FROM session_block_sequence")
    conn.execute("DELETE FROM session_transcript")
    conn.commit()
    conn.close()
    return {"calls_cleared": before, "saved_acks": [tuple(r) for r in saved_acks]}


def restore_credential_acks(warehouse_db_path, saved_acks):
    """ATLASSN-197. Session-side analog of subagent_pull.restore_credential_acks(): re-attaches
    acknowledgements rebuild() saved, matching on payload_sha256 (stable, a hash of the content)
    rather than the call_id they were originally recorded against (an autoincrement rowid that
    does not survive a rebuild). Matches against BOTH session_tool_call.tool_input_json and
    session_tool_result.deny_text -- either could be the payload that was originally
    acknowledged, and this function has no way to know which without the hash lookup itself
    telling it. Returns (restored, orphaned)."""
    if not saved_acks:
        return 0, 0
    conn = migrate.connect(str(warehouse_db_path))
    by_hash = {}
    for call_id, payload in conn.execute(
            "SELECT call_id, tool_input_json FROM session_tool_call "
            "WHERE tool_input_json IS NOT NULL"):
        by_hash.setdefault(hashlib.sha256(payload.encode("utf-8")).hexdigest(), []).append(call_id)
    for call_id, deny_text in conn.execute("""
            SELECT c.call_id, r.deny_text
            FROM session_tool_result r
            JOIN session_tool_call c
                   ON c.transcript_id = r.transcript_id AND c.tool_use_id = r.tool_use_id
            WHERE r.deny_text IS NOT NULL"""):
        by_hash.setdefault(hashlib.sha256(deny_text.encode("utf-8")).hexdigest(), []).append(call_id)
    restored = 0
    orphaned = 0
    for digest, pattern_name, verdict, rationale, acknowledged_by, acknowledged_at in saved_acks:
        call_ids = by_hash.get(digest)
        if not call_ids:
            orphaned += 1
            continue
        for call_id in set(call_ids):
            conn.execute(
                "INSERT OR REPLACE INTO session_credential_ack (call_id, payload_sha256, "
                "pattern_name, verdict, rationale, acknowledged_by, acknowledged_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (call_id, digest, pattern_name, verdict, rationale, acknowledged_by,
                 acknowledged_at),
            )
            restored += 1
    conn.commit()
    conn.close()
    return restored, orphaned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-db", default=str(DEFAULT_WAREHOUSE_DB))
    parser.add_argument("--projects-root", default=str(DEFAULT_PROJECTS_ROOT))
    parser.add_argument("--rebuild", action="store_true",
                        help="clear ingested main-session rows and re-read from zero")
    args = parser.parse_args()

    if args.rebuild:
        cleared = rebuild(Path(args.warehouse_db))
        print(f"[session_pull] rebuild: cleared {cleared['calls_cleared']} tool calls")

    summary = run(Path(args.warehouse_db), Path(args.projects_root))
    print(f"[session_pull] warehouse={args.warehouse_db} pull_run_id={summary['pull_run_id']}")
    print(f"[session_pull] dim_session: {summary['sessions_dimensioned']} rows")
    print(f"[session_pull] sessions: {summary['files_read']} read of "
          f"{summary['files_seen']} discovered ({summary['files_skipped']} skipped, "
          f"{summary['files_errored']} errored)")
    print(f"[session_pull] inserted: {summary['calls_inserted']} tool calls, "
          f"{summary['results_inserted']} tool results, "
          f"{summary['texts_inserted']} assistant texts, "
          f"{summary['lines_malformed']} malformed lines skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
