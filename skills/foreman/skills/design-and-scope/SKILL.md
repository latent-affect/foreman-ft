---
name: design-and-scope
description: Writes one component's detailed design, test criteria, and scope estimate as that component's GOALS.json — the artifact goals_freeze_gate.py checks before implementation can start. Use once ARCHITECTURE.md is reviewed and a specific component is ready for internal design. Do not use for cross-component or interface-level criteria — those belong in .foreman/GOALS.integration.json, written by foreman:integration-test.
---

# foreman:design-and-scope

Detailed design, test strategy, and scope-based estimation collapse into one skill because one
author sitting down to design a component writes its internals, its test criteria, and its
scope count in the same pass — splitting these into three skills would mean three separate
invocations writing to the same `GOALS.json`, which is where drift between "criteria" and
"scope_estimate" would start (`foreman-design.html` §02).

## Trigger when

- `ARCHITECTURE.md` is reviewed and a specific declared component is ready for internal design.
- `foreman` routes here after the architecture stage closes.
- A component's `GOALS.json` needs a deliberate re-freeze — see "Amendments," below.

## Input

`ARCHITECTURE.md` — specifically the target component's responsibility statement and its
declared interfaces (both sides: what it calls, what calls it).

## Output: `<component_root>/GOALS.json`

One `GOALS.json` **per component**, at that component's declared path root (e.g.
`ticketing/store/GOALS.json` for the `store` component whose glob is `ticketing/store/**`) —
**not** one project-wide file. `goals_freeze_gate.py` resolves the file it checks from the
component the touched path belongs to, per `ARCHITECTURE.md`'s component map.

Base schema is `GOALS.json` unmodified (`done_state`, `criteria[]`, `failure_criteria[]`,
`out_of_scope[]`, `constraints[]`, `results[]`, `amendments[]`, `integrity{}`) — see
`GOALS.template.json` for the full annotated schema. This skill's only addition is one block:

```json
"scope_estimate": {
  "components": 1,
  "interfaces": 2,
  "test_criteria": 6,
  "frozen_at": "2026-08-16T00:00:00Z"
}
```

`components` is always 1 here (one component's own GOALS.json); `interfaces` counts this
component's side of its declared interfaces; `test_criteria` is `len(criteria)` at freeze time.
Growth past the frozen `test_criteria` count is read off later by comparing `len(criteria))` to
this number — a mechanical check, not a vibe — and any real growth must show up as an
`amendments[]` entry, per `GOALS.json`'s own convention, not a silent addition.

## Freezing — what `goals_freeze_gate.py` actually checks

1. Write `criteria[]` and the `scope_estimate` block.
2. Set `criteria_frozen_at` to the current timestamp.
3. Compute `integrity.criteria_hash_at_freeze`:
   `"sha256:" + sha256(json.dumps(criteria, sort_keys=True, separators=(",", ":"))).hexdigest()`
   — exact algorithm, matching `goals_freeze_gate.py`'s `criteria_hash()`. A hash computed any
   other way (different separators, unsorted keys, a different serialization) will not match
   and the gate will deny with a false "tampered" reading. Recompute with the exact function
   above, don't hand-roll an equivalent.
4. Write both fields in the same edit that finalizes `criteria[]` — a `GOALS.json` with a stale
   hash from an earlier draft is indistinguishable, to the gate, from a genuinely tampered file.

## Falsification: a separate, genuinely adversarial pass (added 2026-08-23)

Same discipline `foreman:architecture` already requires for `ARCHITECTURE.md`, extended here
because the evidence is the same shape: implementer-authored test criteria default to confirming
what the author already believes the component does, not falsifying it — a documented cognitive
bias in the testing literature (confirmation bias), not a vague concern; see
`TEST-DESIGN-PLACEMENT-DOMAIN-BRIEFING.md`. Two real incidents in this project's own history show
what happens without this step: one real project's confirmed vacuous-test instance
(`VALIDATION-REPORT.md:112`) was caught by an independent review pass, not by the project's own
pre-existing tests, which had asserted the buggy values as correct; another project's own
`INTEGRATION-TEST-REPORT.md` self-discloses "written and run by the same context that wrote the
code" as a real, named limitation, not a hypothetical one.

Before freezing, a context that did **not** author `criteria[]` reviews it adversarially: for each
criterion, could the described verification string still show PASS against a plausible BROKEN
implementation? A criterion whose verification would pass either way is padding, not a criterion —
flag it and rewrite before freeze, the same way `architecture_gate.py`'s falsification pass exists
to catch a design that only looks reviewed.

**Not yet mechanically enforced.** No hook checks for a falsification record the way
`architecture_gate.py` checks for `ARCHITECTURE-REVIEW.md` — say so plainly if this step is
skipped, rather than silently treating frozen criteria as reviewed just because they're frozen.

## Cross-component criteria don't live here

An interface-level test criterion (does `threading→store` actually work end to end) belongs in
`.foreman/GOALS.integration.json` at the project root, owned by `foreman:integration-test`, not
duplicated into either component's own `GOALS.json`.

## Amendments — a criteria change after freeze

`goals_freeze_gate.py` denies on ANY hash mismatch, including a legitimate correction. To
change frozen criteria: append an `amendments[]` entry naming what changed and why, update
`criteria[]`, recompute and rewrite `criteria_hash_at_freeze` with the new hash, and update
`criteria_frozen_at`. This is a deliberate re-freeze, not a workaround — the gate is supposed to
force exactly this ceremony rather than let criteria drift silently under implementation
pressure.

## Gate criteria to next stage

`criteria_frozen_at` set, `integrity.criteria_hash_at_freeze` present and matching a fresh hash
of `criteria[]` — `goals_freeze_gate.py` enforces this mechanically for every write inside the
component's declared path, not just as an end-of-stage check.

## Guardrails

- **One `GOALS.json` per component, not one per project.** Freezing the whole project's design
  before any component can start implementation is exactly the big-design-upfront rigidity the
  hybrid V-Model rejects.
- **Recompute the hash with the documented algorithm, exactly.** A hand-rolled equivalent that
  differs in key ordering or separators produces a hash that will never match.
- **Don't pad `criteria[]` to look thorough.** `scope_estimate.test_criteria` is meant to be a
  real ceiling later growth gets measured against — inflating it at freeze time defeats the
  mechanism the estimation exists for.

## Pairing

```
foreman:architecture → foreman:design-and-scope → /goal (implementation, consumes frozen criteria)
                                                  → foreman:integration-test (reads GOALS.integration.json)
```
