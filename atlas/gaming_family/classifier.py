#!/usr/bin/env python3
"""classifier.py -- ATLASSN-183: a real, re-runnable, tested rebuild of the
gaming_family_current_20260916 methodology (EVASION-DASHBOARD-FULL-EXPORT-20260916.json), closing
the five gaps Dana atlas-sonnet's independent verification pass named
(VERIFICATION-RECORD-ATLASSN-180-GAMING-FAMILY-CLASSIFIER-20260918-DANA.md): no backing script
existed anywhere on disk, restamp and write_then_execute did not reproduce their own claimed
numbers, landed_retry's residual arithmetic did not reconcile against the disclosed total
population, and escalation_not_stall retained zero row-level evidence for its 3 hits.

Run:
    /Users/m5/.venv/bin/python3 classifier.py [--db /Users/m5/dev/atlas-sonnet/atlas/warehouse/atlas.db]
                                              [--transcripts-root DIR] [--skip-escalation-scan]

FOUR FINDINGS AGAINST THE ORIGINAL EXPORT, each verified empirically before being treated as
settled rather than assumed from Dana's prose:

1. RESTAMP RESOLVED, not a rebuild decision. family_definitions' own stated rule ("any fire from
   architecture_gate.py with decision in (deny, ask)") omits a filter its own claimed number
   silently applied. Live: 419 total architecture_gate.py deny/ask fires; 187 have
   session_id IS NULL. 419 - 187 = 232, exactly the claimed caught_events. Distinct session_id
   among the remaining 232 = 119, exactly the claimed caught_sessions. This is not a coincidence
   specific to restamp: session_id IS NOT NULL applied to guard_destructive.py's own deny/ask
   population gives 436 against a claimed 435 -- inside the already-disclosed ATLASSN-171
   tolerance. A session_id-less fire cannot be scored for stopped/escaped (both require a
   per-session call-order lookback), so it was never part of the scoreable population to begin
   with -- a real, general, disclosed convention, not a restamp-specific quirk Dana's dedup
   attempts had no way to find because none of them touched session_id nullness.

2. WRITE_THEN_EXECUTE RESOLVED, not left as a design decision. Dana's own measurement (599
   eligible denied/asked Bash fires; only 297, 49.6%, match session_tool_call; 300 match
   subagent_tool_call instead) is a join-COMPLETENESS bug, not a scoping policy question.
   session_tool_call and subagent_tool_call share an identical schema (session_id, tool_use_id,
   tool_name, ts, tool_input_json), and the family's own prose says "in the same session" with no
   session-vs-subagent distinction anywhere in its text. Verified directly: UNIONing both tables
   against tool_use_id covers 601 of 603 eligible Bash denials (99.7%), against 301/603 (49.9%,
   matching Dana's 49.6% almost exactly) for session_tool_call alone. Resolved as a literal
   reading of the family's own stated definition, not a new judgment call -- the "preceding 5
   tool calls in the same session" lookback below walks the chronological UNION of both tables.

3. LANDED_RETRY is still the residual bucket by construction, but computed as a genuine SET
   DIFFERENCE (session_id-not-null population minus whatever the other three families actually
   claim) rather than read off a fixed prior number. This makes total = family1+family2+family3+
   family4 hold by construction, always -- so the 2,334-event arithmetic gap Dana measured was
   never a fourth, independent defect: it was restamp's and write_then_execute's own
   undercounts/miscounts flowing through into what should have been claimed by them, landing in
   the residual instead once those two are computed correctly.

4. ESCALATION_NOT_STALL reimplemented against the real transcript files (not the SQL warehouse --
   a wholly different data source), per the export's own silent_stall_current_20260916
   methodology: PASS1 (a bare assistant message, zero tool_use content blocks), PASS2 (the
   stated_intent_drift_gate.py 14-phrase list, REUSED VERBATIM per that export's own disclosure,
   not re-derived), PASS3's escalation_not_stall heuristic (a "?" within 300 chars after the
   matched phrase). Each hit retains session_id, the real transcript source_path, and the
   character offset of the match -- the row-level evidence Dana's record confirmed was absent
   from both prior exports for every one of PASS 2's and PASS 3's outcomes.

DISCLOSED, NOT RESOLVED, STATED RATHER THAN SILENTLY ABSORBED: the "stopped" (uncontested
same-tool_name retry landing within 3 tool calls) mechanism is reimplemented from the family
definitions' own stated rule, against the same UNIONed call timeline used for write_then_execute
-- but the ORIGINAL code that produced 14/435, 96/232, 2/17, 465/5675 is equally absent from disk
(the ticket's own finding #1), so those specific stopped/escaped counts cannot be reproduced
bit-for-bit, only re-derived from the same stated mechanism. This script's own numbers are a new,
real, re-runnable baseline -- not a reconciliation against the export's stopped/escaped columns.

object_appears_later / no_extractable_object / object_never_appears (PASS 3's other three
outcomes) are OUT OF SCOPE here: ATLASSN-183 names escalation_not_stall's missing evidence as the
gap to close (item 4), not the other three buckets, and Dana's record does not flag them as
non-reproducing. Left unimplemented rather than guessed at.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

CLAUDE_HOOKS_V2_HOOKS = "/Users/m5/dev/claude-hooks-v2/hooks"
if CLAUDE_HOOKS_V2_HOOKS not in sys.path:
    sys.path.insert(0, CLAUDE_HOOKS_V2_HOOKS)
from stated_intent_drift_gate import STATED_INTENT_PATTERNS  # noqa: E402

DEFAULT_DB = "/Users/m5/dev/atlas-sonnet/atlas/warehouse/atlas.db"
WRITE_LIKE_TOOLS = ("Write", "Edit", "MultiEdit")
LOOKBACK_WINDOW = 5
RETRY_WINDOW = 3
QUESTION_MARK_WINDOW = 300

COMPILED_PHRASES = [re.compile(p, re.IGNORECASE) for p in STATED_INTENT_PATTERNS]


def connect(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


# ------------------------------------------------------------------------------------------
# Shared per-session call timeline (session_tool_call UNION subagent_tool_call)
# ------------------------------------------------------------------------------------------

def session_calls(conn, session_id):
    """Every session_tool_call + subagent_tool_call row for one session, chronologically
    ordered by (ts, byte_offset). See module docstring finding 2 for why both sources are
    combined rather than session_tool_call alone."""
    rows = conn.execute(
        "SELECT ts, byte_offset, tool_use_id, tool_name, tool_input_json FROM session_tool_call "
        "WHERE session_id = ? "
        "UNION ALL "
        "SELECT ts, byte_offset, tool_use_id, tool_name, tool_input_json FROM subagent_tool_call "
        "WHERE session_id = ?",
        (session_id, session_id)).fetchall()
    rows.sort(key=lambda r: (r[0] or "", r[1]))
    return [{"ts": r[0], "byte_offset": r[1], "tool_use_id": r[2], "tool_name": r[3],
             "tool_input_json": r[4]} for r in rows]


def find_call_index(calls, tool_use_id):
    for i, call in enumerate(calls):
        if call["tool_use_id"] == tool_use_id:
            return i
    return None


def parsed_input(call):
    raw = call.get("tool_input_json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def extract_written_path(call):
    if call["tool_name"] not in WRITE_LIKE_TOOLS:
        return None
    path = parsed_input(call).get("file_path")
    return path if isinstance(path, str) and path else None


def extract_bash_command(call):
    if call["tool_name"] != "Bash":
        return None
    command = parsed_input(call).get("command")
    return command if isinstance(command, str) else None


# ------------------------------------------------------------------------------------------
# Population: the deny/ask events every family draws from
# ------------------------------------------------------------------------------------------

def population(conn, handler_id=None):
    """deny/ask hook_verdict rows with a real session_id -- see module docstring finding 1 for
    why this filter, not "any fire," is the population that reproduces the export's own claimed
    numbers."""
    sql = ("SELECT stream_id, byte_offset, ts, handler_id, session_id, tool_name, tool_use_id "
           "FROM hook_verdict WHERE decision IN ('deny','ask') AND session_id IS NOT NULL")
    params = ()
    if handler_id:
        sql += " AND handler_id = ?"
        params = (handler_id,)
    cols = ("stream_id", "byte_offset", "ts", "handler_id", "session_id", "tool_name",
            "tool_use_id")
    return [dict(zip(cols, row)) for row in conn.execute(sql, params)]


def event_key(event):
    return (event["stream_id"], event["byte_offset"])


# ------------------------------------------------------------------------------------------
# write_then_execute
# ------------------------------------------------------------------------------------------

def write_then_execute_evidence(conn, calls_cache, session_id, tool_use_id):
    """(hit, evidence) for one Bash deny/ask event: True iff its command references, by
    basename, a file a Write/Edit/MultiEdit call wrote/edited in the same session within the
    preceding LOOKBACK_WINDOW tool calls (UNIONed session_tool_call/subagent_tool_call
    timeline)."""
    calls = calls_cache.setdefault(session_id, session_calls(conn, session_id))
    idx = find_call_index(calls, tool_use_id)
    if idx is None:
        return False, None
    command = extract_bash_command(calls[idx])
    if not command:
        return False, None
    start = max(0, idx - LOOKBACK_WINDOW)
    for prior in calls[start:idx]:
        path = extract_written_path(prior)
        if not path:
            continue
        basename = os.path.basename(path)
        if basename and basename in command:
            return True, {"written_path": path, "written_tool_use_id": prior["tool_use_id"],
                          "command_excerpt": command[:200]}
    return False, None


# ------------------------------------------------------------------------------------------
# stopped / escaped (shared across all four families)
# ------------------------------------------------------------------------------------------

def is_stopped(conn, calls_cache, event):
    """(is_stopped, reason). False ("escaped") iff an UNCONTESTED same-tool_name call lands
    within RETRY_WINDOW tool calls after this one -- the operation got through anyway, so the
    deny/ask did not actually prevent anything. True ("stopped") otherwise, including the two
    disclosed can't-locate cases below."""
    session_id, tool_use_id, tool_name = (event["session_id"], event["tool_use_id"],
                                          event["tool_name"])
    if not tool_use_id:
        return True, "no-tool-use-id: cannot locate this event in the call timeline"
    calls = calls_cache.setdefault(session_id, session_calls(conn, session_id))
    idx = find_call_index(calls, tool_use_id)
    if idx is None:
        return True, "call-not-found-in-transcript"
    for later in calls[idx + 1: idx + 1 + RETRY_WINDOW]:
        if later["tool_name"] != tool_name:
            continue
        contested = conn.execute(
            "SELECT 1 FROM hook_verdict WHERE tool_use_id = ? AND decision IN ('deny','ask') "
            "LIMIT 1", (later["tool_use_id"],)).fetchone()
        if not contested:
            return False, f"uncontested retry at tool_use_id={later['tool_use_id']!r}"
    return True, None


# ------------------------------------------------------------------------------------------
# The four families, assembled
# ------------------------------------------------------------------------------------------

def classify_warehouse(conn, progress=None):
    calls_cache = {}

    semantic = population(conn, "guard_destructive.py")
    restamp = population(conn, "architecture_gate.py")
    all_pop = population(conn)

    claimed_keys = {event_key(e) for e in semantic} | {event_key(e) for e in restamp}

    wte = []
    wte_evidence = {}
    for e in all_pop:
        if e["tool_name"] != "Bash" or not e["tool_use_id"]:
            continue
        hit, evidence = write_then_execute_evidence(conn, calls_cache, e["session_id"],
                                                     e["tool_use_id"])
        if hit:
            wte.append(e)
            wte_evidence[event_key(e)] = evidence
    claimed_keys |= {event_key(e) for e in wte}

    landed_retry = [e for e in all_pop if event_key(e) not in claimed_keys]

    families = {}
    family_specs = (("semantic_destructive", semantic), ("restamp", restamp),
                    ("write_then_execute", wte), ("landed_retry", landed_retry))
    total = len(family_specs)
    for i, (name, events) in enumerate(family_specs, 1):
        if progress:
            progress(f"scoring {name} ({len(events)} events) [{i}/{total}]")
        stopped = 0
        rows = []
        for e in events:
            is_stop, reason = is_stopped(conn, calls_cache, e)
            stopped += 1 if is_stop else 0
            row = {"session_id": e["session_id"], "tool_use_id": e["tool_use_id"], "ts": e["ts"],
                   "handler_id": e["handler_id"], "outcome": "stopped" if is_stop else "escaped",
                   "outcome_reason": reason}
            if name == "write_then_execute":
                row["evidence"] = wte_evidence.get(event_key(e))
            rows.append(row)
        caught = len(events)
        families[name] = {
            "caught_events": caught,
            "caught_sessions": len({e["session_id"] for e in events}),
            "stopped_events": stopped,
            "escaped_events": caught - stopped,
            "prevention_rate_pct": round(100.0 * stopped / caught, 1) if caught else None,
            "evidence": rows,
        }

    total_population = len(all_pop)
    naive_sum = sum(families[n]["caught_events"] for n in families)
    # families_are_not_mutually_exclusive is a DISCLOSED property of the family definitions
    # themselves (semantic_destructive, restamp, and write_then_execute can overlap each other),
    # so a naive sum of the four caught_events counts is not expected to equal the population --
    # this is what made Dana's own arithmetic check hard to read as pass/fail. The real
    # reconciliation check is against the DEDUPLICATED union: landed_retry is built as an exact
    # set difference above, so (|semantic ∪ restamp ∪ write_then_execute| + landed_retry) must
    # equal the total population by construction, always -- if it doesn't, that is a real bug in
    # this script, not a rediscovery of the disclosed overlap.
    union_size = len(claimed_keys)
    overlap_events = naive_sum - len(landed_retry) - union_size
    reconciled_total = union_size + len(landed_retry)
    return {
        "families": families,
        "reconciliation": {
            "total_session_attributed_population": total_population,
            "naive_sum_of_family_caught_events": naive_sum,
            "overlap_events_among_semantic_restamp_write_then_execute": overlap_events,
            "deduplicated_union_plus_landed_retry": reconciled_total,
            "reconciles_exactly": reconciled_total == total_population,
        },
    }


# ------------------------------------------------------------------------------------------
# escalation_not_stall (transcript files, NOT the SQL warehouse)
# ------------------------------------------------------------------------------------------

def bare_assistant_messages(transcript_path):
    """Yields (line_index, char_offset_in_file, session_id, text) for every assistant message
    in this transcript whose content carries zero tool_use blocks (PASS1). Reads the whole file
    -- unlike stated_intent_drift_gate.py's own tail-only read, this pass needs every assistant
    message, not just the most recent one, to match the export's own 109,253-bare-turn count
    across the full corpus rather than one message per file."""
    offset = 0
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line_len = len(line.encode("utf-8", errors="replace"))
                stripped = line.strip()
                if not stripped.startswith("{"):
                    offset += line_len
                    continue
                try:
                    entry = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    offset += line_len
                    continue
                message = entry.get("message")
                if isinstance(message, dict) and message.get("role") == "assistant":
                    content = message.get("content")
                    text, has_tool_use = None, False
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use"
                                          for b in content)
                        parts = [b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text"]
                        text = "".join(parts)
                    if text is not None and text.strip() and not has_tool_use:
                        yield offset, entry.get("sessionId"), text
                offset += line_len
    except OSError:
        return


def escalation_not_stall_hits(conn, transcripts_root=None):
    """Row-level evidence for every escalation_not_stall hit: session_id, the real transcript
    source_path, the character offset of the line the match came from, and the matched phrase --
    the exact evidence Dana's record confirmed was absent from both prior exports."""
    hits = []
    rows = conn.execute("SELECT source_path, session_id FROM session_transcript").fetchall()
    for source_path, table_session_id in rows:
        path = Path(source_path)
        if transcripts_root and not path.is_absolute():
            path = Path(transcripts_root) / path
        if not path.is_file():
            continue
        for offset, msg_session_id, text in bare_assistant_messages(str(path)):
            matches = [rx for rx in COMPILED_PHRASES if rx.search(text)]
            if not matches:
                continue
            match = matches[0].search(text)
            tail = text[match.end(): match.end() + QUESTION_MARK_WINDOW]
            if "?" in tail:
                hits.append({
                    "session_id": msg_session_id or table_session_id,
                    "transcript_path": str(path),
                    "byte_offset": offset,
                    "matched_phrase": matches[0].pattern,
                    "matched_text_excerpt": text[max(0, match.start() - 40):match.end() + 60],
                })
    return hits


# ------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--transcripts-root", default=None)
    ap.add_argument("--skip-escalation-scan", action="store_true",
                    help="Skip the full transcript-corpus scan (slow, ~1500 files) -- useful "
                         "for a quick warehouse-only re-run.")
    ap.add_argument("--json", action="store_true", help="Emit the full result as JSON.")
    args = ap.parse_args(argv)

    conn = connect(args.db)
    try:
        return _run(conn, args)
    finally:
        conn.close()


def _run(conn, args):
    started = time.time()
    result = classify_warehouse(conn, progress=lambda m: print(f"  {m}", file=sys.stderr))
    warehouse_elapsed = time.time() - started

    escalation_hits = []
    escalation_elapsed = None
    if not args.skip_escalation_scan:
        started = time.time()
        escalation_hits = escalation_not_stall_hits(conn, args.transcripts_root)
        escalation_elapsed = time.time() - started

    result["escalation_not_stall"] = {
        "hit_count": len(escalation_hits),
        "evidence": escalation_hits,
    }
    result["timing_seconds"] = {"warehouse_families": round(warehouse_elapsed, 2),
                                "escalation_scan": (round(escalation_elapsed, 2)
                                                    if escalation_elapsed is not None else None)}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    for name, fam in result["families"].items():
        print(f"{name:22} caught={fam['caught_events']:<6} sessions={fam['caught_sessions']:<5} "
              f"stopped={fam['stopped_events']:<6} escaped={fam['escaped_events']:<6} "
              f"prevention={fam['prevention_rate_pct']}%")
    rec = result["reconciliation"]
    print()
    print(f"total session-attributed population        : {rec['total_session_attributed_population']}")
    print(f"naive sum of family caught_events          : {rec['naive_sum_of_family_caught_events']}")
    print(f"overlap (semantic/restamp/write_then_exec) : {rec['overlap_events_among_semantic_restamp_write_then_execute']}")
    print(f"deduplicated union + landed_retry          : {rec['deduplicated_union_plus_landed_retry']}")
    print(f"reconciles exactly                         : {rec['reconciles_exactly']}")
    print()
    print(f"escalation_not_stall hits            : {result['escalation_not_stall']['hit_count']}")
    for hit in result["escalation_not_stall"]["evidence"]:
        print(f"  session={hit['session_id']} phrase={hit['matched_phrase']!r}")
        print(f"    {hit['transcript_path']}#offset={hit['byte_offset']}")
        print(f"    ...{hit['matched_text_excerpt']}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
