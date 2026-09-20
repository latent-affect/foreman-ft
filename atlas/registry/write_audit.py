"""ATLASSN-132 -- registry I7, the write-audit trail (GOALS.json C13, C14).

The store is git-ignored by design (34.0), so this JSONL plus the ATLAS ingested copy is what
replaces version control for recoverability. Two properties, and they are different:

  C14 is ORDERING. The write path appends the line and fsyncs it BEFORE it commits, and an
  append failure ABORTS the transaction with reason `audit-append-failed`. The store never
  advances past its tamper-evidence trail. That ordering lives in write_path.py's transaction
  because it IS the transaction; what lives here is the append primitive it calls and the
  replay that makes the trail worth writing.

  C13 is RECONSTRUCTION. Replaying the trail rebuilds `assertion` and `spent_ref` uid-keyed and
  field-complete -- every column except the local rowid, which carries no cross-file meaning.

WHY REPLAY REPORTS RATHER THAN REPAIRS. An audit line with no store row is the crash window:
the append landed, the commit did not. C14 requires that to surface as a DETECTED DIVERGENCE,
never a silent reconciliation, and F4 is the reason -- a divergence written to a channel nobody
reads is the failure this whole trail exists to prevent. So nothing in this module writes to a
live store. `replay_into_store` builds a SEPARATE store from the trail and hands both to the
caller to compare.

THE REVOKE LINE'S SHAPE, and the one ambiguity in the ruling that had to be resolved here.
DESIGN-REGISTRY-SCHEMA-AI5-ATLASSN-110-20260912.md (ruled 2026-09-12, e18b1cf) says a revoke
line carries the same full assert-field set copied from the row, PLUS the revocation triple,
PLUS "the revoking write's dispatch_record_id and its own payload_sha256". Taken literally that
is two fields both called `dispatch_record_id` in one JSON object, which cannot be written.
Resolved, and stated rather than left to the next reader: the assert fields keep their own
names verbatim (`dispatch_record_id` is the ASSERTING record, copied from the stored row, which
is what makes the cross-check a cross-check), and the revoking act's record travels as
`revoking_dispatch_record_id`. `payload_sha256` is the line's OWN digest in both ops, computed
over that line's other fields -- so it is excluded from the assert-field cross-check, which
compares exactly the ten NOT NULL assertion columns and nothing else.

`revoking_dispatch_record_id` has no column in `assertion` and is not meant to get one: it is
trail-only accountability, so it is excluded from the replay comparison by construction rather
than by a special case. C13's "every column except the local rowid" is about columns, and this
is not one.

HASH CHAINING (Alice proposal, 2026-09-16, unfiled finding from tonight's validate-stage pass --
see the proposing session's rationale for why it stayed unfiled). Two gaps named against the
pre-chained design: payload_sha256 was unkeyed (no secret, no dependency on anything but the
line's own fields) and unchained (no dependency on any OTHER line), so a same-uid tamperer --
someone with the same disk access the legitimate writer has, which every real writer on this
machine has for this git-ignored file -- could edit one line's fields and simply recompute its
own payload_sha256 to match, and DIGEST_MISMATCH would never fire. Separately, truncating
trailing lines off the file is invisible to replay: nothing in the trail records how many lines
SHOULD exist, so a shortened file just looks like a shorter, otherwise-consistent trail.

WHAT CHAINING BUYS, STATED PRECISELY RATHER THAN OVERCLAIMED: every line now carries
`prev_sha256`, the RECOMPUTED digest of the line immediately before it (GENESIS_PREV_SHA256 for
the first line), and that field is itself covered by the line's own payload_sha256 -- so it
cannot be edited without also invalidating this line's own digest. Editing line N therefore
requires ALSO editing line N+1's prev_sha256 (and payload_sha256, and every line after it, all
the way to the current tip) to keep the trail internally consistent -- a single-line
edit-and-recompute, the exact reproduced gap, no longer suffices. replay()'s new CHAIN_BROKEN
divergence catches any point where this cascading re-signing was not done consistently, INCLUDING
at the very front: a trail that does not open with prev_sha256 == GENESIS_PREV_SHA256 reports
CHAIN_BROKEN on line 1, which is a real, if incidental, front-truncation detector.

WHAT CHAINING DOES NOT BUY, NAMED RATHER THAN LEFT IMPLICIT: a pure in-file hash chain CANNOT
detect a clean TRAILING truncation (deleting the last k lines wholesale). The surviving lines
still chain consistently among themselves -- there is no line after the new, shorter "last line"
whose prev_sha256 could disagree with anything, because there is no such line. Detecting that
requires an EXTERNAL anchor outside this file: something durable that independently records
"the chain's tip was at least digest D as of transaction T," so a later read can notice the
trail no longer reaches D. The natural anchor is write_path.py's own SQLite transaction (the
store is NOT git-ignored the way this trail is), but wiring a chain-tip column into that
transaction's schema and commit sequence is real design work this proposal does not attempt --
named here as the concrete follow-up, not silently assumed solved by chaining alone. This
proposal closes the "edit-and-recompute a single line" gap the finding names; it does not close
trailing truncation, and says so rather than implying otherwise.

A same-uid tamperer with UNLIMITED willingness to rewrite the whole file, front to back, can
still produce an internally-consistent forged chain from scratch -- no local mechanism (chaining
included) changes that; only an external, independently-anchored tip value can. This is the same
residual PDP-RATIONALE.md's own honesty ceiling names for this whole class of local mechanism,
restated here rather than overclaimed away.
"""

import hashlib
import json
import os
from pathlib import Path

from atlas.registry import ddl, migrate

OP_ASSERT = "assert"
OP_REVOKE = "revoke"
OPS = (OP_ASSERT, OP_REVOKE)

# The ten NOT NULL `assertion` columns a line carries, in the order the schema declares them.
# `state` is deliberately NOT here: it is not carried, it is DERIVED by replay from the op
# sequence (assert -> live, a later revoke -> revoked). Carrying it as well would give the trail
# two sources of truth for the same fact and no way to arbitrate when they disagree.
ASSERTION_COLUMN_FIELDS = (
    "assertion_uid", "edge_id", "component", "class", "lease_s", "verified_at",
    "verifier_session_id", "dispatch_record_id", "evidence_tool_use_id", "evidence_class",
)

REVOCATION_TRIPLE = ("revoked_at", "revoked_by_session_id", "revocation_evidence_tool_use_id")

# `prev_sha256` sits between the assertion columns and payload_sha256 -- it is covered by the
# digest (payload_digest hashes every field except payload_sha256 itself, so prev_sha256 is
# included), and it must be present before payload_sha256 is computed, matching the order these
# tuples already declare.
ASSERT_LINE_FIELDS = ("ts", "op") + ASSERTION_COLUMN_FIELDS + ("prev_sha256", "payload_sha256")
REVOKE_LINE_FIELDS = (("ts", "op") + ASSERTION_COLUMN_FIELDS + REVOCATION_TRIPLE
                      + ("revoking_dispatch_record_id", "prev_sha256", "payload_sha256"))

# Divergence kinds. Named constants rather than inline strings because a caller filtering on a
# misspelled kind gets an empty list, which reads exactly like "no divergences" -- the silent
# direction, on the module whose entire job is to not be silent.
ORPHAN_AUDIT_LINE = "orphan-audit-line"
STORE_ROW_WITHOUT_AUDIT_LINE = "store-row-without-audit-line"
FIELD_MISMATCH = "store-row-field-mismatch"
SPENT_REF_DIVERGENCE = "spent-ref-divergence"
REVOKE_OF_UNKNOWN_ASSERTION = "revoke-line-unknown-assertion"
REVOKE_FIELD_MISMATCH = "revoke-line-assert-field-mismatch"
DUPLICATE_ASSERT_LINE = "duplicate-assert-line"
MALFORMED_AUDIT_LINE = "malformed-audit-line"
DIGEST_MISMATCH = "payload-digest-mismatch"
CHAIN_BROKEN = "chain-broken"

# The chain's root sentinel -- the value the FIRST real line's prev_sha256 must equal. No real
# sha256 digest can equal this by construction with overwhelming probability (the same standing
# convention hash-chained logs elsewhere already use for a genesis link), so a collision here is
# not a real design concern.
GENESIS_PREV_SHA256 = "0" * 64


class AuditAppendFailed(Exception):
    """Raised by append_line when the trail could not be durably extended. write_path catches
    this and converts it to its own `audit-append-failed` rejection -- the reason string belongs
    to the write path's vocabulary, not to this module's."""


class ChainTipUnreadable(AuditAppendFailed):
    """Raised by current_chain_tip (and therefore by assert_line/revoke_line) when the trail's
    own last line cannot be read, parsed, or does not match its own recomputed digest. A
    SUBCLASS of AuditAppendFailed, not a new exception vocabulary: the write path already knows
    how to fail closed on that type (abort the transaction, refuse `audit-append-failed`), and a
    trail whose current tip is already unreadable or already tampered is exactly the same kind
    of failure -- refusing to chain a new, honest line onto a broken tip rather than silently
    extending a trail that is already lying."""


def payload_digest(payload):
    """sha256 over every field EXCEPT payload_sha256 itself, sorted, compact.

    Excluding the digest field is what makes the value recomputable by a reader; an earlier
    shape hashed the dict before inserting the digest, which works only if the caller happens to
    insert it last and is unverifiable afterwards. `prev_sha256`, once present in `payload`, is
    an ordinary field like any other here -- it is covered by this digest precisely because it
    is not excluded, which is what makes the chain link tamper-evident rather than decorative.
    """
    body = {key: value for key, value in payload.items() if key != "payload_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def current_chain_tip(audit_log_path):
    """The digest a new line should chain onto: GENESIS_PREV_SHA256 for an empty or absent
    trail, otherwise the RECOMPUTED (not merely stored) digest of the trail's real last line.

    Recomputing rather than trusting the stored payload_sha256 is deliberate: if the tip itself
    is already inconsistent (edited without its own digest being kept honest), a new line
    chained from the STORED value would silently launder that tamper into a chain that verifies
    from this point forward. Raises ChainTipUnreadable instead, refusing to extend a trail whose
    current tip cannot be trusted.
    """
    lines = read_lines(audit_log_path)
    if not lines:
        return GENESIS_PREV_SHA256
    _number, payload, raw = lines[-1]
    if payload is None or not isinstance(payload.get("payload_sha256"), str):
        raise ChainTipUnreadable(
            f"{audit_log_path}: last line is malformed or missing payload_sha256, refusing to "
            f"chain a new line onto an unreadable tip: {raw[:200]!r}"
        )
    recomputed = payload_digest(payload)
    if payload["payload_sha256"] != recomputed:
        raise ChainTipUnreadable(
            f"{audit_log_path}: last line's own payload_sha256 does not match its recomputed "
            f"digest -- the trail's current tip is already tampered or corrupted; refusing to "
            f"chain a new line onto it rather than silently extending a broken trail"
        )
    return recomputed


def assert_line(ts, assertion_columns, audit_log_path):
    """Build the canonical assert line from the ten column values, chained onto the trail's
    current tip, digest included.

    `audit_log_path` is REQUIRED, not defaulted -- a defaulted None that silently produced an
    unchained line would leave a real, callable path to the exact pre-chaining gap this fix
    exists to close, reachable by any caller that simply forgot the argument. There are exactly
    two real callers in this codebase (write_path.py's write_assertion/revoke_assertion, each
    already holding audit_log_path as a parameter of its own), so requiring it costs nothing a
    real call site did not already have on hand.
    """
    payload = {"ts": ts, "op": OP_ASSERT}
    for field in ASSERTION_COLUMN_FIELDS:
        payload[field] = assertion_columns[field]
    payload["prev_sha256"] = current_chain_tip(audit_log_path)
    payload["payload_sha256"] = payload_digest(payload)
    return payload


def revoke_line(ts, assertion_columns, revoked_at, revoked_by_session_id,
                revocation_evidence_tool_use_id, revoking_dispatch_record_id, audit_log_path):
    """Build the canonical revoke line: the SAME ten assert fields copied from the stored row,
    the revocation triple, the revoking act's own record, this line's chain link, and this
    line's own digest.

    The duplicated assert fields are the point, not redundancy: replay validates them against
    the state it has already rebuilt, so an attacker editing an assert line must now edit every
    later revoke line consistently or the trail reports a divergence -- and, since this fix,
    editing ANY line now also requires re-chaining every line after it, not just this one.

    `audit_log_path` is REQUIRED for the same reason assert_line's is -- see that function's
    docstring.
    """
    payload = {"ts": ts, "op": OP_REVOKE}
    for field in ASSERTION_COLUMN_FIELDS:
        payload[field] = assertion_columns[field]
    payload["revoked_at"] = revoked_at
    payload["revoked_by_session_id"] = revoked_by_session_id
    payload["revocation_evidence_tool_use_id"] = revocation_evidence_tool_use_id
    payload["revoking_dispatch_record_id"] = revoking_dispatch_record_id
    payload["prev_sha256"] = current_chain_tip(audit_log_path)
    payload["payload_sha256"] = payload_digest(payload)
    return payload


def append_line(audit_log_path, payload):
    """Append one line and fsync it. Raises AuditAppendFailed on any OS-level failure.

    fsync on the FILE DESCRIPTOR, not just flush: flush moves bytes out of Python's buffer into
    the kernel's, which survives a process crash and does not survive a power loss. C14's
    ordering claim is about durability before commit, so the weaker one would not discharge it.
    """
    try:
        with open(audit_log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AuditAppendFailed(str(exc)) from exc


def read_lines(audit_log_path):
    """Every line of the trail, in file order, as (line_number, parsed_or_None, raw).

    A line that will not parse is handed back rather than skipped: a corrupt line in a
    tamper-evidence trail is evidence, and dropping it silently is the one thing this file may
    not do.
    """
    path = Path(audit_log_path)
    if not path.is_file():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            out.append((number, parsed if isinstance(parsed, dict) else None, stripped))
    return out


def replay(audit_log_path):
    """Rebuild the in-memory state the trail implies.

    Returns {"assertions": {uid: {column: value, ..., "state": ...}},
             "spent_refs":  {tool_use_id: {"tool_use_id","spent_at","assertion_uid"}},
             "divergences": [{"kind","line","detail"}]}

    Every anomaly is a divergence entry AND the line is still applied where applying it is
    well-defined, because a replay that stops at the first problem tells an operator less than
    one that rebuilds what it can and names everything it could not.

    CHAIN VERIFICATION, added alongside DIGEST_MISMATCH rather than replacing it: DIGEST_MISMATCH
    asks "does this line's content match what it claims about itself"; CHAIN_BROKEN asks "does
    this line's claimed prev_sha256 match what the line before it ACTUALLY, RECOMPUTED, digests
    to" -- using the recomputed value, not the previous line's own stored payload_sha256, is
    what makes an edit-without-cascading detectable: an edited line N that recomputed ITS OWN
    digest to hide a DIGEST_MISMATCH still changes what line N truly digests to, so line N+1's
    unedited prev_sha256 (pointing at N's ORIGINAL content) disagrees with it. The chain tracker
    resets (loses continuity, checked again from the next parseable line) across a line that
    could not be parsed at all or carried an unknown op -- there is no digest to chain against
    for a line whose content this reader never obtained. Missing-assertion-column lines are NOT
    reset points: a digest was still computed over whatever fields the line carried, so the
    NEXT line's chain link is still checkable against it, even though this line is separately
    flagged MALFORMED_AUDIT_LINE for its own missing fields.

    NOT DETECTED HERE, BY CHAIN VERIFICATION ALONE, STATED RATHER THAN LEFT IMPLICIT: a clean
    trailing truncation (the last k lines removed wholesale). See this module's own docstring,
    "HASH CHAINING" section, for why a pure in-file chain cannot see that and what closing it
    would require.
    """
    assertions = {}
    spent_refs = {}
    divergences = []

    def diverge(kind, line_number, detail):
        divergences.append({"kind": kind, "line": line_number, "detail": detail})

    chain_tip = GENESIS_PREV_SHA256
    chain_established = True

    for number, payload, raw in read_lines(audit_log_path):
        if payload is None:
            diverge(MALFORMED_AUDIT_LINE, number, raw[:200])
            chain_established = False
            continue
        op = payload.get("op")
        if op not in OPS:
            diverge(MALFORMED_AUDIT_LINE, number, f"unknown op {op!r}")
            chain_established = False
            continue

        recomputed = payload_digest(payload)
        expected = payload.get("payload_sha256")
        if expected != recomputed:
            diverge(DIGEST_MISMATCH, number, payload.get("assertion_uid"))

        if chain_established:
            found_prev = payload.get("prev_sha256")
            if found_prev != chain_tip:
                diverge(CHAIN_BROKEN, number, {
                    "expected_prev_sha256": chain_tip, "found_prev_sha256": found_prev,
                })
        chain_tip = recomputed
        chain_established = True

        missing = [f for f in ASSERTION_COLUMN_FIELDS if f not in payload]
        if missing:
            diverge(MALFORMED_AUDIT_LINE, number, f"missing fields {missing}")
            continue

        uid = payload["assertion_uid"]
        columns = {field: payload[field] for field in ASSERTION_COLUMN_FIELDS}

        if op == OP_ASSERT:
            if uid in assertions:
                diverge(DUPLICATE_ASSERT_LINE, number, uid)
                continue
            row = dict(columns)
            row["state"] = "live"
            row["revoked_at"] = None
            row["revoked_by_session_id"] = None
            row["revocation_evidence_tool_use_id"] = None
            assertions[uid] = row
            spent_refs[payload["evidence_tool_use_id"]] = {
                "tool_use_id": payload["evidence_tool_use_id"],
                "spent_at": payload["verified_at"],
                "assertion_uid": uid,
            }
            continue

        # op == OP_REVOKE
        if uid not in assertions:
            diverge(REVOKE_OF_UNKNOWN_ASSERTION, number, uid)
            continue
        known = assertions[uid]
        disagreeing = [field for field in ASSERTION_COLUMN_FIELDS
                       if known[field] != columns[field]]
        if disagreeing:
            diverge(REVOKE_FIELD_MISMATCH, number,
                    {field: {"trail": columns[field], "replayed": known[field]}
                     for field in disagreeing})
        missing_triple = [f for f in REVOCATION_TRIPLE if payload.get(f) is None]
        if missing_triple:
            diverge(MALFORMED_AUDIT_LINE, number, f"revoke missing {missing_triple}")
            continue
        known["state"] = "revoked"
        for field in REVOCATION_TRIPLE:
            known[field] = payload[field]
        spent_refs[payload["revocation_evidence_tool_use_id"]] = {
            "tool_use_id": payload["revocation_evidence_tool_use_id"],
            "spent_at": payload["revoked_at"],
            "assertion_uid": uid,
        }

    return {"assertions": assertions, "spent_refs": spent_refs, "divergences": divergences}


ASSERTION_REPLAY_COLUMNS = ASSERTION_COLUMN_FIELDS + (
    "state", "revoked_at", "revoked_by_session_id", "revocation_evidence_tool_use_id")


def replay_into_store(audit_log_path, store_path):
    """Materialize the replayed state as a real v1 store at `store_path`, which must not exist.

    A real store rather than a dict, because C13's claim is that the trail reconstructs the
    TABLES -- including that every reconstructed row satisfies the same CHECK constraints the
    original had to. A dict comparison would pass on a half-revoked row the schema forbids.
    """
    state = replay(audit_log_path)
    connection = migrate.create_store(store_path)
    columns = ", ".join(ASSERTION_REPLAY_COLUMNS)
    placeholders = ", ".join("?" for _ in ASSERTION_REPLAY_COLUMNS)
    connection.execute("BEGIN")
    try:
        for uid in sorted(state["assertions"]):
            row = state["assertions"][uid]
            connection.execute(
                f"INSERT INTO assertion ({columns}) VALUES ({placeholders})",
                tuple(row[column] for column in ASSERTION_REPLAY_COLUMNS))
        for tool_use_id in sorted(state["spent_refs"]):
            spent = state["spent_refs"][tool_use_id]
            connection.execute(
                "INSERT INTO spent_ref (tool_use_id, spent_at, assertion_uid) VALUES (?, ?, ?)",
                (spent["tool_use_id"], spent["spent_at"], spent["assertion_uid"]))
    except Exception:
        connection.execute("ROLLBACK")
        connection.close()
        raise
    connection.execute("COMMIT")
    return connection, state


def assertion_rows_by_uid(connection):
    """Every assertion row as {uid: {column: value}}, EXCLUDING `id` -- the local rowid, which
    carries no cross-file meaning and is the one column C13 exempts."""
    columns = [name for name in ddl.table_columns(connection, "assertion") if name != "id"]
    quoted = ", ".join(f'"{name}"' for name in columns)
    rows = connection.execute(f"SELECT {quoted} FROM assertion").fetchall()
    return {dict(zip(columns, row))["assertion_uid"]: dict(zip(columns, row)) for row in rows}


def spent_rows_by_key(connection):
    columns = ddl.table_columns(connection, "spent_ref")
    quoted = ", ".join(f'"{name}"' for name in columns)
    rows = connection.execute(f"SELECT {quoted} FROM spent_ref").fetchall()
    return {dict(zip(columns, row))["tool_use_id"]: dict(zip(columns, row)) for row in rows}


def compare_store_to_replay(original_connection, replayed_connection):
    """Uid-keyed, field-complete comparison in BOTH directions.

    Returns a list of divergence entries. An empty list is the C13 pass condition: the trail
    carried enough to rebuild the store exactly, on every column except the rowid.

    Both directions matter and they mean different things. A store row the trail does not
    explain means the store advanced past its tamper-evidence trail, which is the property C14
    exists to guarantee cannot happen -- so it gets its own kind rather than being folded into
    the orphan case, which is the opposite failure.
    """
    divergences = []
    original = assertion_rows_by_uid(original_connection)
    replayed = assertion_rows_by_uid(replayed_connection)

    for uid in sorted(set(replayed) - set(original)):
        divergences.append({"kind": ORPHAN_AUDIT_LINE, "line": None, "detail": uid})
    for uid in sorted(set(original) - set(replayed)):
        divergences.append({"kind": STORE_ROW_WITHOUT_AUDIT_LINE, "line": None, "detail": uid})
    for uid in sorted(set(original) & set(replayed)):
        for column in sorted(original[uid]):
            if original[uid][column] != replayed[uid].get(column):
                divergences.append({
                    "kind": FIELD_MISMATCH, "line": None,
                    "detail": {"assertion_uid": uid, "column": column,
                               "store": original[uid][column],
                               "replay": replayed[uid].get(column)}})

    original_spent = spent_rows_by_key(original_connection)
    replayed_spent = spent_rows_by_key(replayed_connection)
    for key in sorted(set(original_spent) ^ set(replayed_spent)):
        divergences.append({"kind": SPENT_REF_DIVERGENCE, "line": None,
                            "detail": {"tool_use_id": key,
                                       "in_store": key in original_spent,
                                       "in_replay": key in replayed_spent}})
    for key in sorted(set(original_spent) & set(replayed_spent)):
        if original_spent[key] != replayed_spent[key]:
            divergences.append({"kind": SPENT_REF_DIVERGENCE, "line": None,
                                "detail": {"tool_use_id": key,
                                           "store": original_spent[key],
                                           "replay": replayed_spent[key]}})
    return divergences


__all__ = [
    "OP_ASSERT", "OP_REVOKE", "ASSERTION_COLUMN_FIELDS", "REVOCATION_TRIPLE",
    "ASSERT_LINE_FIELDS", "REVOKE_LINE_FIELDS", "AuditAppendFailed", "ChainTipUnreadable",
    "GENESIS_PREV_SHA256", "current_chain_tip", "payload_digest",
    "assert_line", "revoke_line", "append_line", "read_lines", "replay", "replay_into_store",
    "assertion_rows_by_uid", "spent_rows_by_key", "compare_store_to_replay",
    "ORPHAN_AUDIT_LINE", "STORE_ROW_WITHOUT_AUDIT_LINE", "FIELD_MISMATCH",
    "SPENT_REF_DIVERGENCE", "REVOKE_OF_UNKNOWN_ASSERTION", "REVOKE_FIELD_MISMATCH",
    "DUPLICATE_ASSERT_LINE", "MALFORMED_AUDIT_LINE", "DIGEST_MISMATCH", "CHAIN_BROKEN",
]
