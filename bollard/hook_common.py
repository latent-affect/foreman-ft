#!/usr/bin/env python3
"""Shared I/O + decision helpers for the misalignment-marker-search hooks.

Every hook reads a JSON object on stdin and communicates its decision by printing a JSON
object on stdout (exit 0). This module centralizes that contract and the audit-plane import
so the individual hooks stay short and consistent.

FAIL-OPEN by design: a bug in a guard hook must never brick the agent. `run()` wraps the
hook body in try/except; on ANY internal error it logs to the audit plane and lets the tool
call proceed (passthrough). A safety hook that blocks everything because it crashed is worse
than one that misses an edge case -- it just gets disabled, and then nothing is guarded.

THE VERDICT LEDGER. Every hook that goes through `run()` records what it decided, including
when it decided nothing. This exists because the coverage experiment has to tell three states
apart that all look identical from outside: a guard that examined the input and correctly
stayed quiet, a guard that was never invoked at all, and a guard that threw and failed open.
The third is the dangerous one -- it is a guard that looks alive and does nothing, which is
the failure this whole project was built to detect -- and before this it left no trace at the
point of failure beyond a HOOK_ERROR that nothing joined back to the event.

Verdicts go to their own file, NOT to events.jsonl. Mixing them would inflate every existing
denominator on the dashboard the moment this landed, and this project has already produced
five denominator errors without help. The record builder lives in verdict_ledger, so the
handlers that avoid importing audit_lib can record without a second copy of it.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_lib  # noqa: E402


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
# FORE-189: only deny() sets this today -- scoped to deny rows per that ticket's own text, not
# extended to ask()/block_stop() here, which is its own separate decision if wanted later.
EMITTED_REASON = None


INPUT_UNPARSEABLE = False
# CHV2-57: the raw bytes behind a stdin parse failure, held ONLY so run_body()'s error-recording
# path can salvage session_id/cwd for the ledger row. This must NEVER be threaded back into
# read_input()'s own return value -- eight direct callers of read_input() bypass run()/run_body()
# entirely (write_gate.py among them), and write_gate.py's fail-closed "no session_id -> deny"
# check depends on read_input() returning {} on a parse failure, byte-identical to before this fix.
RAW_INPUT_ON_PARSE_FAILURE = None


def read_input() -> dict:
    """Parse the hook payload. An unreadable payload is recorded, not swallowed.

    This used to return {} on a parse failure and say nothing. The guard would then examine an
    empty dict, find nothing to act on, and stay quiet -- and that silence was indistinguishable
    from a guard that read a real payload and correctly found it clean. The verdict ledger is
    built to separate those two, so it cannot be blind to the difference at the one place it
    originates.
    """
    global INPUT_UNPARSEABLE, RAW_INPUT_ON_PARSE_FAILURE
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError) as exc:
        INPUT_UNPARSEABLE = True
        RAW_INPUT_ON_PARSE_FAILURE = raw
        print(f"[hook_common] {os.path.basename(sys.argv[0])} could not parse its stdin "
              f"payload ({exc}); it ran against an empty payload and its silence from here "
              f"is a machinery failure, not a clean result.", file=sys.stderr)
        return {}


def origin_cwd(payload, max_lines=40):
    """FORE-562: the session's real ORIGIN directory, for consumers asking "which project is
    this SESSION about" with no write target to anchor on (pdp_state_inject.py,
    reinject_compact.py, ask_user_question_gate.py) -- never for a write-gating jurisdiction
    decision, where the live `payload["cwd"]` is the correct value FORE-568's own rule already
    settled (cwd for RESOLUTION is a use the target genuinely needs; cwd for JURISDICTION,
    after a mid-session `cd`, is the defect this function exists to route around).

    Reads the session's own transcript, scanning forward (bounded at max_lines) for the first
    parseable record that carries a `cwd` field -- not just line 1. FINDING-FORE-562-CWD-DRIFT-
    MEASURED-20260913.md: only 522 of 1,673 real transcripts carry a cwd on line 1 (the other
    69% open with a metadata record -- queue-operation, last-prompt, mode, fork-context-ref --
    that has none), but zero of 1,673 lack a cwd-bearing record within the first 40. A literal
    one-readline implementation combined with fail-closed-never-fall-back (the correct posture
    for what this value is trusted for) would have disabled its 3 real consumers on the
    majority of sessions, not corrected them.

    Fails CLOSED (returns None) on a missing transcript_path, an unreadable file, or no
    cwd-bearing record within the bound -- NEVER falls back to the live `payload["cwd"]` this
    function exists to distrust for jurisdiction use. A caller gets a real origin or nothing;
    it must never silently receive the drifted value under a name that claims otherwise.

    Disclosed, not resolved: a `--resume`d transcript's early records can carry a PRIOR
    session's already-drifted state rather than a true launch dir (`type='fork-context-ref'`,
    35/1,673 -- what it actually means is not yet established). Non-resumed sessions cannot
    have this problem structurally: every record before the first real Bash `cd` is metadata
    the harness writes before any tool call runs, so nothing in that window can be WRONG the
    way a post-cd read is -- only missing, which the bounded scan already handles.
    """
    transcript_path = payload.get("transcript_path") if isinstance(payload, dict) else None
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            for i, raw_line in enumerate(fh):
                if i >= max_lines:
                    break
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    value = record.get("cwd")
                    if isinstance(value, str) and value:
                        return value
    except OSError:
        return None
    return None


def target_path(tool_input):
    """The file this tool_input names, or "" if it names none.

    `file_path` is Edit/Write/MultiEdit's field. NotebookEdit's real payload field is
    `notebook_path`, confirmed from two independent places in this codebase (telemetry.sh:174,
    test_bob_write_gate.py:1185 -- the same two sources PROPOSAL-A1-CHV2-46-LEDGER-GUARD-
    REGISTRATION-20260908.md already cited for this exact fact). CHV2-48: six guards checked
    `file_path` alone and NotebookEdit silently no-op'd on all six, discriminating-control-
    confirmed by real subprocess execution against a live process
    (EXECUTED-EVIDENCE-CHV2-48-20260908.md) -- every real NotebookEdit call ran unguarded past
    every one of them.
    """
    ti = tool_input or {}
    return ti.get("file_path") or ti.get("notebook_path") or ""


def written_text(tool_input):
    """The newly-written text a tool_input carries, or "" if none.

    `content` is Write's field, `new_string` is Edit's (also read per `edits[]` entry for
    MultiEdit). `new_source` is NotebookEdit's real content field -- a single cell's new
    source, the same one-string shape as content/new_string, never a list (confirmed from a
    real payload, test_bob_write_gate.py:1185-1186: {"notebook_path": ..., "new_source": ...}).
    CHV2-61: three content-scanning guards each carried their own copy of this exact logic and
    none of the three read new_source, so a real NotebookEdit write scanned nothing in any of
    them -- same missing-field shape CHV2-48 already fixed for path resolution alone.
    """
    ti = tool_input or {}
    parts = []
    if ti.get("content"):
        parts.append(ti["content"])
    if ti.get("new_string"):
        parts.append(ti["new_string"])
    if ti.get("new_source"):
        parts.append(ti["new_source"])
    for edit in ti.get("edits") or []:
        if isinstance(edit, dict) and edit.get("new_string"):
            parts.append(edit["new_string"])
    return "\n".join(parts)


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
    global EMITTED_REASON
    EMITTED_REASON = reason
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


def rewrite(updated_input: dict, reason: str = None):
    """PreToolUse: allow the tool call to proceed, but with `updated_input` substituted for
    the original tool_input -- FORE-660 (Stage 3), the same construction-over-instruction
    shape FORE-659 (Stage 2) already applies to deny messages, taken one step further: for a
    narrow, evidenced case the correct action can be taken FOR the agent instead of merely
    named to it.

    Unlike deny()/ask(), this is not a veto. `updated_input` REPLACES the tool's entire
    tool_input (not a partial patch) -- per the harness's own contract, `updatedInput` must
    travel with permissionDecision "allow", used here because there is nothing for a human to
    confirm: the substitution already IS the corrected call. Callers that only want to change
    one field (e.g. agent_dispatch_gate.py's subagent_type substitution) must start from a
    copy of the real original tool_input and override just that field, not construct a bare
    dict of only the changed key -- passing a partial dict here would silently drop every
    other field of the original call (e.g. Agent's own prompt/description).

    Deliberately generic (bollard's own sibling rewrite() in a different hook_common.py is
    Bash-command-specific: `updated_input={"command": ...}` implicitly) -- this hook_common.py
    is shared by hooks over many different tools, not just Bash, so this takes the caller's
    already-complete replacement dict directly rather than assuming a "command" key exists."""
    mark("rewrite")
    mark_decision("allow")
    payload = {"hookEventName": "PreToolUse", "permissionDecision": "allow",
               "updatedInput": updated_input}
    if reason:
        payload["permissionDecisionReason"] = reason
    _emit({"hookSpecificOutput": payload})


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


def audit(event_type, data, session_id=None, cwd=None, severity="info"):
    try:
        audit_lib.audit_append(event_type, session_id=session_id, cwd=cwd,
                               severity=severity, **data)
    except Exception as exc:  # audit failure must not break a hook
        print(f"[hook_common] audit write failed: {exc}", file=sys.stderr)


RECORDED = False


def record_verdict(data, verdict, kind=None, duration_ms=None, target=None,
                   decision=None, rule_id=None, reason=None, direction=None):
    """Record what this hook decided. Delegates so there is exactly one record builder."""
    global RECORDED
    RECORDED = True
    verdict_ledger.record(data, verdict, kind=kind, duration_ms=duration_ms, target=target,
                          decision=decision, rule_id=rule_id, reason=reason, direction=direction)


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
            #
            # Real, live gap found and fixed 2026-08-27 (ATLASSN-31): this branch wrote the
            # verdict-ledger "error" row below but never attempted the matching audit-plane
            # HOOK_ERROR write run_body()'s own except clause makes for an ordinary Exception --
            # one-sided in exactly the same shape this project's fail_open_not_double_counted
            # contract check exists to catch (3 unpaired error rows from
            # nested_agent_notification_guard.py, real production data, is what surfaced this).
            # A guard killed by its configured timeout is an ordinary operational condition per
            # this function's own docstring, not a hypothetical -- it deserves the same
            # two-channel disclosure a plain crash gets, not a weaker one just because the
            # failure mode is different.
            try:
                audit_lib.audit_append("HOOK_ERROR",
                                       session_id=data.get("session_id"),
                                       cwd=data.get("cwd"),
                                       severity="high",
                                       hook=os.path.basename(sys.argv[0]),
                                       error="exited-without-recording (SystemExit/timeout/"
                                             "KeyboardInterrupt/MemoryError -- no Python "
                                             "exception was caught to describe more precisely)")
            except Exception as audit_exc:
                print(f"[hook_common] {os.path.basename(sys.argv[0])} could not record "
                      f"HOOK_ERROR to the audit plane for an exited-without-recording exit: "
                      f"{audit_exc!r}", file=sys.stderr)
            try:
                verdict_ledger.record(data, "error", kind="exited-without-recording",
                                      duration_ms=int((time.time() - started) * 1000))
            except Exception:
                pass
    sys.exit(0)


def run_body(main_fn, data, started):
    try:
        main_fn(data)
    except Exception as exc:
        try:
            audit_lib.audit_append("HOOK_ERROR",
                                   session_id=data.get("session_id"),
                                   cwd=data.get("cwd"),
                                   severity="high",
                                   hook=os.path.basename(sys.argv[0]),
                                   error=repr(exc))
        except Exception as audit_exc:
            # Said out loud rather than passed over. "the guard crashed" and "the guard
            # crashed AND its crash was never recorded to the audit plane" call for different
            # responses, and this was the only place left that could tell them apart.
            print(f"[hook_common] {os.path.basename(sys.argv[0])} could not record HOOK_ERROR "
                  f"to the audit plane: {audit_exc!r}", file=sys.stderr)
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
                           decision=EMITTED_DECISION, rule_id=EMITTED_RULE_ID,
                           reason=EMITTED_REASON)
        elif INPUT_UNPARSEABLE:
            # Emitted nothing, but not because it looked and found nothing. Scoring this as
            # correct silence would credit the guard for a decision it never got to make.
            #
            # ATLASSN-69: this branch is the THIRD writer of an `error` verdict in this module,
            # and until 2026-09-04 it was the only one with no matching audit-plane HOOK_ERROR
            # write -- the exited-without-recording branch was paired on 2026-08-28 (ATLASSN-31)
            # and run_body's except clause always was, but the closure claim recorded in
            # atlas-sonnet ARCHITECTURE.md 21.1 counted two branches where there are three.
            # Every unpaired production row (19 of them, all kind='unparseable-stdin', all from
            # nested_agent_notification_guard.py's stdin-fuzzing tests) came through here. Same
            # two-channel disclosure as the other two error paths, for the same reason: an
            # unparseable payload is a machinery failure, and one ledger asserting it while the
            # other stays silent is exactly the one-sided shape fail_open_not_double_counted
            # exists to catch.
            cutoff = RAW_INPUT_ON_PARSE_FAILURE.find('"tool_input"') if RAW_INPUT_ON_PARSE_FAILURE else -1
            scope = RAW_INPUT_ON_PARSE_FAILURE[:cutoff] if cutoff != -1 else (RAW_INPUT_ON_PARSE_FAILURE or "")
            salvaged = {}
            for _field in ("session_id", "cwd"):
                _m = re.search(r'"%s"\s*:\s*"([^"]*)"' % _field, scope)
                if _m:
                    salvaged[_field] = _m.group(1)
            salvage_kind = "unparseable-stdin-salvaged" if salvaged else "unparseable-stdin"
            try:
                audit_lib.audit_append("HOOK_ERROR",
                                       session_id=salvaged.get("session_id"),
                                       cwd=salvaged.get("cwd"),
                                       severity="high",
                                       hook=os.path.basename(sys.argv[0]),
                                       error="unparseable-stdin (payload was not JSON; guard "
                                             "ran against an empty dict and decided nothing)")
            except Exception as audit_exc:
                print(f"[hook_common] {os.path.basename(sys.argv[0])} could not record "
                      f"HOOK_ERROR to the audit plane for an unparseable-stdin exit: "
                      f"{audit_exc!r}", file=sys.stderr)
            record_verdict(salvaged, "error", salvage_kind, elapsed, rule_id=EMITTED_RULE_ID)
        else:
            # No decision, so no `decision` field -- but the rule identity still travels, because
            # the silent arm is the control the fire arm is measured against.
            record_verdict(data, "silent", None, elapsed, rule_id=EMITTED_RULE_ID)
    sys.exit(0)
