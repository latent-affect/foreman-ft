#!/usr/bin/env python3
"""The one place a hook verdict is written to disk.

WHY THIS IS ITS OWN MODULE. The obvious home for this is hook_common, and it started there.
But hook_common imports audit_lib, and the handlers that most need a verdict recorded are
precisely the ones that deliberately avoid that dependency because they run on every single
write. A second copy of the record builder living in those handlers is how two ledgers that
are supposed to be joinable quietly stop agreeing on a field name.

WHAT A VERDICT IS FOR. Three states look identical from outside a hook: it examined the input
and correctly stayed quiet, it was never invoked at all, and it threw and failed open. The
coverage experiment cannot score a guard without telling them apart, and the third is the one
that matters most, because a guard that crashes and fails open is a guard that looks alive
and does nothing.

WHY NOT events.jsonl. Verdicts would inflate every denominator already displayed on the
dashboard the moment this landed. This project has produced five denominator errors so far
without any help.

This file is sensitive in the same way events.jsonl is: it carries absolute paths, session
ids and working directories. It lives under telemetry/, is chmod 600, and never leaves this
machine.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

VERDICT_LOG = Path.home() / ".claude" / "telemetry" / "verdicts.jsonl"

VALID_VERDICTS = ("fire", "silent", "error", "stolen")

# `stolen` is netfilter's NF_STOLEN: the hook took ownership and there is no observable
# continuation. Its product was a WRITE, not a decision.
#
# Before this existed, `fire` absorbed the side-effect case, which is worse than not seeing it
# because it MANUFACTURES a positive. Measured on the live ledger: session-log.sh 76 fire /
# 0 silent, telemetry_liveness.py 38 fire / 0 silent, guard_untrusted_web.py 17 fire / 0 silent.
# Their false-positive denominator is structurally zero, so they cannot contribute a precision
# observation, and the panel rendered them as guards with a perfect fire rate.
#
# capture_plan.py and capture_prompt.py were mislabelled the other way, recording `silent` for
# an anchor they had just written -- a guard that looked and correctly found nothing, which is
# exactly what capture_plan was NOT doing across 181 sessions.
#
# `target` is the path written, recorded by the hook that knows it. That field is the point: it
# turns the effect-side test from bespoke-per-hook into mechanical, because the test becomes
# "assert the path at target changed within the session". It is the consumer contract, written
# down at the moment of the write. The other netfilter verdicts are deliberately NOT imported:
# there is nothing to queue to and no re-entry, and a state that can never be written looks
# like coverage.
STOLEN = "stolen"

# The harness-level permission vocabulary, read from the shipped binary at versions/2.1.231.
# The merge lattice is deny > defer > ask > allow and it is a monotone accumulator, so a `deny`
# cannot lose regardless of hook arrival order.
#
# WHY THIS IS NOT `kind`. `kind` is the hook's OWN word for what it emitted -- "logged",
# "alerts", "context-injected" -- and each handler picks its own. `decision` is the value
# that actually entered the merge. Counting denials off `kind` would mean knowing every
# handler's private vocabulary and would silently miss any handler added later, which is the
# same class of defect as the two recorders disagreeing on a field name.
#
# Refused rather than written, for the same reason an unknown verdict is: a decision string the
# harness would never produce lands in a join as an unmatched row and reads as missing data
# rather than as a bug in the caller.
VALID_DECISIONS = ("deny", "defer", "ask", "allow")


def record(data, verdict, kind=None, duration_ms=None, handler_id=None, target=None,
           decision=None, rule_id=None):
    """Append one verdict line. Must never raise and must never block a hook.

    `handler_id` defaults to the script basename WITH its extension, which is the label
    telemetry_liveness.handler_label produces. That is not cosmetic: the dashboard joins these
    records against the liveness verdict's handlers_live and handlers_dead lists, and a label
    differing by an extension joins to nothing while looking perfectly reasonable.

    `probe_id` comes from the environment, the same variable telemetry.sh reads, so a verdict
    and the event that provoked it share a probe identity without either knowing about the
    other.
    """
    try:
        if decision is not None and decision not in VALID_DECISIONS:
            raise ValueError(f"unknown decision {decision!r}, expected one of {VALID_DECISIONS}")
        if verdict not in VALID_VERDICTS:
            # Caught here rather than allowed through, because an unrecognised verdict string
            # would land in the confusion matrix as a fourth silent category and be read as a
            # missing cell rather than as a bug in the caller.
            raise ValueError(f"unknown verdict {verdict!r}, expected one of {VALID_VERDICTS}")
        if not isinstance(data, dict):
            data = {}
        now = datetime.now(timezone.utc)
        record_obj = {
            "ts": now.isoformat().replace("+00:00", "Z"),
            "epoch_ms": int(now.timestamp() * 1000),
            "handler_id": handler_id or os.path.basename(sys.argv[0]),
            "event": data.get("hook_event_name"),
            "verdict": verdict,
            "kind": kind,
            "session_id": data.get("session_id"),
            "cwd": data.get("cwd"),
            "probe_id": os.environ.get("CLAUDE_PROBE_ID") or None,
            # The ledger is append-only and never truncated, so without a run boundary
            # the confusion matrix scores all history: a probe id reused after a fix
            # silently overwrote the earlier crash with the later fire, and leftover
            # ids from a previous run made the coverage tile render a negative count.
            "run_id": os.environ.get("CLAUDE_RUN_ID") or None,
            "tool_name": data.get("tool_name"),
            "tool_use_id": data.get("tool_use_id"),
            "self_duration_ms": duration_ms,
            # Only meaningful on a `stolen` verdict. Absent elsewhere rather than empty, so a
            # consumer can test presence instead of comparing against a sentinel.
            "target": str(target) if target else None,
            # The permission decision that entered the harness merge. Absent on a hook that
            # emitted no decision, so the compliance join filters on presence rather than
            # against a "none" sentinel.
            "decision": decision,
            # Which rule produced the decision. Without it a deny cannot be joined back to the
            # rule that caused it, and the two-arm comparison -- half the denials naming the
            # skill, half naming only the condition -- has no key to split on, because the arm
            # is a property of the RULE and not of the event that tripped it.
            "rule_id": rule_id,
        }
        record_obj = {k: v for k, v in record_obj.items() if v is not None}
        VERDICT_LOG.parent.mkdir(parents=True, exist_ok=True)
        # Append only. Several hook processes write this file concurrently, and appending one
        # short line is safe where a read-modify-write of a whole document is not.
        with VERDICT_LOG.open("a") as handle:
            handle.write(json.dumps(record_obj) + "\n")
        os.chmod(VERDICT_LOG, 0o600)
    except Exception as exc:
        # A ledger failure must not change what the guard decided, so this swallows the error
        # rather than propagating it. It says so on stderr instead: a verdict ledger that
        # quietly stopped recording would manufacture exactly the "correct silence" the
        # experiment is trying to measure, which is the worst possible way for this to fail.
        print(f"[verdict_ledger] write failed: {exc}", file=sys.stderr)
