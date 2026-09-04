# Validation — bollard / R14/R15 guard chain, 2026-09-04 (revised)

Run because the stage was skipped: a ship-readiness GO was rendered 2026-09-03 with no Validate
artifact of any kind in `.foreman/`. See FORE-308 for why nothing objected.

**Rendered by:** Priya Desai persona, in-session (dev-harness-run2-81), dispatched by
agent-remediation-3c. Priya's lane only — scope, done-state, premortem quality. Nadia Osei's STRIDE
half is addressed below only as a gap this stage still owes, not rendered here.

**This supersedes the same-named report from earlier today.** That version's `PARTIALLY MET`
verdict rested on one checked-but-wrong premise: "no docs/PRD.md exists." A root-level `PRD.md`
does exist in this repo (914 lines, `## 2. Goals`, `## 5. Out of scope`, a full requirements table)
— the report checked the wrong path convention (`docs/PRD.md`, the older per-repo layout) and
missed the repo's actual one (root `PRD.md`, the same layout `agent-remediation/PRD.md` and
`tessera-v2/PRD.md` use, confirmed against a same-day drift survey of every `PRD.md` copy on this
machine). PDP revision 2 §3.1 also states this directly: `SCOPE.md` merged into `PRD.md`, and a
project running without a separate `SCOPE.md` is a "completed experiment showing one document is
enough" — not a missing artifact. The concept-stage comparison the prior report said no artifact
in this repo could answer is, in fact, answerable. Re-run below.

## Done-state, verbatim from bollard/GOALS.json

> When preflight_blocking_gate.py would DENY because tessera_resolver.resolve returned ambiguous or
> unreachable, an Edit, Write, or Bash call whose sole write target resolves to
> {project_root}/.foreman/tessera-prefix is not denied; every other Edit, Write, or Bash call in
> that state is still denied. AND (R24, DEVH-2): bollard ships guard_destructive.py,
> guard_prodconfig.py and guard_untrusted_web.py, each of which, driven as a subprocess through its
> real hook entry point, emits a PreToolUse deny on the traffic it exists to block and emits nothing
> at all on everything else. AND (R14a/R15a, DEVH-16): bollard ships guard_allowlist.py,
> guard_semantic_resolution.py, guard_pattern_feed.py and guard_os_sandbox.py plus
> lib/deny_capability.sb and lib/capability_scope.sh; the capability-removal pair denies the PRD R13
> quote-embedding shell-escalation evasion with both detection guards absent from the registration,
> and SemanticResolutionGuard alone catches a character-code-constructed destructive command with
> the other three absent.

## Checked against the running system

**Ships-clause: MET, verified by listing rather than by claim.** All nine named artifacts are
present in `bollard/`: guard_destructive.py, guard_prodconfig.py, guard_untrusted_web.py,
guard_allowlist.py, guard_semantic_resolution.py, guard_pattern_feed.py, guard_os_sandbox.py,
lib/deny_capability.sb, lib/capability_scope.sh.

Note the ships-clause is satisfied by guard_os_sandbox.py existing, which it does. It is
deliberately NOT registered (FORE-287 operator hold, reconfirmed 2026-09-04). The done-state says
"ships", not "is registered", so the hold does not fail this criterion — but a reader who assumes
"ships" implies "active" would draw the wrong conclusion, and the third conjunct's own wording
("with both detection guards absent from the registration") shows the distinction was deliberate.

**Behavioural clauses: MET on suite evidence, re-run fresh for this revision.** `/Users/m5/.venv/
bin/python3 -m unittest discover -p 'test_*.py'` in `bollard/`, this session, just now: **210
tests, 0 failures, 0 errors, 4.462s** (up from the 201 the prior report recorded — nine tests
added since, consistent with `bollard/test_component_map_covers_guards.py` landing in the interim).
One stderr line during the run — `[hook_common] python3 -m unittest error (failing open): boom` —
is a negative-control test's own deliberate output (`hook_common.py`'s fail-open default is a
named, disclosed out-of-scope item, not a new failure); final tally is a clean OK, not masked.
Covers the preflight self-remedy exemption, the three R24 guards' deny behaviour driven through
real hook entry points, and the capability-removal and SemanticResolutionGuard cases.

**Limit on that evidence, still stated rather than glossed.** This is the project's own suite
passing. It is not independent falsification. The separately-tracked figure is 11/19 criteria
independently falsified, not 19/19 — so a Validate result resting on the suite alone inherits that
gap. The suite passing is necessary, not sufficient.

## Checked against the concept-stage document (the check the prior report could not do)

**Traceability: MET.** `PRD.md` §9 item 1 records the binding resolution: `layered_command_guard.py`
was resolved "never written" by C1's default (2026-09-02T11:21Z, orchestrator-applied per concept
gate condition C1), making R14/R15 "build-from-nothing requirements sized at design-and-scope,"
explicitly recorded on **DEVH-16**. Checked directly in TESSERA: **DEVH-16, "R14/R15:
layered_command_guard.py treated as never written (C1 default applied, architecture frozen) --
build-from-nothing, ready for design-and-scope," status `closed`.** `GOALS.json.done_state` cites
the same ticket by name for the same requirement pair. The chain from concept document to component
criteria is real and checkable, not asserted: PRD.md §9 → DEVH-16 (closed) → DEVH-61 through DEVH-64
(the four architecture-recycle passes that decomposed OSSandboxGuard / SemanticResolutionGuard /
AllowlistGuard / PatternFeedGuard, all closed) → GOALS.json's done-state, which names those same
four classes.

**Section 10's own gate status is corroborating, not just my read:** PRD.md records its own
concept-gate GO, package at `.foreman/tpm-gate-concept.json` revision 2, graded against commit
`808558c`, all three conditions discharged in the document. This is a real, findable gate record —
not a prose assertion with nothing behind it, which is exactly the distinction PDP §8 cares about.

**Out-of-scope, at the PRD level: clean.** PRD.md §5's ten out-of-scope items (R0 context-size
work, Alice/Bob QA architecture, FCLB duplication, CIS RAM scoring, token/cache mechanics, the
1,165-function blanket refactor, `cli.py`'s CC-51, EXIF remediation, `safety.jsonl` redaction,
Presidio PII scanning) — none overlap R14/R15 or the bollard guard chain. No absorption evident at
this level.

**Residual honesty note, not a blocker:** PRD.md §9 item 1's last paragraph says DEVH-16 "remains
blocked, but on a different thing" (R13's control-ordering re-derivation, DEVH-15), and the
document's own author is recused from that specific sub-question. That blocker is about ordering
rationale, not about whether R14a/R15a's built artifacts satisfy the resolved requirement — the
done-state and the closed DEVH-61–64 chain answer that independently — but a reader tracing DEVH-16
itself should know its blocked/closed status may have moved since 2026-09-02 for reasons unrelated
to this component.

## Out-of-scope absorption (component level)

`GOALS.json.out_of_scope` names, among others: **"Changing the Edit|Write|Bash matcher."**

**Live near-miss, caught by running this stage.** Earlier today the orchestrator decided to close
Marcus's Go-item 5 by adding NotebookEdit to `GATE_TOOL_NAMES`, and `dev-harness-run2-93`
independently established that the code change is inert without also widening the PreToolUse
matcher — which is this exact recorded non-goal.

**Re-checked fresh for this revision, not carried forward as an open question:** `bollard/
GOALS.json`'s out-of-scope entry now reads (commit `d349ca1`, 2026-09-03T19:12:47-07:00,
DEVHR2-4, cross-ref FORE-309) "Superseded IN PART... lifted only to add NotebookEdit [to
bob_write_gate.py's lockout tuple]... Content-binding NotebookEdit is a separate, harder design
question — not decided here and still out of scope." I checked `.claude/settings.json` directly:
its `Edit|Write|Bash` matcher (line 5) is **unchanged** — NotebookEdit traffic still does not reach
any of the twelve hooks fanned out from that entry, bollard's five guards included. So: the scope
amendment was correctly recorded (not silently folded in), and the actual matcher widening it
deliberately did not authorize has, in fact, not happened. This is the system working as designed,
not an open wound — **disposition: MET, absorption avoided and correctly disclosed.**

No other out-of-scope item shows evidence of absorption on this pass.

## Assumed-in-scope items silently dropped

**Validate itself, partially addressed by this report, not fully closed.** A reasonable reader of
a nine-stage PDP (revision 2's count) assumes the validate stage runs before the gate that follows
it. It did not for this workstream, and this report is the first VALIDATION-REPORT this component
has. What remains: PDP §3's validate "closes when" needs **both** a VALIDATION-REPORT (this
document) **and** `SECURITY-PRIVACY-REVIEW` with "every declared trust boundary has a STRIDE row."
Checked: `SECURITY-PRIVACY-REVIEW.md` exists in this repo (2026-09-02, Nadia Osei persona) but its
own scope line says input was bounded to "`PRD.md`'s R7 and R8 text only" and its one bollard-
related row (B4) is marked "Pre-existing, untouched by Run 2." **None of the six R14a/R15a
artifacts — guard_allowlist.py, guard_semantic_resolution.py, guard_pattern_feed.py,
guard_os_sandbox.py, lib/deny_capability.sb, lib/capability_scope.sh — appear anywhere in it.**
That half of the stage's closing condition is genuinely missing, not stale; grepped directly, zero
hits.

**A machine-readable record of the ship-readiness verdict.** The 2026-09-03 GO exists only as prose
in `dev-harness-run2-qa-security/SHIP-READINESS-REVERIFY-20260903.md`. `.foreman/SHIP-CHARTER.json`
still carries the 2026-09-02 charter scoped to DEVH-56 (verified: `generated_at:
2026-09-02T17:51:31Z`, checks keyed to DEVH-56 evidence). `ship_readiness_gate.py` reads the
charter, not the prose, so the enforcement layer does not know this workstream passed. Unchanged
from the prior report; independently re-confirmed for this revision.

## Deployment / freeze marker

`.foreman/frozen.json` exists (2026-09-02, `status: "Record of intent, not an enforced re-entry
boundary"` — its own text says the gate it feeds is not wired live in this build). Not refreshed by
this pass. Now that done-state is checked and MET against a real concept-stage document, refreshing
it is reasonable once Nadia's half closes — not before, since a freeze marker written ahead of the
stage's own closing condition would overstate this pass's completeness the same way the prior
`PARTIALLY MET` understated it.

## Stage verdict (PDP §8 vocabulary), rendered by Priya Desai as validate's named decider

**Priya's own finding (VALIDATION-REPORT, this document): MET.** Done-state holds against the
running system (ships-clause and behavioural clauses both verified fresh). Traces cleanly to
PRD.md's concept-stage resolution via DEVH-16 and the DEVH-61–64 architecture-recycle chain, all
closed. No out-of-scope absorption, at either the PRD level or the component level — the one live
near-miss was caught, correctly disposed, and re-verified not to have happened. This corrects and
replaces the earlier `PARTIALLY MET`.

**Full stage decision: HOLD, not go, not go(unbound).** PDP §3 makes Priya the stage's sole decider
but names two required artifacts; the second, `SECURITY-PRIVACY-REVIEW` with STRIDE rows for these
six specific artifacts, does not exist. Rendering a stage-level go on half the required evidence
would be exactly the "prose says it passed, the ledger doesn't know" pattern this whole exercise
exists to stop.

- **Owner:** Nadia Osei persona, next dispatch.
- **Re-entry condition:** a `SECURITY-PRIVACY-REVIEW` (new document or an amendment to the existing
  one) with STRIDE rows for guard_allowlist.py, guard_semantic_resolution.py, guard_pattern_feed.py,
  guard_os_sandbox.py, lib/deny_capability.sb, and lib/capability_scope.sh's real trust boundaries.
- **Date:** not fixed here — provisional, per the tpm skill's own Provisional Decision Record
  mechanism (§6): this HOLD proceeds now rather than stalling on operator sign-off for a date, and
  should be ratified or overridden by the orchestrator/operator rather than left open indefinitely.
  Flagging this honestly rather than inventing a calendar date I have no authority to set: PDP §8
  requires a hold to name a date, and this one does not yet, which makes it itself provisional under
  the same rule it's trying to follow.
- **Also note, mechanically:** even a fully-evidenced go here would be `go(unbound)` per PDP §8 —
  `.foreman/ledger.jsonl` and `.foreman/pipeline.json` do not exist in this repo (checked directly),
  so no verdict rendered in this repo today can satisfy the four go-validity conditions (externally
  computed hash, dispatch-captured identity, ablation rejected in the same run, a ledger row that
  IS the go). That is a pre-existing, machine-wide gap (PDP §8's own text: "the ledger... does not
  exist in any project"), not specific to this stage or this render.

## HOLD re-entry, checked and closed — 2026-09-04

`dev-harness-run2-93` rendered Nadia Osei and added an addendum to `SECURITY-PRIVACY-REVIEW.md`
(commit `c549299`). Checked myself, not taken on the orchestrator's word:

- **Commit is real:** `c549299`, message and diff read directly.
- **All six named artifacts covered:** `guard_allowlist.py` (B7), `guard_semantic_resolution.py`
  and `guard_pattern_feed.py` (B6, jointly — consistent with this document's own stated method,
  "per-interaction against named boundaries, not per-component"), `guard_os_sandbox.py` (B5, B9),
  `deny_capability.sb` (B9), `capability_scope.sh` (B8). Confirmed the six names were genuinely
  absent from the pre-addendum text: `git show c549299~1:SECURITY-PRIVACY-REVIEW.md | grep -c`
  against all six names → 0.
- **Two real findings, F4/F5, filed as their own tickets:** checked directly in TESSERA —
  `DEVH-104` and `DEVH-105` both exist, open, summaries match the addendum's own description.
  Neither marked FATAL.
- **Spot-checked the code claims, not just the prose:** F4's quoted `guard_allowlist.py:91-100`
  matches the file verbatim, `.get("tool_input")` extraction sits ahead of the guarded `try` block
  exactly as described. F5's `BOLLARD_DENY_CAPABILITY_SB_OVERRIDE` grep claim ("two hits, both
  already known") reproduced exactly: `guard_os_sandbox.py` and `test_guard_os_sandbox.py`, nothing
  else.

**Re-entry condition MET, in full, against my own criteria — not a technicality close.** My HOLD
named exactly one condition: STRIDE rows for the six R14a/R15a artifacts. It did not imply
anything beyond that six-file absence, and I have no other unstated condition to surface now that
I'm looking again with the addendum in hand. The ship-readiness charter still being stale
(`SHIP-CHARTER.json` scoped to DEVH-56) and Marcus's item 5 remaining open are real, but they are
not part of Validate's own closing condition per PDP §3 — they were already routed to Marcus/
`agent-remediation-94` separately and stay open on their own track, not reopened here.

## Stage verdict, re-rendered

**Validate stage: go(unbound).** Both required artifacts now exist and both close their own
criteria: this document (done-state MET, no absorption) and `SECURITY-PRIVACY-REVIEW.md`'s
addendum (STRIDE rows for all six artifacts, two findings dispositioned, neither blocking). Not
plain `go`: `.foreman/ledger.jsonl` and `.foreman/pipeline.json` still do not exist in this repo,
so PDP §8's four go-validity conditions cannot be satisfied mechanically — same pre-existing,
machine-wide gap noted in the original render, not specific to this stage.

**R14/R15 Validate closes here.** Downstream consequence, stated rather than left implicit:
`ship_readiness_gate.py` still reads a charter scoped to DEVH-56 and does not know this workstream
passed Validate — that gap is Marcus's/ship-readiness's to close, not reopened by this stage
closing clean.

## Routes back to

- FORE-308: stage-order enforcement and the missing validate gate (mechanism still absent; this
  report and its addendum are a manual instance of the check it would run).
- Marcus Webb / `agent-remediation-94`: item 5 remains genuinely OPEN, and the ship-readiness
  charter still needs to learn this workstream passed — both already flagged, both unaffected by
  this stage's closure.
- F4/F5 (`DEVH-104`, `DEVH-105`): real, non-blocking, routed to whoever owns `bollard` next.
