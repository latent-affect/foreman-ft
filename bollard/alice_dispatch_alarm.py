#!/usr/bin/env python3
"""A2 (FORE-422 rescope) -- real-time Alice dispatch alarm + attribution.

Per ARCH-REVIEW-FORE-422-RESCOPE-AUDIT-LAYER-VERDICT-20260906.md, Signal A: at the already-wired
PreToolUse:Agent hook point, record a DISTINCT, alarmable entry whenever an alice subagent is
dispatched, tagged with the persona-attribution field both A2 and A3 depend on.

WHY THIS IS NOT A SECOND DENY, AND WHAT IT ADDS. agent_dispatch_gate.py (FORE-206) already blanket-
denies in-process Agent dispatch in any registered project (allowlist = {Explore}), and already
records a generic SAFETY_DENY. So this hook deliberately does NOT deny -- it would be duplicating a
control that exists. What it adds is the two things that generic deny does not give:
  1. A persona-SPECIFIC alarm: agent_dispatch_gate's SAFETY_DENY fires identically for bob, clint,
     dana and every other non-Explore type. This fires only for alice, the zero-write persona, so
     the alice signal is queryable on its own rather than buried in the general dispatch-deny stream.
  2. The durable attribution row (persona_attribution) that A3's retrospective sweep reads.

Both hooks run under the same matcher; a deny by agent_dispatch_gate does not stop this hook from
recording, so the alarm/attribution lands whether or not the dispatch is ultimately denied. The
`decision` field records that the dispatch was (in a registered project) denied, so the attribution
is honest that this was an ATTEMPT, not a session that ran.

COVERAGE BOUNDARY, STATED. This is a hook, so it shares agent_dispatch_gate's FORE-354 limit: a
dispatch from an unregistered cwd fires no hook at all, so neither the deny nor this alarm covers
it. That path is closed by prevention instead -- A1 (LANDED 2026-09-06: Write removed from
alice.md's frontmatter, verified `tools: Read, Grep, Glob`), which is identity-scoped and needs no
hook, so an in-process alice cannot write even from an unregistered cwd. With A1 landed, this
alarm's role is squarely defense-in-depth attribution -- a queryable record that an alice dispatch
was attempted -- not a write-prevention control. A3 (the retrospective sweep) is the net under the
CLI-launched path, where permissions.deny is the wall.

FAIL-OPEN via hc.run: this is an ALARM/recorder, not a policy gate. It must never block a dispatch
(that is agent_dispatch_gate's job, fail-closed). A bug here must not brick agent dispatch, so it
uses hook_common.run's fail-open contract -- the opposite choice from agent_dispatch_gate, and
correct for the opposite job.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import persona_attribution  # noqa: E402

RULE_ID = "ALICE-DISPATCH-ALARM"
ALARM_PERSONA = "alice"


def main(data):
    if data.get("tool_name") != "Agent":
        hc.set_rule(f"{RULE_ID}:not-an-agent-dispatch")
        return

    tool_input = data.get("tool_input") or {}
    subagent_type = tool_input.get("subagent_type")

    # Exact-string match only, same discipline as agent_dispatch_gate.py's allowlist: a renamed
    # or look-alike persona must not silently trip or dodge the alice alarm.
    if subagent_type != ALARM_PERSONA:
        hc.set_rule(f"{RULE_ID}:non-alice-dispatch")
        return  # silent -- not an alice dispatch, nothing to alarm on

    session_id = data.get("session_id")
    cwd = data.get("cwd")

    # In a registered project agent_dispatch_gate.py denies this dispatch; alice is not on its
    # allowlist. We record that as the decision so the attribution is honest that the session did
    # not actually run here. If some future config allowlisted alice, this field would say "allow"
    # and A3 would then have a real running session to watch for writes -- the alarm stays correct
    # either way because it does not assume the deny.
    decision = "deny-expected(agent_dispatch_gate)"

    persona_attribution.record_attribution(
        session_id=session_id,
        persona=ALARM_PERSONA,
        cwd=cwd,
        source="alice_dispatch_alarm",
        decision=decision,
        subagent_type=subagent_type,
    )

    # Distinct, alarmable audit-plane entry, high severity, keyed on the alice persona -- separable
    # from agent_dispatch_gate's generic in-process-dispatch-denied stream.
    hc.audit(
        "ALICE_ZERO_WRITE_PERSONA_DISPATCH",
        {
            "guard": "alice_dispatch_alarm",
            "persona": ALARM_PERSONA,
            "subagent_type": subagent_type,
            "note": "zero-write Alice persona dispatched in-process; attribution recorded",
        },
        session_id,
        cwd,
        severity="high",
    )
    hc.set_rule(f"{RULE_ID}:alice-dispatch-alarmed")
    # A side effect (two ledger writes), not a permission decision. mark a real kind so the verdict
    # ledger records a fire rather than scoring this as correct silence -- but emit no stdout, so no
    # permission decision and no additionalContext: nothing is injected into the model's context and
    # the dispatch is not altered. The alarm IS the ledger rows, not a message.
    hc.mark("alarm")


if __name__ == "__main__":
    hc.run(main)
