"""ATLASSN-137 -- the consumption trail and the trigger-1 query definition (C11, section 34.5).

TWO TRAILS, OPPOSITE POSTURES, AND THE CONTRAST IS LOAD-BEARING.

  - The WRITE-AUDIT (ATLASSN-132) is FAIL-CLOSED. It backs a guarantee: the store must never
    advance past its tamper-evidence trail, so an append failure aborts the transaction and
    refuses the write.
  - This CONSUMPTION trail is FAIL-OPEN, deliberately, and says so out loud. It is an analytics
    channel. An append failure here must never alter, block or delay a read result, because a
    gate that denied real work over a failed analytics write would be trading a guarantee it
    does have for one it does not need.

Getting these two the same way round would be a serious defect in either direction: a fail-open
write-audit silently loses tamper evidence, and a fail-closed consumption trail turns a full
disk into a fleet-wide denial. They are in one codebase and must never be refactored into a
shared "append a line" helper that picks one posture for both.

TRIGGER 1 IS A QUERY DEFINITION SHIPPED FROM HERE AND EXECUTED ELSEWHERE. Section 34.5 puts the
execution home in the analytics plane, over ATLAS's ingested copy of these trails. What this
component owes is the DEFINITION plus evidence that it computes what it claims, which is why the
tests reproduce a rate worked out by hand rather than by calling this function twice.

TRIGGER 2 IS NOT HERE AND ITS ABSENCE IS INTENTIONAL. The too-long-lease trigger is procedural:
a postmortem-template item owned with the template, not a computation. Shipping it as a query
would dress a human review obligation up as a satisfied check, which is F4's exact shape -- a
check nobody reads, except worse, because it would look like one somebody did.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CONSUMPTION_PATH = (
    Path.home() / ".claude" / "foreman" / "registry" / "registry-consumption.jsonl")

# The declared line shape (schema doc, "Consumption JSONL"). Every field is always present;
# assertion_uid is null rather than omitted when no row resolved, so a reader never has to
# distinguish "absent key" from "no assertion".
LINE_FIELDS = ("ts", "edge_id", "assertion_uid", "computed_status", "consumer", "decision")

EXPIRED_STATUS = "expired"


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_line(result, consumer, ts=None):
    """One consumption record from one StatusResult."""
    return {
        "ts": ts or utc_now_iso(),
        "edge_id": result.edge_id,
        "assertion_uid": result.assertion_uid,
        "computed_status": result.status,
        "consumer": consumer,
        "decision": result.decision,
    }


def record_consultation(results, consumer, path=None, ts=None):
    """Append one line per consulted edge. FAIL-OPEN: never raises, never blocks the read.

    Returns the number of lines actually written, so a caller that wants to know can ask --
    but a caller that ignores the return value is behaving correctly, which is the difference
    between this and the write-audit.

    The whole body is wrapped, including the directory creation and the open, because every one
    of those can fail on a full or read-only disk and none of them is worth failing a gate read
    over. There is deliberately no logging fallback here either: a log write is another thing
    that can fail, and the trail's own absence is what the two-plane check in 34.4 detects.
    """
    target = Path(path) if path is not None else DEFAULT_CONSUMPTION_PATH
    written = 0
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps(build_line(result, consumer, ts)) + "\n")
                written += 1
    except Exception:
        # Analytics channel. The read already happened and its result stands.
        return written
    return written


def read_trail(path):
    """Parse a consumption trail into line dicts, skipping unparseable lines.

    Skipping is correct HERE, unlike in a fail-closed clause: this trail feeds a rate, a
    malformed line is a missing sample rather than a security decision, and refusing to compute
    a rate because one line is corrupt would make the analytics plane brittle for no gain.
    """
    lines = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return lines
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            lines.append(parsed)
    return lines


# The shipped definition, stated in words so it can be checked against the code and reimplemented
# on the analytics plane without reading this file's Python.
TRIGGER_ONE_DEFINITION = (
    "expiry-before-consumption rate = (number of consumption lines whose computed_status is "
    "'expired') divided by (number of consumption lines that resolved to an assertion, i.e. "
    "assertion_uid is not null), over the window under examination. Lines that resolved to no "
    "assertion are excluded from BOTH numerator and denominator: a never-registered edge says "
    "nothing about whether a lease was too short, and counting it would dilute the rate toward "
    "zero exactly when the registry is least populated. A window with no resolving lines has no "
    "rate, reported as None rather than as 0.0 -- 'no evidence' and 'evidence of no problem' "
    "are different answers and a too-short-lease trigger must not confuse them."
)


def expiry_before_consumption_rate(lines):
    """Trigger 1, per TRIGGER_ONE_DEFINITION. Returns a float, or None when undefined."""
    resolving = [line for line in lines if line.get("assertion_uid") is not None]
    if not resolving:
        return None
    expired = [line for line in resolving if line.get("computed_status") == EXPIRED_STATUS]
    return len(expired) / len(resolving)


def rate_from_trail(path):
    return expiry_before_consumption_rate(read_trail(path))


__all__ = [
    "DEFAULT_CONSUMPTION_PATH", "LINE_FIELDS", "build_line", "record_consultation",
    "read_trail", "TRIGGER_ONE_DEFINITION", "expiry_before_consumption_rate", "rate_from_trail",
    "utc_now_iso",
]
