"""ATLASSN-27 / ATLASSN-28: the live plane's ingest source -- subagent transcript JSONL.

ARCHITECTURE.md section 18 is the design. The short version of why this file exists: a Foreman
parent session dispatched a background subagent, the operator caught that subagent's own commands
tripping the Bash permission classifier, and the parent had no way to inspect what its subagent
had actually done. Two separate gaps produced that. This file closes the first one -- the data was
never ingested at all, not ingested late. The second (six-hour cadence) is closed by the launchd
job at scripts/atlas_subagent_pull_cron.sh, which runs this module.

Measured on the real corpus, 2026-08-26, before any of this was written: 1,526 real agent-*.jsonl
transcripts, 628 MB, 28,890 tool_use blocks. Of 24,380 distinct tool_use_id values in them, only
9,736 (40%) appear in hook_verdict -- the other 60% are Read, Glob, Grep, SendMessage and every
Bash call no registered hook matched. Those are invisible to ATLAS at any cadence without this.

Runnable standalone, which is also the zero-staleness path a parent can use instead of waiting
for the next 60-second tick:

    /Users/m5/.venv/bin/python3 -m atlas.ingest.subagent_pull
    /Users/m5/.venv/bin/python3 -m atlas.ingest.subagent_pull --warehouse-db /path/to/atlas.db

Three things this deliberately does NOT do, each for a reason recorded in section 18:
  - It writes no ingest_run row. v_atlas_status selects MAX(run_id) FROM ingest_run and the query
    facade refuses everything unless that row reports checks_evaluated > 0, so a 60-second job
    that created one without running the 19 dq checks would refuse all 18 gated views, every
    minute, forever (section 18.3).
  - It does not reuse ingest_source for watermarks. That table's resume state is keyed by
    source_name alone and the batch pipeline's rotation accounting reads it; 1,526 files sharing
    it would make every pull look like a rotation of the others. Watermarks live on
    subagent_transcript instead.
  - It does not use stream.tail(). tail() is one file, one watermark row, one mapper returning one
    row per line -- this source is many files and many rows per line (a single assistant message
    can carry several tool_use blocks). The discipline is reused; the function is not.
"""

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

from atlas.warehouse import dq_runner, migrate

# ATLASSN-33: the line parsing, stream identity, partial-line discipline and deny extraction moved
# to transcript_parse so the main-session pull uses the SAME implementation rather than a second
# one written to the same spec. Re-exported here because every existing caller and test names them
# on this module, and a rename would be churn unrelated to the change that motivated the move.
from .transcript_parse import (  # noqa: F401
    ASSISTANT_TEXT_CAP_BYTES,
    DENY_BODY_PREFIXES,
    DENY_TEXT_CAP_BYTES,
    STREAM_ID_HEAD_BYTES,
    extract_deny_text,
    map_transcript_line,
    split_complete_lines,
    stream_id_of_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAREHOUSE_DB = REPO_ROOT / "atlas" / "warehouse" / "atlas.db"
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


# The reference implementation cited in ATLASSN-27 (grok/hook-gaming/extract-deny-next-fixed.py's
# session_files()) globs "*.jsonl" ONE level under subagents/. Measured against the real corpus,
# that returns 1,234 of 1,526 files: it drops every Workflow-dispatched transcript, which lives at
# <session>/subagents/workflows/<wf_id>/, and it picks up 9 workflow journal.jsonl files that are
# not transcripts at all. Recursive, and restricted to the agent-* naming convention, for both
# reasons. Copying the reference would have silently lost 20% of the corpus.
TRANSCRIPT_GLOB = "agent-*.jsonl"

# agent-<hex>.jsonl is a real dispatched subagent and has a meta.json sidecar.
# agent-aside_question-<hex>.jsonl and agent-acompact-<hex>.jsonl are neither dispatched by a
# parent nor attributable to one, and account for all 56 real sidecar-less transcripts. They are
# stored with their kind recorded rather than filtered out, so their absence from the attributed
# set is a fact in the data instead of a silent exclusion.
AGENT_KIND_PATTERNS = (
    (re.compile(r"^agent-aside_question-"), "aside_question"),
    (re.compile(r"^agent-acompact-"), "compact"),
)

# ATLASSN-32. Assistant text blocks are where an agent states intent, and the letter-versus-intent
# rubric labels from it directly. Measured on the real corpus: 9,310 blocks, 13.0 MB total, one
# outlier at 484 KB. Capped per block rather than in total, with an explicit truncated flag, so a
# truncated block is never mistaken for a short one. The median block is far below this.


class SubagentPullError(RuntimeError):
    pass


def nowIso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def ledger_deny_ids(conn):
    """Every tool_use_id the verdict ledger already calls a deny.

    Loaded once per pull and passed into the parser so a result the ledger calls a deny yields its
    named remedy even when no prefix matches -- rule_frame_probe.py composes situation-specific
    prose, so 68 real denies had no remedy text until this route existed. The prefix route stays
    separate and keeps its own flag; this is the authoritative one."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT tool_use_id FROM hook_verdict "
        "WHERE tool_use_id IS NOT NULL AND decision = 'deny'")}


def reconcile_deny_text(conn, result_table, transcript_table):
    """ATLASSN-194. Re-derives deny_text for any `result_table` row still NULL because the
    corresponding hook_verdict deny row had not yet been ingested at the moment this line was
    ORIGINALLY pulled.

    ROOT CAUSE, measured directly against the real live warehouse rather than assumed: this is
    NOT a prefix-shape miss and NOT a bug in map_transcript_line's own extraction logic (proven
    correct by calling it directly against the real affected record with the correct
    ledger_deny_ids set -- it returns the right deny_text immediately). `ledger_deny_ids()` is a
    ONE-SHOT snapshot taken once per pull run (subagent_pull.run()/session_pull.run() each call it
    exactly once, before iterating files) -- but the verdict-ledger-to-hook_verdict ingest path is
    a SEPARATE pipeline with its own, independent, sometimes much slower cadence. Measured: for
    ALL 288 affected subagent_tool_result rows and ALL 757 affected session_tool_result rows
    (100% in both cases, not a majority), the row's own pull_run.started_at was strictly BEFORE
    the ingest_run.started_at of whatever batch actually inserted the corresponding hook_verdict
    row -- one real example measured directly: the transcript line was first_seen_at 37 SECONDS
    after the real deny event, but the matching hook_verdict row was not inserted until an
    ingest_run that started over 3 HOURS later. subagent_pull's byte_offset is monotonic and never
    revisits a consumed line, so a deny whose verdict-ledger ingestion lags the transcript pull is
    missed PERMANENTLY under the original one-shot-snapshot design -- not for write_gate.py/
    agent_dispatch_gate.py specifically (the ticket's own working title), but for ANY handler
    whose verdict lands late relative to the transcript pull. Confirmed: session_tool_result
    shows the identical shape (757 affected rows) even though the ticket's own repro named only
    the subagent side -- same shared map_transcript_line, same ledger_deny_ids staleness, same
    fix needed in both places. This function is that one shared fix, not a subagent-specific one.

    Self-healing AND retroactive in one mechanism, deliberately, rather than a separate one-time
    backfill script: called every pull tick (wired into both subagent_pull.run() and
    session_pull.run(), right after the main per-file loop, using a FRESH ledger_deny_ids()
    query -- not the stale one from the top of this same run), it finds every row that is STILL
    NULL and asks 'does hook_verdict confirm a real deny for this tool_use_id NOW' -- true for
    both a decades-old historical gap and a gap from thirty seconds ago whose verdict just
    landed. The existing 288+757 rows are recovered on the very next tick after this lands, with
    no separate backfill script to remember to run, and any FUTURE occurrence of this same race
    (a different handler, tomorrow) self-heals on whichever later tick finally sees the verdict.

    Never touches is_hook_deny -- that flag means specifically 'the prefix route matched', and
    reconciliation only ever fills deny_text via the ledger-fallback route, never retroactively
    claims a prefix match that never happened (same "two routes, kept distinguishable on purpose"
    design transcript_parse.py's own docstring already states). Matches on the exact
    (transcript_id, tool_use_id, byte_offset) triple the row was inserted under and requires
    deny_text IS NULL, so it can never touch a row it did not come from and can never overwrite a
    value already written correctly -- the same discipline backfill_ledger_origin.py already
    established for an analogous NULL-recovery case.

    Cheap to call unconditionally on every tick: the candidate query is bounded to rows that are
    STILL NULL with a NOW-existing hook_verdict deny, and once caught up this returns zero rows
    on almost every call, at negligible cost. `result_table`/`transcript_table` let this one
    function serve both subagent_tool_result/subagent_transcript and session_tool_result/
    session_transcript, which share this exact result-row shape and the same source-line format
    -- reused, not duplicated, matching this module's own map_transcript_line precedent.

    Returns a dict: candidates_checked, rows_updated, lines_unreadable (the source transcript
    file is no longer readable at the recorded byte_offset -- e.g. deleted or rotated away; rare,
    counted rather than silently skipped, and NOT retried again next tick since the underlying
    condition -- an unreadable source file -- will not have changed by the next call either)."""
    fresh_deny_ids = ledger_deny_ids(conn)
    candidates = conn.execute(f"""
        SELECT r.transcript_id, r.tool_use_id, r.byte_offset, t.source_path
        FROM {result_table} r
        JOIN {transcript_table} t ON t.transcript_id = r.transcript_id
        WHERE r.deny_text IS NULL AND r.is_hook_deny = 0
          AND EXISTS (SELECT 1 FROM hook_verdict hv
                       WHERE hv.tool_use_id = r.tool_use_id AND hv.decision = 'deny')
    """).fetchall()
    rows_updated = 0
    lines_unreadable = 0
    for transcript_id, tool_use_id, byte_offset, source_path in candidates:
        try:
            with open(source_path, "rb") as fh:
                fh.seek(byte_offset)
                line = fh.readline()
            obj = json.loads(line)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            lines_unreadable += 1
            continue
        _calls, results, _texts, _blocks = map_transcript_line(obj, byte_offset, fresh_deny_ids)
        match = next((r for r in results if r["tool_use_id"] == tool_use_id), None)
        if match is None or match["deny_text"] is None:
            continue
        cur = conn.execute(
            f"UPDATE {result_table} SET deny_text = ? "
            f"WHERE transcript_id = ? AND tool_use_id = ? AND byte_offset = ? "
            f"AND deny_text IS NULL",
            (match["deny_text"], transcript_id, tool_use_id, byte_offset),
        )
        rows_updated += cur.rowcount
    conn.commit()
    return {
        "candidates_checked": len(candidates),
        "rows_updated": rows_updated,
        "lines_unreadable": lines_unreadable,
    }


def discover_transcripts(projects_root=DEFAULT_PROJECTS_ROOT):
    """Every real subagent transcript under projects_root, at any depth below a subagents/
    directory. Returns a sorted list of Paths -- sorted so a run's file order is deterministic
    and a diff between two runs is readable.

    A projects_root that does not exist is an empty list, not a crash: this runs every 60
    seconds on a machine where the directory could legitimately be absent, and the F3 failure
    signature for a missing real source in this component is a named, reported skip."""
    root = Path(projects_root)
    if not root.is_dir():
        return []
    hits = []
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        for session_dir in project_dir.iterdir():
            if not session_dir.is_dir():
                continue
            subagents = session_dir / "subagents"
            if subagents.is_dir():
                hits.extend(subagents.rglob(TRANSCRIPT_GLOB))
    return sorted(hits)


def classify_agent_kind(filename):
    for pattern, kind in AGENT_KIND_PATTERNS:
        if pattern.match(filename):
            return kind
    return "agent"


def parse_transcript_path(path, projects_root=DEFAULT_PROJECTS_ROOT):
    """Pulls the identity a transcript's own location encodes: which project directory, which
    parent session, which agent, and -- when the transcript sits under subagents/workflows/<wf> --
    which workflow. Returns a dict, or None if the path is not shaped like a transcript at all."""
    try:
        rel = Path(path).resolve().relative_to(Path(projects_root).resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    # <project_dir>/<session_id>/subagents/[workflows/<wf_id>/]<agent-*.jsonl>
    if len(parts) < 4 or parts[2] != "subagents":
        return None
    workflow_id = None
    if len(parts) == 6 and parts[3] == "workflows":
        workflow_id = parts[4]
    elif len(parts) != 4:
        return None
    filename = parts[-1]
    stem = filename[: -len(".jsonl")] if filename.endswith(".jsonl") else filename
    return {
        "project_dir": parts[0],
        "parent_session_id": parts[1],
        "workflow_id": workflow_id,
        "agent_id": stem[len("agent-"):] if stem.startswith("agent-") else stem,
        "agent_kind": classify_agent_kind(filename),
    }


def read_sidecar(transcript_path):
    """The agent-<id>.meta.json beside a transcript carries agentType, description, spawnDepth,
    and -- the one that matters -- toolUseId: the id of the PARENT's own Agent tool call. That is
    the join back to the dispatching session, and it is why this source answers "what did MY
    subagent do" rather than merely logging subagent activity in general.

    Returns (meta_state, meta_dict). 1,470 of 1,526 real transcripts have one; the 56 that do not
    are the aside_question and compact agents, which were never dispatched by a parent and so
    have no dispatch to point at. Absent is a recorded state, never a skipped transcript, and
    never an invented attribution."""
    sidecar = Path(transcript_path).parent / (Path(transcript_path).stem + ".meta.json")
    if not sidecar.is_file():
        return "absent", {}
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # Unreadable is its OWN state, distinct from absent: absent means there was never a
        # dispatch to record, unreadable means there was one and this pull could not read it.
        # Collapsing them would report a real read failure as a structural property of the file.
        return "unreadable", {}
    if not isinstance(meta, dict):
        return "unreadable", {}
    return "present", meta




def upsert_transcript(conn, path, identity, meta_state, meta, stream_id):
    """Returns (transcript_id, resume_byte_offset). A transcript whose first 4096 bytes changed
    is treated as a new stream and re-read from zero, same rotation rule stream.py applies --
    though a subagent transcript is append-only in practice, so this fires on a genuinely
    replaced file rather than on ordinary growth."""
    now = nowIso()
    attribution = {
        "agent_type": meta.get("agentType"),
        "dispatch_description": meta.get("description"),
        "parent_tool_use_id": meta.get("toolUseId"),
        "spawn_depth": meta.get("spawnDepth"),
    }
    if meta_state != "present":
        # The table's own CHECK constraint enforces this too; doing it here as well means the
        # code and the schema agree rather than the code relying on the schema to catch it.
        attribution = {k: None for k in attribution}

    row = conn.execute(
        "SELECT transcript_id, stream_id, byte_offset FROM subagent_transcript "
        "WHERE source_path = ?", (str(path),)
    ).fetchone()

    if row is None:
        cur = conn.execute(
            "INSERT INTO subagent_transcript (source_path, stream_id, project_dir, "
            "parent_session_id, agent_id, agent_kind, workflow_id, agent_type, "
            "dispatch_description, parent_tool_use_id, spawn_depth, meta_state, byte_offset, "
            "calls_ingested, first_seen_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
            (str(path), stream_id, identity["project_dir"], identity["parent_session_id"],
             identity["agent_id"], identity["agent_kind"], identity["workflow_id"],
             attribution["agent_type"], attribution["dispatch_description"],
             attribution["parent_tool_use_id"], attribution["spawn_depth"], meta_state,
             now, now),
        )
        return cur.lastrowid, 0

    transcript_id, prior_stream_id, prior_offset = row
    resume_offset = 0 if prior_stream_id != stream_id else prior_offset
    conn.execute(
        "UPDATE subagent_transcript SET stream_id = ?, agent_type = ?, dispatch_description = ?, "
        "parent_tool_use_id = ?, spawn_depth = ?, meta_state = ?, updated_at = ? "
        "WHERE transcript_id = ?",
        (stream_id, attribution["agent_type"], attribution["dispatch_description"],
         attribution["parent_tool_use_id"], attribution["spawn_depth"], meta_state, now,
         transcript_id),
    )
    return transcript_id, resume_offset


def pull_transcript(conn, path, pull_run_id, projects_root=DEFAULT_PROJECTS_ROOT,
                    deny_ids=frozenset()):
    """Reads one transcript from its stored watermark forward. Returns
    (calls_inserted, results_inserted, lines_malformed), or None if the path is not a transcript.

    Never raises on a malformed line -- a subagent transcript being written while this reads it
    is the normal case, not an exception. A duplicate (transcript_id, byte_offset, block_index)
    DOES raise, deliberately: that means the watermark arithmetic re-read bytes it had already
    committed, which is a real bug that should fail a test loudly rather than be deduplicated
    away into a test that passes for the wrong reason."""
    identity = parse_transcript_path(path, projects_root)
    if identity is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None

    stream_id = stream_id_of_path(p)
    if stream_id is None:
        # The first record is still being written, so there is no stable identity yet -- and no
        # complete line to ingest either. Skipped, counted, picked up on the next tick.
        return None
    meta_state, meta = read_sidecar(p)
    transcript_id, start_offset = upsert_transcript(
        conn, p, identity, meta_state, meta, stream_id
    )

    size = p.stat().st_size
    if size <= start_offset:
        # Nothing new. A file that SHRANK below its watermark is a truncation; the stream_id
        # check above already re-read it from zero if its head changed, and if the head is
        # identical there is nothing new to read regardless.
        conn.execute(
            "UPDATE subagent_transcript SET updated_at = ? WHERE transcript_id = ?",
            (nowIso(), transcript_id),
        )
        return 0, 0, 0, 0

    with open(p, "rb") as fh:
        fh.seek(start_offset)
        new_data = fh.read()

    complete_lines, consumed = split_complete_lines(new_data)

    calls_inserted = 0
    results_inserted = 0
    texts_inserted = 0
    malformed = 0
    offset = start_offset
    for line in complete_lines:
        this_offset = offset
        offset += len(line) + 1
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            malformed += 1
            continue
        if not isinstance(obj, dict):
            malformed += 1
            continue
        # ATLASSN-104: map_transcript_line now returns a fourth list, blocks -- structural
        # session_block_sequence rows, session-transcript scope only (ARCHITECTURE.md section 40
        # names the table session_block_sequence specifically, not a subagent-parallel table).
        # Discarded here rather than threaded through: this pull's four content tables
        # (subagent_transcript/tool_call/tool_result/assistant_text) are unchanged, and adding a
        # subagent-side block-sequence table is explicitly out of this ticket's scope -- flagged
        # in the proposal rationale for whoever picks that up next, not silently done or dropped.
        calls, results, texts, _blocks = map_transcript_line(obj, this_offset, deny_ids)
        for text in texts:
            conn.execute(
                "INSERT INTO subagent_assistant_text (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, text, text_bytes, truncated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, text["byte_offset"], text["block_index"], pull_run_id,
                 text["ts"], text["record_uuid"], text["text"], text["text_bytes"],
                 text["truncated"]),
            )
            texts_inserted += 1
        for call in calls:
            conn.execute(
                "INSERT INTO subagent_tool_call (transcript_id, byte_offset, block_index, "
                "pull_run_id, ts, record_uuid, session_id, agent_id, cwd, git_branch, "
                "tool_use_id, tool_name, tool_input_json, tool_input_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, call["byte_offset"], call["block_index"], pull_run_id,
                 call["ts"], call["record_uuid"], call["session_id"], call["agent_id"],
                 call["cwd"], call["git_branch"], call["tool_use_id"], call["tool_name"],
                 call["tool_input_json"], call["tool_input_bytes"]),
            )
            calls_inserted += 1
        for result in results:
            # OR IGNORE here and NOT on the call insert, and the asymmetry is deliberate. A
            # duplicate CALL means the watermark re-read committed bytes -- a real bug. A
            # duplicate RESULT is ordinary: the same tool_use_id can legitimately appear in more
            # than one result block within a transcript, and the first one is the one that
            # matters. Silencing the first would hide a bug; silencing the second is correct.
            cur = conn.execute(
                "INSERT OR IGNORE INTO subagent_tool_result (transcript_id, tool_use_id, "
                "byte_offset, pull_run_id, ts, is_error, result_bytes, is_hook_deny, "
                "deny_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, result["tool_use_id"], result["byte_offset"], pull_run_id,
                 result["ts"], result["is_error"], result["result_bytes"],
                 result["is_hook_deny"], result["deny_text"]),
            )
            results_inserted += cur.rowcount

    new_offset = start_offset + consumed
    conn.execute(
        "UPDATE subagent_transcript SET byte_offset = ?, "
        "calls_ingested = calls_ingested + ?, updated_at = ? WHERE transcript_id = ?",
        (new_offset, calls_inserted, nowIso(), transcript_id),
    )
    return calls_inserted, results_inserted, texts_inserted, malformed


def _unacknowledged_credential_hits(conn, ack_table, candidates):
    """ATLASSN-197. Shared by every credential-pattern scan in this module (subagent/session,
    tool_input_json/deny_text): candidates is a list of (call_id_or_None, payload_text) pairs.
    Returns how many match a dq_runner.CREDENTIAL_PATTERNS entry AND are not already
    acknowledged in ack_table for that EXACT payload -- the same "acknowledgement binds to a hash
    of the exact payload triaged" discipline scan_stored_tool_inputs_for_credentials already
    established, factored out so a new payload source does not need a second implementation.

    call_id=None (a deny_text hit whose tool_use_id has no resolvable call row -- rare, and only
    possible if a result somehow lands with no matching call) can never be acknowledged and is
    always counted when it matches: unconditional counting is the safe default for a row nothing
    can bind an acknowledgement to."""
    acked = dict(conn.execute(f"SELECT call_id, payload_sha256 FROM {ack_table}")) if candidates \
        else {}
    hits = 0
    for call_id, payload in candidates:
        if not payload:
            continue
        for pattern in dq_runner.CREDENTIAL_PATTERNS.values():
            if re.search(pattern, payload):
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                if call_id is None or acked.get(call_id) != digest:
                    hits += 1
                break
    return hits


def scan_stored_tool_inputs_for_credentials(conn, call_table="subagent_tool_call",
                                            result_table="subagent_tool_result",
                                            ack_table="subagent_credential_ack"):
    """ARCHITECTURE.md section 18.2: this source is classified secret-bearing because it stores
    verbatim tool inputs -- every Bash command a subagent ran. This project's own recorded
    incident (a Gemini key typed as a literal `export KEY=value`) lands here exactly as it lands
    in the audit plane, so the same obligation applies, and it reuses dq_runner's own
    CREDENTIAL_PATTERNS rather than being a second implementation of the same idea.

    ATLASSN-197 (Nadia Osei's real STRIDE/validate finding, Build 8). This used to scan ONLY
    {call_table}.tool_input_json -- {result_table}.deny_text, the full verbatim tool_result body
    of a denied call (ARCHITECTURE.md section 19.2: "a denied tool_result body IS the remedy
    text, entire"), was never covered, even though a real leak path into it was traced directly:
    claude-hooks-v2's agent_dispatch_gate.py embeds up to 60 characters of the caller-supplied
    dispatch prompt or description into its own hc.deny() message, which becomes the denied
    tool_result body, which becomes deny_text. Compounding this: ATLASSN-194's reconcile_deny_text
    actively BACKFILLS this exact unscanned column at scale on every tick, so the gap was
    self-reinforcing rather than a one-time miss. Now scans both sources every call, using ONE
    shared table-parameterised implementation (call_table/result_table/ack_table) rather than a
    second, separately-maintained function for the session side -- same reuse discipline
    reconcile_deny_text() already established for the parsing side of this exact problem.

    A deny_text hit is resolved to its call_id via the (transcript_id, tool_use_id) join every
    other deny-aware view in this schema already uses for that same purpose (see
    v_transcript_deny_join, ARCHITECTURE.md section 20.5), and acknowledged through the SAME
    ack_table as a tool_input_json hit on that call -- not a second table per source. Disclosed
    trade-off, not a silent gap: on the rare call_id where BOTH sources independently match a
    credential pattern with DIFFERENT payload text, acknowledging one overwrites the stored hash
    for the other and re-arms it on the next scan. That fails toward MORE scrutiny, never less --
    it can only make a previously-silenced hit visible again, never silence a hit that was never
    acknowledged -- so it is accepted rather than solved with a second ack table per source (see
    session_credential_ack's own migration comment, atlas/warehouse/migrations/
    0026_session_credential_ack.sql, for the same disclosure on the session side).

    Scans EVERY stored row, not the pull's delta -- same reason as always: a delta-only scan goes
    fail-closed for one tick and fail-open forever after. Counts only UNACKNOWLEDGED hits, for the
    reason section 8 states and this function's own history already proved (8/8 real first-pull
    hits were placeholder text a subagent wrote into a script it was authoring). Returns the
    unacknowledged hit count, tool_input_json and deny_text combined."""
    input_rows = conn.execute(
        f"SELECT call_id, tool_input_json FROM {call_table} WHERE tool_input_json IS NOT NULL"
    ).fetchall()
    deny_rows = conn.execute(f"""
        SELECT c.call_id, r.deny_text
        FROM {result_table} r
        LEFT JOIN {call_table} c
               ON c.transcript_id = r.transcript_id AND c.tool_use_id = r.tool_use_id
        WHERE r.deny_text IS NOT NULL
    """).fetchall()
    return _unacknowledged_credential_hits(
        conn, ack_table, list(input_rows) + list(deny_rows))


def _upsert_credential_ack(conn, ack_table, call_id, payload, pattern_name, verdict, rationale,
                           acknowledged_by):
    if payload is None:
        raise SubagentPullError(
            f"call_id {call_id} has no stored payload for this source -- refusing to record an "
            f"acknowledgement that binds to nothing"
        )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    conn.execute(
        f"INSERT INTO {ack_table} (call_id, payload_sha256, pattern_name, verdict, "
        f"rationale, acknowledged_by, acknowledged_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        f"ON CONFLICT(call_id) DO UPDATE SET payload_sha256=excluded.payload_sha256, "
        f"pattern_name=excluded.pattern_name, verdict=excluded.verdict, "
        f"rationale=excluded.rationale, acknowledged_by=excluded.acknowledged_by, "
        f"acknowledged_at=excluded.acknowledged_at",
        (call_id, digest, pattern_name, verdict, rationale, acknowledged_by, nowIso()),
    )
    conn.commit()
    return digest


def acknowledge_credential_hit(conn, call_id, pattern_name, verdict, rationale,
                               acknowledged_by, call_table="subagent_tool_call",
                               ack_table="subagent_credential_ack"):
    """Record a triaged credential hit on a call's own tool_input_json, so it stops closing the
    live gate.

    Binds the acknowledgement to a hash of the payload as it stands right now. That is not
    ceremony: an acknowledgement keyed on call_id alone would keep silencing that row even if its
    stored tool input later changed, which is a blanket off-switch wearing the costume of a triage
    record. Raises rather than recording an acknowledgement that binds to nothing.

    ATLASSN-197: call_table/ack_table let this same function serve session_tool_call/
    session_credential_ack too -- the first five positional arguments are UNCHANGED from before
    this ticket, so every existing call site keeps working with no edit needed. Acknowledging a
    call's OWN deny_text hit (rather than its tool_input_json) is
    acknowledge_deny_credential_hit(), a separate function below, since the two sources need
    different lookup SQL to find the payload actually being triaged."""
    row = conn.execute(
        f"SELECT tool_input_json FROM {call_table} WHERE call_id = ?", (call_id,)
    ).fetchone()
    if row is None:
        raise SubagentPullError(
            f"call_id {call_id} has no stored tool input -- refusing to record an "
            f"acknowledgement that binds to nothing"
        )
    return _upsert_credential_ack(
        conn, ack_table, call_id, row[0], pattern_name, verdict, rationale, acknowledged_by)


def acknowledge_deny_credential_hit(conn, transcript_id, tool_use_id, pattern_name, verdict,
                                    rationale, acknowledged_by, call_table="subagent_tool_call",
                                    result_table="subagent_tool_result",
                                    ack_table="subagent_credential_ack"):
    """ATLASSN-197. The deny_text-side analog of acknowledge_credential_hit(): triages a
    credential-pattern hit on {result_table}.deny_text rather than on the call's own
    tool_input_json. Resolved to the call's own call_id via the same (transcript_id, tool_use_id)
    join scan_stored_tool_inputs_for_credentials() uses, and recorded in the SAME ack_table a
    tool_input_json hit on that call would use -- see scan_stored_tool_inputs_for_credentials()'s
    own docstring for the disclosed, accepted trade-off this shared-table design carries. Raises
    if the (transcript_id, tool_use_id) pair has no resolvable call row at all -- the same "refuse
    to bind to nothing" discipline acknowledge_credential_hit() already applies, and the reason
    _unacknowledged_credential_hits() treats a call_id-less deny hit as permanently
    unacknowledgeable rather than silently skipped."""
    row = conn.execute(f"""
        SELECT c.call_id, r.deny_text
        FROM {result_table} r
        JOIN {call_table} c ON c.transcript_id = r.transcript_id AND c.tool_use_id = r.tool_use_id
        WHERE r.transcript_id = ? AND r.tool_use_id = ?
    """, (transcript_id, tool_use_id)).fetchone()
    if row is None:
        raise SubagentPullError(
            f"(transcript_id={transcript_id}, tool_use_id={tool_use_id!r}) has no resolvable "
            f"call row -- refusing to record an acknowledgement that binds to nothing"
        )
    call_id, deny_text = row
    return _upsert_credential_ack(
        conn, ack_table, call_id, deny_text, pattern_name, verdict, rationale, acknowledged_by)


def run(warehouse_db_path=DEFAULT_WAREHOUSE_DB, projects_root=DEFAULT_PROJECTS_ROOT):
    """One pull. Returns a summary dict.

    The subagent_pull_run row is opened as 'running' with finished_at NULL and closed at the end.
    v_subagent_live_status keys off the last FINISHED run precisely so this in-flight row does not
    dark the live view for the duration of every tick (section 18.5, FATAL-A in the architecture
    review). A pull that dies without closing its row therefore does not produce a distinct
    'incomplete' state -- the previous run's staleness simply grows past the 300-second bound,
    which is the fail-closed behaviour that was wanted anyway."""
    conn = migrate.connect(str(warehouse_db_path))
    cur = conn.execute(
        "INSERT INTO subagent_pull_run (started_at, status, atlas_version, host_id) "
        "VALUES (?, 'running', ?, ?)",
        (nowIso(), migrate.ATLAS_VERSION, migrate.hostId()),
    )
    pull_run_id = cur.lastrowid
    conn.commit()

    deny_ids = ledger_deny_ids(conn)
    files = discover_transcripts(projects_root)
    files_read = 0
    files_skipped = 0
    calls_inserted = 0
    results_inserted = 0
    texts_inserted = 0
    malformed = 0
    try:
        for path in files:
            outcome = pull_transcript(conn, path, pull_run_id, projects_root,
                                      deny_ids)
            if outcome is None:
                # A discovered path whose shape parse_transcript_path() does not recognise, or
                # that vanished between discovery and read. Counted, not silently passed over:
                # a discovery bug that started rejecting every path would otherwise show up as a
                # clean run that ingested nothing.
                files_skipped += 1
                continue
            calls, results, texts, bad = outcome
            files_read += 1
            calls_inserted += calls
            results_inserted += results
            texts_inserted += texts
            malformed += bad
            if calls or results or texts:
                # Commit per file rather than once at the end: a 60-second job holding one
                # transaction over 1,526 files would keep a reader on the previous snapshot for
                # the whole pull, and a cold backfill would hold it for the whole backfill.
                conn.commit()
        conn.commit()
        credential_hits = scan_stored_tool_inputs_for_credentials(conn)
        # ATLASSN-194: self-healing reconciliation for deny_text rows a stale ledger_deny_ids()
        # snapshot missed -- see reconcile_deny_text()'s own docstring for the full incident.
        # Cheap on every tick: near-zero candidates once caught up.
        reconciled = reconcile_deny_text(conn, "subagent_tool_result", "subagent_transcript")
        detail = (
            f"{files_read} of {len(files)} transcripts read ({files_skipped} skipped), "
            f"{calls_inserted} calls, {results_inserted} results, "
            f"{texts_inserted} assistant texts, {malformed} malformed lines, "
            f"{reconciled['rows_updated']} deny_text rows reconciled "
            f"({reconciled['candidates_checked']} candidates checked, "
            f"{reconciled['lines_unreadable']} source lines unreadable)"
        )
        conn.execute(
            "UPDATE subagent_pull_run SET finished_at = ?, status = 'ok', files_seen = ?, "
            "files_read = ?, calls_inserted = ?, results_inserted = ?, lines_malformed = ?, "
            "credential_hits = ?, detail = ? WHERE pull_run_id = ?",
            (nowIso(), len(files), files_read, calls_inserted, results_inserted, malformed,
             credential_hits, detail, pull_run_id),
        )
        conn.commit()
    except Exception as exc:
        # The run is closed as 'error' with finished_at SET, so v_subagent_live_status can see it
        # and report live-pull-failed. Leaving finished_at NULL would make a hard failure
        # indistinguishable from a pull that is merely still running, and the view would keep
        # serving the previous run's rows as live until staleness eventually caught up.
        conn.execute(
            "UPDATE subagent_pull_run SET finished_at = ?, status = 'error', files_seen = ?, "
            "files_read = ?, calls_inserted = ?, results_inserted = ?, lines_malformed = ?, "
            "detail = ? WHERE pull_run_id = ?",
            (nowIso(), len(files), files_read, calls_inserted, results_inserted, malformed,
             f"{type(exc).__name__}: {exc}", pull_run_id),
        )
        conn.commit()
        conn.close()
        raise

    deny_results = conn.execute(
        "SELECT COUNT(*) FROM subagent_tool_result WHERE is_hook_deny = 1").fetchone()[0]
    live_state = conn.execute("SELECT live_state FROM v_subagent_live_status").fetchone()[0]
    conn.close()
    return {
        "pull_run_id": pull_run_id,
        "files_seen": len(files),
        "files_read": files_read,
        "files_skipped": files_skipped,
        "calls_inserted": calls_inserted,
        "results_inserted": results_inserted,
        "texts_inserted": texts_inserted,
        "lines_malformed": malformed,
        "credential_hits": credential_hits,
        "deny_results": deny_results,
        "live_state": live_state,
    }


def rebuild(warehouse_db_path=DEFAULT_WAREHOUSE_DB):
    """Clears the live plane's ingested data and resets every watermark, so the next pull
    re-reads the whole corpus from zero.

    Exists because this data is cheap to re-derive -- about four seconds for the full corpus --
    and because a schema change that adds a column leaves existing rows holding a default rather
    than the real value. Backfilling in place would mean a second, differently-written parser for
    the same bytes; re-deriving means there is only ever one.

    Credential acknowledgements are carried across rather than discarded, and the mechanism
    matters. subagent_credential_ack is keyed by call_id, which is an autoincrement rowid that
    does NOT survive a rebuild -- the same tool input comes back under a different call_id. So
    the acknowledgements are saved by payload_sha256, which is stable because it is a hash of the
    content, and re-attached after the pull by matching that hash. An acknowledgement whose
    payload is no longer present anywhere simply does not come back, which is correct: it was
    triage of a row that no longer exists.

    subagent_pull_run history is left intact, so the record of what ran stays continuous.

    Returns the saved acknowledgements for restore_credential_acks() to re-apply."""
    conn = migrate.connect(str(warehouse_db_path))
    before = conn.execute("SELECT COUNT(*) FROM subagent_tool_call").fetchone()[0]
    saved = conn.execute(
        "SELECT payload_sha256, pattern_name, verdict, rationale, acknowledged_by, "
        "acknowledged_at FROM subagent_credential_ack"
    ).fetchall()
    # Children before parents. The ack table references subagent_tool_call, so it goes first.
    conn.execute("DELETE FROM subagent_credential_ack")
    conn.execute("DELETE FROM subagent_tool_result")
    conn.execute("DELETE FROM subagent_assistant_text")
    conn.execute("DELETE FROM subagent_tool_call")
    conn.execute("DELETE FROM subagent_transcript")
    conn.commit()
    conn.close()
    return {"calls_cleared": before, "saved_acks": [tuple(r) for r in saved]}


def restore_credential_acks(warehouse_db_path, saved_acks):
    """Re-attaches acknowledgements saved by rebuild(), matching on payload_sha256 rather than on
    the call_id they were originally recorded against. Returns (restored, orphaned).

    ATLASSN-197: matches against BOTH subagent_tool_call.tool_input_json and
    subagent_tool_result.deny_text now -- an acknowledgement saved before this ticket can only
    ever have come from tool_input_json (deny_text was never scanned), but one saved after it
    could be either, and this function has no way to know which without the hash lookup itself
    telling it."""
    if not saved_acks:
        return 0, 0
    conn = migrate.connect(str(warehouse_db_path))
    by_hash = {}
    for call_id, payload in conn.execute(
            "SELECT call_id, tool_input_json FROM subagent_tool_call "
            "WHERE tool_input_json IS NOT NULL"):
        by_hash.setdefault(hashlib.sha256(payload.encode("utf-8")).hexdigest(), []).append(call_id)
    for call_id, deny_text in conn.execute("""
            SELECT c.call_id, r.deny_text
            FROM subagent_tool_result r
            JOIN subagent_tool_call c
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
                "INSERT OR REPLACE INTO subagent_credential_ack (call_id, payload_sha256, "
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
    parser.add_argument(
        "--rebuild", action="store_true",
        help="clear ingested live-plane rows and re-read the whole corpus from zero (about "
             "4 seconds); use after a schema change that adds a column existing rows cannot have",
    )
    args = parser.parse_args()

    saved_acks = []
    if args.rebuild:
        cleared = rebuild(Path(args.warehouse_db))
        saved_acks = cleared["saved_acks"]
        print(f"[subagent_pull] rebuild: cleared {cleared['calls_cleared']} tool calls, "
              f"holding {len(saved_acks)} credential acknowledgements for re-attachment")

    summary = run(Path(args.warehouse_db), Path(args.projects_root))

    if saved_acks:
        restored, orphaned = restore_credential_acks(Path(args.warehouse_db), saved_acks)
        print(f"[subagent_pull] rebuild: re-attached {restored} credential acknowledgements "
              f"by payload hash, {orphaned} had no matching row and were dropped")
        # The pull's own run row recorded credential_hits BEFORE the acknowledgements came back,
        # and v_subagent_live_status reads that stored column rather than recomputing. Without
        # this the gate stays closed on a count that is already wrong -- observed on the first
        # real rebuild: 0 unacknowledged hits reported, live_state still live-credential-hit.
        # Rescan and correct the row, then re-read the gate.
        conn = migrate.connect(str(args.warehouse_db))
        summary["credential_hits"] = scan_stored_tool_inputs_for_credentials(conn)
        conn.execute(
            "UPDATE subagent_pull_run SET credential_hits = ? WHERE pull_run_id = ?",
            (summary["credential_hits"], summary["pull_run_id"]),
        )
        conn.commit()
        summary["live_state"] = conn.execute(
            "SELECT live_state FROM v_subagent_live_status").fetchone()[0]
        conn.close()
    print(f"[subagent_pull] warehouse={args.warehouse_db} pull_run_id={summary['pull_run_id']}")
    print(f"[subagent_pull] transcripts: {summary['files_read']} read of "
          f"{summary['files_seen']} discovered")
    print(f"[subagent_pull] inserted: {summary['calls_inserted']} tool calls, "
          f"{summary['results_inserted']} tool results, "
          f"{summary['texts_inserted']} assistant texts, "
          f"{summary['lines_malformed']} malformed lines skipped")
    print(f"[subagent_pull] hook denies captured: {summary['deny_results']} "
          f"tool results carry a named remedy")
    print(f"[subagent_pull] credential scan: {summary['credential_hits']} hits over all stored "
          f"tool inputs")
    print(f"[subagent_pull] live_state: {summary['live_state']}")
    return 0 if summary["live_state"] == "live" else 1


if __name__ == "__main__":
    raise SystemExit(main())
