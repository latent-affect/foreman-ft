# Design-and-scope record — dev-harness Run 2

**Stage:** `foreman:design-and-scope` (Run 2, stage 3 of 3)
**Owner:** Priya Desai persona, session `dev-harness-run2`, branch `build-brief-run2`
**Date:** 2026-09-02
**Inputs:** `PRD.md` (R1-R24), `ARCHITECTURE.md` Run 2 section (Clint Eastwood, falsified by
`ARCHITECTURE-REVIEW.md`), `.foreman/tpm-gate-concept.json` revision 2.

This record exists because the stage produced three different kinds of output and only one of
them is a frozen artifact. Reading `GOALS.json` alone would leave the other two invisible.

---

## 1. What was frozen

One component, deliberately.

| Component | Path | Requirement | Criteria | Frozen |
|---|---|---|---|---|
| `root-docs-and-scratch` | `GOALS.json` (repo root) | R23 (DEVH-6) | 4 | 2026-09-02T07:05:32Z |

Hash `sha256:db6db08c...af739a`, computed by importing `goals_freeze_gate.criteria_hash` rather
than reimplementing the convention, then verified by re-reading the written file from disk and
recomputing. The skill warns that a hand-rolled equivalent differing in key ordering or
separators produces a hash the gate reads as tampering, so the gate's own function is the only
safe source.

The file sits at the repository root because `component_root('root-docs-and-scratch')` resolves
to the project root: its first declared glob is a bare filename, so the empty prefix is correct.
Confirmed by reading `component_coupling.component_root`, not assumed.

**Why R23 and only R23.** Concept-gate condition C3 assigns it first, and PRD.md's own sequencing
clause puts it ahead of R1, R3 and R21. Every quantitative claim in this pipeline traces to three
untracked scripts, one of which destroys prior evidence on ordinary reuse. Freezing criteria for
requirements whose baseline numbers are not yet reproducible would bake in exactly the problem
R23 exists to remove.

## 2. What was NOT frozen, and why that is the correct outcome

Sixteen requirements have tickets and designs but no frozen `GOALS.json`. This is not an
unfinished stage. `foreman:design-and-scope`'s own guardrail: "One `GOALS.json` per component,
not one per project. Freezing the whole project's design before any component can start
implementation is exactly the big-design-upfront rigidity the hybrid V-Model rejects."

Each remaining component freezes when its own requirement reaches the front of the queue. The
blocked ones cannot freeze at all yet, and naming why is more useful than a frozen file built on
an assumption:

| Requirement | Blocked on | Whose call |
|---|---|---|
| R1 (DEVH-7) | The target number off the 6.86 baseline is not pinned | Operator, coupled to R21 |
| R3 (DEVH-9) | R23 landing; there is no tool SHA to pin a "before" against | Sequencing |
| R9 (DEVH-14) | A current measured misrouted-row count. The brief's 477 was never verified | Measurement |
| R13 (DEVH-15) | The control ordering needs re-deriving, not inheriting. See §4 | Not mine |
| R14/R15 (DEVH-16) | Operator confirming whether the artifact ever existed | Operator |
| R16 (DEVH-17) | A data source for model identity. D5's own stated blocker | Investigation |
| R21 (DEVH-21) | R23, R18, **and DEVH-4 + DEVH-5 closing** (condition C3) | Sequencing |

## 3. R20 discharged — one ticket per requirement

Seventeen tickets, DEVH-6 through DEVH-22, one per live requirement. R24 was already DEVH-2 and
was not duplicated. R20 itself is the process being executed here rather than a separate work
item. R6, R10, R11, R12 and R15 carry no ticket: PRD.md section 8 records each as closed,
retired, or absorbed.

Every ticket carries an idempotency key, so a re-run of the creation script cannot mint
duplicates. Each names its verification, its blocking precondition where it has one, and the
evidence its claims rest on with file and line.

Priority reflects sequencing and blast radius, not enthusiasm. DEVH-6 (R23) is the only P0
because everything measurable depends on it. DEVH-13 (R8) and DEVH-16 (R14/R15) are P1: one is a
credential-exposure channel with a real prior incident, the other gates how much of an entire
section can be trusted.

## 4. Two things this stage deliberately did not decide

**R13's control ordering.** The falsification pass raised a fair challenge to PRD.md's own
reasoning: section 0.1 discards R13's scored pass ("12 candidates, six-way tie") as unverified,
then keeps its ordering as "defensible on its own argument" — while marking R14/R15 BLOCKED on
what is arguably the same evidentiary shape. My answer is that the two are different kinds of
claim: R14/R15 assert an artifact exists, which is falsifiable by search and failed, while the
ordering is a judgment about which control class is stronger, falsifiable by argument. But that
answer does not make the ordering verified, and I stated it as settled when it was inherited.

Per the operator's measure-then-decide rule, the ordering gets re-derived against N2's
sub-millisecond latency budget and the real threat model, not adopted. Cheap detection for
high-frequency low-severity cases with capability removal reserved for high-severity is a live
alternative that was never scored against anything. Recorded on DEVH-15, unfrozen.

**Anything requiring the operator.** R1's target number, R14/R15's existence question, and R16's
data source are all recorded with their defaults and left open. C1's `treat as never written`
default is carried onto DEVH-16 verbatim so silence cannot become an assumed yes.

## 5. Disclosures

**The falsification pass on `GOALS.json`'s criteria has not run.** The skill requires a context
that did not author `criteria[]` to check whether each verification could still pass against a
plausible broken implementation. I authored them. The skill also says this is not mechanically
enforced and to say so plainly rather than treat frozen criteria as reviewed because they are
frozen. So: frozen, not independently falsified. The `failure_criteria[]` block is my own attempt
at the discipline, which is not the same thing and should not be counted as it.

**A structural conflict this stage does not resolve.** `ARCHITECTURE.md`'s closing section flags
it and it is correct: this PRD was authored by one persona, architected by a second, and its
design-and-scope stage is owned by the first again — including the corrections the concept gate
raised against that same author's document. For a project whose stated premise is that an
unverified account of a system is not trustworthy, that is a real repeat of the pattern one level
up. It was mitigated here by outside contexts at every step (the concept gate found C2, the
falsification pass found the overstated "zero hits" claim, both landed), and the orchestrator has
a fresh session queued to check the operator-confirmation items survived intact. Recording it
because the mitigation is a practice, not a mechanism, and practices lapse quietly.

**Two documents sit outside every declared component.** `PRD.md` and this file both resolve to
`None` under `component_of`, so `goals_freeze_gate` does not cover either. Verified by running
the resolver, not by reading the map. Whether to declare them is the architecture author's call,
raised rather than taken.

## 6. Verification performed in this stage

Not read, run.

- `component_of` against the pre-fix `ARCHITECTURE.md` from `HEAD` and against the fixed one:
  every `bollard/` path returned `None` before, `bollard` after. Clint's headline bug and its fix
  both confirmed by execution before anything was built on them.
- The freeze hash recomputed from the file on disk after writing, not from the in-memory object
  that produced it.
- The machine-wide `layered_command_guard` search re-run independently. Six files, all narrative,
  none executable — which corrected PRD.md's own "zero hits" phrasing.
- One ticket created as a probe and its stored fields read back from the database before the
  remaining sixteen were batched.

## 7. Next

Implementation starts at DEVH-6. Nothing else should start before it, and R3 and R21 in
particular are unfalsifiable until it lands.
