"""Parsing primitives shared by every transcript source (ATLASSN-33).

Subagent transcripts and main-session transcripts are the SAME record format -- measured, not
assumed: sessionId, timestamp, cwd, gitBranch, uuid and message are present in both, and only
agentId is absent from a main-session record. So the line parsing, the partial-line discipline,
the stream identity and the deny extraction are one implementation used by both pulls, and only
discovery and identity differ between them.

Extracted from subagent_pull.py, which re-exports these names so existing callers and tests are
unaffected. The code moved verbatim; ATLASSN-27's tests cover it unchanged.
"""

import hashlib
import json

STREAM_ID_HEAD_BYTES = 4096

ASSISTANT_TEXT_CAP_BYTES = 65536

# The exact opening strings the real hc.deny() call sites produce. Derived from the corpus, not
# guessed: across all 34 ledger-confirmed denied subagent results, 'permissionDecisionReason' and
# 'hookSpecificOutput' appear ZERO times -- the harness strips the JSON envelope and delivers the
# bare reason string, so a denied tool_result body IS the deny message and nothing else.
#
# This set is the SECONDARY detector. The authoritative one is the hook_verdict join on
# tool_use_id, which needs no heuristic at all. This exists to catch a deny the ledger never
# recorded -- a real case, one confirmed instance in FORE-190. test_subagent_pull.py asserts this
# set covers every ledger-confirmed deny, so a new handler whose message shape escapes it fails a
# test rather than silently eroding coverage.
DENY_BODY_PREFIXES = (
    "Foreman: ",                        # architecture_gate, goals_freeze_gate, concept_gate
    "Foreman preflight:",               # preflight_blocking_gate.py -- a DIFFERENT opening from
                                        # the others, and the "Foreman: " prefix above (with its
                                        # trailing space) missed all 9 of its real denies until
                                        # the full-corpus run surfaced them
    "Blocked destructive command",      # guard_destructive.py
    "Blocked recursive-force delete",   # guard_destructive.py
    "This edit adds a dependency",      # rule_dependency_docs.py / dependency_provenance_gate
)

# Handlers a prefix set structurally CANNOT cover, and why -- not a to-do list.
#
# rule_frame_probe.py composes a situation-specific sentence per deny: "This adds a capability by
# writing it from scratch...", "You are about to hand-roll this...", "This changes the behaviour
# of a tool whose documented semantics...". Across 68 real denies there is no shared opening,
# because the message IS the reasoning rather than a template. Adding prefixes until the corpus
# happens to be covered would fit noise and break on the next unseen phrasing.
#
# These handlers are still fully covered, by the AUTHORITATIVE route: the hook_verdict join on
# tool_use_id needs no heuristic at all, and the pull captures the remedy text for any result the
# ledger already calls a deny. The prefix set exists only as an INDEPENDENT second route, to catch
# a deny the ledger never recorded (FORE-190). So a handler listed here loses the second route,
# not the first.
PROSE_DENY_HANDLERS = frozenset({"rule_frame_probe.py"})

# Handlers confirmed, by direct measurement against the real live warehouse (ATLASSN-194's own
# follow-up ticket), to have their ENTIRE real subagent-side deny population resolved via the
# ledger route only -- is_hook_deny=0 for 100% of rows (287/287 write_gate.py, 1/1
# agent_dispatch_gate.py), with NO other handler in the same real corpus showing partial or full
# uncoverage. Unlike PROSE_DENY_HANDLERS, this is NOT a claim that the message is unbounded prose
# -- write_gate.py's one measured example ("payload carries no edits/tool_input to derive a
# project root from") reads like a small number of fixed per-rule templates, not free-form
# reasoning. It is listed here rather than added to DENY_BODY_PREFIXES because a single verbatim
# example per handler is not enough evidence to add a prefix without fitting noise (the exact
# anti-pattern DENY_BODY_PREFIXES' own comment warns against). This set is therefore a SNAPSHOT of
# current coverage, not a permanent structural fact like PROSE_DENY_HANDLERS -- revisit once
# enough real examples exist to derive a safe prefix for either handler.
LEDGER_ONLY_DENY_HANDLERS = frozenset({"write_gate.py", "agent_dispatch_gate.py"})
DENY_TEXT_CAP_BYTES = 4096


def stream_id_of_path(path):
    """Content-addressed identity for one transcript, stable under append. Returns None when the
    file has not yet written enough to establish one.

    stream.py hashes a fixed 4096-byte head, which is stable for verdicts.jsonl because that file
    is always far longer than 4096 bytes. It is NOT stable for a file shorter than that: the head
    IS the whole file, so every append changes the identity, every pull reads it as a rotation,
    and the re-read from zero collides with rows already committed. Found by a test, not in
    review, and it matters most exactly where this feature is aimed -- a transcript is shortest
    when its subagent has just started, which is when a parent most wants to watch it.

    Measured on the real corpus: 0 of 1,527 transcripts are currently under 4096 bytes (a single
    record usually exceeds it), so the bug is latent here rather than live. The identity is fixed
    anyway, because "latent on today's data" is not a property worth depending on.

    Two stable anchors, in order:
      - the first complete line. A transcript is append-only, so its first record never changes.
      - failing that, the full 4096-byte head, which is equally fixed once the file has reached
        that length. 405 of 1,527 real transcripts have no newline in their first 4096 bytes,
        so this branch is load-bearing, not a fallback nobody takes.
    Neither is available only while the very first record is still being written -- and there is
    nothing to ingest in that state either, so the file is skipped and picked up next tick."""
    with open(path, "rb") as fh:
        head = fh.read(STREAM_ID_HEAD_BYTES)
    newline = head.find(b"\n")
    if newline != -1:
        return hashlib.sha256(head[:newline]).hexdigest()
    if len(head) >= STREAM_ID_HEAD_BYTES:
        return hashlib.sha256(head).hexdigest()
    return None


def split_complete_lines(data):
    """(complete_lines, consumed_bytes). Identical partial-line discipline to stream.py: the
    trailing element of a split on b'\\n' is either b'' (the data ended on a newline, fully
    consumed) or a genuine partial line, which must NOT be consumed -- a transcript being written
    by a still-running subagent ends mid-line constantly, and consuming it would both lose the
    record and desynchronise every later byte offset."""
    parts = data.split(b"\n")
    trailing = parts[-1]
    if trailing == b"":
        return parts[:-1], len(data)
    return parts[:-1], len(data) - len(trailing)


def map_transcript_line(obj, byte_offset, ledger_deny_ids=frozenset()):
    """One JSONL record in, (calls, results, texts, blocks) out.

    A single assistant message can carry SEVERAL tool_use blocks, so this returns a list, and
    block_index is part of each call's identity -- (transcript_id, byte_offset, block_index) is
    the idempotency key. One row per line would silently drop every tool call after the first in
    a parallel-tool-call turn, which is exactly the shape a busy subagent produces.

    tool_result blocks come back separately because a result arrives in a LATER record than its
    call, and often in a later pull entirely. They land in their own table keyed by tool_use_id
    so an incremental pass never has to go back and update a row it already committed.

    ATLASSN-104/REQ-64 (ARCHITECTURE.md section 40): blocks is a FOURTH list, structural only --
    one entry per block, at every position, for EVERY block type, including a type none of the
    branches below name (thinking, fallback, and anything not yet observed) and including a
    position whose value is not even a dict. This is deliberately NOT the elif-chain-plus-a-
    fourth-named-branch shape: adding a named branch for "thinking" here would repeat the exact
    defect this ticket exists to fix -- an implicit type allowlist that silently drops whatever it
    doesn't recognise, with no row and no error anywhere. Every position in content produces
    exactly one blocks entry, unconditionally, before any type-specific handling below decides
    whether that same block ALSO belongs in calls/results/texts. blocks never carries block
    CONTENT (ARCHITECTURE.md section 40's own deliberate decision, alongside ATLASSN-11's
    already-open redaction question) -- only byte_offset/block_index/ts/record_uuid/block_type."""
    calls = []
    results = []
    texts = []
    blocks = []
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return calls, results, texts, blocks

    ts = obj.get("timestamp")
    record_uuid = obj.get("uuid")
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            # A block that is not a dict still gets a structural row (block_type NULL) rather
            # than being skipped -- the position is real even when the shape at that position is
            # not, and skipping it would silently disagree with every other position's presence.
            blocks.append({
                "byte_offset": byte_offset,
                "block_index": index,
                "ts": ts,
                "record_uuid": record_uuid,
                "block_type": None,
            })
            continue
        block_type = block.get("type")
        # Recorded for EVERY block type, before any of the type-specific branches below run --
        # this unconditional capture, not a fourth named branch, is what stops a future unnamed
        # type from being dropped the way thinking/fallback were before this ticket.
        blocks.append({
            "byte_offset": byte_offset,
            "block_index": index,
            "ts": ts,
            "record_uuid": record_uuid,
            "block_type": block_type,
        })
        if block_type == "text":
            # ATLASSN-32: the agent's own words, which is where stated intent lives. Only kept
            # for assistant records -- a 'text' block on a user record is the operator's prompt,
            # not the agent's reasoning, and conflating the two would let a rubric attribute the
            # operator's words to the agent.
            if obj.get("type") != "assistant":
                continue
            raw = block.get("text") or ""
            encoded = raw.encode("utf-8")
            truncated = len(encoded) > ASSISTANT_TEXT_CAP_BYTES
            texts.append({
                "byte_offset": byte_offset,
                "block_index": index,
                "ts": ts,
                "record_uuid": record_uuid,
                # Slice the ENCODED bytes then decode with errors='ignore', so the cap is a real
                # byte bound and a multi-byte character straddling it cannot raise.
                "text": encoded[:ASSISTANT_TEXT_CAP_BYTES].decode("utf-8", errors="ignore")
                if truncated else raw,
                "text_bytes": len(encoded),
                "truncated": 1 if truncated else 0,
            })
        elif block_type == "tool_use":
            tool_input = block.get("input")
            input_json = json.dumps(tool_input, separators=(",", ":"), sort_keys=True) \
                if tool_input is not None else None
            calls.append({
                "byte_offset": byte_offset,
                "block_index": index,
                "ts": ts,
                "record_uuid": record_uuid,
                "session_id": obj.get("sessionId"),
                "agent_id": obj.get("agentId"),
                "cwd": obj.get("cwd"),
                "git_branch": obj.get("gitBranch"),
                "tool_use_id": block.get("id"),
                "tool_name": block.get("name"),
                "tool_input_json": input_json,
                "tool_input_bytes": len(input_json.encode("utf-8")) if input_json else 0,
            })
        elif block_type == "tool_result":
            tool_use_id = block.get("tool_use_id")
            if not tool_use_id:
                continue
            raw = block.get("content")
            body = raw if isinstance(raw, str) else (
                json.dumps(raw) if raw is not None else "")
            # Two routes, kept distinguishable on purpose. is_hook_deny is the PREFIX route
            # only -- an independent second opinion that can catch a deny the ledger never
            # recorded. deny_text is populated by EITHER route, so a handler emitting prose the
            # prefix set cannot match (PROSE_DENY_HANDLERS) still yields its named remedy via the
            # authoritative ledger join. Collapsing these into one flag would erase the
            # disagreement that FORE-190 is made of.
            prefix_deny = extract_deny_text(body)
            deny_text = prefix_deny
            if deny_text is None and tool_use_id in ledger_deny_ids and body:
                deny_text = body.lstrip("[]{}\"' \n\t")[:DENY_TEXT_CAP_BYTES]
            results.append({
                "byte_offset": byte_offset,
                "tool_use_id": tool_use_id,
                "ts": ts,
                # is_error is genuinely tri-state on real data: True, False, and absent. Absent
                # is stored as NULL rather than coerced to 0 -- "the harness did not say" is not
                # the same fact as "it said no error", and 27,035 real result blocks include both.
                #
                # It is ALSO not a deny signal, which is the whole reason deny_text exists:
                # measured, of 8 tool_result bodies that were hook denies, is_error was truthy on
                # zero. A duck that was blocked again and a duck that landed are the same row
                # under this column.
                "is_error": None if block.get("is_error") is None
                else int(bool(block.get("is_error"))),
                "result_bytes": len(body.encode("utf-8")),
                "is_hook_deny": 1 if prefix_deny is not None else 0,
                "deny_text": deny_text,
            })
    return calls, results, texts, blocks


def extract_deny_text(body):
    """The deny's named remedy, or None if this result is not a hook deny.

    Returns the WHOLE body when it is one, because a denied body is the reason string entire --
    verified across all 34 ledger-confirmed denied subagent results, in none of which the JSON
    envelope survives. There is nothing to parse out. Capped only against a pathological case,
    not against the real distribution (100 to 250 bytes).

    Leading whitespace and the bracket/quote noise a JSON-serialised content list leaves behind
    are stripped before matching, since the same deny arrives as a bare string in some records
    and inside a one-element list in others."""
    if not body:
        return None
    head = body.lstrip("[]{}\"' \n\t")
    for prefix in DENY_BODY_PREFIXES:
        if head.startswith(prefix):
            return head[:DENY_TEXT_CAP_BYTES]
    return None
