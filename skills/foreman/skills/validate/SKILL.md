---
name: validate
description: Checks the finished system against SCOPE.md's original done-state and GOALS.json's done_state — "did we build the RIGHT THING," distinct from verification's "did we build it right." Use once verification (adversarial-code-review + the audit-plane judge) has passed and before calling a Foreman project done. Also writes the freeze marker and closes out the lightweight deployment checklist for local/headless systems.
---

# foreman:validate

"Did we build it right" is covered by existing tooling (`adversarial-code-review`, the
audit-plane judge pattern). Nothing checks the finished product against the original
concept-stage problem statement — this skill is that check, reading `SCOPE.md`'s done-state and
`GOALS.json`'s `done_state` back after the system is built, rather than writing them before work
starts. Deployment is folded in as this skill's closing section rather than a separate skill,
since deployment for a local/headless system is thin enough that a dedicated skill would be
un-triggerable on its own merits (`foreman-design.html` §02 — a named judgment call, not a
silent default).

## Trigger when

- Verification has passed (SHIP or SHIP WITH FIXES, zero FATAL findings open) for every
  component.
- `foreman:integration-test`'s report has zero FAIL rows outstanding.
- A `foreman` router hand-off names this as the final stage before freeze.

## Input

`SCOPE.md`'s done-state and out-of-scope sections; each component's `GOALS.json.done_state`.

## Method

1. **Check the done-state, literally.** `SCOPE.md`'s done-state is meant to be "one sentence a
   third party could check" — check it against the actual running system, not against a
   description of the work done.
2. **Check for unflagged out-of-scope absorption.** `SCOPE.md`'s non-goals are a first-class
   output; did the build quietly grow to cover something explicitly excluded? Name it if so —
   scope creep during the work is indistinguishable from thoroughness unless it's checked
   against the recorded non-goals.
3. **Check for the inverse: something a reasonable reader would assume in-scope, missing.** The
   done-state can be technically true while a load-bearing expectation from `SCOPE.md`'s in/out
   section was silently dropped.

## Output: `VALIDATION-REPORT.md`

```
## Validation — <project>, <date>

**Done-state:** "<verbatim from SCOPE.md>"
**Checked against running system:** MET / NOT MET / PARTIALLY MET, with what specifically was checked
**Out-of-scope items:** <named, and whether any got silently absorbed>
**Assumed-in-scope items:** <named, and whether any got silently dropped>
```

## Deployment (this skill's closing section, not a separate stage)

For a local/headless system: confirm the write path and any read-back trigger actually run
end to end at least once (not just unit-tested in isolation), then write the freeze marker:

```
.foreman/frozen.json — {"edges": [...], "graph_hash": "sha256:...", "frozen_at": "..."}
```

`foreman-design.html` §05 describes the fuller freeze/re-qualification gate this marker feeds;
that gate isn't wired live in this build (see the `foreman` router skill's guardrails), so
writing `frozen.json` here is a record of intent, not yet an enforced re-entry boundary.

## Gate criteria (this is the end of the pipeline)

Done-state independently checkable and MET; no unflagged out-of-scope absorption; deployment
checklist closed. A NOT MET or PARTIALLY MET result routes back to whichever stage the gap
traces to — architecture if a component is structurally missing, design-and-scope if a criterion
was never written, implementation if a criterion exists but wasn't met.

## Guardrails

- **Read the target back; don't re-derive it.** `SCOPE.md` and `GOALS.json.done_state` already
  say what "done" means — this skill's job is comparison, not re-litigating what done-state
  should have been.
- **A PARTIALLY MET result is a real finding, not a rounding-up to MET.** Name exactly what's
  missing.

## Pairing

```
adversarial-code-review + audit-plane judge (verification) → foreman:validate → done
```
