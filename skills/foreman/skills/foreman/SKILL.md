---
name: foreman
description: Routes software-build work through hard-gated development stages — architecture, detailed design, implementation, integration test, verification, validation — and triages each change into Tier 1/2/3 by actual code coupling, not by the architecture doc's prose. Use when starting a new component or project, asking what stage a change belongs in, whether a diff needs an architecture update before it can land, or whether a change is safe to ship without re-qualification. Not for research papers, manuscripts, or long-form arguments — see conductor for that.
---

# Foreman

The lifecycle router for a hybrid V-Model software build. Foreman does not do stage work
itself — it detects which stage a project or change is in and routes to the skill, hook, or
existing tool that owns that stage. Full design: `foreman-design.html` (kept alongside the
project this plugin was built for; ask the operator for its current location if you need the
worked coupling-engine proofs or the tier-triage detail — this SKILL.md carries the routing
table and gate contracts, not the full derivation).

## Non-overlap with conductor

`research-lifecycle:conductor` routes prose that gets published or submitted — papers, essays,
manuscripts, reviews — through `muse`, `knowledge-gap-hero`, `toy-models`, `experiment-ledger`,
`peer-review`, `spine`. Nothing in this table names a manuscript, a claim, or a submission;
nothing in conductor's table names a component, an interface, or a diff. The one shared
hand-off is `scope` itself, at the front end of both — correct reuse, not overlap.

## The routing table

| Signal | Route to |
|---|---|
| "I want to build X" / rough idea, no scope yet | `scope` |
| Scope brief (`SCOPE.md`) exists, ready to design the system | `foreman:architecture` → Clint Eastwood review |
| `ARCHITECTURE.md` exists and is reviewed | `foreman:design-and-scope` |
| A component's `GOALS.json` criteria are frozen | `/goal` — enforced by `goals_freeze_gate.py`, not just a router signal |
| ≥2 components exist, interfaces untested | `foreman:integration-test` |
| A diff is ready for merge/handoff | `adversarial-code-review` (six checks) + an audit-plane judge subagent |
| A build has passed verification | `foreman:validate` |
| Any edit, any time (not routed — always live) | `architecture_gate.py` + `goals_freeze_gate.py` (hard hooks, project-local `.claude/settings.json`) |

## How to run it

1. **Detect phase from artifacts on disk, not from what the user says they're doing.** Check
   for `SCOPE.md`, `ARCHITECTURE.md` + `ARCHITECTURE-REVIEW.md`, and each touched component's
   `GOALS.json` (`criteria_frozen_at` set, `integrity.criteria_hash_at_freeze` present). The
   artifacts are the source of truth; a claim of "the architecture's done" with no
   `ARCHITECTURE.md` on disk is not a completed stage.
2. **Route to exactly one thing**, name which and why, then get out of the way. Foreman
   sequences; the sibling skills and reused tools do the work.
3. **Don't re-route a stage that already has a fresh, unchanged artifact.** If `ARCHITECTURE.md`
   hasn't changed since the last review, don't re-invoke `foreman:architecture` — say so and
   move to the next stage.
4. **The two hard hooks are not optional and are not this skill's job to enforce.** If a Write
   or Edit gets denied by `architecture_gate.py` or `goals_freeze_gate.py`, route to whichever
   stage the denial names — don't attempt to work around the hook.
5. **A project with no `.foreman/` marker directory has not adopted Foreman.** Don't apply this
   routing table to a project that hasn't opted in; suggest `foreman:architecture` as the first
   step, which will create the marker as part of its own output contract.

## Guardrails

- **Route, don't do.** Foreman selects and sequences; the stage skills and reused tools do the
  actual work.
- **Reuse over rebuild, always.** `scope` for concept/requirements, Clint Eastwood for
  architecture review, `adversarial-code-review` + an audit-plane judge for verification,
  `/goal` for implementation loops. None of these get reimplemented under a Foreman name.
- **Tier triage and the freeze/re-qualification gate are designed but not yet wired as live
  hooks in this build** (`foreman-design.html` v0.2 §04/§05, proven via `coupling_ext_proof.py`
  but out of scope for the v0.3 pass). Don't claim they're enforced until they are.

## Pairing

```
scope (front-end project router)
  → foreman:architecture      (component/interface design, Clint Eastwood review)
  → foreman:design-and-scope  (per-component GOALS.json — design, test criteria, scope estimate)
  → /goal                      (implementation loops, one per component)
  → foreman:integration-test  (executes every declared interface, not just reasons about a diff)
  → adversarial-code-review + audit-plane judge  (verification)
  → foreman:validate           (built the right thing? — checks against SCOPE.md's done-state)
```
