#!/usr/bin/env python3
"""spawn_governance_probe.py -- FORE-698, the real structural fix FORE-559's own closing comment
named as not yet built: "verifying that a spawned child has live hooks before it is treated as
governed."

WHY THIS IS A LIBRARY, NOT A HOOK -- and why that is not a stylistic choice here. FORE-559's own
incident record (FOREMAN.md Part Eight, "The headless-dispatch ban"): 98.4% of headless spawns
ran with zero governance-hook activity for their entire lifetime, and the root cause was named
formally as the confused-deputy problem -- a spawned child's write jurisdiction was resolved from
its ambient launch context, never from a real capability bound to what it was touching. A hook
that runs INSIDE the child cannot be the thing that proves the child's hooks are wired, for the
same reason a witness cannot notarize their own signature: if the child's hook layer is the part
that failed to load, nothing inside that child's own process can report the failure, because the
mechanism that would report it is exactly what is missing. FOREMAN.md states this precisely:
"no gate inside the current substrate can fix the mechanism itself." So this check has to run
OUTSIDE the child, from the spawning side, against independent evidence the child cannot fabricate
by staying silent -- the verdict ledger a real, live hook actually writes.

THE MECHANISM. The caller (whatever spawns a headless/subagent child -- dispatch_writer.py, a
future router, an orchestrator's own spawn code) is responsible for two things this module does
not do: spawning the child, and causing it to execute exactly ONE real, cheap, harmless tool call
tagged with a caller-chosen probe_tool_use_id (a Bash `echo` is sufficient -- the specific tool
does not matter; what matters is that ANY governed PreToolUse/PostToolUse hook fires for it).
verify_child_governed() then polls hooks/verdict_ledger.py's own VERDICT_LOG
(~/.claude/telemetry/verdicts.jsonl, append-only, the same file every real hook in this project
writes through hook_common.record_verdict/verdict_ledger.record) for a row whose session_id AND
tool_use_id both match. A real match proves the harness's hook layer actually executed governed
code for THIS specific child, at THIS specific moment -- not that the child's cwd looks like a
registered project (the exact ambient-authority substitution FORE-559's incident is made of), and
not that the child's own settings.json merely CONTAINS the right hook entries (config presence is
not runtime liveness -- a hook can be correctly configured and still fail to fire for reasons
config inspection cannot see: a broken launchd job, a missing interpreter, a permissions error on
the hook script itself).

FAIL-CLOSED, THE SAME DISCIPLINE ATLASSN-187's registry_gate_client ALREADY ESTABLISHES IN THIS
CODEBASE (PDP.md section 5's D2, restated here for a different edge): a timeout with no matching
row returns UNGOVERNED, never GOVERNED by default. An unreadable ledger returns LEDGER_UNAVAILABLE,
never a silent pass. There is no code path in this module that can return GOVERNED without a real,
observed ledger row proving it.

WHAT THIS DOES NOT DO, AND WHY THAT IS DELIBERATE, NOT AN OVERSIGHT:
  - Does not fix jurisdiction resolution itself (the specific FORE-559 defect: jurisdiction read
    from mutable cwd rather than the write's real target). That is a different, already-named
    mechanism (FORE-561, decider-dispatch capability bound at construction time). This module
    answers a narrower, prior question -- "are this child's hooks running at all" -- that FORE-559's
    own closing comment states as the structural fix still owed, independent of FORE-561.
  - Does not spawn anything, does not wire into any spawn call site, and does not gate any tool
    call itself. No hook_common import, no main(), no PreToolUse registration -- a future spawn
    caller imports verify_child_governed() and decides what UNGOVERNED means for it (refuse to
    trust the child's writes, kill it, escalate). Inventing that consumer here would be scope this
    ticket does not name.
  - Does not persist a governance verdict anywhere (no registry write, unlike registry_gate_client's
    read side). A caller that wants a durable, leased assertion rather than a one-shot check builds
    that on top of this, using the SAME registry.db pattern ATLASSN-187 already established --
    named as a natural next step, not built here, since no ticket asks for it yet.

STDLIB ONLY: json, time, pathlib. No cross-repo import (this ledger and this fix both belong to
claude-hooks-v2, unlike ATLASSN-187's registry.db which lives in atlas-sonnet).
"""

import json
import time
from collections import namedtuple
from pathlib import Path

# Mirrors hooks/verdict_ledger.py's VERDICT_LOG exactly -- not imported, because verdict_ledger.py
# is a WRITE-side module (hook_common's dependency, loaded on every hook's hot path) and this is a
# read-side, spawn-time-only check with no reason to share that import surface. Read directly,
# not paraphrased, from hooks/verdict_ledger.py line 31, 2026-09-20.
DEFAULT_VERDICT_LOG = Path.home() / ".claude" / "telemetry" / "verdicts.jsonl"

GOVERNED = "governed"
UNGOVERNED = "ungoverned"
LEDGER_UNAVAILABLE = "ledger-unavailable"

DEFAULT_TIMEOUT_S = 5.0
DEFAULT_POLL_INTERVAL_S = 0.05

GovernanceResult = namedtuple(
    "GovernanceResult",
    "session_id status detail matched_ts matched_handler_id",
    defaults=(None, None),
)


def _scan_for_match(ledger_path, start_offset, session_id, tool_use_id):
    """One pass over whatever has been appended since start_offset. Returns (GovernanceResult or
    None, new_offset).

    Reads via handle.readline() IN A LOOP, deliberately, not `for line in handle`. Python's text
    -mode file objects raise `io.UnsupportedOperation: telling position disabled by next() call`
    from `.tell()` once the iterator protocol (`for`/`next()`) has been used and the internal
    decode buffer has read ahead past the logical position -- a real, measured bug in this
    function's own first draft (self-tested this session: every fixture that matched and BROKE
    out of a `for` loop early raised this on the following `.tell()`, misreported as
    LEDGER_UNAVAILABLE, and only a fixture whose loop ran to natural exhaustion happened not to
    trip it). `readline()` does not carry that restriction -- `.tell()` after a `readline()` call
    is well-defined per the stdlib's own documented exception (`io.IOBase.tell`).

    A malformed line is skipped, not fatal -- a concurrent writer's own documented failure mode
    (verdict_ledger.record()'s fallback marker) can produce a line this function does not need to
    parse to keep scanning the rest."""
    with ledger_path.open("r", encoding="utf-8") as handle:
        handle.seek(start_offset)
        matched = None
        while True:
            raw_line = handle.readline()
            if not raw_line:
                break
            if not raw_line.endswith("\n"):
                # A partial trailing line mid-write by a concurrent hook process -- not yet a
                # complete record. Rewind to before it and leave it for the next poll, same
                # discipline as this project's other append-only-ledger readers.
                handle.seek(handle.tell() - len(raw_line.encode("utf-8")))
                break
            try:
                row = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            if row.get("session_id") == session_id and row.get("tool_use_id") == tool_use_id:
                matched = GovernanceResult(
                    session_id, GOVERNED,
                    f"real verdict row matched at handler {row.get('handler_id')!r}",
                    row.get("ts"), row.get("handler_id"),
                )
                break
        new_offset = handle.tell()
    return matched, new_offset


def verify_child_governed(session_id, probe_tool_use_id, timeout_s=DEFAULT_TIMEOUT_S,
                          ledger_path=DEFAULT_VERDICT_LOG,
                          poll_interval_s=DEFAULT_POLL_INTERVAL_S, sleep=time.sleep,
                          now=time.monotonic):
    """Polls `ledger_path` for a real hook_verdict row proving `session_id` executed a governed
    hook path for `probe_tool_use_id`, within `timeout_s`. Returns a GovernanceResult; never
    raises.

    D2 (PDP.md section 5), restated for this edge: zero code paths here default to GOVERNED on
    missing, stale, or unreadable evidence.

      - GOVERNED: a real matching row was found. `matched_ts`/`matched_handler_id` carry the
        evidence, so a caller can log WHICH hook proved liveness, not just that one did.
      - UNGOVERNED: the deadline passed with no matching row -- the child's hooks did not (yet,
        or ever) produce evidence they are live for this exact session/probe. The caller decides
        what UNGOVERNED means for it; this function only reports the fact.
      - LEDGER_UNAVAILABLE: the ledger file could not be read at all (permission denied, or any
        other OSError once it does exist) -- a store-level fault, distinct from UNGOVERNED's "we
        looked and found nothing" and named separately for the same reason registry_gate_client
        separates REGISTRY_UNAVAILABLE from NEVER_REGISTERED: an auditor must be able to tell
        "the check never actually ran" from "it ran and the child failed it." A ledger that simply
        does not exist YET (this machine's very first hook write has not happened) is treated as
        "nothing to find yet" and keeps polling, not as a fault -- it may legitimately appear
        mid-window once the probe's own hook fires and creates it, which the whole point of
        polling (rather than checking once) exists to catch.

    `sleep`/`now` are test-only injection points (matching registry_gate_client's own `connect`
    convention) so a test can drive this function's timeout logic without a real wall-clock
    wait; they default to the real `time.sleep`/`time.monotonic` and are never overridden in
    production."""
    deadline = now() + timeout_s
    offset = 0
    while True:
        try:
            if ledger_path.is_file():
                matched, offset = _scan_for_match(ledger_path, offset, session_id,
                                                  probe_tool_use_id)
                if matched is not None:
                    return matched
        except OSError as exc:
            return GovernanceResult(
                session_id, LEDGER_UNAVAILABLE, f"could not read {ledger_path}: {exc}",
            )
        if now() >= deadline:
            return GovernanceResult(
                session_id, UNGOVERNED,
                f"no verdict row for session_id={session_id!r} tool_use_id="
                f"{probe_tool_use_id!r} within {timeout_s}s",
            )
        sleep(poll_interval_s)


__all__ = [
    "DEFAULT_VERDICT_LOG", "DEFAULT_TIMEOUT_S", "DEFAULT_POLL_INTERVAL_S",
    "GOVERNED", "UNGOVERNED", "LEDGER_UNAVAILABLE", "GovernanceResult",
    "verify_child_governed",
]
