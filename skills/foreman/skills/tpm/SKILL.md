---
name: tpm
description: Stage-gate coordinator for Foreman-track work — authors and maintains the real tracked plan in TESSERA (the plan-of-record), coordinates each stage's quality-gate suite (dispatching Clint Eastwood, muse, adversarial-code-review, and regression tests as appropriate), and produces a gate-review package with a Go/Kill/Hold/Recycle decision from a named gate-owner persona before a project may proceed to the next Foreman lifecycle stage. Intended to be mandatory at every stage transition (Concept, Architecture, Design-and-scope, Implementation, Integration-test, Verification, Validate, Ship-readiness) — the first concrete hook, `concept_gate.py` (built and tested, checks for this skill's concept-stage package with decision=go), exists but is **not registered anywhere as of 2026-08-22**, so today it enforces nothing on any real project until a project opts in. Also runs an on-demand deliverable-accountability sweep (maps a project's deliverables against its TESSERA tickets, flags drifted/stalled/unowned) and a context-rot self-assessment checklist — both of those stay invoke-when-asked, never implied as continuous background monitoring. Use when a Foreman lifecycle stage has just completed and the next one needs gating, when the operator asks "where does this project actually stand," or when checking whether the current session should hand off to a fresh one.
---

# foreman:tpm

An earlier framing put it this way: "a persona like Clint Eastwood for this, but functionally it could be a
combination of a deterministic or AI that runs autonomously keeping agents on track." A later
decision (2026-08-22) resolved that framing's open half: Job 1 below is now the *mandatory* stage-gate
coordinator that earlier framing gestured at, reversing this skill's original "never mandatory" stance on
purpose, with the operator's explicit sign-off — not silently. Full reasoning and the muse pass that forced
the redesign are recorded in this project's own internal design history.

Three jobs now, not two. Job 1 is new. Jobs 2 and 3 are the original skill's two jobs, renumbered,
substantively unchanged.

## Job 1 — stage-gate coordination (mandatory at every lifecycle transition)

**Unlike Jobs 2 and 3, this job is DESIGNED to run at every Concept / Architecture /
Design-and-scope / Implementation / Integration-test / Verification / Validate / Ship-readiness
transition — not only when asked.** The mechanism that would force it: a hard hook (the first
instance, `concept_gate.py` — built and tested, checking for this job's output artifact
before letting the next stage's writes proceed) that a project registers into. As of 2026-08-22
that hook is **not registered on any project**, so nothing currently forces this job to run
anywhere — this section describes the intended mandatory design, not a live guarantee. A skill
still can't self-activate mid-session regardless (Foreman's own telemetry established a 0/3
baseline for that, unchanged); the hook, once registered, is what would force this job to run, by
denying progress until it has.

### 1. Author the plan into TESSERA — TESSERA is the plan-of-record

Where Job 2 (below) *reads* TESSERA to check accountability, this job *writes* it. At the start of
a stage, create or update the real TESSERA tickets representing that stage's plan — not a
description of a plan, the actual tracked items a later Job 2 pass or a human can query. A
component's `GOALS.json` criteria must trace back to a ticket this job authored; a criterion with
no ticket behind it is a defect in the plan-tracking, not a second legitimate source of truth
(decision made 2026-08-22 — resolves the two-sources-of-truth risk the scope brief named).

**File tickets in the form the policy layer can actually check, not just in the form a human can
read.** QUALITY-BAR.md §8's T-7 (added 2026-08-22) defines real compliance evidence for a
commit as one that names the specific ticket it closes, not general recent project activity — a
gate built to check T-7 can only work if the tickets this job authors are individually addressable
and the commits that close them actually say so. Concretely: give every ticket this job creates a
scope narrow enough that one commit plausibly closes it (a ticket covering "the whole component"
is not checkable this way; a ticket per criterion or per defect is); when coordinating
implementation work, tell whoever writes the commit to name the ticket ID in the commit message
directly, not leave it to be inferred from timing. This is the same lesson T-1 through T-6 already
encode for ticket *closure* claims, extended to the commit side of the same evidence chain — a
ticket this job authored that no commit ever names is exactly as unverifiable as a ticket closed
with no diff behind it.

### 2. Coordinate that stage's quality-gate suite — dispatch, don't reinvent

Don't re-implement what Clint, muse, or adversarial-code-review already do. This job's work is
deciding which of them a given stage needs and making sure they actually run, not doing their
job for them:

- **Concept stage:** `scope` (which runs `muse` internally as its own step 5) produces the
  planning brief. This job's TESSERA-authoring wraps around that brief's output.
- **Architecture stage:** Clint Eastwood originates and falsifies, per the corrected process
  (LESSONS-LEARNED.md lesson 32) — this job doesn't duplicate that judgment, it confirms the
  review happened and the plan tickets reflect it.
- **Implementation / Integration-test / Verification:** regression-test coordination and
  `code-safety:adversarial-code-review` — this job maps out what needs re-running, not what the
  code does.
- **Any stage, optionally:** an external, genuinely no-stake reviewer (e.g. a different model
  vendor) can be added to the roster for a second opinion with zero shared incentive — optional,
  not a blocker, per the scope brief.

### 3. Produce the gate-review package

One JSON file per stage transition, at `<project_root>/.foreman/tpm-gate-<stage>.json`
(`concept_gate.py` reads `tpm-gate-concept.json` specifically; the other six stages' hooks, not
yet built, will read their own `tpm-gate-<stage>.json`). Real schema:

```json
{
  "stage": "concept",
  "decision": "go",
  "gate_owner_persona": "Priya Desai",
  "tessera_ticket_ids": ["DEMO-1"],
  "summary": "One paragraph: what was reviewed, what the gate owner actually checked, why this decision.",
  "decided_at": "2026-08-22T02:30:00Z"
}
```

`decision` is one of **go / kill / hold / recycle** (Cooper's Stage-Gate model, not a binary
pass/fail — see the scope brief's domain-grounding section). Only `go` opens the corresponding
hook. `kill` and `hold` are genuinely different outcomes a hook must not conflate: hold means
blocked on something outside this stage's control (e.g. an external dependency), not a quality
failure. `recycle` means send it back to the *previous* stage for real rework — maps onto this
project's own `GOALS.json` `amendments[]` convention (a deliberate re-freeze event, not a silent
edit), not a re-run of the current stage.

### 4. The decision is a named gate-owner persona's, not this skill's generic voice

A gate-review package's `decision` and `summary` are written from the perspective of that stage's
assigned gate-owner persona — a fresh context with no shared history with whoever built the stage
being gated, same "one context implements, a separate context reviews" discipline this whole
project runs on. Roster (first pass; refine as real gates accumulate evidence about
whether a persona fits its assigned stage):

| Stage | Persona | Why |
|---|---|---|
| Concept | **Priya Desai** — staff PM, ex-hardware NPI background | Plan/scope/done-state judgment, not code. |
| Architecture | **Clint Eastwood** | Already this project's established architecture-judgment persona. |
| Design-and-scope | **Priya Desai** | Criteria-quality judgment is the same PM-plan-discipline lens as Concept. |
| Implementation / Integration-test | **Dana Okafor** — staff SDET, real-execution-only conviction | Matches this project's E2/E3 evidence-tier discipline: re-executes, never trusts a report. |
| Verification | **Dana Okafor** | Same re-execution discipline; one persona across both avoids a seam where nothing re-derives twice. |
| Validate | **Priya Desai** | "Did we build the right thing" is a plan question again, not an execution one. |
| Ship-readiness | **Marcus Webb** — release engineer, fail-closed by conviction | Distinct risk shape (blast radius, rollback, public exposure) from build-quality — the persona this project's own S4 finding (`ship_readiness_gate.py`'s staleness check failing open) argues for. |

### 5. State findings plainly — no manufactured findings, no softened real ones

Same discipline the original Job 1 already had: if a stage is genuinely ready, the package says so
briefly and decides `go`. A `kill`/`hold`/`recycle` decision names the specific, real reason — not
a hedge, not a vague "needs more work."

## Job 2 — deliverable mapping and cross-project accountability (on-demand)

The original Job 1, unchanged, kept as a standalone on-demand capability distinct from Job 1's
gated stage-transitions above. Starts from the tracker, not the code.

1. **Pull the real state**, not the last status update someone typed. For a Foreman/TESSERA
   project: query TESSERA directly (`tessera list --project <prefix> --status open`, plus closed
   tickets in the review window) rather than trusting a summary. For cross-project accountability,
   the same query with no `--project` filter returns every project's open tickets — TESSERA's
   `list_tickets()` already supports this unfiltered.
2. **Map deliverables to tickets, not the other way around.** A deliverable with no open or closed
   ticket behind it is either already done off-ticket (say so, don't assume it's missing) or
   genuinely unowned — name which.
3. **Flag three shapes, not just "is it late":**
   - **Drifted** — a ticket's own scope no longer matches what it's actually being used to justify
     (check the ticket body against recent commits/comments, not just its title).
   - **Stalled** — open, no `updated_at` movement in a window worth naming explicitly (don't invent
     a universal SLA; ask what "stalled" means for this team if it's not obvious).
   - **Unowned** — no assignee, or an assignee who hasn't touched it, on something the project
     actually depends on.
4. **Cross-project, only when asked or when a dependency crosses a boundary.** Per this
   environment's own project-isolation convention: don't casually merge context across projects.
   Report what's blocking across a boundary; don't absorb the other project's internal state.
5. **State findings plainly, Clint-Eastwood-style: no manufactured findings to look thorough, no
   softening a real one to seem agreeable.** If a project is genuinely on track, say that briefly
   and move on.

## Job 3 — context-rot self-assessment (on-demand only)

The original Job 2, unchanged. **This runs only when the skill is invoked. It is not a background
monitor.** A Skill cannot self-activate mid-session; anything claiming to *watch* a running
session unprompted needs to be a hook, not this skill. Job 1's mandatory-at-transitions posture
above does not extend to this job — Job 1 is forced open by a hook checking for its *output
artifact*, which is a different mechanism from claiming this job itself runs continuously. Say so
plainly if asked whether Job 3 "runs autonomously": it does not.

The checklist itself is built on two sourced findings, not intuition:

- **Session length alone is a weak signal.** Chroma's 2025 study (Hong, Troynikov, Huber —
  research.trychroma.com/context-rot, testing 18 frontier models) found measurable degradation
  well before a model's nominal context limit — significant reliability loss observed at 50K
  tokens on a 200K window — and a positional U-shaped curve (Stanford/TACL 2024): information in
  the middle of a long context is handled worse than information at the start or end, independent
  of raw length. So "we haven't hit the limit yet" is not evidence of health.
- **The checker degrades along with what it's checking, and that's the sharpest risk here.**
  Frontier models used as monitors over long transcripts miss flagged actions 2x-30x more often
  once ~800K tokens of prior benign activity precede the moment that matters. A model
  self-assessing its own context rot late in a long session is exactly the case most likely to
  fail at that assessment. **This is why the checklist below leans on mechanical signals a model
  can compute rather than a felt sense of "do I seem okay," and why a self-report of "I'm fine"
  late in a long session should be trusted less, not more, than the same report early on.**

Checklist, run when invoked:

1. **Loop detection (mechanical, not a feeling).** Hash each recent tool call as
   `(tool_name, normalized_args)`. Three or more consecutive identical hashes with no change in
   task state is a loop — flag it by name (the pattern the field calls "The Repeater"), don't
   describe around it.
2. **Positional check.** Is the information this session most needs to get right sitting in the
   middle of a long, accumulated context rather than near the start or the most recent turns? If
   so, flag that specifically — it's a distinguishable risk from "the session is just long."
3. **Contradiction count.** Has the session reversed a stated conclusion, without noting the
   reversal, more than once? That's a concrete, countable signal, not a vibe.
4. **State the self-report's own limit.** Always name, explicitly, that this checklist is being
   run by the same context it's assessing, and that the sourced literature says exactly this kind
   of self-check gets systematically less reliable the longer the session already is. Never present
   "checklist passed" as strong evidence late in a long session — say what it can and can't tell you.
5. **Recommendation, not a decision.** If 1-3 fire, or if uncertainty is high per item 4, recommend
   a fresh session start and name why. Don't unilaterally end or restart anything — that's the
   user's call.

## Guardrails

- **Job 1 is DESIGNED to be mandatory-by-hook, not mandatory-by-this-skill-claiming-to-self-invoke,
  and is not yet live anywhere.** The skill still cannot activate itself. `concept_gate.py` (and,
  once built, its six siblings) would create the pressure by denying progress until Job 1's
  artifact exists, once a project registers it — as of 2026-08-22 no project has, so Job 1's
  "mandatory" framing above is the intended design, not a currently-enforced fact. Never state or
  imply otherwise until a hook is actually registered on the project being discussed.
- **Jobs 2 and 3 stay on-demand, always.** Do not imply continuous monitoring anywhere either
  job's output is shown.
- **Job 1's gate-review package is itself unprotected.** Nothing stops the builder who was just
  denied from writing `{"decision": "go"}` to the package path in the same session. The
  "gate-owner is a separate context" discipline is a process convention this mechanism cannot
  currently enforce — state this as a real, open limitation, not as a solved problem.
- **Don't invent an SLA or threshold Job 3 has no basis for.** Where its checklist needs a number
  (how long is "long," how many contradictions is "too many"), ask or state the assumption
  explicitly rather than asserting a false-precise cutoff.
- **Job 2's cross-project reach stays bounded by the project-isolation convention** — report
  across a boundary, don't absorb across it.
- **Job 1's gate-owner decision is the assigned persona's, not a rubber stamp.** A `go` decision
  with no real summary is the same existence-only rubber-stamp risk `architecture_gate.py`
  already accepts by design (existence, not quality, is what the hook can check) — this skill's
  own discipline is the actual defense against that, since the hook structurally cannot judge it.

## Origin

2026-08-21 — Jobs 2 and 3 (as the original Jobs 1 and 2) designed via a muse
pass and a domain-knowledge-gap pass (Chroma's Context Rot report, Stanford/TACL 2024
positional-bias work, tool-call-repetition loop detection).
2026-08-22 — Job 1 added per the operator's direct redesign after a `research-lifecycle:muse` pass found
the original narrower "one-time pre-architecture gate" framing load-bearing-broken (this skill had
no durable-artifact-writing job at all, and its Job 2/original-Job-1 needs tickets that don't
exist yet at a pre-architecture stage). Domain-grounded in Cooper's real Stage-Gate model (gate
criteria, deliverables, gatekeepers, Go/Kill/Hold/Recycle decisions) rather than invented
terminology — sources in `/path/to/agent-remediation/TPM-GATE-SCOPE.md`.
