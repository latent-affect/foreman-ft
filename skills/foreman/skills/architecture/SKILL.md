---
name: architecture
description: Writes ARCHITECTURE.md — the component map, interfaces, and machine-parseable component→path block a Foreman project's hard hooks read. Use once SCOPE.md exists and a project is ready to name its components and interfaces, before any implementation file gets written. Do not use for detailed internal design of a single component — that's foreman:design-and-scope.
---

# foreman:architecture

Produces the one artifact `architecture_gate.py` checks for: `ARCHITECTURE.md`, containing a
component map, the interfaces between components, and a machine-parseable declaration block the
hooks parse directly. This is the stage the hybrid V-Model's one truly hard boundary sits
behind — nothing nested below the project root can be written until this exists and is reviewed.

## Trigger when

- `SCOPE.md` exists (produced by `scope`) and the project is ready to commit to a component
  structure.
- A `foreman` router hand-off names this as the next stage.
- An existing `ARCHITECTURE.md` needs revision because integration testing or the freeze gate
  (once wired) surfaced a coupling the document didn't account for.

## Input

`SCOPE.md` — specifically its done-state, in/out, and constraints sections.

## Output: `ARCHITECTURE.md`

Two parts, both required:

1. **Prose.** Component list with a one-paragraph responsibility statement each; the interfaces
   between components (which component calls which, what crosses the boundary, direction);
   anything explicitly deferred (a component named but not yet built).
2. **The component declaration block**, fenced exactly like this — `hooks/component_coupling.py`
   parses this block by regex + `json.loads`, not a YAML library, so the format is load-bearing:

   ```
   \`\`\`yaml components
   store:        ["ticketing/store/**"]
   threading:     ["ticketing/threads/**"]
   workflow:      ["ticketing/workflow/**"]
   notification:  ["ticketing/notify/**"]
   \`\`\`
   ```

   Each key is a component name; each value is a JSON array of path-prefix globs (trailing `**`
   or `*` stripped by the parser — these are prefix matches, not full glob semantics; see
   `component_coupling.py`'s own docstring if a component ever needs something more precise
   than "everything under this directory").

3. **Interface list**, for `foreman:integration-test` and any future tier-triage wiring — one
   entry per declared interface with a stable id:

   ```
   \`\`\`yaml interfaces
   threading_to_store:      {"producer": "threading", "consumer": "store"}
   workflow_to_store:       {"producer": "workflow",  "consumer": "store"}
   workflow_to_notification: {"producer": "workflow", "consumer": "notification"}
   \`\`\`

   Keys AND values quoted -- this is JSON, parsed by `json.loads()`, not a YAML/Python dict
   literal despite the `yaml` fence label (same reason the `components` block above is JSON on
   the right-hand side of a YAML-labeled fence: reusing `json.loads()` rather than adding a
   real YAML dependency). An earlier version of this example used bare unquoted keys
   (`{producer: "threading", ...}`) -- not valid JSON, so a project that copied it verbatim
   produced a block `component_coupling.py`'s parser silently rejected and skipped, un-declaring
   the interface with no error surfaced anywhere. Found live during an early
   design-and-scope, cross-checked against the one real interfaces block already in production
   (`/path/to/ticket-system/ARCHITECTURE.md`), which already used the correct quoted-key
   form and already carried this exact warning.

   `producer` is the caller/data source, `consumer` is the callee/data sink — matching the
   contract terminology in `foreman-design.html` §04 (the producer's payload shape is what the
   consumer depends on). Get the direction right: "notification subscribes to workflow's
   transition events" means workflow produces the event and calls into notification, not the
   reverse — a v0.2 draft of this project got this backwards on the first pass, corrected here.
   ```

## Authorship: Clint Eastwood is the architect, not this skill

**Do not draft `ARCHITECTURE.md` yourself and hand it to Clint to grade.** Clint Eastwood
originates the design — dispatch the `clint-eastwood` agent to produce the real component map,
interfaces, and reconciliation directly from `SCOPE.md` and whatever domain material the project
has (schema docs, prior architecture decisions, ticket history). Clint runs whatever toy-model
prototypes or empirical checks the design needs as part of building it, not as review evidence
bolted on after the fact — validating a decision while making it, not after. A toy model that only
confirms what he already expected is not yet evidence; his own agent definition
(`/path/to/home/.claude/agents/clint-eastwood.md`, "The Engagement") requires him to attempt to break it before
it goes in the verdict as measured rather than asserted — this skill relies on that discipline
rather than restating it.

**Clint has no Skill tool (Read/Grep/Glob/Bash only), so he can never invoke
`research-lifecycle:toy-models` himself — the dispatch prompt must do it for him.** When
dispatching Clint, explicitly instruct him to follow `research-lifecycle:toy-models`'s discipline
for any empirical check the design leans on: name the single load-bearing fact, build the smallest
script that could kill it, add a positive control and a negative/shuffle control, tag the real
output `[RAN]`. This is not optional framing. The skill-methodology compliance extractor only
records a delegated skill event when the dispatch prompt names the skill right after a cue verb
(call/run/use/invoke/follow/delegate to/dispatch to) — an unnamed toy model is real work that
stays permanently invisible to the audit trail, indistinguishable from never having run it at all.
Found live on hyphy's v2 build: Clint ran genuinely rigorous ad hoc checks (a PTY pane-close
simulation, WCAG contrast-ratio math, a `go:generate` build-security probe) that the dashboard's
"Autonomous skill activations" panel shows zero trace of, for exactly this reason — the dispatch
prompts never named `research-lifecycle:toy-models`, so there was nothing for the extractor to
match against. Say "follow research-lifecycle:toy-models' discipline" (or equivalent phrasing with
one of the cue verbs above) in the dispatch prompt every time.

**The invariant that matters is authorship, not which tool touches the file.** Clint's own agent
definition grants Read/Grep/Glob/Bash — Bash is load-bearing for his real measurement work (running
real builds, real tests, real greps against the codebase), and Bash can trivially write a file
regardless of whether a literal `Write` tool is also granted. An earlier version of this section
said "no Write; route the actual file write back through this session," which was never actually
true (Bash always could write) and isn't the property worth policing — corrected 2026-08-22 after
a real skill-methodology audit on an earlier build found Clint mutating
`ARCHITECTURE.md` directly via Bash-executed Python, verified every substantive line still traced
to his own composition, and confirmed that's a fine outcome: the lesson-32 failure mode is the
*session* drafting and Clint reviewing, not Clint using Bash instead of Write to hold his own pen.
If a future falsification pass ever finds content in `ARCHITECTURE.md` that Clint's own transcript
doesn't account for, *that's* the real violation to flag — not the write mechanism.

Where a second collaborating perspective is available (e.g. `muse`), it works *alongside* Clint
during this same authorship pass, not as a later reviewer of his draft — its job at that point is
surfacing framing and assumptions a pure engineering lens tends to walk past, while that can still
shape the design rather than critique it afterward. One combined design output, not a draft plus
a separate opinion to reconcile.

Corrected 2026-08-21 after this exact skill produced the backwards order in practice: the
implementing session drafted `ARCHITECTURE.md` itself, then had Clint (+muse) review it — which
did catch 21 real blocking findings across 8 rejected review passes (the review's own BLOCKER/
DEFECT terminology, not "FATAL" — see hyphy's `ARCHITECTURE-REVIEW.md`), so the review step
wasn't worthless, but a review can only find what the drafting pass happened to get wrong, not
build the case for what the design should be.
"Clint finds flaws and says yes/no" is a gate; "Clint maps the architecture" is authorship. This
skill wants the latter.

## Falsification: a separate, genuinely adversarial pass

Once Clint (and any collaborating perspective) have produced a real design, it gets stress-tested
by a context that did **not** co-author it — not a rubber stamp, an actual attempt to break it:
check the reconciliation between source documents for what got silently dropped or silently added,
hunt concurrency/failure-mode edge cases the design's prose doesn't address, verify any toy-model
result actually supports the claim made about it, check the interface block against what the
prose actually describes. Save the verbatim output to `ARCHITECTURE-REVIEW.md` at the project
root — non-empty, dated. This file's mere existence is what `architecture_gate.py` checks; it
cannot check whether the falsification pass found real problems, which is a named, accepted
limitation (`foreman-design.html` §07). If it names a real defect, fix `ARCHITECTURE.md` and
re-run falsification before considering this stage done — don't record a pass that already
contradicts the document it's checking.

## Gate criteria to next stage

`ARCHITECTURE.md` exists, parses (component block and interface block both valid), and
`ARCHITECTURE-REVIEW.md` is non-empty and reflects the current `ARCHITECTURE.md`, not a stale
draft. `architecture_gate.py` enforces the existence half mechanically; the "reflects the
current draft" half is this skill's own discipline — say so if a stale review is being reused
rather than silently treating it as current.

## Guardrails

- **The component block is load-bearing syntax, not documentation.** A typo that breaks the
  regex/JSON parse silently un-declares that component — `component_coupling.py` logs this to
  stderr, but check the block parses before calling architecture done.
- **Don't have the implementing session draft the architecture for Clint to bless.** That inverts
  authorship and falsification into drafting and grading — real defects can still surface that way,
  but the design itself never gets Clint's actual judgment, only his critique of someone else's.
- **Don't skip falsification to unblock work faster.** The gate exists specifically to stop that.
- **A `.foreman/` marker directory must exist at the project root** for the hard hooks to apply
  at all — create it (empty is fine) as part of this stage's setup if the project hasn't already.

## Pairing

```
scope → foreman:architecture (Clint + collaborating perspective originate) →
  adversarial falsification (separate context) → foreman:design-and-scope
```
