#!/usr/bin/env python3
"""Shared I/O + decision helpers for Bollard's hooks.

Every hook reads a JSON object on stdin and communicates its decision by printing a JSON
object on stdout (exit 0). This module centralizes that contract so the individual hooks stay
short and consistent.

FAIL-OPEN by design: a bug in a guard hook must never brick the agent. `run()` wraps the
hook body in try/except; on ANY internal error it records the failure to the verdict ledger
and lets the tool call proceed (passthrough). A safety hook that blocks everything because it
crashed is worse than one that misses an edge case -- it just gets disabled, and then nothing
is guarded.

THE VERDICT LEDGER. Every hook that goes through `run()` records what it decided, including
when it decided nothing. This exists because the coverage experiment has to tell three states
apart that all look identical from outside: a guard that examined the input and correctly
stayed quiet, a guard that was never invoked at all, and a guard that threw and failed open.
The third is the dangerous one -- it is a guard that looks alive and does nothing, which is
the failure this whole project was built to detect.

Verdicts go to their own file, NOT to events.jsonl. Mixing them would inflate every existing
denominator on the dashboard the moment this landed, and this project has already produced
five denominator errors without help. The record builder lives in verdict_ledger, so every
handler records through one shared path rather than a copy each.

(This originally also wrote a HOOK_ERROR entry to a second, project-specific audit-plane
ledger belonging to an unrelated internal research project. Dropped for this release -- the
verdict ledger below already records every error verdict on its own, and that second ledger
had nothing to do with Bollard.)
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class VerdictLedgerUnavailable:
    """Stand-in used when verdict_ledger cannot be imported. Records nothing, says so once.

    The instrument must never be able to disable the guards it measures. Before this, every
    hook_common guard imported verdict_ledger at module scope with no guard, so one unreadable
    file took out guard_destructive, guard_install, guard_prodconfig, scan_write,
    verify_input_schema, session_flag_check, reinject_compact, capture_plan and capture_prompt
    at once. Reproduced in review: with the module corrupted, guard_destructive's DENY of an
    `mkfs` command vanished, the tool proceeded, and nothing was recorded anywhere.

    Catching Exception rather than ImportError is deliberate: the reproduction was a
    SyntaxError, which ImportError does not catch.
    """

    def __init__(self, exc):
        self.exc = exc
        self.warned = False

    def record(self, *args, **kwargs):
        if not self.warned:
            self.warned = True
            print(f"[hook_common] verdict ledger unavailable ({self.exc!r}); this hook still "
                  f"ran and still made its decision, but nothing will be recorded, so its "
                  f"absence from the ledger is not evidence about the guard.", file=sys.stderr)


try:
    import verdict_ledger  # noqa: E402
except Exception as verdict_import_exc:  # noqa: BLE001 -- see the class docstring above
    verdict_ledger = VerdictLedgerUnavailable(verdict_import_exc)

# Set by the decision helpers below, read by run(). A hook that emitted nothing leaves this
# None, which is the "correct silence" cell of the confusion matrix.
EMITTED_KIND = None
EMITTED_DECISION = None
EMITTED_RULE_ID = None


INPUT_UNPARSEABLE = False


def read_input() -> dict:
    """Parse the hook payload. An unreadable payload is recorded, not swallowed.

    This used to return {} on a parse failure and say nothing. The guard would then examine an
    empty dict, find nothing to act on, and stay quiet -- and that silence was indistinguishable
    from a guard that read a real payload and correctly found it clean. The verdict ledger is
    built to separate those two, so it cannot be blind to the difference at the one place it
    originates.
    """
    global INPUT_UNPARSEABLE
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError) as exc:
        INPUT_UNPARSEABLE = True
        print(f"[hook_common] {os.path.basename(sys.argv[0])} could not parse its stdin "
              f"payload ({exc}); it ran against an empty payload and its silence from here "
              f"is a machinery failure, not a clean result.", file=sys.stderr)
        return {}


def _emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def mark(kind: str):
    """Record which decision this hook reached, for the verdict ledger.

    Kept separate from `_emit` rather than inferred from the payload it was handed. Inferring
    would mean re-deriving "deny vs ask" from the shape of a dict at read time, and a guard
    whose decision type is guessed by the instrument measuring it is not measured.
    """
    global EMITTED_KIND
    EMITTED_KIND = kind


def mark_decision(decision: str):
    """Record the permission decision that entered the harness merge.

    Deliberately NOT folded into mark(). `kind` is doing double duty: measured on the live
    ledger it carries nineteen different vocabularies, and "deny" is one word among "logged",
    "candidates", "wrap" and "not-a-commit". Counting denials off `kind` therefore means knowing
    which words are decisions and which are a handler's private label, and it silently misses any
    handler added afterwards. The two are set together for the decision helpers below and would
    look redundant there, which is exactly why this is worth writing down: they diverge for every
    hook that marks a kind without emitting a decision, and that is most of them.
    """
    global EMITTED_DECISION
    EMITTED_DECISION = decision


def set_rule(rule_id: str):
    """Name the rule that produced this hook's decision, so a deny joins back to its cause.

    Recorded on EVERY verdict rather than only on a deny. A rule that evaluated and stayed
    silent is the control arm for the rule that fired, and dropping its identity would leave the
    silent side of the ledger unattributable -- the same shape of gap as scoring a crash as a
    correct silence.
    """
    global EMITTED_RULE_ID
    EMITTED_RULE_ID = rule_id


# ---- PreToolUse decisions -------------------------------------------------
def deny(reason: str):
    mark("deny")
    mark_decision("deny")
    _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "permissionDecision": "deny",
                                  "permissionDecisionReason": reason}})


def ask(reason: str):
    mark("ask")
    mark_decision("ask")
    _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "permissionDecision": "ask",
                                  "permissionDecisionReason": reason}})


# ---- PostToolUse (non-blocking warning) -----------------------------------
def warn(context: str):
    mark("warn")
    _emit({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                  "additionalContext": context}})


def warn_with_system_message(system_message: str, context: str):
    """A PostToolUse warning that is ALSO shown to the human in the terminal.

    Exists so no hook has to hand-roll the envelope to get a `systemMessage` field. A hook that
    writes straight to stdout bypasses mark(), and run() then records `silent` for a decision
    that was genuinely emitted -- scoring as a miss on a fire-expected probe, or as a correct
    silence on a silent-expected one. The live instance was tool_docs_first_nudge.py, which
    needs systemMessage because warn() does not carry it.
    """
    mark("warn")
    _emit({"systemMessage": system_message,
           "hookSpecificOutput": {"hookEventName": "PostToolUse",
                                  "additionalContext": context}})


# ---- SessionStart / UserPromptSubmit context injection --------------------
def inject(event_name: str, context: str):
    mark("inject")
    _emit({"hookSpecificOutput": {"hookEventName": event_name,
                                  "additionalContext": context}})


# ---- Stop (block completion) ----------------------------------------------
def block_stop(reason: str):
    mark("block_stop")
    _emit({"decision": "block", "reason": reason})


def passthrough():
    """Do nothing; let the action proceed. Empty stdout, exit 0."""
    return


# ---- side effects, which are not decisions ---------------------------------
STOLEN_TARGET = None


def stolen(target):
    """Record that this hook's product was a WRITE, not a decision. `target` is the path.

    netfilter's NF_STOLEN: the hook took ownership and there is no observable continuation.

    Without this, a side-effect hook had two ways to be mislabelled and both were live.
    session-log.sh recorded `fire`, which MANUFACTURES a positive: 76 fires against 0 silents,
    a structurally empty false-positive denominator, and a panel row reading as a guard with a
    perfect fire rate. capture_plan.py recorded `silent`, which reads as a guard that looked and
    correctly found nothing -- precisely what it was NOT doing across 181 sessions where it
    captured no plan at all.

    `target` is the consumer contract written at the moment of the write. It makes the
    effect-side test mechanical -- assert the path changed within the session -- instead of
    bespoke per hook, which is how capture_plan's fix would otherwise have to be re-derived for
    capture_prompt, session-log and every future rule that records and says nothing.
    """
    global STOLEN_TARGET
    STOLEN_TARGET = str(target)


RECORDED = False


def record_verdict(data, verdict, kind=None, duration_ms=None, target=None,
                   decision=None, rule_id=None):
    """Record what this hook decided. Delegates so there is exactly one record builder."""
    global RECORDED
    RECORDED = True
    verdict_ledger.record(data, verdict, kind=kind, duration_ms=duration_ms, target=target,
                          decision=decision, rule_id=rule_id)


def run(main_fn):
    """Entry point: read stdin, call main_fn(data), fail-open on any error.

    The recording is in a `finally`, not only in the except/else pair, because `SystemExit`
    derives from `BaseException` and therefore misses BOTH. A hook body that reaches a decision
    and then calls `sys.exit()` used to emit its decision and leave no trace of it, which reads
    downstream as `never_invoked` -- a guard that denied a tool call, scored as one that was
    never given the chance. The same hole swallowed a hook killed by its configured timeout,
    which is an ordinary operational condition rather than a hypothetical.
    """
    started = time.time()
    data = read_input()
    try:
        run_body(main_fn, data, started)
    finally:
        if not RECORDED:
            # Reached only when neither branch below ran: SystemExit, KeyboardInterrupt, a
            # timeout signal, or a MemoryError. Recorded as an error because the guard did not
            # complete, whatever it managed to print on the way out.
            try:
                verdict_ledger.record(data, "error", kind="exited-without-recording",
                                      duration_ms=int((time.time() - started) * 1000))
            except Exception as ledger_exc:
                print(
                    f"[hook_common] failed to record exited-without-recording: {ledger_exc}",
                    file=sys.stderr,
                )
    sys.exit(0)


def run_body(main_fn, data, started):
    try:
        main_fn(data)
    except Exception as exc:
        print(f"[hook_common] {os.path.basename(sys.argv[0])} error (failing open): {exc}",
              file=sys.stderr)
        # fail open: no decision emitted => tool proceeds. Recorded as its own verdict rather
        # than as silence, because a guard that crashed and a guard that looked and found
        # nothing are the two states this experiment exists to stop conflating.
        # rule_id travels on the crash path too. A rule that crashes before deciding is the case
        # the `error` verdict exists for, and without its identity the crash cannot be attributed
        # to the rule that caused it -- which is the whole reason `error` was split from `silent`.
        record_verdict(data, "error", type(exc).__name__, int((time.time() - started) * 1000),
                       rule_id=EMITTED_RULE_ID)
    else:
        elapsed = int((time.time() - started) * 1000)
        if STOLEN_TARGET:
            # Checked BEFORE EMITTED_KIND. A hook may legitimately do both -- write an anchor
            # and then inject context -- and the write is the claim that can be verified from
            # outside the process, so it is the one that must survive into the ledger.
            record_verdict(data, "stolen", EMITTED_KIND, elapsed, target=STOLEN_TARGET,
                           decision=EMITTED_DECISION, rule_id=EMITTED_RULE_ID)
        elif EMITTED_KIND:
            record_verdict(data, "fire", EMITTED_KIND, elapsed,
                           decision=EMITTED_DECISION, rule_id=EMITTED_RULE_ID)
        elif INPUT_UNPARSEABLE:
            # Emitted nothing, but not because it looked and found nothing. Scoring this as
            # correct silence would credit the guard for a decision it never got to make.
            record_verdict(data, "error", "unparseable-stdin", elapsed, rule_id=EMITTED_RULE_ID)
        else:
            # No decision, so no `decision` field -- but the rule identity still travels, because
            # the silent arm is the control the fire arm is measured against.
            record_verdict(data, "silent", None, elapsed, rule_id=EMITTED_RULE_ID)
    sys.exit(0)
