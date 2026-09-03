# Decision: bob_write_gate.py live/production posture

**Decided:** Option D. Keep cases 1-7 (routing/lockout) registered live exactly as they are
today. Add one explicit structural precondition guard immediately before case 8, so cases
8-19 (the record-based content-approval chain) are dormant *by construction* rather than by
the happenstance that no real C2 dispatch-record writer exists yet.

**Why:** Converged, adversarially-reviewed finding from three independent passes (6a/Priya,
ae/Clint, ticket-system-65/muse — full trail in `BLAST-RADIUS-MITIGATION-DESIGN.md` §1-9).
Cases 1-7 run on every real call today and are demonstrated self-revealing (DEVH-81 was
found through ordinary use in hours). Cases 8-19 require a real by-session record, and
`.foreman/dispatch/by-session/` does not exist in this worktree — so cases 8-19 are
*provably* unreachable by real traffic right now, registered or not, independent of any
future decision about C2. Unregistering the whole file would trade away the one proven,
low-risk benefit (cases 1-7 catching real bugs) to guard against a risk (cases 8-19 firing)
that isn't currently live. A structural guard makes that "isn't live" fact durable instead of
incidental — the next engineer who builds a C2 writer won't accidentally wake up cases 8-19
without deciding to.

Applied per REQ-55: none of the four operator-reserved conditions are met (not irreversible,
this whole closure pass is dogfood-scoped not production-Go, doesn't commit the operator
personally, doesn't contradict a standing instruction) — this was the orchestrating session's
own call, logged as a repeat escalation-drift occurrence on FORE-265.

**What it would take to overturn:** New evidence that cases 1-7 are *not* actually
self-revealing in some real scenario the falsification pass missed, or an operator decision
that the residual risk (any future code path that could open a dispatch before C2 exists) is
unacceptable even though none exists today.

**Ticket tracking ratification:** DEVH-86 (Alice/Bob write-gate consolidation epic).

**Also decided in the same pass, same evidence trail:**
- FORE-281's blocker list gets a second addition (ae/muse's independent find): FORE-282
  (TESSERA worktree-resolution bug) joins the existing C2-writer blocker. Case 4's
  unconditional call to `resolve_projects_for_repo` is currently masked by `index_open`
  being false; once C2 lands and dispatches actually open, an unregistered-worktree write
  target would hit a real deny. FORE-282 should land before or alongside C2, not
  "whenever convenient."
- `BLAST-RADIUS-MITIGATION-DESIGN.md` needs its DEVH-81/DEVH-85 citation corrected (opposite
  failure shapes, not the same evidence for the same claim) and its FORE-286 "one file"
  framing precision-corrected (bob_write_confirm.py is a separate file, correctly grouped
  for serialization, not literally the same collision surface).

**Addendum, 2026-09-03 (post-implementation):** 6a re-verified this decision's premise against the
actual code (`bob_write_gate.py:212-300`) rather than accepting the summary above at face value.
The real live-deny surface today is narrower than "cases 1-7": only case 6 (`agent_type` present
-> deny, unconditional), case 7 (no `session_id` -> deny, unconditional), and the fail-closed error
path are genuinely live. Cases 2 and 4 are also dormant, gated on the same `index_open` flag as
cases 8-19 -- neither reads a dispatch record, so both stay valid regardless of whether C2 ever
exists.

**Correction, same day, caught by 6a re-checking its own claim against the landed commit:** an
earlier version of this addendum stated 5b's structural guard "moves cases 2 and 4 into
dormant-by-construction... making FORE-282's worktree bug visible instead of silently armed once
C2 lands." That was wrong and has been removed. Verified directly against commit `13df9e1`: case
7b sits between case 7 and case 8, and gates only the record-based chain (case 8 onward). Cases 2,
4, and 5 are unaffected by this guard -- they remain dormant exactly as before, gated on
`index_open`, which is correct design, not a shortfall. **Option D does nothing about the FORE-282
exposure.** Case 4 still calls `project_resolve` unconditionally on every real Write and still arms
the moment C2 lands. FORE-282 must stay a real blocker on FORE-281 regardless of D landing -- it
already was added there (comment, this session) and stays there. Do not drop it on the strength of
D having landed; that reasoning would be wrong.

One real disclosure surfaced by this re-verification, not a decision: case 6 denies every
Edit/Write/Bash carrying `agent_type`, unconditionally -- meaning no in-process subagent can write
anything in this repo while this gate is registered. This is 4.4.3b's own mandate working as
designed, not a new risk, but it was previously undiscoverable without reading source. Filed as
DEVH-89 (disclosure, not escalated). Confirmed live by driving the gate as a real subprocess:
ordinary Write with no dispatch -> silent, exit 0; identical payload with `agent_type` present ->
`permissionDecision: deny`.

**Decided by:** agent-remediation-aa (orchestrating session), 2026-09-03, per REQ-55.
