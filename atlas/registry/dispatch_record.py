"""ATLASSN-125 -- dispatch-record schema and consumption-time validator (ARCHITECTURE.md 34.7).

Field-level schema per DESIGN-REGISTRY-SCHEMA-AI5-ATLASSN-110-20260912.md's "Dispatch record"
section. Consulted by ATLASSN-131's D1 write path as its clause (a)-(d) gate before any
assertion write is accepted.

CONSUMPTION ONLY. This module does NOT create dispatch records, and that is a freeze-domain
boundary rather than a division of labour: §34.6's responsibility list gives the registry
dispatch-record CONSULTATION, `dispatchrecord_to_registry` declares the producer as an external
untrusted-input endpoint, and §34.7 assigns authorship to the dispatching authority. A writer
living here would let the consuming component mint its own clause-(a) inputs -- the confused
deputy the four-layer boundary exists to defeat. The writer is
claude-hooks-v2/hooks/verifier_dispatch_record.py (CHV2-69).

PRODUCER/CONSUMER AGREEMENT, VERIFIED NOT ASSUMED (2026-09-12, before this module was written):
the writer emits exactly {record_id, author_session_id, named_session_id, role, project,
repo_scope, created_at} plus an optional note, into ~/.claude/foreman/dispatch-records/, with
timestamps like 2026-09-12T15:00:00.123Z. The sessions stream's real lines carry `session_id`
and `ts` like 2026-08-10T01:51:37Z. Both formats are read here and both were checked against the
real artifacts on disk, because a silent shape disagreement across a repo boundary is exactly
the class of defect no test on either side would catch alone.

THE FOUR CLAUSES, in order, first failure wins:
  (a) author_session_id != named_session_id
  (b) created_at strictly precedes the named session's first appearance in the sessions stream
  (c) role == the caller-supplied required_role
  (d) record.project == the edge's repo EXACTLY, and the edge's component is a member of
      record.repo_scope EXACTLY -- never substring or prefix (falsification G5)

Original proposal authored by Alice; clause (b)'s fail-closed partition is hers after this
session flagged the direction of its failure. See first_seen() for why that matters.
"""

import json
from datetime import datetime
from pathlib import Path

DEFAULT_RECORDS_DIR = Path.home() / ".claude" / "foreman" / "dispatch-records"
DEFAULT_SESSIONS_STREAM = Path.home() / ".claude" / "telemetry" / "sessions.jsonl"

REQUIRED_KEYS = ("record_id", "author_session_id", "named_session_id", "role", "project",
                 "repo_scope", "created_at")
OPTIONAL_KEYS = ("note",)


class ValidationResult:
    """ok=True means all four clauses passed. ok=False always carries a named reason.

    `retryable` separates a transient dependency failure (the sessions stream unreachable) from
    a genuine rejection. A caller must not busy-retry a rejection: a self-authored record will
    still be self-authored in a second, and retrying one is how a refusal becomes a spin.
    """

    __slots__ = ("ok", "reason", "retryable")

    def __init__(self, ok, reason=None, retryable=False):
        self.ok = ok
        self.reason = reason
        self.retryable = retryable

    def __repr__(self):
        return (f"ValidationResult(ok={self.ok!r}, reason={self.reason!r}, "
                f"retryable={self.retryable!r})")

    def __eq__(self, other):
        return (isinstance(other, ValidationResult) and self.ok == other.ok
                and self.reason == other.reason and self.retryable == other.retryable)

    def __hash__(self):
        return hash((self.ok, self.reason, self.retryable))


def read_dispatch_record(record_id, records_dir=DEFAULT_RECORDS_DIR):
    """The parsed record, or None if no such record exists.

    Raises ValueError on a file that exists but is not valid JSON, is not an object, or is
    missing a required key. Absence and corruption are different facts and a caller has to be
    able to tell them apart: one means "nobody dispatched this", the other means "something is
    wrong with the store".
    """
    path = Path(records_dir) / f"{record_id}.json"
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"dispatch record {path} is not valid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError(f"dispatch record {path} is not a JSON object")
    missing = [key for key in REQUIRED_KEYS if key not in record]
    if missing:
        raise ValueError(f"dispatch record {path} is missing required key(s): {sorted(missing)}")
    return record


def parse_iso(timestamp):
    """Parse the stream's 'Z'-suffixed form and the writer's fractional-second form.

    Raises ValueError on anything else. A malformed timestamp must never silently sort as
    epoch-zero, which would make every record look like it predates everything.
    """
    text = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    return datetime.fromisoformat(text)


def first_seen(session_id, sessions_stream_path=DEFAULT_SESSIONS_STREAM):
    """("unreachable", None) | ("absent", None) | ("found", datetime).

    SECURITY-CRITICAL DIRECTION -- read this before simplifying anything below. Skipping a
    malformed line and continuing is correct for an ingest pipeline and wrong here, because
    skipping the wrong line moves the computed first-seen LATER than the truth, and clause (b)
    tests `created_at < first_seen_ts`. A later, wrong first_seen makes MORE records
    incorrectly PASS, not fewer. One corrupted line -- or one written out-of-band by the same T1
    actor §34.7 layer 2 already concedes can rewrite this file -- would turn a record that
    should fail clause (b) into one that passes. The bug is invisible unless the inequality and
    the direction of the error are held in mind together, which is why this paragraph exists.

    So the three cases are partitioned by what can be RULED OUT:
      - a line that cannot be attributed to any session (not JSON, or not an object) might have
        named this session with an even earlier ts that can no longer be read -> unreachable;
      - a line that IS this session's but whose ts is missing, non-string or unparseable is
        known to be this session's own record and cannot be used -> unreachable;
      - a line positively confirmed to name a DIFFERENT session is ruled out as a competing
        earlier appearance by the same check that identifies it -> safe to skip.

    Only the third is skipped. This keeps ordinary unrelated corruption in a shared stream from
    making the clause permanently unusable, without letting any unreadable line decide it.
    """
    path = Path(sessions_stream_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "unreachable", None

    earliest = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return "unreachable", None
        if not isinstance(entry, dict):
            return "unreachable", None
        if entry.get("session_id") != session_id:
            continue
        raw_timestamp = entry.get("ts")
        if not isinstance(raw_timestamp, str):
            return "unreachable", None
        try:
            seen_at = parse_iso(raw_timestamp)
        except ValueError:
            return "unreachable", None
        if earliest is None or seen_at < earliest:
            earliest = seen_at

    if earliest is None:
        return "absent", None
    return "found", earliest


def validate_dispatch_record(record, edge_repo, edge_component, required_role,
                             sessions_stream_path=DEFAULT_SESSIONS_STREAM):
    """The consumption-time gate. All four clauses, in order, first failure wins.

    `edge_repo` and `edge_component` are the asserted edge's already-RESOLVED repo and
    component. This function trusts them as resolved inputs and never re-derives them from
    anything in `record` -- deriving the comparison input from the artifact being checked would
    make clause (d) self-satisfying. Resolving them is ATLASSN-131's concern, through I2's
    shared parser, which is a different clause (C3) about a different thing.
    """
    missing = [key for key in REQUIRED_KEYS if key not in record]
    if missing:
        return ValidationResult(
            False, reason=f"malformed-dispatch-record:missing-{sorted(missing)[0]}")

    # (a) -- the confused-deputy check. A session may not author its own authorization.
    if record["author_session_id"] == record["named_session_id"]:
        return ValidationResult(False, reason="self-authored-dispatch-record")

    # (b) -- temporal. The stream is a hard third dependency (falsification G1): unreachable is
    # retryable, absent is a fail-closed rejection, never a skipped clause.
    stream_status, first_seen_at = first_seen(record["named_session_id"], sessions_stream_path)
    if stream_status == "unreachable":
        return ValidationResult(False, reason="sessions-stream-unreachable", retryable=True)
    if stream_status == "absent":
        return ValidationResult(False, reason="named-session-absent-from-stream")
    try:
        created_at = parse_iso(record["created_at"])
    except (ValueError, TypeError):
        return ValidationResult(False, reason="malformed-dispatch-record:bad-created_at")
    # Strictly precedes: a record written in the same instant the session started is not
    # evidence it was written BEFORE it.
    if not created_at < first_seen_at:
        return ValidationResult(False, reason="dispatch-record-postdates-session-start")

    # (c)
    if record["role"] != required_role:
        return ValidationResult(False, reason="role-mismatch")

    # (d) -- exact equality only, never substring or prefix (falsification G5).
    if record["project"] != edge_repo:
        return ValidationResult(False, reason="scope-mismatch:project")
    repo_scope = record.get("repo_scope")
    if not isinstance(repo_scope, list) or edge_component not in repo_scope:
        return ValidationResult(False, reason="scope-mismatch:component")

    return ValidationResult(True)


__all__ = [
    "DEFAULT_RECORDS_DIR", "DEFAULT_SESSIONS_STREAM", "REQUIRED_KEYS", "OPTIONAL_KEYS",
    "ValidationResult", "read_dispatch_record", "parse_iso", "first_seen",
    "validate_dispatch_record",
]
