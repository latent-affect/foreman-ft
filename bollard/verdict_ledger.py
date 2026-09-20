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

VALID_VERDICTS = ("fire", "silent", "error", "stolen", "not_applicable")

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

# FORE-571 C1. `agent_type` must be recorded VERBATIM, and `null` is a meaningful value: it
# denotes the main session rather than a subagent, which is the exact boundary FORE-528's premise
# rests on. Every other optional field here is stripped when None so a consumer can test presence
# -- that convention would silently delete this one's most common and most load-bearing value, and
# F2 names dropping it as a failure.
#
# So a sentinel distinguishes "the caller had nothing to say about agent_type" from "the caller
# says it is null", and the field is written AFTER the None-strip, exactly as ledger_origin and
# origin_signal already are and for the same stated reason: a row with no value here is the row
# the field exists to make impossible.
AGENT_TYPE_UNSET = object()

# Who wrote this row. FORE-273: 1,485 of 1,957 deny rows in the warehouse had no tool_use_id and
# 1,033 of those had cwd=/tmp -- guard test suites calling the real writer, indistinguishable from
# real traffic once landed, polluting every prior a downstream KPI would read.
#
# THIS FIELD IS A NOISE FILTER, NOT AN AUTHENTICITY CLAIM. Read that before using it for anything.
#
# `harness-heuristic` is named for the strength of the claim, on purpose (FORE-291). It is
# FORGEABLE: any local process that sets CLAUDE_PID to its own pid before spawning a descendant is
# classified `harness-heuristic` by both writers, reproduced live -- a plain python process with no
# claude anywhere in its chain clears the check. The value carries that caveat in its own name
# because a consumer meets the value and may never open this file.
#
# NO SECURITY OR EVASION-DETECTION DECISION MAY DEPEND ON THIS FIELD. It separates test noise from
# ordinary traffic among cooperating processes. It does not establish who wrote a row.
#
# STANDING CONSTRAINT (FORE-291, and the reason the value is named this way): if later work needs
# writer identity as EVIDENCE rather than as a noise filter -- the hook-gaming / evasion lane is
# the obvious candidate -- that requires an out-of-band mechanism and its own ticket. It must never
# arrive as an incremental strengthening of this heuristic. Each strengthening step looks locally
# reasonable while the claim quietly re-inflates, and a hook runs as the same user with the same
# environment as anything trying to spoof it, so no amount of local inspection closes the class.
# Considered and deliberately NOT adopted: `ps -o comm= -p $CLAUDE_PID` (a process name is as
# forgeable as the pid, plus 1.64ms per call against 0.58us on a path that blocks every tool call)
# and the macOS ctypes proc_pidpath variant (cheaper, same structural ceiling, and its real risk is
# becoming the excuse to keep the strong claim).
#
# `harness-heuristic` is still a POSITIVE ASSERTION rather than a default: every ambiguous case
# resolves to something else, because the failure that matters is a test row counted as real
# traffic, not a real row conservatively labelled `descendant`.
#
# Env inheritance is why this cannot be one env var. A test suite launched from inside a Claude
# session inherits CLAUDECODE, CLAUDE_PID and everything else the harness exports -- so a passive
# env marker says "somewhere under a session", true of both populations, discriminating nothing.
# The parent-pid comparison separates them for the ACCIDENTAL case, which is the case that matters
# here: the harness spawns a hook itself, so a hook's parent IS the session process, while a
# test-spawned guard's parent is the test runner. ORIGIN_ENV is the deterministic path and does the
# real work; the pid comparison is the free fallback for a runner that did not announce itself.
ORIGIN_ENV = "CHV2_LEDGER_ORIGIN"
VALID_ORIGINS = ("harness-heuristic", "test", "cli", "descendant", "unknown")

# FORE-189: hc.deny(reason) already has this text in hand at the moment it writes a verdict --
# without it, the letter-versus-intent rubric's COMPLIED/ILLUSORY distinction (what the deny
# actually told the agent to do) is recoverable only via a transcript join, which fails outright
# for any deny whose transcript is missing. Bounded, not a free-text blob: this ledger is
# chmod 600 and never leaves this machine, but an unbounded field is still an unbounded field,
# and a silent mid-sentence cut would read as a different (shorter) remedy to the rubric than
# the one actually given -- so a truncation marks itself rather than looking complete.
# CHV2-119: the bound was right and the CUT was in the wrong place. FORE-189's reasoning above
# is unchanged and still holds -- the field must stay bounded, and a truncation must mark itself.
# What it did not anticipate is WHERE the remedy sits. FORE-659 put the literal, runnable next
# command at the END of every deny message, and head-truncation discards the end. So the one
# thing this field exists to record, "what the deny actually told the agent to do", was exactly
# the thing being dropped, and TRUNCATION_SUFFIX said only that something was lost, never what.
#
# Measured (agent_dispatch_gate, real subprocesses, real messages): the ListAgents() literal sits
# at byte offset 551 in the SHORTEST deny this gate can emit (772 chars, empty tool_input) and at
# 527 with a one-character subagent_type. Both past 500. There was therefore no payload at all
# for which remedy (2) reached the ledger -- not an unlucky message, a structural certainty.
#
# Keeping the head AND the tail fixes that for every hook at once, without any hook changing.
# The split is derived from the real content rather than picked:
#
#   TAIL 350  >= write_gate.RECOVERY_NOTE's 341 chars (read from source), which is appended to
#             the end of five deny reasons. A shorter tail cuts the recovery guidance mid-way --
#             the same defect one file over. 350 keeps it whole for ANY prefix length.
#   HEAD 300  is what the measured agent_dispatch_gate messages need to carry the policy opener
#             plus remedy (1), which sit at the front.
#
# Both existing bound tests keep passing unmodified because they reference MAX_REASON_LEN
# symbolically rather than the literal 500, and it remains the total content budget.
#
# RESIDUAL, DISCLOSED AND NOT FIXED HERE: an agent-supplied subagent_type is interpolated into
# the middle of agent_dispatch_gate's message, unbounded and repr'd twice. A 600-char one
# produces a 1946-char reason whose middle is the caller's own string, and no truncation
# strategy can keep a remedy that has been pushed out of both ends -- measured, remedy (1) is
# lost under every candidate including this one. That is a cap belonging at the message
# construction site, in a different file, and it is a separate change from this one.
MAX_REASON_HEAD = 300
MAX_REASON_TAIL = 350
MAX_REASON_LEN = MAX_REASON_HEAD + MAX_REASON_TAIL
TRUNCATION_SUFFIX = "...[truncated]"


def _bound_reason(reason):
    if reason is None:
        return None
    if len(reason) <= MAX_REASON_LEN:
        return reason
    return reason[:MAX_REASON_HEAD] + TRUNCATION_SUFFIX + reason[-MAX_REASON_TAIL:]


def resolve_origin():
    """Return (origin, signal). Never raises: an origin that cannot be resolved is `unknown`,
    which is a recordable answer, and never `harness-heuristic`.

    `signal` is the evidence, recorded alongside the verdict rather than discarded, so a
    misclassification can be diagnosed from the ledger instead of re-derived by argument. It is
    also how this classification gets falsified on real traffic: if harness-launched hooks come
    back `descendant`/`ppid-not-claude`, the harness spawns hooks through an intermediate process
    and the comparison below needs to walk one more level -- a fact about the harness that is
    measured here rather than assumed.

    Costs nothing on the hot path: environment reads and one getppid(), no subprocess.
    """
    try:
        override = os.environ.get(ORIGIN_ENV)
        if override in VALID_ORIGINS:
            return override, "env-override"
        # In-process test invocation: the guard was imported by a test rather than spawned. Also
        # exports ORIGIN_ENV so any guard this test process spawns LATER inherits the deterministic
        # label instead of falling back to the pid comparison. That propagation is why a test
        # module only has to reach this module once, by any path, to cover its own subprocesses.
        if "unittest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            os.environ.setdefault(ORIGIN_ENV, "test")
            return "test", "test-runner-in-process"
        claude_pid = os.environ.get("CLAUDE_PID")
        if claude_pid and claude_pid.isdigit():
            if os.getppid() == int(claude_pid):
                return "harness-heuristic", "ppid-is-claude"
            return "descendant", "ppid-not-claude"
        if os.environ.get("CLAUDECODE"):
            return "descendant", "claudecode-no-claude-pid"
        return "cli", "no-session-env"
    except Exception:
        return "unknown", "resolve-failed"


def _write_fallback_marker(resolved_handler_id, verdict, data, exc):
    """CHV2-1, porting DEVH-77's behavior (bollard's verdict_ledger.py) into this module on its
    own terms -- a different lineage, its own field set (ledger_origin/origin_signal), not
    copied in and not a restructuring toward bollard's shape.

    WHY THIS EXISTS. record()'s own docstring already states the invariant: never raise, never
    block a hook. Its except-Exception handler prints to stderr on any write failure and
    returns -- and stderr in a hook subprocess (under launchd once armed, or inside an ordinary
    harness turn) is not a durable record. Traced concretely for CHV2-1: bob_write_gate.py
    resolves its own `import verdict_ledger` to THIS file (sys.path.insert on its own directory
    then a direct import, confirmed by resolving it exactly as the real process would), and
    bypasses hook_common's record_verdict entirely for its own top-level verdict -- a custom
    `finally` block calls this module's record() directly for every verdict the gate ever
    writes. So before this fix, a failed ledger write during the operator's one bounded,
    personally-supervised first arming run is indistinguishable on disk from the gate correctly
    deciding to stay silent.

    THE RETRY TARGET IS THE SAME VERDICT_LOG PATH, deliberately, matching DEVH-77's own choice
    rather than inventing a separate fallback location: this makes the marker only actually
    recoverable for a TRANSIENT failure (a momentary disk condition, a concurrent-writer race),
    not a structurally broken path (VERDICT_LOG's own parent being unwritable, say) -- in that
    second case this retry fails identically to the first attempt, every time, and there is
    nothing else this function can do about it. That is a real, inherited limit of the
    mechanism, not a defect this port introduces; recorded here so a future reader does not
    mistake this function for a general failure-immune write path.

    FAIL-CLOSED DECISION, made deliberately rather than by default: if the marker write ITSELF
    fails, that failure is caught and swallowed here too, never re-raised. record()'s own
    invariant ("must never raise and must never block a hook") is absolute and this function is
    reached FROM inside record()'s own except block -- letting an exception escape here would
    propagate past record() entirely and into the caller's own control flow (bob_write_gate.py's
    finally block has no wrapping try/except around this call), which could crash a hook process
    after it had already decided allow/deny. A hook that crashes on a double ledger failure is a
    strictly worse outcome than one that loses a diagnostic marker and prints to stderr as the
    last resort -- the same trade DEVH-77 made in bollard, for the same structural reason, kept
    here rather than reopened."""
    try:
        now = datetime.now(timezone.utc)
        origin, origin_signal = resolve_origin()
        fallback_obj = {
            "ts": now.isoformat().replace("+00:00", "Z"),
            "epoch_ms": int(now.timestamp() * 1000),
            "handler_id": resolved_handler_id,
            "ledger_write_failed": True,
            "attempted_verdict": verdict if isinstance(verdict, str) else repr(verdict),
            "error": repr(exc),
            "session_id": data.get("session_id") if isinstance(data, dict) else None,
            "ledger_origin": origin,
            "origin_signal": origin_signal,
        }
        fallback_obj = {k: v for k, v in fallback_obj.items() if v is not None}
        VERDICT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with VERDICT_LOG.open("a") as handle:
            handle.write(json.dumps(fallback_obj) + "\n")
        os.chmod(VERDICT_LOG, 0o600)
    except Exception as fallback_exc:
        print(f"[verdict_ledger] fallback write also failed: {fallback_exc}", file=sys.stderr)


def record(data, verdict, kind=None, duration_ms=None, handler_id=None, target=None,
           decision=None, rule_id=None, reason=None, direction=None,
           agent_type=AGENT_TYPE_UNSET):
    """Append one verdict line. Must never raise and must never block a hook.

    `handler_id` defaults to the script basename WITH its extension, which is the label
    telemetry_liveness.handler_label produces. That is not cosmetic: the dashboard joins these
    records against the liveness verdict's handlers_live and handlers_dead lists, and a label
    differing by an extension joins to nothing while looking perfectly reasonable.

    `probe_id` comes from the environment, the same variable telemetry.sh reads, so a verdict
    and the event that provoked it share a probe identity without either knowing about the
    other.
    """
    resolved_handler_id = handler_id or os.path.basename(sys.argv[0])
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
        origin, origin_signal = resolve_origin()
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
            # FORE-189: present only on a deny (today's only caller that sets it, via
            # hook_common.deny()) -- absent otherwise, same presence-over-sentinel convention as
            # `target` and `decision` above.
            "reason": _bound_reason(reason),
            "direction": direction,
        }
        record_obj = {k: v for k, v in record_obj.items() if v is not None}
        # Set AFTER the None-strip, deliberately. Every other field here is absent when it has
        # nothing to say, so a consumer can test presence; these two are the opposite -- a row
        # with no origin is exactly the row this field exists to make impossible, so they are
        # always present and `unknown` carries the failure rather than absence.
        record_obj["ledger_origin"] = origin
        record_obj["origin_signal"] = origin_signal
        # FORE-571 C1, and set here for the same reason as the two lines above: `null` is this
        # field's meaningful "main session, not a subagent" value and the None-strip would erase
        # exactly that case. Written verbatim -- not normalized, not defaulted, not coerced to a
        # string -- because F2 makes any of those a failure and because a consumer distinguishing
        # session-wrote from subagent-wrote needs the payload's own value, not this module's
        # opinion of it.
        if agent_type is not AGENT_TYPE_UNSET:
            record_obj["agent_type"] = agent_type
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
        # CHV2-1 (porting DEVH-77): stderr alone leaves a lost row indistinguishable from
        # correct silence to anything reading VERDICT_LOG after the fact -- see
        # _write_fallback_marker's own docstring for the full trace of why that mattered enough
        # to fix before the operator's first arming run.
        _write_fallback_marker(resolved_handler_id, verdict, data, exc)
