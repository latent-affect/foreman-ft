#!/usr/bin/env python3
"""PDP turn-boundary gate (FORE-170, REQ-38). Stop hook.

Unifies REQ-35 (finite named stop conditions), REQ-36 (batch gate dispatch) and REQ-37
(self-scheduled continuation) into one check that runs at the turn boundary itself, so the
process is enforced by the harness rather than recalled by the session. Design and the full
flowchart: /Users/m5/dev/foreman-v2/docs/PDP-FLOWCHART.md.

The finding this exists for: REQ-35/36/37 were all written, were confirmed by the build
session repeatedly, and it still went idle repeatedly. Prose cannot bind at a turn boundary --
the one moment with no pending tool call and no forcing function. So this refuses the stop.

FAIL-OPEN, DELIBERATELY, and this inverts the project's usual posture. For a privacy
guarantee, failing open ships an identifying artifact silently, so fail-closed is right.
Here, failing closed means a session that can never end: unbounded token spend, recoverable
only by killing it. Failing open means at worst the original stall, which is visible and has
a watchdog behind it. For the continuation-authority branch below, every error path, every
unknown, and the consecutive-block ceiling all allow the stop.

FORE-345 ADDS A SECOND BRANCH THAT INVERTS THIS, for the orchestrator claim only, and the
paragraph above no longer describes the whole file. That branch fails CLOSED on every error
path, has no block ceiling, and does not run under hc.run() -- because that wrapper would
allow the stop its logic just refused. Scope determination still fails open: a session this
hook cannot identify is given no obligation. The two postures govern two different roles and
neither is a default for the other.

CHV2-43 (REQ-72's second half): `_orchestrator_scope()` now delegates its claim read to
`session_role.orchestrator_claim()` (CHV2-37) instead of this file's own `read_orchestrator()`,
which is removed below -- centralizing the read+session_id-match logic a future hook needing
the same check would otherwise reimplement a third time (PDP.md section 16.1's own
verdict_ledger.py warning). The raw claim dict is still what `_orchestrator_scope()` returns,
not a bare role string: `_run_orchestrator_branch`'s S4 staleness check reads `claimed_at`
directly off it, and `session_role.resolve_role()`'s bare-string return cannot supply that --
the regression this project's own architecture pass named before this rewire was written
(session_role.GOALS.json C3). `read_authority()` and `main()`'s continuation-authority branch
are UNCHANGED -- out of this ticket's own stated scope (session_role.GOALS.json out_of_scope:
"CHV2-43's own scope is the orchestrator-branch rewire specifically, not a general refactor of
the hook").
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import session_role  # noqa: E402

RULE_ID = "FOREMAN-PDP-TURN-GATE"

# Provisional PoC value (FORE-170). NOT a settled constant -- no evidence behind the exact
# number; calibrate from real PoC data. One block is a reminder, three is a fight.
MAX_CONSECUTIVE_BLOCKS = 2

# REQ-35's four stop conditions, verbatim keys. The list is exhaustive by REQ-35's own text.
STOP_CONDITIONS = {
    "discard-completed-work",
    "land-shared-infra",
    "gate-kill",
    "declare-done",
}

# Dispositions that mean "this requirement is NOT standing work".
#
# Read carefully: `disposition` is NOT a validated progress field. worksite/registry.py
# constrains `state` against READINESS_STATES but places no constraint on `disposition` at
# all -- it is merely REQUIRED when state == "deferred-by-choice". The progress values below
# were adopted by build sessions mid-run as a convention, not designed as a schema. So this
# is an explicit allow-list and anything outside it counts as standing work, which surfaces
# schema drift loudly instead of silently misclassifying it. The block ceiling bounds the
# cost of being wrong. Filed for worksite to formalise.
NOT_STANDING = {
    "closed",
    "superseded",
    "research",
    "held-dogfood-sufficient",
    "measured-informational-only",
    # FORE-172, semantics supplied by the session that coined it (foreman-v2-c1): implementation
    # complete and verified; the sole remaining step is an explicit shared-infra landing decision
    # that belongs to the operator, i.e. REQ-35 condition 2 scoped to one item. Terminal per-item,
    # NOT turn-ending -- it must not keep feeding the "N un-implemented" count, and it is not a
    # reason to stop the turn. Old spelling kept so a mid-rename file still reads correctly.
    "verified-pending-operator-landing",
    "verified-and-held",
}

# States that are not available to work on now, regardless of disposition.
UNAVAILABLE_STATES = {"blocked-on-precondition", "deferred-by-choice"}


def read_authority(root):
    """`.foreman/pdp-continuation-authority.json` names the ONE session holding REQ-35's
    standing continuation authority. Absent or unreadable means nobody holds it and the gate
    enforces nothing -- deliberately, since a gate that imposes obligations on sessions that
    never accepted them is worse than one that is briefly inert."""
    try:
        blob = json.loads((root / ".foreman" / "pdp-continuation-authority.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return blob if isinstance(blob, dict) and blob.get("session_id") else None


def allow_early(root, session_id, note):
    """Allow without touching the block counter: a session the gate does not govern must not
    accumulate state that would change another session's behaviour."""
    hc.set_rule(f"{RULE_ID}:{note}")
    return


# CHV2-12. Measured baseline to regress against: 792 turn-final blocks/day, avg 1264 bytes, max
# 5615 (ATLAS session_assistant_text, HOOKS-VS-PDP-AND-SUMMARY-BLOAT-20260904.md Part 2 2.1).
MESSAGE_BYTE_LIMIT = 1500


def _message_too_long(data):
    """(too_long, byte_len). Byte length, not character count -- the measured baseline this
    threshold regresses against is itself byte-denominated. `last_assistant_message` is a real,
    directly-provided Stop-hook field (confirmed against Claude Code's documented schema), not
    the transcript file -- no parsing, no async-flush timing race to worry about. Absent or
    non-string fails toward NOT blocking, same fail-open direction as the rest of this
    population: a message this hook cannot measure cannot be fairly judged against a limit."""
    message = data.get("last_assistant_message")
    if not isinstance(message, str) or not message:
        return False, 0
    n = len(message.encode("utf-8"))
    return n > MESSAGE_BYTE_LIMIT, n


def state_path(root):
    return root / ".foreman" / "pdp-turn-gate-state.json"


def load_state(root, session_id):
    try:
        blob = json.loads(state_path(root).read_text())
    except (OSError, json.JSONDecodeError):
        blob = {}
    entry = blob.get(session_id) or {}
    return blob, {
        "last_stop_ts": float(entry.get("last_stop_ts") or 0.0),
        "consecutive_blocks": int(entry.get("consecutive_blocks") or 0),
    }


def save_state(root, session_id, blob, last_stop_ts, consecutive_blocks):
    blob[session_id] = {
        "last_stop_ts": last_stop_ts,
        "consecutive_blocks": consecutive_blocks,
    }
    try:
        state_path(root).write_text(json.dumps(blob, indent=2))
    except OSError:
        pass  # losing the counter degrades toward allowing stops, the safe direction


def fresh_json(path, since_ts):
    """A marker counts only if it was written during the turn now ending, i.e. after the
    previous Stop for this session. Returns the parsed object or None."""
    try:
        if path.stat().st_mtime <= since_ts:
            return None
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def survey_work(root):
    """(remaining_to_implement, batch_awaiting_review, unknown_dispositions)."""
    try:
        data = json.loads((root / ".foreman" / "work-sites.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None, None, []
    reqs = data.get("requirements")
    if not isinstance(reqs, dict):
        return None, None, []

    remaining, batch, unknown = [], [], []
    for req_id, rec in reqs.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("state") in UNAVAILABLE_STATES:
            continue
        disp = rec.get("disposition")
        if disp is None:
            remaining.append(req_id)
        elif disp in NOT_STANDING:
            continue
        elif "pending" in str(disp):
            batch.append(req_id)
        else:
            unknown.append(f"{req_id}={disp}")
            remaining.append(req_id)
    return sorted(remaining), sorted(batch), sorted(unknown)


ORCH_RELPATH = Path(".foreman") / "pdp-orchestrator.json"
ACTIVITY_RELPATH = Path(".foreman") / "pdp-activity-marker.json"
WAKEUP_RELPATH = Path(".foreman") / "pdp-wakeup-marker.json"
DECLARED_RELPATH = Path(".foreman") / "stop-declared.json"

# PDP.md §11.3's escalation list and nothing else. Deliberately NOT the same set as
# STOP_CONDITIONS above, which are REQ-35's builder-scoped conditions: conflating the two
# vocabularies is how a builder condition would come to satisfy an orchestrator obligation.
ESCALATION_CONDITIONS = {"E1", "E2", "E3", "E4"}

ACTIVITY_HOOK = "pdp_activity_marker"

# CHV2-43: read_orchestrator() removed. Its only caller, _orchestrator_scope() below, now calls
# session_role.orchestrator_claim(root, session_id) instead (CHV2-37) -- the same read-plus-
# session_id-match logic, centralized so the next hook needing an orchestrator-claim check does
# not reimplement it a third time. ORCH_RELPATH above is kept (still read by
# _run_orchestrator_branch's own docstrings/comments as the canonical relpath name) but no
# function in this file reads that path directly any more.


def activity_marker_registered(root):
    """(registered, where). S1: the deny message must not promise a way out that is not
    wired. Checks the two settings files that could govern this project for the marker
    hook, so the message can say what is actually true here rather than assert that any
    tool call satisfies condition (a)."""
    candidates = [Path.home() / ".claude" / "settings.json",
                  root / ".claude" / "settings.json"]
    found = []
    for p in candidates:
        try:
            if ACTIVITY_HOOK in p.read_text():
                found.append(str(p))
        except OSError:
            continue
    return bool(found), found


def verify_declared_artifact(declared, root, since):
    """(ok, detail). A declaration must name an escalation condition AND an artifact this
    hook confirms is real, recent, and this project's.

    S3: existence alone is not evidence. `/etc/hosts` exists; so does `/`. The checkable
    claim implied by the word "artifact" is *something this turn produced, inside this
    project*, so both are required: resolution under the project root, and an mtime newer
    than the turn's start -- the same freshness test conditions (a) and (b) already use.

    The path is used read-only, for `resolve()` and `stat()`. It is never composed into a
    write target, which is the defect `PDP.md` §16.4 records live with `dispatch_id`.
    Absolute is required so the check cannot depend on the hook's cwd.

    Scope note, stated because it looks wrong at first: E1 escalations concern shared
    infrastructure *outside* this repo, yet the artifact must be *inside* it. That is
    intended -- the artifact is the EVIDENCE FOR the escalation, which the escalating
    session produced, not the TARGET of it.
    """
    if not isinstance(declared, dict):
        return False, "stop-declared.json is not a JSON object"
    cond = declared.get("condition")
    if cond not in ESCALATION_CONDITIONS:
        return False, (f"condition {cond!r} is not one of {sorted(ESCALATION_CONDITIONS)}; "
                       f"REQ-35's builder stop conditions do not satisfy this branch")
    artifact = declared.get("artifact")
    if not isinstance(artifact, str) or not artifact.strip():
        return False, "no 'artifact' path named"
    p = Path(artifact)
    if not p.is_absolute():
        return False, f"artifact path {artifact!r} is not absolute"
    try:
        rp = p.resolve()
        root_r = Path(root).resolve()
        if root_r != rp and root_r not in rp.parents:
            return False, (f"artifact {artifact!r} resolves outside this project "
                           f"({root_r}); the evidence for an escalation is produced here")
        st = rp.stat()
    except OSError as exc:
        return False, f"artifact {artifact!r} could not be resolved or stat-ed: {exc!r}"
    if st.st_mtime <= since:
        return False, (f"artifact {artifact!r} predates this turn; it is not evidence "
                       f"about the escalation being declared now")
    why = declared.get("why")
    if not isinstance(why, str) or len(why.strip()) < 12:
        return False, "'why' is missing or too short to be a reason"
    return True, f"{cond} declared, artifact verified fresh and in-project at {artifact}"


def orchestrator_turn_check(root, session_id, since, now, blob):
    """Fail-closed. Returns (allow, note, reason_if_blocking)."""
    activity = fresh_json(root / ACTIVITY_RELPATH, since)
    if isinstance(activity, dict) and activity.get("session_id") == session_id:
        return True, "orch-activity-this-turn", None

    wakeup = fresh_json(root / WAKEUP_RELPATH, since)
    if isinstance(wakeup, dict) and wakeup.get("session_id") == session_id:
        return True, "orch-continuation-scheduled", None

    declared = fresh_json(root / DECLARED_RELPATH, since)
    if declared is not None:
        ok, detail = verify_declared_artifact(declared, root, since)
        if ok:
            return True, "orch-escalation-declared", None
        return False, "orch-declaration-rejected", (
            f"A stop-declared.json was written this turn and it does not hold up: {detail}."
        )

    return False, "orch-no-evidence", (
        "This turn did no recorded tool work, scheduled no continuation, and declared no "
        "escalation."
    )


def main(data):
    if data.get("hook_event_name") not in (None, "Stop"):
        return
    cwd = data.get("cwd") or ""
    if not cwd:
        return
    root = Path(cwd)

    # 1. Scope. Registration is what limits this today, but enforce it in code too: a Stop
    #    hook that fires machine-wide and blocks unrelated sessions is a far worse bug than
    #    the stall it fixes.
    if not (root / ".foreman").is_dir():
        return

    session_id = data.get("session_id") or "unknown-session"

    # 1b. ROLE SCOPE (FORE-186). Project scope is not the right axis on its own. REQ-35's
    #     standing continuation authority was granted by the operator to a BUILDER session
    #     running an open-ended autonomous build. A reviewer session dispatched into the same
    #     project has a bounded task with a natural end -- finish the review, report back --
    #     and never received that authority. Firing on it forced a real reviewer session to
    #     choose between self-scheduling indefinite continuation and fabricating a REQ-35 stop
    #     condition it had no basis to name. It correctly refused both and escalated; the
    #     defect was the gate's, not its.
    #
    #     So the gate is now OPT-IN: it enforces only against the session that has explicitly
    #     claimed continuation authority. Default off is the right posture for a mechanism that
    #     imposes obligations -- authority is granted, never assumed, which is REQ-35's own
    #     logic applied to the gate itself.
    #
    #     CHV2-43 note: this branch is UNCHANGED by this ticket. read_authority() stays as this
    #     file's own hand-rolled read -- out of scope per session_role.GOALS.json's own
    #     out_of_scope list ("CHV2-43's own scope is the orchestrator-branch rewire
    #     specifically, not a general refactor of the hook"), and this branch never reads a
    #     claim field beyond session_id, so there is no claimed_at-style regression risk here
    #     to fix in the first place.
    authority = read_authority(root)
    if authority is None:
        return allow_early(root, session_id, "no-authority-holder")
    if authority.get("session_id") != session_id:
        return allow_early(root, session_id, "not-authority-holder")

    now = time.time()
    blob, st = load_state(root, session_id)
    since = st["last_stop_ts"]

    def allow(note, blocks=0):
        save_state(root, session_id, blob, now, blocks)
        hc.set_rule(f"{RULE_ID}:{note}")
        return

    # 2. Loop guard, before anything that could throw.
    if st["consecutive_blocks"] >= MAX_CONSECUTIVE_BLOCKS:
        hc.audit("PDP_TURN_GATE_FAILOPEN",
                 {"session": session_id, "consecutive_blocks": st["consecutive_blocks"],
                  "note": "ceiling reached; allowing stop so the session cannot be locked"},
                 session_id, cwd, severity="medium")
        return allow("failopen-ceiling", blocks=0)

    # 2b. CHV2-12: the verbosity backstop, not the mechanism -- CHV2-7's per-turn state
    # injection (pointer-only final messages, instructed every turn) is the actual broad fix;
    # this only fires when that instruction didn't land. Scoped to the SAME population this
    # whole branch already governs (the continuation-authority holder) rather than a generic
    # "non-interactive session" concept: Claude Code's Stop hook payload has no field for
    # interactive vs non-interactive (confirmed against the documented schema, not assumed),
    # so inventing a heuristic here risked blocking a real interactive session's own message --
    # exactly what this ticket's acceptance criterion forbids. Continuation authority is never
    # granted to a bounded interactive task (FORE-186), so it is the one signal already
    # established in this file for "open-ended autonomous run," reused rather than duplicated.
    # Placed after the loop guard (so a session already at the block ceiling still fails open,
    # same as everything else) and shares its consecutive_blocks counter, so a persistent
    # inability to shorten a message cannot loop past the same ceiling REQ-38 already bounds.
    too_long, msg_bytes = _message_too_long(data)
    if too_long:
        blocks = st["consecutive_blocks"] + 1
        save_state(root, session_id, blob, now, blocks)
        hc.set_rule(f"{RULE_ID}:message-too-long")
        hc.audit("PDP_TURN_GATE_MESSAGE_TOO_LONG",
                 {"session": session_id, "bytes": msg_bytes, "limit": MESSAGE_BYTE_LIMIT,
                  "block_number": blocks}, session_id, cwd, severity="low")
        hc.block_stop(
            f"PDP turn gate (CHV2-12): this turn's final message is {msg_bytes} bytes, over "
            f"the {MESSAGE_BYTE_LIMIT}-byte backstop. Rewrite as pointers -- if the long form "
            f"is already in a durable artifact (a handoff file, a ticket comment, a written "
            f"report), reference its path instead of repeating the content here; PDP.md §10 "
            f"item 9 is report to peers by pointer. Block {blocks} of {MAX_CONSECUTIVE_BLOCKS}; "
            f"after the ceiling this gate allows the stop rather than locking the session."
        )
        return

    # 3. REQ-35: did this turn declare one of the four named stop conditions?
    declared = fresh_json(root / ".foreman" / "stop-declared.json", since)
    if isinstance(declared, dict) and declared.get("condition") in STOP_CONDITIONS:
        return allow("req35-stop-declared")

    # 4. Standing work.
    remaining, batch, unknown = survey_work(root)
    if remaining is None:
        return allow("no-worksites-file")  # cannot judge, so do not block
    if not remaining and not batch:
        return allow("no-standing-work")

    # 5. REQ-37: was a continuation actually scheduled during this turn?
    marker = fresh_json(root / ".foreman" / "pdp-wakeup-marker.json", since)
    if isinstance(marker, dict) and marker.get("session_id") == session_id:
        return allow("req37-continuation-scheduled")

    # 6. REQ-36 branch, named so the session cannot pick it by inertia.
    if remaining:
        branch = (f"IMPLEMENT next. {len(remaining)} requirement(s) not yet implemented "
                  f"({', '.join(remaining[:6])}{'...' if len(remaining) > 6 else ''}). "
                  f"REQ-36: hold the {len(batch)}-item review batch, do NOT dispatch yet.")
    else:
        branch = (f"DISPATCH the consolidated gate review. Nothing left to implement and "
                  f"{len(batch)} requirement(s) are waiting ({', '.join(batch[:6])}"
                  f"{'...' if len(batch) > 6 else ''}). REQ-36: one review across all of them.")

    drift = f" SCHEMA DRIFT, unrecognised disposition(s): {', '.join(unknown)}." if unknown else ""

    blocks = st["consecutive_blocks"] + 1
    save_state(root, session_id, blob, now, blocks)
    hc.set_rule(f"{RULE_ID}:needs-continuation")
    hc.audit("PDP_TURN_GATE_BLOCK",
             {"session": session_id, "remaining": len(remaining), "batch": len(batch),
              "block_number": blocks, "unknown_dispositions": unknown},
             session_id, cwd, severity="medium")
    hc.block_stop(
        f"PDP turn gate (REQ-38): this turn is ending with standing work and no scheduled "
        f"continuation. {branch}{drift} Do ONE of: (a) keep working now; (b) call "
        f"ScheduleWakeup to schedule your own continuation, per REQ-37; or (c) if you have "
        f"genuinely reached one of REQ-35's four stop conditions, write "
        f".foreman/stop-declared.json with {{\"condition\": one of "
        f"{sorted(STOP_CONDITIONS)}, \"why\": \"...\"}} and stop. "
        f"Block {blocks} of {MAX_CONSECUTIVE_BLOCKS}; after the ceiling this gate allows the "
        f"stop rather than locking the session."
    )


def _orchestrator_scope(data):
    """Is this session the orchestrator here? Fail-OPEN: any doubt returns None.

    CHV2-43: delegates the claim read to session_role.orchestrator_claim() (CHV2-37) instead of
    this file's own read_orchestrator() -- removed below, since this was its only caller. Still
    returns the RAW claim dict, not a bare role string: _run_orchestrator_branch's S4 staleness
    check reads claimed_at directly off it, and session_role.resolve_role()'s bare-string return
    cannot supply that (session_role.GOALS.json C3's own named regression risk for this rewire).
    """
    try:
        cwd = data.get("cwd") or ""
        if not cwd:
            return None
        root = Path(cwd)
        if not (root / ".foreman").is_dir():
            return None
        session_id = data.get("session_id")
        if not session_id:
            return None
        claim = session_role.orchestrator_claim(root, session_id)
        if claim is None:
            return None
        return root, session_id, claim
    except Exception:
        return None


def _ways_out(root):
    """The deny message's list of exits, with (a) told truthfully (S1)."""
    registered, where = activity_marker_registered(root)
    if registered:
        a = ("(a) keep working -- any real tool call this turn satisfies this, because "
             f"{ACTIVITY_HOOK}.py is registered ({', '.join(where)})")
    else:
        a = (f"(a) IS NOT AVAILABLE HERE. It requires {ACTIVITY_HOOK}.py registered on "
             f"PostToolUse, and it is not registered in ~/.claude/settings.json or this "
             f"project's .claude/settings.json. Doing more work will NOT satisfy this gate "
             f"until it is. Use (b) or (c), and report the missing registration")
    return (
        f"Do ONE of: {a}; (b) call ScheduleWakeup to schedule your own continuation; or "
        f"(c) if you have genuinely reached one of PDP.md §11.3's escalation conditions, "
        f'write .foreman/stop-declared.json as {{"condition": "E1"|"E2"|"E3"|"E4", '
        f'"artifact": "<absolute path, inside this project, written this turn>", '
        f'"why": "..."}} and stop. This hook resolves and stat-s the artifact: a path that '
        f"does not exist, sits outside this project, or predates this turn is refused."
    )


def _record(data, verdict, note):
    """N1. The orchestrator branch bypasses hc.run_body(), which is what normally writes
    this hook's ledger row -- so this branch must write its own, and an earlier revision
    wrote `"checked"`, which is not in verdict_ledger's VALID_VERDICTS ("fire", "silent",
    "error", "stolen"). record() raised ValueError on every single invocation and a bare
    `except: pass` swallowed it, so the highest-blast-radius code here wrote nothing to
    the ledger, ever. That is precisely the state hook_common's own docstring says the
    ledger exists to make visible: a guard that looks alive and does nothing.

    The failure is now printed rather than swallowed, because the defect was not the
    wrong string -- it was that the wrong string could not be noticed."""
    try:
        hc.record_verdict(data, verdict, kind="pdp-turn-gate-orchestrator",
                          rule_id=f"{RULE_ID}:{note}")
    except Exception as exc:
        print(f"[pdp_turn_gate] verdict-ledger write failed for verdict {verdict!r}: "
              f"{exc!r}", file=sys.stderr)


def _refuse(data, root, session_id, note, message):
    """S6: the last line of a fail-closed branch must not be able to fail open. If
    block_stop itself raises, no decision reaches the harness and no decision is an
    allow, so this catches BaseException and makes one final attempt."""
    # No `decision=` here. verdict_ledger accepts only
    # ('deny', 'defer', 'ask', 'allow'), and hc.block_stop() itself calls mark()
    # rather than mark_decision(), so the ordinary path records no decision for a
    # blocked stop either. Passing "block" reproduced N1 exactly one field over --
    # a second invalid-enum write, swallowed the same way. Caught only because the
    # test asserts the row lands rather than that the call was made.
    _record(data, "fire", note)
    try:
        hc.block_stop(message)
    except BaseException:
        try:
            print(json.dumps({"decision": "block", "reason": message}))
        except BaseException:
            print('{"decision": "block", "reason": "PDP turn gate: fail-closed"}')


def _run_orchestrator_branch(data, root, session_id, claim):
    """Fail-CLOSED from here on."""
    try:
        now = time.time()
        blob, st = load_state(root, session_id)

        # S4: claimed_at is required once the session IS identified. Absent or unparseable
        # would leave the first-turn floor at 0, and any stale marker would satisfy the
        # gate exactly once. Requiring it in read_orchestrator instead would let a
        # malformed claim escape scope entirely, which is the wrong direction.
        raw = claim.get("claimed_at")
        try:
            claimed_at = float(raw)
        except (TypeError, ValueError):
            return _refuse(data, root, session_id, "orch-claim-malformed",
                           "PDP turn gate (FORE-345): .foreman/pdp-orchestrator.json names "
                           "this session but carries no usable 'claimed_at', so the freshness "
                           "floor cannot be established and a stale marker could satisfy this "
                           "gate. Re-claim the role to write a well-formed file.")

        # FIRST-TURN FLOOR. load_state returns 0.0 when this session has no prior Stop, so
        # on a first turn any pre-existing marker would count as fresh exactly once. Floor
        # the window at the claim: a marker predating the claim is not evidence about this
        # orchestrator's turn.
        since = max(st["last_stop_ts"], claimed_at)

        # CHV2-30: main()'s check 2b (CHV2-12's verbosity backstop) never ran for this branch
        # -- __main__ dispatches to EITHER main() OR this function, never both -- so an
        # orchestrator-claim session was structurally exempt from the backstop entirely. That
        # is exactly backwards: the bloat measurement this backstop regresses against
        # (HOOKS-VS-PDP-AND-SUMMARY-BLOAT-20260904.md Part 2 2.1) named this population as the
        # worst offender. Checked unconditionally and first, same ordering as main(), so it
        # applies regardless of whether the turn otherwise has a valid way out -- verbosity is
        # a defect independent of whether the underlying stop was justified. Fail-closed and
        # uncapped, same posture as the rest of this branch: there is no block-ceiling escape
        # here, a session recovers by writing a shorter message, not by exhausting a counter.
        too_long, msg_bytes = _message_too_long(data)
        if too_long:
            save_state(root, session_id, blob, now, 0)
            hc.set_rule(f"{RULE_ID}:message-too-long")
            hc.audit("PDP_TURN_GATE_MESSAGE_TOO_LONG",
                     {"session": session_id, "bytes": msg_bytes, "limit": MESSAGE_BYTE_LIMIT,
                      "scope": "orchestrator"}, session_id, str(root), severity="high")
            _refuse(data, root, session_id, "orch-message-too-long",
                    f"PDP turn gate (CHV2-12/CHV2-30, orchestrator scope): this turn's final "
                    f"message is {msg_bytes} bytes, over the {MESSAGE_BYTE_LIMIT}-byte "
                    f"backstop. Rewrite as pointers -- if the long form is already in a "
                    f"durable artifact (a handoff file, a ticket comment, a written report), "
                    f"reference its path instead of repeating the content here; PDP.md §10 "
                    f"item 9 is report to peers by pointer. There is no block ceiling on this "
                    f"branch; a message under the limit ends the turn.")
            return

        allow_stop, note, reason = orchestrator_turn_check(root, session_id, since, now, blob)
        save_state(root, session_id, blob, now, 0)
        hc.set_rule(f"{RULE_ID}:{note}")
        if allow_stop:
            hc.audit("PDP_TURN_GATE_ORCH_ALLOW", {"session": session_id, "note": note},
                     session_id, str(root), severity="info")
            _record(data, "silent", note)
            return
        hc.audit("PDP_TURN_GATE_ORCH_BLOCK", {"session": session_id, "note": note},
                 session_id, str(root), severity="high")
        _refuse(data, root, session_id, note,
                f"PDP turn gate (FORE-345, orchestrator scope): {reason} "
                f"PDP.md §10 item 8 allows a turn to end only if one of three things is "
                f"true. {_ways_out(root)} There is no block ceiling on this branch. "
                f"Releasing .foreman/pdp-orchestrator.json also ends the obligation, and "
                f"is a resignation from the role rather than a bypass of it.")
    except Exception as exc:
        try:
            hc.audit("PDP_TURN_GATE_ORCH_FAILCLOSED",
                     {"session": session_id, "error": repr(exc)},
                     session_id, str(root), severity="high")
        except Exception:
            pass
        _refuse(data, root, session_id, "orch-failclosed",
                f"PDP turn gate (FORE-345, orchestrator scope) failed while checking this "
                f"turn and is refusing the stop rather than allowing it: {exc!r}. This "
                f"branch is fail-closed by design. If this is a defect in the gate, "
                f"releasing .foreman/pdp-orchestrator.json ends the obligation.")


if __name__ == "__main__":
    _started = time.time()
    _data = hc.read_input()
    _scope = _orchestrator_scope(_data)
    if _scope is None:
        hc.run_body(main, _data, _started)
    else:
        _run_orchestrator_branch(_data, _scope[0], _scope[1], _scope[2])
    # No tail record_verdict here. hc.run_body() writes its own row for the
    # non-orchestrator path, and the orchestrator branch records inside itself on both
    # the allow and block paths -- an earlier revision wrote one invalid row here for
    # both paths and silently failed on every invocation (N1).
    sys.exit(0)
