---
name: integration-test
description: Executes every interface declared in ARCHITECTURE.md end to end and records pass/fail evidence in INTEGRATION-TEST-REPORT.md — distinct from per-component review, which only reasons about one diff at a time. Use once at least two components exist and their declared interfaces haven't been exercised together yet. Do not use as a substitute for adversarial-code-review's Check 6 (cross-unit contract integrity) — this skill calls that check first, then adds the executed-evidence half it can't do alone.
---

# foreman:integration-test

Nothing else in this framework tests interfaces between components specifically — only
components in isolation (`foreman-design.html` §01 item 2). `adversarial-code-review`'s
Check 6 (cross-unit contract integrity) reasons about a *diff*: producer/consumer pairing,
promises in comments vs. behavior — a static read, unaware of what interfaces the architecture
actually declared. It cannot answer "did we exercise **every** interface named in
`ARCHITECTURE.md`," only "does this specific diff look internally consistent."

## Trigger when

Two different triggers for two different activities this skill owns — don't conflate them (this
split is a 2026-08-23 correction; criteria authorship used to share execution's trigger, which
deferred writing down what "this interface works" means until as late as EXECUTION — reintroducing
the exact "late test design" pattern shift-left testing exists to correct; see
`TEST-DESIGN-PLACEMENT-DOMAIN-BRIEFING.md` for the field grounding):

**Criteria authorship** (`.foreman/GOALS.integration.json`'s `criteria[]`) — as soon as
`ARCHITECTURE.md` declares ≥2 components with at least one interface between them. The interface's
producer/consumer shape is fully known at that point; there's no reason to wait for real code to
exist before writing down what execution must show. Author this alongside
`foreman:design-and-scope`, not after implementation.

**Execution** (the Method below, `INTEGRATION-TEST-REPORT.md`) — unchanged: both sides of the
interface need real (not stub) code first. You cannot execute an interface that doesn't exist yet;
only the criteria describing what execution must show can be written early.

- A `foreman` router hand-off names this as the next stage, for either authorship or execution per
  the above.
- A prior `INTEGRATION-TEST-REPORT.md` is stale relative to either side of an interface (execution
  trigger only).

## Input

`ARCHITECTURE.md`'s interface block (`producer`/`consumer` pairs) plus the real code on both
sides.

## Method

1. **Run `adversarial-code-review` Check 6 first**, scoped to the files on both sides of each
   declared interface. This is the static-reasoning half — producer/consumer pairing, promises
   in comments vs. actual behavior, interface agreement across the call boundary. Don't
   re-derive this reasoning by hand; call the existing check.

   **This call does not suspend `adversarial-code-review`'s own rules — they bind here exactly
   as they do on a standalone run.** (An earlier version of this instruction
   was ambiguous about which of the two skills' rules governed a call from one into the other,
   and a real invocation resolved the ambiguity by silently dropping the callee's independence
   and no-rewrite guardrails — one check run inline instead of six, no subagent, no second
   opinion on the FATAL it found, and the finding patched directly rather than diagnosed. The
   operator's tiebreak, 2026-08-20: where a caller's method and a callee's own guardrails conflict, the
   callee's rules win.) Concretely: run Check 6 in an independent subagent, not inline in this
   session's own context — the same reason a fresh subagent catches what the implementing
   session structurally can't. If Check 6 returns a FATAL / DO-NOT-SHIP finding, re-confirm it
   with a second, independent subagent before trusting it, same as a standalone six-check run
   requires. **Do not patch the finding directly from this call** —
   `adversarial-code-review` diagnoses, it doesn't silently rewrite, and that guardrail travels
   with the call. A FATAL found here routes back to whichever component owns the broken side,
   the same as an execution FAIL two steps below — not to an inline edit made by this skill's
   own session. State how many independent passes actually ran, same disclosure a standalone
   run already requires: "we called Check 6" is not the same claim as "an independent subagent
   ran it and its FATAL got a second opinion."
2. **For each interface, actually execute both sides together and record evidence** — a real
   call from the consumer into the producer (or the reverse, per the declared direction), the
   real output observed, not a mock standing in for either side. This is the half Check 6
   structurally can't do, because it never runs anything.
3. **One row per declared interface, PASS/FAIL, with the evidence** — the exact command run and
   what its output showed, matching `GOALS.json`'s own verification-string convention ("the
   exact command, and what its output must show," not "tests pass").

## Output: `INTEGRATION-TEST-REPORT.md`

```
## Integration test — <project>, <date>

| Interface | Check 6 (static) | Executed | Evidence |
|---|---|---|---|
| threading_to_store | PASS | PASS | `$ pytest test_integration.py::test_thread_persists_to_store` exits 0 |
| workflow_to_store | PASS | FAIL | transition() writes status but not status_changed_at; store schema expects both |
```

## Gate criteria to next stage

Zero FAIL rows outstanding across every interface `ARCHITECTURE.md` declares. A FAIL routes back
to whichever component owns the broken side, not forward to verification.

## Guardrails

- **Every declared interface gets a row, not just the ones a recent diff touched.** This is the
  systematic pass Check 6 alone doesn't provide — skipping interfaces because "nothing changed
  there recently" defeats the point.
- **"Executed" means a real call ran, not that a mock returned a plausible value.** A test that
  mocks the producer side to verify the consumer's handling is a component-level test, not an
  integration test, and doesn't earn a PASS in the Executed column.
- **Don't duplicate Check 6's reasoning by hand.** Call it; add only the executed-evidence half.

## Pairing

```
foreman:design-and-scope → /goal (implementation) → foreman:integration-test
                                                    → adversarial-code-review (full six-check, verification)
                                                    → foreman:validate
```
