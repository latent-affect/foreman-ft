#!/usr/bin/env python3
"""write_gate (C4) -- FORE-399 (FABLE-J). The PreToolUse hook Claude Code actually invokes.

A thin, UNSANDBOXED supervisor. It builds a real sandbox-exec profile (J-C4: one resolved
interpreter, network*/process-fork/process-exec* denied; J-C12/FORE-413: file-read* denied on
the dispatch's real rationale/ sibling, built from a CANONICALIZED path -- see
resolve_rationale_dir()'s own .resolve() call, closing the /var-vs-/private/var trap), spawns
write_gate_decision.py as a genuinely separate, freshly-exec'd CHILD PROCESS under that profile
(J-C1: never a bare os.fork(), which would inherit the parent's already-imported modules via
copy-on-write and could never reproduce a relocated-module import failure), and checks the
child's real exit status BEFORE ever parsing its stdout (J-C3's sharpest case: a valid ALLOW
emitted, then the child SIGKILLs itself, is still denied because exit status is checked first).

This module does NOT route through hook_common.run(), whose documented contract is fail-open
(REQ-54-D6) -- it uses hook_common only for input reading and deny emission, and supplies its
own fail-closed entrypoint: any incomplete verdict from the child (nonzero exit, timeout,
unparseable/truncated stdout, an unrecognized verdict shape) is denied by the SUPERVISOR itself.

J-C13 (FORE-415) -- gate_to_verdict_ledger DESIGN NOTE. ARCHITECTURE.md declares this interface
on equal footing with gate_to_hook_common (section 2 line 220, section 10's yaml), but it was a
real, undisclosed omission from this ticket's own frozen scope until FORE-411's re-verification
caught it (see FORE-415). Since write_gate deliberately never calls hook_common.run()/run_body()
(the ONE place that would otherwise translate a hook's mark()/mark_decision()/stolen() state into
a real verdict_ledger row), this module calls hook_common.record_verdict() directly, replicating
run_body()'s own real branching logic (read at source; as of FORE-565 that source is this
repo's own hooks/hook_common.py, not the diverged ~/.claude/hooks copy this line used to cite)
rather than inventing a parallel convention:

  - A real deny (whether raised by the sandboxed child's own decide() or by this supervisor's own
    pre-spawn checks -- ancestry, project-root rederivation, or a post-spawn incomplete-verdict
    case) -> verdict="fire", kind="deny", decision="deny", rule_id=the same `class` string
    already used for the deny reason. Supervisor-origin and child-origin denies are NOT
    distinguished by verdict/decision -- both are real, completed denials -- only by rule_id,
    which already carries the distinction (e.g. "ancestry-mismatch"/"project-root-rederivation-
    failed" vs "qa-verdict-provenance"/"once-only-replay"), matching the ledger's own existing
    convention of using rule_id, not a second axis, to attribute WHICH check produced a verdict.

  - A real silent allow -> verdict="silent", no decision (no permission decision was ever emitted
    to the harness), no rule_id (the full chain of checks all passed; there is no single rule
    that "decided" to stay quiet the way a deny has exactly one failing check to name).

  - target is NEVER set by this module. verdict_ledger.record()'s own docstring is explicit that
    target is "only meaningful on a stolen verdict" -- the hook's own product being a write. This
    module never performs the write itself (a real Write tool call does, after this hook merely
    permits it); the only component in this pipeline whose own product IS a write is
    write_gate_confirm.py's PostToolUse half (see that module's own matching design note).

  - verdict="error" is reserved for a genuine crash in THIS module's own Python code, now caught
    by main()'s own outermost try/except (a related, previously-undiscovered gap: main() had no
    exception handling around run_supervised() at all before this fix) -- distinct from every
    child-side crash, which the supervisor already converts into a real "fire"/deny via
    evaluate_child_result(), never an uncaught exception reaching this far.

FORE-494 (P0/S1, ingredient 2) -- the unverifiable-branch fix, added below. See
cwd_falls_under()'s own docstring and the two new sub-branches inside run_supervised()'s
"index open, record missing" case for the design and its honest limits. Nothing else in this
file changed; this docstring's own text above is otherwise untouched from FORE-399/FORE-449.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
# FORE-565. This was Path.home() / ".claude" / "hooks", which imported a DIFFERENT hook_common
# from a different lineage: the ~/.claude copy is 16,764 bytes dated Aug 27, this repo's own is
# 22,057 dated Sep 8. The two have diverged (FORE-556 is the family-level ticket), so a module
# living in this repo was enforcing with another repo's helper, and the divergence was guaranteed
# to reach this file rather than merely possible.
#
# MEASURED BEFORE CHANGING IT, not assumed safe. Every name this module uses, compared by ast
# across both copies: read_input, deny, set_rule, run and run_body have IDENTICAL signatures.
# record_verdict differs by exactly two added optional kwargs (reason=, direction=), which is
# purely additive and cannot break a caller that passes neither -- and this module passes
# neither. No function was removed, no required argument added, no module-level constant changed
# value; the new copy only ADDS target_path(), written_text(), two globals and `import re`.
#
# So this is a latent footgun closed while it is still latent: the day a call site here passes
# reason= or direction=, the old copy would have raised on a signature it never grew, and the
# fix would have looked like a bug in this file.
#
# parents[2] is this repo's own hooks/ root (gate -> alice_bob_fable -> hooks), derived rather
# than hardcoded, so a relocation of this subtree cannot silently reintroduce a cross-repo import.
HOOK_COMMON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOK_COMMON_DIR))
import hook_common  # noqa: E402 -- deny()/read_input() only, never run() (REQ-54-D6)

# FABLE-I (dispatch_writer) is a real, already-frozen SIBLING component in this same rebuild,
# landed together under one hooks/alice_bob/ tree at the real anchor (FORE-418) -- a normal
# sibling-package import.
FABLE_I_DIR = THIS_DIR.parent / "dispatcher"
sys.path.insert(0, str(FABLE_I_DIR))
# J-C11 (re-scoped, FORE-409/410): the SAME shared function FORE-398/dispatch_writer.py already
# ships, imported directly here -- moved from write_gate_decision.py (see that module's decide()
# docstring) because only the UNSANDBOXED supervisor can safely let it fork `git`.
from dispatch_writer import (  # noqa: E402
    rederive_project_root, DISPATCH_INDEX_DIR, DispatchDenied,
)

DECISION_SCRIPT = THIS_DIR / "write_gate_decision.py"
DEFAULT_DISPATCH_ROOT = Path.home() / ".claude" / "foreman" / "alice-bob-dispatch"
DECISION_TIMEOUT_SECONDS = 10

# FORE-551 part A, DISCLOSURE ONLY -- this names a recovery path that already exists; it grants
# nothing and changes no decision. Every class it is appended to below denies regardless of
# tool_name, so a session in one of these states loses EVERY Bash call, not only writes, and has
# no in-session recovery. That posture is deliberate and reviewed (Nadia Osei's Q1/N3/N4): a
# record that exists but is damaged is the opposite conclusion from a record that is absent, and
# is treated as a tampering signature rather than as ordinary non-Alice/Bob traffic.
#
# What was missing was not an escape, it was the SENTENCE. The escape is structural: by-session
# records are keyed on session_id, so a genuinely new session has no record and stands aside
# normally. An operator hitting this had no way to discover that from the deny message, which
# named only the class -- they would have had to read this source to find it.
#
# Deliberately NOT a bypass the denied session can use. Whether a real, audited, operator-issued
# override should exist for a stuck session is a live security-tradeoff question sitting on top of
# a reviewed control, escalated rather than answered here; an override the stuck session itself
# could trigger would be precisely the bypass this gate exists to close.
#
# One constant rather than five copies of the same prose, for the same reason CHV2-68 imported an
# allowlist rather than restating it: five copies drift, and prose that drifts across deny paths
# is how two of them end up describing different recoveries.
RECOVERY_NOTE = (
    " RECOVERY: this session cannot clear this state from inside itself -- every Bash call, not "
    "only writes, will keep being denied for the rest of its life. Dispatch records are keyed on "
    "session_id, so starting a NEW session has no record of this kind and is not gated by it. "
    "Nothing in the current session can be done to bypass this, by design."
)

INTERPRETER_RESOLVED = os.path.realpath(sys.executable)


def resolve_launch_helper(interpreter_resolved):
    """A macOS framework build of Python (e.g. Homebrew's python@3.14) internally re-execs
    through a second binary, .../Versions/X.Y/Resources/Python.app/Contents/MacOS/Python, to
    get proper app-bundle behavior -- confirmed live this session (posix_spawn failed with a
    cryptic 'Undefined error: 0' until this second binary was also permitted), and already
    documented as a required second permit in this project's own prior toy-model script
    (toy-models/sandbox_denypath_trial.py's LAUNCH_HELPER constant). Derives the helper path
    generically from the resolved interpreter's own path shape rather than hardcoding a
    version number, and returns None if this machine's interpreter isn't a framework build
    with that layout (nothing to add in that case)."""
    bin_dir = Path(interpreter_resolved).parent
    if bin_dir.name != "bin":
        return None
    versions_dir = bin_dir.parent  # .../Versions/X.Y
    helper = versions_dir / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    return str(helper) if helper.is_file() else None


LAUNCH_HELPER_RESOLVED = resolve_launch_helper(INTERPRETER_RESOLVED)


class RecordDamaged(Exception):
    """FORE-449, per Nadia Osei's review: raised by load_by_session_record() for the two causes
    that mean a dispatch record EXISTS and something damaged it (unreadable, malformed) --
    distinct from genuine absence, which returns None. Before FORE-449's stand-aside change this
    distinction was academic (this docstring itself said so: "there is no live bypass today,"
    now false) because write_gate_decision.py's own sandboxed re-read denied on any of the three
    causes regardless of what this layer returned. FORE-449 made run_supervised() trust this
    layer's return value directly for the first time, which is exactly the "future refactor"
    this module's own prior docstring warned would need this disclosure to be real, not just
    academic. Same split as AncestryCheckFailed/PidIdentityCheckFailed already established in this
    file: a real operational failure gets its own class, distinguishable in the verdict ledger
    from "no record was ever written for this session at all.\""""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def load_by_session_record(dispatch_root, session_id):
    """Same minimal, non-decisional metadata read resolve_rationale_dir() already performs --
    factored out so both it and check_ancestry() read the record exactly once, from the same
    real file, rather than duplicating the read.

    FORE-438/FORE-449: distinguishes its three real "no record" causes -- genuine absence returns
    None (the legitimate "no dispatch was ever opened for this session" case); unreadable or
    malformed raises RecordDamaged (a dispatch record EXISTS and something is wrong with it, the
    opposite conclusion -- must never be treated the same as absence by a caller standing aside on
    None)."""
    by_session_path = Path(dispatch_root) / "by-session" / f"{session_id}.json"
    if not by_session_path.is_file():
        print(f"[write_gate] no by-session record at {by_session_path}", file=sys.stderr)
        return None
    try:
        text = by_session_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[write_gate] by-session record at {by_session_path} unreadable: {exc}", file=sys.stderr)
        raise RecordDamaged(f"by-session record at {by_session_path} unreadable: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"[write_gate] by-session record at {by_session_path} is not valid JSON: {exc}", file=sys.stderr)
        raise RecordDamaged(f"by-session record at {by_session_path} is not valid JSON: {exc}") from exc


def default_ancestry():
    """REAL DEFECT FOUND AND FIXED HERE (FORE-409/410 end-to-end integration run): this exact
    ps-based ancestry walk used to live inside write_gate_decision.py, the SANDBOXED child --
    but the sandbox profile that child runs under denies process-fork and process-exec* by
    design (J-C4), and os.popen()/subprocess internally fork+exec to launch `ps`. Confirmed
    live, firing the real supervisor against a real control plane for the first time ever: the
    sandboxed child's own ancestry walk always returned an empty list, because `ps` itself could
    never be spawned under its own sandbox -- meaning this check, as originally placed, would
    have denied EVERY real write, legitimate or not, the first time it ever ran for real. Never
    caught by any prior unit test, because every prior test called decide() directly with an
    injected, controllable ancestry_fn, never exercising the real subprocess's own inability to
    compute this at all.

    Moved here, to the UNSANDBOXED supervisor, which can safely fork `ps` -- this function now
    runs BEFORE the sandboxed child is ever spawned, and its result is what gets checked against
    the record's own target_pid. The sandboxed child no longer performs this check itself; see
    write_gate_decision.py's own module docstring for the corresponding removal.

    FORE-434: a `ps` invocation that itself fails (OSError) or returns unparseable output
    (ValueError) is a real operational failure, not a completed walk -- it must not be silently
    swallowed into a truncated/empty chain, because that chain then gets checked against
    target_pid exactly like a genuinely completed walk, and a real spoofing attempt (a forged
    target_pid absent from a correctly-computed ancestry) becomes indistinguishable in the audit
    trail from "ps broke". Raises AncestryCheckFailed instead of breaking early; an empty `out`
    (ps ran, reported no parent) is still a clean, successful stop, not a failure."""
    chain = []
    pid = os.getpid()
    for _ in range(16):
        try:
            out = os.popen(f"ps -o ppid= -p {pid}").read().strip()
        except OSError as exc:
            raise AncestryCheckFailed(f"ps invocation failed walking pid {pid}: {exc}") from exc
        if not out:
            break
        try:
            pid = int(out)
        except ValueError as exc:
            raise AncestryCheckFailed(f"ps produced a non-numeric ppid {out!r} for pid {pid}: {exc}") from exc
        chain.append(pid)
        if pid <= 1:
            break
    return chain


class AncestryCheckFailed(Exception):
    """FORE-434: raised when the ps-based ancestry walk itself could not complete -- an
    operational failure (ps missing, killed, or malfunctioning), distinct from a walk that
    completed cleanly and simply does not contain target_pid (a real ancestry mismatch, which
    stays a plain (False, reason) return from check_ancestry, never this exception)."""


def check_ancestry(record, ancestry_fn=default_ancestry):
    """Runs in the unsandboxed supervisor, before the sandboxed child is ever spawned -- see
    default_ancestry()'s own docstring for why this cannot run in the child. Returns
    (ok: bool, reason_if_not_ok) for a completed walk; propagates AncestryCheckFailed, never
    converts it to a (False, reason) tuple, so the caller can log/deny it under its own distinct
    class rather than the "ancestry-mismatch" class a real spoofing event gets (FORE-434)."""
    target_pid = record.get("target_pid")
    if not isinstance(target_pid, int):
        return False, "record carries no valid target_pid"
    ancestry = ancestry_fn()
    if target_pid not in ancestry:
        return False, f"target_pid {target_pid} not in the supervisor's own process ancestry {ancestry}"
    return True, None


class PidIdentityCheckFailed(Exception):
    """Mirrors AncestryCheckFailed's split (FORE-434): a real operational failure resolving the
    live pid's start time (`ps` itself could not be spawned, or emitted something unparseable)
    is a different audit-trail outcome than a completed lookup that simply finds the pid dead or
    held by a process whose start time doesn't match -- that stays a plain (False, reason)
    return from check_pid_identity, never this exception."""


PS_TRUNCATION_MARGIN_MS = 1500


def live_process_started_at_ms(pid):
    """FORE-442 (Nadia Osei, STRIDE spoofing finding). Reuses THIS supervisor's own established
    process-introspection primitive -- `ps`, forked here rather than in the sandboxed child
    because the sandbox denies process-fork (see default_ancestry()'s docstring for the full
    reasoning) -- with `-o lstart=` instead of `-o ppid=`, to answer a different question about
    the SAME target_pid default_ancestry() already resolved as a live ancestor: not who is its
    parent, but when did the process CURRENTLY holding this pid actually start.

    Deliberately does NOT call `claude agents --json` (dispatch_writer.py's resolve_live_session()
    source for the record's own target_started_at_ms): that source only enumerates live `claude`
    agent sessions, so it can never observe an unrelated, non-agent process that has since reused
    target_pid -- exactly the FORE-442 attack shape this check exists to catch. `ps` observes any
    live OS process regardless of what it is, which is the actual property this check needs.

    `ps -o lstart=` has only whole-second resolution, unlike target_started_at_ms's millisecond
    source -- check_pid_identity() compares the two with PID_IDENTITY_TOLERANCE_MS, never exact
    equality, for exactly this reason.

    Returns None if `pid` is not currently a live process (`ps` prints nothing) -- a target_pid
    this function can no longer see is already a pid check_pid_identity() cannot vouch for.
    Raises PidIdentityCheckFailed on a real `ps` operational failure or unparseable output."""
    try:
        out = os.popen(f"ps -o lstart= -p {pid}").read().strip()
    except OSError as exc:
        raise PidIdentityCheckFailed(f"ps invocation failed reading lstart for pid {pid}: {exc}") from exc
    if not out:
        return None
    try:
        parsed = datetime.strptime(out, "%a %b %d %H:%M:%S %Y")
    except ValueError as exc:
        raise PidIdentityCheckFailed(f"ps produced unparseable lstart {out!r} for pid {pid}: {exc}") from exc
    return int(parsed.timestamp() * 1000)


def check_pid_identity(record, live_started_at_fn=live_process_started_at_ms):
    """FORE-444 (Marcus Webb ship-readiness review, following Nadia Osei's original FORE-442
    finding): check_ancestry() above confirms target_pid is a real, currently-live ancestor of
    this process -- but a pid is only a name the OS hands back out, not an identity. If the
    ORIGINAL session behind target_pid exits and macOS reuses that exact pid for an unrelated
    process before this dispatch record's expires_in_seconds elapses, check_ancestry() alone
    cannot tell the two apart: the recycled pid really is in the live ancestry chain.
    dispatch_writer.py already captures target_started_at_ms specifically to close this gap
    (resolve_live_session()'s own docstring: 'the ONLY source of truth for pid/startedAt') --
    this function is the first and only reader of that field anywhere in this tree.

    FORE-442's first landing (commit 67bdcca) compared live_started_at_ms against
    target_started_at_ms with a SYMMETRIC +/-1500ms tolerance, on the unmeasured assumption that
    the two sources merely disagreed by clock noise. Real measurement (FORE-444,
    MARCUS-VERDICT-DOGFOOD-20260905.md F2) found this false-denies a real, live, never-recycled
    session about 1 time in 7 on this machine: `claude agents --json`'s startedAt is the moment
    the CLI session became READY, not the OS process's real fork time, and the gap between those
    two events grows with how much session history there is to resume -- measured -506ms to
    -1036ms for ordinary sessions, -5706ms for a 3720-line/6.9MB resumed transcript (pid 1248).
    Flooring both sides to whole seconds (an alternative considered and tested, not merely
    proposed) does NOT close this: pid 1248's floored delta was still -5000ms. This gap is
    unbounded in principle (a larger resume, a slower machine, produces a larger gap), so no
    symmetric tolerance derived from any finite sample is safe against a future, larger resume.

    The fix is not a wider number, it is recognizing the comparison only needs ONE direction.
    `ps -o lstart=` truncates to whole seconds (floor), so a live process's reported start time
    is always <= its own true fork instant, which is always <= its own self-reported ready time
    (fork precedes "ready", not the other way around) -- so for the session THIS dispatch was
    opened for, live_started_at_ms <= target_started_at_ms always holds, with no floor needed
    (confirmed against all 7 real live sessions measured, including the 5706ms outlier). A pid
    RECYCLED onto an unrelated process can only acquire that pid strictly after the original
    process released it (exited), which is strictly after the original process's own fork --
    and dispatch_writer.py could only have recorded target_started_at_ms by observing the
    ORIGINAL process still alive and holding target_pid at `claude agents --json` query time, so
    the original's fork precedes that query, which is at-or-after target_started_at_ms. Any
    later process holding the same pid therefore starts strictly AFTER target_started_at_ms,
    never before or at it. Denying only on "later than" (with PS_TRUNCATION_MARGIN_MS of slack
    for `ps`'s own whole-second truncation at the boundary, not for cross-source disagreement)
    catches every real recycling event with no dependency on how long a legitimate session took
    to resume, and is strictly narrower than the old two-sided window (the "earlier than" half,
    which never indicated an attack, is gone).

    Returns (ok, reason_if_not_ok); propagates PidIdentityCheckFailed, never converts it to a
    (False, reason) tuple, so the caller can log/deny it under its own distinct class -- the
    same split check_ancestry()/AncestryCheckFailed already established above."""
    target_started_at_ms = record.get("target_started_at_ms")
    if not isinstance(target_started_at_ms, int) or isinstance(target_started_at_ms, bool):
        return False, "record carries no valid target_started_at_ms"
    target_pid = record.get("target_pid")
    if not isinstance(target_pid, int) or isinstance(target_pid, bool):
        return False, "record carries no valid target_pid"
    live_started_at_ms = live_started_at_fn(target_pid)
    if live_started_at_ms is None:
        return False, f"pid {target_pid} is not currently a live process -- cannot verify identity"
    if live_started_at_ms > target_started_at_ms + PS_TRUNCATION_MARGIN_MS:
        return False, (f"pid {target_pid} is live but its real start time ({live_started_at_ms}) "
                        f"is after the dispatch record's target_started_at_ms "
                        f"({target_started_at_ms}) by more than {PS_TRUNCATION_MARGIN_MS}ms -- "
                        f"a process cannot start after its own dispatch was already confirmed "
                        f"open for it, so this pid was very likely reused")
    return True, None


def build_sandbox_profile(rationale_dir_real):
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny process-fork)",
        "(deny process-exec*)",
        f'(allow process-exec (literal "{INTERPRETER_RESOLVED}"))',
    ]
    if LAUNCH_HELPER_RESOLVED is not None:
        lines.append(f'(allow process-exec (literal "{LAUNCH_HELPER_RESOLVED}"))')
    if rationale_dir_real is not None:
        lines.append(f'(deny file-read* (subpath "{rationale_dir_real}"))')
    return "\n".join(lines) + "\n"


def spawn_decision_child(payload_path, dispatch_root, profile_path, project_root_real,
                          timeout=DECISION_TIMEOUT_SECONDS):
    """J-C1: a genuinely separate, freshly-exec'd process image -- subprocess.run always
    exec()s a new process, never fork()s the current one, so a relocated/missing module on the
    decision script's own import path is independently re-resolved here every single time,
    never inherited from this supervisor's own already-imported state.

    `project_root_real`: computed by THIS unsandboxed supervisor (via rederive_project_root(),
    which forks `git`) and passed to the sandboxed child as a trusted 4th argument -- see
    write_gate_decision.py's decide() docstring for why the child can no longer derive this
    itself. Passed as a plain positional string, same as dispatch_root already is.

    Returns (exit_code_or_None, stdout_str, timed_out: bool). On a real timeout,
    subprocess.run's own documented behavior is to kill() the child and re-raise -- verified
    this session, not assumed -- so a hung child is actually terminated, not merely reported as
    denied while it keeps running in the background (J-C3's case 11)."""
    cmd = ["/usr/bin/sandbox-exec", "-f", str(profile_path),
           INTERPRETER_RESOLVED, str(DECISION_SCRIPT), str(payload_path), str(dispatch_root),
           str(project_root_real)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, False
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or ""), True


def evaluate_child_result(exit_code, stdout, timed_out):
    """Exit status is checked BEFORE stdout is ever parsed (J-C3's sharpest case). Returns a
    dict with a `final` key ('deny'/'silent') and, on deny, a `reason`/`class`."""
    if timed_out:
        return {"final": "deny", "class": "supervisor-timeout", "reason": "decision child exceeded its time budget"}
    if exit_code != 0:
        return {"final": "deny", "class": "supervisor-nonzero-exit", "reason": f"decision child exited {exit_code}"}
    if not stdout or not stdout.strip():
        return {"final": "deny", "class": "supervisor-empty-verdict", "reason": "decision child exited 0 but emitted no verdict"}
    try:
        verdict = json.loads(stdout)
    except json.JSONDecodeError:
        return {"final": "deny", "class": "supervisor-unparseable-verdict", "reason": "decision child's stdout does not parse as JSON"}
    if not isinstance(verdict, dict) or "decision" not in verdict:
        return {"final": "deny", "class": "supervisor-unrecognized-verdict", "reason": f"decision child emitted an unrecognized shape: {verdict!r}"}
    if verdict["decision"] == "deny":
        return {"final": "deny", "class": verdict.get("class", "child-deny"), "reason": verdict.get("reason", "denied")}
    if verdict["decision"] == "silent":
        return {"final": "silent"}
    return {"final": "deny", "class": "supervisor-unrecognized-verdict", "reason": f"unrecognized decision value {verdict['decision']!r}"}


def cwd_falls_under(cwd, candidate_root_str):
    """FORE-494 (P0/S1, ingredient 2). (True/False/None) -- True if `cwd` IS `candidate_root_str`
    or is nested under it; False if both resolve cleanly and neither holds; None if either is
    missing or fails to resolve ("cannot determine," never coerced to a boolean guess).

    Why this exists: the "index open, record missing" branch below has exactly one other source
    of "which project does this write concern" once rederive_project_root() has nothing to work
    from (a Bash payload with no file_path yields no edit paths at all) -- the hook PAYLOAD's own
    `cwd` field. This is harness-populated on every real PreToolUse call (the harness reports it
    faithfully), but it is agent-INFLUENCED, not agent-authored: the harness does not choose the
    directory or its spelling, the agent does, and a `cd` through a case-variant path is fully
    within an agent's own control (QA-Bob round-1 finding, corrected from this docstring's own
    first draft, which overstated cwd's trust level as equal to session_id/tool_name -- it is
    not, and the case-fold retry below exists precisely because it is not).

    Honest limit, stated rather than assumed: `cwd` is a directory the CURRENT tool call happens
    to be running from, not a cryptographic binding to a project the way pre_image_sha256 binds
    a write to on-disk content. A session whose actual cwd genuinely sits inside the index's
    claimed project, but whose real intent is unrelated, is not distinguishable from a real
    tampering attempt by this check alone -- which is exactly why a cwd-confirmed match below
    still denies, under its own class, rather than being treated as weaker evidence than a
    candidate_root match.

    QA-Bob round-1 finding (blocking, fixed here): Path.resolve() does NOT case-normalize on
    this machine's case-insensitive, case-preserving filesystem, so a case-variant cwd naming
    the IDENTICAL real directory (measured live: /Users/m5/dev/claude-hooks-v2 vs.
    /Users/m5/DEV/claude-hooks-v2) produced opposite outcomes -- a real new fail-open, on an
    input that correctly denied before this fix existed. This is FORE-128's exact class,
    already found and fixed once in this repo against component_coupling.py (that fix's own
    _true_case()). Closed here with the SAME local casefold-retry pattern
    dispatch_writer.py's own validate_scope_prefixes() already uses (its own "LOCAL MITIGATION,
    pending FORE-414" comment) -- try the real paths first, retry case-folded only if that
    fails -- rather than importing tessguard's root_matches_internal into this otherwise
    tessguard-free supervisor for what is, underneath, one string comparison."""
    if not cwd or not candidate_root_str:
        return None
    try:
        cwd_real = Path(cwd).resolve()
        root_real = Path(candidate_root_str).resolve()
    except OSError:
        return None
    if cwd_real == root_real or root_real in cwd_real.parents:
        return True
    cwd_folded = Path(str(cwd_real).casefold())
    root_folded = Path(str(root_real).casefold())
    if cwd_folded == root_folded or root_folded in cwd_folded.parents:
        return True
    return False


def run_supervised(payload, dispatch_root=None, timeout=DECISION_TIMEOUT_SECONDS, ancestry_fn=default_ancestry,
                    live_started_at_fn=live_process_started_at_ms, dispatch_index_dir=None):
    """The real, testable entry point -- separate from main() so tests call this directly
    rather than shelling out through stdin every time.

    `ancestry_fn`: the same real, injectable seam decide()'s own removed ancestry_fn parameter
    used to provide (see write_gate_decision.py's decide() docstring for why it moved here) --
    tests can supply a controlled fixture without needing to fork a real Claude Code session
    tree; production leaves this at its real default_ancestry().

    `live_started_at_fn`: the matching injectable seam for check_pid_identity() (FORE-442),
    same convention as ancestry_fn -- production leaves this at its real
    live_process_started_at_ms().

    `dispatch_index_dir`: FORE-449's dispatch-index cross-check (see below) needs its own
    injectable seam, same convention as dispatch_root -- DISPATCH_INDEX_DIR (imported from
    dispatch_writer) is a single real, machine-wide path with no per-project/per-test scoping of
    its own, confirmed live: a stale, unexpired index entry from an unrelated prior test run
    (project_root a since-deleted temp dir, same shared fixture session_id) was found sitting in
    the real ~/.claude/foreman/dispatch-index/ while building this fix, and would otherwise leak
    into every test and every other project sharing that session_id across its lifetime.
    Production leaves this at its real default (DISPATCH_INDEX_DIR)."""
    dispatch_root = Path(dispatch_root) if dispatch_root else DEFAULT_DISPATCH_ROOT
    dispatch_index_dir = Path(dispatch_index_dir) if dispatch_index_dir else DISPATCH_INDEX_DIR
    session_id = payload.get("session_id")

    # FORE-449, hoisted here per Nadia Osei's review: this is a pure payload-shape check needing
    # no record at all, and it MUST run before any dispatch-state branch below, not after. Before
    # this fix, a subagent-origin write with no dispatch record reached write_gate_decision.py's
    # decide() (its own first check) and was denied there under this exact class. The stand-aside
    # change below made that path unreachable for every record-less session -- in all 12 DF
    # projects, no session has a record, so decide() never ran and this refusal never fired for
    # any subagent, anywhere, until this hoist.
    if "agent_type" in payload:
        agent_type_value = payload.get("agent_type")
        if not isinstance(agent_type_value, str) or not agent_type_value.strip():
            return {"final": "deny", "class": "agent-type-invariant-violated",
                    "reason": f"payload carries an agent_type field whose value does not "
                              f"match the measured shape (a non-empty, non-whitespace "
                              f"string): {agent_type_value!r}"}
        return {"final": "deny", "class": "subagent-origin",
                "reason": "payload carries agent_type -- in-process subagent dispatch is refused"}

    # FORE-449: this gate's own traffic is defined by having an open Alice/Bob dispatch, never by
    # "any write anywhere" -- dispatch_writer.py is the sole writer of by-session records, so their
    # absence means no dispatch was ever opened for this exact session, not "unknown." CORRECTION
    # to this comment's own first version: the prior deny path here was NOT a crash --
    # write_gate_decision.py's decide() has its own separate load_by_session_record() that
    # explicitly raises Decision("no-open-dispatch", ...), a deliberate, tested fail-closed
    # posture (test_no_open_dispatch_denied,
    # test_reach_control_write_with_no_open_dispatch_but_registered_project_still_denied_correctly
    # -- both call decide() directly, never run_supervised(), so neither actually exercises this
    # supervisor-level change; per Nadia's review this needs new run_supervised()-level coverage,
    # not just leaving those two green and implying they still cover the production path).
    # This IS an intentional reversal of that posture for the supervisor layer, not a bug patch --
    # risk-assessed (RISK-ASSESSMENT-write-gate-dispatch-absence-20260906/) and approved by the
    # operator for DF scope, pending Nadia Osei's confirmation before production scope (given, with
    # two open items -- see her NADIA-REVIEW-write-gate-stand-aside-20260906.md). CORRECTED, per
    # her Q3 pushback: standing aside here does NOT rest on FORE-422 (Alice's OS-level permission
    # fragment) as a safety boundary -- FORE-422 is staged but unarmed anywhere tonight, so it
    # cannot carry that argument yet. The honest claim is narrower: standing aside restores exactly
    # the write-access level that existed in each of these 12 projects BEFORE this gate was wired
    # in, via whatever that project's own ordinary settings.json permissions already allow --
    # neither better nor worse than today, for any session this gate was never tracking a dispatch
    # for. Missing session_id stays fail-closed (anomalous for real Claude Code traffic). A present
    # session_id with no by-session record is the legitimate stand-aside case UNLESS the record
    # exists but is damaged (RecordDamaged, below -- the opposite conclusion from absence) or the
    # separate dispatch index disagrees (see below).
    if not session_id:
        return {"final": "deny", "class": "missing-session-id",
                "reason": "payload carries no session_id -- cannot determine dispatch state"
                          + RECOVERY_NOTE}
    try:
        record = load_by_session_record(dispatch_root, session_id)
    except RecordDamaged as exc:
        # Per Nadia's Q1: a record that EXISTS but is unreadable/malformed is the opposite
        # conclusion from genuine absence and must never be treated as "not Alice/Bob traffic."
        return {"final": "deny", "class": "record-damaged", "reason": exc.reason + RECOVERY_NOTE}
    if record is None:
        # Tampering/inconsistency guard (risk-assessment R1, corrected per Nadia's N3): NOT a race
        # condition -- dispatch_writer.py writes the by-session record BEFORE the separate
        # dispatch-index entry, both under the same session lock, so the write ordering itself can
        # only ever produce "record present, index missing" (harmless, falls through to the
        # normal checked path below), never the direction checked here. "Index open, record
        # absent" can only arise from something deleting or corrupting the record after the fact
        # (or a future writer of the index disagreeing with dispatch_writer.py) -- a tampering
        # threat, not a timing one. Ceiling, stated plainly per Nadia's review: the by-session
        # record, the index, this script, and the settings.json that registers it are all
        # same-uid files under $HOME -- this defends a misbehaving-but-in-band Bob whose only
        # channel to disk is gated tool calls, not an adversary with arbitrary same-uid execution,
        # which no arrangement of files here can distinguish. If the index claims a dispatch is
        # genuinely open for THIS session AND THIS project while the record itself is missing,
        # that is the tampering signature, not ordinary non-Alice/Bob traffic -- deny loudly rather
        # than silently stand aside, matching the quarantined lineage's own two-signal
        # reconciliation (bob_write_gate.py: "an open dispatch with no control plane at all is a
        # real inconsistency worth a loud, distinctly-labeled deny").
        #
        # An expiry check is required, not just "status open" -- confirmed live while building
        # this fix: session_id is scoped to a whole Claude Code conversation, not to one project,
        # and DISPATCH_INDEX_DIR has no per-project namespacing of its own. A real, unexpired, but
        # unrelated index entry (project_root a since-deleted temp dir from an entirely different
        # test/dispatch) was found sitting in the real ~/.claude/foreman/dispatch-index/ for this
        # exact fixture's session_id while building this fix. Without the expiry check
        # specifically, ANY cleanup of the dispatch root (an operator recursively deleting it,
        # a tmp reaper, a rollback) would permanently write-lock every session that ever held a dispatch,
        # surfacing as a cryptic class name mid-work -- this expiry check is what prevents that.
        #
        # N4, per Nadia Osei's second review pass: project-root derivation here is NOT allowed to
        # default a real tampering signature to "stand aside" just because it couldn't be computed.
        # By the time this branch runs, four conditions already hold -- index entry exists, says
        # open, unexpired, record missing -- and that conjunction IS the tampering signature on
        # its own; project-match only ever existed to rule OUT a stale-but-genuinely-unrelated
        # entry (staleness itself is now the expiry check's job, independently). The first draft's
        # non-denying try/except defaulted "can't compute a project root" to "no match, stand
        # aside" -- confirmed live by her probe to fail open for exactly the traffic shape that
        # matters most: Bash (no file_path, so candidate_root is always None -- the guard never
        # fired for Bash at all), a write outside any git working tree, and a write in a project
        # not registered in TESSERA (plausibly several of the 12 DF targets). Fixed: an
        # unverifiable project root, WITH the tampering signature already present, denies under
        # its own distinct class rather than standing aside. A project root that CAN be computed
        # and does NOT match the index's claimed project still stands aside correctly -- that is
        # the genuine stale-unrelated-entry case this comparison exists to allow through.
        #
        # FORE-494 (P0/S1, ingredient 2) CORRECTION to N4's own posture, argued independently
        # rather than taken from the ticket: N4 was right that "can't compute a root" must not
        # default to stand-aside WHILE genuinely unverifiable. It over-reached by treating
        # candidate_root as the ONLY available signal. Confirmed live the night this fix was
        # written: a bare `echo probe` (or any read-only Bash with no write target) denies here
        # unconditionally today, locking the session out of Bash entirely -- including the one
        # tool a locked session would need to diagnose itself. But the payload's own `cwd` field
        # is a second, independent, harness-populated source of the same fact candidate_root was
        # trying to establish, and it is available in exactly the case candidate_root has
        # nothing. Absence of a DERIVABLE root is not the same claim as absence of ALL
        # information -- N4's own four-condition tampering signature (index open, unexpired,
        # record missing) is unaffected by anything below; only the "can we rule the mismatch
        # out" question gets a second source. Three sub-cases, kept distinguishable in the
        # verdict ledger by class, exactly as N4's own convention already establishes elsewhere
        # in this file:
        #   - cwd resolves and does NOT fall under the index's claimed project -> the genuine
        #     stale-unrelated-entry case, same conclusion C already reaches via candidate_root,
        #     reached here via a different signal -- stand aside, distinct class so the two
        #     paths stay separately measurable.
        #   - cwd resolves and DOES fall under the index's claimed project -> the tampering
        #     signature is now positively confirmed via a second source, not merely
        #     unfalsified -- deny, under its own class, same severity as a candidate_root-
        #     confirmed match, never weaker.
        #   - cwd is absent, or fails to resolve -> genuinely no information from either source.
        #     Falls through to the ORIGINAL unverifiable deny below, unchanged in class or
        #     reason -- this is deliberate: a payload carrying neither a derivable write target
        #     nor a usable cwd is synthetic/malformed by real Claude Code traffic's own norms
        #     (cwd is populated on every real PreToolUse call), and the fail-closed default for
        #     that residual case is unchanged from N4.
        index_path = Path(dispatch_index_dir) / f"{session_id}.json"
        if index_path.is_file():
            try:
                index_entry = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return {"final": "deny", "class": "dispatch-index-unreadable",
                        "reason": f"dispatch index entry for {session_id!r} exists but is "
                                  f"unreadable: {exc}"}
            expires = index_entry.get("expires_epoch")
            index_live = isinstance(expires, (int, float)) and expires >= time.time()
            if index_entry.get("status") == "open" and index_live:
                candidate_edits = payload.get("edits") or (
                    [{"path": (payload.get("tool_input") or {}).get("file_path")}]
                    if (payload.get("tool_input") or {}).get("file_path") else [])
                candidate_root = None
                if candidate_edits:
                    try:
                        candidate_root = rederive_project_root(
                            [e["path"] for e in candidate_edits if e.get("path")])
                    except Exception:  # noqa: BLE001 -- comparison-only, does not itself deny
                        candidate_root = None
                if candidate_root is None:
                    cwd_match = cwd_falls_under(payload.get("cwd"), index_entry.get("project_root"))
                    if cwd_match is False:
                        return {"final": "silent", "class": "not-alice-bob-traffic-cwd-differs"}
                    if cwd_match is True:
                        return {"final": "deny", "class": "dispatch-index-mismatch-unverifiable-cwd-confirms",
                                "reason": f"dispatch index claims an open dispatch for "
                                          f"{session_id!r} with no by-session record; this "
                                          f"write's own project root could not be derived from "
                                          f"its target, but its cwd ({payload.get('cwd')!r}) "
                                          f"falls under the index's claimed project "
                                          f"({index_entry.get('project_root')!r}) -- real "
                                          f"inconsistency, confirmed by a second source, not "
                                          f"ordinary non-Alice/Bob traffic"}
                    return {"final": "deny", "class": "dispatch-index-mismatch-unverifiable",
                            "reason": f"dispatch index claims an open dispatch for "
                                      f"{session_id!r} with no by-session record, and this "
                                      f"write's own project root could not be determined (no "
                                      f"derivable write target, and no usable cwd either) to "
                                      f"rule the mismatch out -- real inconsistency already "
                                      f"present, not ordinary non-Alice/Bob traffic"}
                if str(candidate_root) == index_entry.get("project_root"):
                    return {"final": "deny", "class": "dispatch-index-record-mismatch",
                            "reason": f"dispatch index claims an open dispatch for "
                                      f"{session_id!r} in this exact project "
                                      f"({candidate_root}) but no by-session record exists -- "
                                      f"real inconsistency, not ordinary non-Alice/Bob traffic"}
        return {"final": "silent", "class": "not-alice-bob-traffic"}

    if record is not None:
        try:
            ancestry_ok, ancestry_reason = check_ancestry(record, ancestry_fn=ancestry_fn)
        except AncestryCheckFailed as exc:
            # FORE-434: a real ps-subprocess failure gets its own class, distinguishable in the
            # verdict ledger from an actual spoofing event (class "ancestry-mismatch" below) --
            # these are different audit-trail outcomes, not the same denial for two causes.
            return {"final": "deny", "class": "ancestry-check-failed",
                    "reason": str(exc) + RECOVERY_NOTE}
        if not ancestry_ok:
            return {"final": "deny", "class": "ancestry-mismatch",
                    "reason": ancestry_reason + RECOVERY_NOTE}

        try:
            identity_ok, identity_reason = check_pid_identity(record, live_started_at_fn=live_started_at_fn)
        except PidIdentityCheckFailed as exc:
            # Same split as ancestry-check-failed above: a real `ps` operational failure is a
            # different audit-trail outcome than a completed lookup that finds a genuine
            # mismatch (class "ancestry-pid-reused" below).
            return {"final": "deny", "class": "pid-identity-check-failed", "reason": str(exc)}
        if not identity_ok:
            return {"final": "deny", "class": "ancestry-pid-reused",
                    "reason": identity_reason + RECOVERY_NOTE}

    rationale_dir_real = None
    if record is not None:
        rationale_dir = record.get("rationale_dir")
        if rationale_dir:
            # J-C12's own canonicalization requirement -- resolve BEFORE writing into the
            # profile, closing the real /var-vs-/private/var trap FORE-413 found live.
            rationale_dir_real = Path(rationale_dir).resolve()
    profile_text = build_sandbox_profile(rationale_dir_real)

    # REAL DEFECT FIX (FORE-409/410): computed here, not inside the sandboxed child -- see
    # write_gate_decision.py's decide() docstring. Same edit-shape derivation decide() itself
    # uses (payload["edits"] wins if present and non-empty, else fall back to tool_input's
    # single file_path), kept in sync deliberately -- both need "what paths are being written"
    # before either can do anything useful with them. Uses .get() throughout, unlike decide()'s
    # own payload["tool_input"]["file_path"] direct indexing, since a malformed/adversarial
    # payload reaching the supervisor must deny cleanly here, never KeyError into a crash.
    edits = payload.get("edits")
    if not edits:
        file_path = (payload.get("tool_input") or {}).get("file_path")
        edits = [{"path": file_path}] if file_path else []
    if not edits:
        return {"final": "silent", "class": "bash-no-derivable-target"}
    try:
        project_root_real = rederive_project_root([e["path"] for e in edits])
    except DispatchDenied as exc:
        # FORE-659 (Stage 2): the old message was bare str(exc) regardless of which of
        # rederive_project_root's three real failure shapes fired. Operator decision, logged
        # on FORE-659, 2026-09-18: diagnostic-only, not self-service -- this project already
        # gates registration-shaped actions (wiring_registration, decider_dispatch) as
        # operator-authorized, so a TESSERA registration remedy is NAMED for a human reviewing
        # the deny, never framed as something the agent hitting this gate should run itself.
        # Two of the three real causes have no universal fix at all -- disclosed as such
        # rather than forcing a uniform pre-fill treatment that doesn't apply to them.
        reason = _explain_rederivation_failure(exc)
        return {"final": "deny", "class": "project-root-rederivation-failed", "reason": reason}
    except Exception as exc:  # noqa: BLE001 -- any OTHER rederivation failure still denies,
        # never crashes -- the same fail-closed posture as before, for whatever isn't a
        # DispatchDenied (e.g. a genuine bug inside rederive_project_root itself).
        return {"final": "deny", "class": "project-root-rederivation-failed", "reason": str(exc)}
    if project_root_real is None:
        return {"final": "deny", "class": "project-root-rederivation-failed",
                "reason": "payload carries edits/tool_input but no project root could be derived from them"}

    tmp_dir = Path(dispatch_root) / "gate-tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    payload_path = tmp_dir / f".payload.{os.getpid()}.{time.time_ns()}.json"
    profile_path = tmp_dir / f".profile.{os.getpid()}.{time.time_ns()}.sb"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    profile_path.write_text(profile_text)
    try:
        exit_code, stdout, timed_out = spawn_decision_child(
            payload_path, dispatch_root, profile_path, project_root_real, timeout=timeout)
        return evaluate_child_result(exit_code, stdout, timed_out)
    finally:
        # FORE-438: this cleanup is still best-effort (a failed unlink must never mask the
        # real decision returned above), but no longer silent -- these temp files can carry
        # full hook payload content, so a stale gate-tmp/ leftover with no operator visibility
        # is itself worth disclosing, not swallowing.
        for p in (payload_path, profile_path):
            try:
                p.unlink()
            except OSError as exc:
                print(f"[write_gate] could not remove temp file {p}: {exc}", file=sys.stderr)


def main(argv=None):
    """J-C13 (FORE-415, real gap found and closed during FORE-411 re-verification): records
    exactly one verdict_ledger row per invocation, via hook_common.record_verdict() -- the SAME
    "one record builder" every other hook in this repo uses, never a second, independently
    re-derived write path. See this module's own top-of-file docstring for the full design
    rationale (fire/silent/error mapping, why target never appears here).

    Also closes a related, previously-undiscovered gap found while wiring this in: main() had
    ZERO exception handling around run_supervised() -- a bug in the supervisor's own Python code
    (not the sandboxed child, which is already isolated by the subprocess boundary and converted
    to a real deny by evaluate_child_result()) would have propagated uncaught, which this
    document's own text names as fail-open at the harness level ("a guard that crashes and fails
    open is a guard that looks alive and does nothing"). REQ-54-D6/this module's own docstring
    already claim write_gate "supplies its own fail-closed entrypoint" -- this closes the one
    place that claim did not yet hold.

    A THIRD real gap found while writing this fix's own tests (this project's own "test your
    own test code" rule doing its job): main() previously called run_supervised(payload) with
    NO dispatch_root argument at all, always defaulting to the real production path -- unlike
    its own PostToolUse sibling, write_gate_confirm.py, which already reads a
    WRITE_GATE_DISPATCH_ROOT env var override. That asymmetry meant main() itself, as opposed
    to run_supervised() directly, could never be exercised against an isolated test fixture --
    exactly the shape of gap this task's own first attempt at a real main()-level test walked
    straight into (silently exercising the real production dispatch root instead of the test's
    own tmp fixture). Fixed to match the sibling's own already-correct convention."""
    started = time.time()
    payload = {}
    try:
        payload = hook_common.read_input()
        dispatch_root = os.environ.get("WRITE_GATE_DISPATCH_ROOT")
        dispatch_index_dir = os.environ.get("WRITE_GATE_DISPATCH_INDEX_DIR")
        result = run_supervised(payload, dispatch_root=dispatch_root,
                                 dispatch_index_dir=dispatch_index_dir)
        elapsed_ms = int((time.time() - started) * 1000)
        if result["final"] == "deny":
            hook_common.set_rule(result["class"])
            hook_common.deny(result["reason"])
            # BUILD4-I1: reason=result["reason"] threaded through explicitly -- this module's own
            # custom fail-closed entrypoint (see module docstring for why it cannot use
            # hc.run()) re-implements the ledger-write call independently, matching
            # hook_common.run()'s own standard "fire" branch, and previously never passed the
            # reason it had just computed one line above into this call at all.
            hook_common.record_verdict(payload, "fire", kind="deny", duration_ms=elapsed_ms,
                                        decision="deny", rule_id=result["class"],
                                        reason=result["reason"])
        else:
            # silent: emit nothing to the harness either way, per "denies or stays silent, never
            # asks" -- but the LEDGER still distinguishes two different silents (FORE-449, closing
            # a confirmed regression test_deny_records_fire_with_decision_and_rule_id caught):
            # result.get("class") is present ONLY for the stand-aside case (no Alice/Bob dispatch
            # open at all -- this write was never this gate's traffic), and absent for the
            # original "the full chain of checks all passed" case, where no single rule decided
            # anything. Passing it through as rule_id when present keeps the two tellable apart in
            # the verdict ledger without adding a second axis alongside verdict/decision -- same
            # convention the deny branch above already uses.
            hook_common.record_verdict(payload, "silent", duration_ms=elapsed_ms,
                                        rule_id=result.get("class"))
    except Exception as exc:  # noqa: BLE001 -- the one place this module deliberately widens
        # its own catch, exactly because there is nothing narrower left between here and an
        # uncaught crash reaching the harness as an implicit allow.
        elapsed_ms = int((time.time() - started) * 1000)
        # CHV2-158: reason is built ONCE, here, and threaded into both calls below -- before
        # this fix, record_verdict() below was called first with no reason= at all, and the
        # f-string that becomes the actual deny message was only constructed on the following
        # line. An internal-error deny therefore landed in the ledger with no record of what the
        # agent was actually told, the same missing-reason shape CHV2-134 fixes elsewhere, but
        # caused here by ordering rather than by the reason genuinely not existing yet. Matches
        # this module's own deny branch above (BUILD4-I1), which already threads
        # result["reason"] into record_verdict for the identical reason.
        reason = f"write_gate internal error: {exc}"
        hook_common.record_verdict(payload, "error", kind=type(exc).__name__,
                                    duration_ms=elapsed_ms, reason=reason)
        hook_common.deny(reason)
    sys.exit(0)


def _explain_rederivation_failure(exc):
    """FORE-659: turns a DispatchDenied from rederive_project_root() into a message that
    names which of the three real causes fired and what a HUMAN (not the denied agent) can do
    about it -- never a self-service command for the agent to run, per the operator's own
    2026-09-18 decision on this ticket (project registration is a permanent registry mutation,
    the same class of action wiring_registration/decider_dispatch already gate as operator-
    authorized, not agent self-service)."""
    if exc.klass == "cross-project-edits":
        return (
            f"This write touches paths from more than one project at once ({exc.reason}). "
            f"No single command fixes this -- split the edit into separate calls, one per "
            f"project, based on which paths actually belong together."
        )
    if exc.klass == "project-root-unresolvable":
        if "matches zero registered TESSERA projects" in exc.reason:
            return (
                f"{exc.reason}. Diagnostic for a human reviewing this deny, NOT a command "
                f"for this session to run itself (creating a new TESSERA project entry is a "
                f"permanent registry mutation, the same class of action this project already "
                f"gates as operator-authorized elsewhere): an operator can register this repo "
                f"with `python3 -m tessera.api.cli register-project --new-codename <NAME> "
                f"--new-prefix <PREFIX> --source-root <the repo root named above>`."
            )
        return (
            f"{exc.reason}. No single command fixes this -- whether this path should be "
            f"inside a git repository at all depends on what it actually is; a human should "
            f"decide, not this session guessing."
        )
    return exc.reason


if __name__ == "__main__":
    main()
