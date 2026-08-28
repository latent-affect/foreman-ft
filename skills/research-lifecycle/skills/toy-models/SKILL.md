---
name: toy-models
description: Builds and runs the smallest script that could disprove a load-bearing claim, with controls. Use when the user is about to build on an assumption, asks whether something can be checked cheaply, or states a structural claim a small computation could kill.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[toy-models · loaded · 939E0534]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary in `security-audit/verify.sh` — a clean result is only
> trustworthy if the canary came back.


# Toy Models

The **micro Russian-doll** skill: take a load-bearing claim, decompose it to the *smallest
independently-checkable structural fact* it rests on, build the smallest script that could
**kill** that fact, and run it. Where [[muse]] surfaces blind spots by *reading* and
[[peer-review]] red-teams a near-final draft, `toy-models` **acts** — it writes and executes a
minimal falsifier so a claim is tested at the moment it is proposed, not weeks later at execution.

The motivating failure it prevents: a claim that *could have been cheaply checked* gets written
into a paper, built on, and only falsified after real effort is spent. (Canonical case: a topological
hardness signal that was structurally impossible because the underlying graph was triangle-free —
a five-line check would have killed it before it became a research direction.)

## Creativity claims
When the load-bearing claim is about **creative output** ("this generates more novel ideas,"
"this approach produces more diverse solutions"), reduce it to a novelty×utility joint test, not
novelty alone. Combinatorial-creativity-as-generalization research (arXiv 2509.21043, 2025) found
a novelty–utility tradeoff as a structural property: maximizing novelty without utility constraint
is easy (generate random outputs) and maximizing utility without novelty constraint is easy
(retrieve the best existing solution). The real creativity claim lives in the joint space. A toy
model that only tests novelty (e.g., semantic distance from prior art) while ignoring utility (can
someone actually use this?) is testing the wrong object. Lock both dimensions before running.

## Environment note (Claude Code first)
- **Claude Code (primary, highest fidelity):** the toy and its controls are committed files run for
  real via **Bash**; every result is `[RAN]`-tagged with its exact command and logged to the
  [[experiment-ledger]]. This is where forge's mandatory hand-off actually lands — a forged
  hypothesis is not tested until a toy has *run* here. For independent falsification, a fresh
  **Agent subagent** can build and run the killer without the proposing thread's bias.
- **claude.ai:** runs via the code-execution tool; toys are small enough to run inline. Adversarial
  distance is fine here because the toy is *code that runs*, not an opinion — the environment, not
  the model, returns the verdict.

## Trigger when
- A **speculative or empirical claim is about to be committed** to a draft, a plan, or a hand-off —
  fire *during generation/research*, not only before execution. This is the point of the skill.
- A claim asserts a **structural property** ("X always has property P", "the obstruction is rare /
  present / nonzero", "this metric tracks Y") that a small, exact computation could confirm or kill.
- A result came back and you need to know whether it is real before believing it → build the toy
  that reproduces the load-bearing step independently.
- Before believing any **proxy**: build the toy that checks whether the proxy even *can* see the
  thing it claims to measure (the WRONG-OBJECT guard).

## How to run it (the discipline)
1. **Name the single load-bearing fact.** Reduce the claim to one structural statement a computer
   can settle. If you cannot, the claim is not toy-checkable — say so; that is itself a finding
   (it means the claim needs formalization, a real research step, not a toy).
2. **Build the smallest killer.** The toy should be the *minimum* code that could return FALSE if
   the claim is wrong. Prefer a structural/closed-form check (e.g. a parity argument) over a
   simulation — a structural impossibility kills a whole direction; a simulation kills one instance.
3. **Search for a witness; never assert one.** If the claim is "there exist two X with property P,"
   *search* the space and *check* the witness — do not hand-pick, which can accidentally prove
   nothing (a hand-picked pair once both computed the constant function and demonstrated nothing).
4. **Add both controls.** A **positive control** (feed an input with a known non-trivial answer;
   confirm the toy reports it — catches false negatives / hard-wiring). A **negative/shuffle
   control** (feed a spurious or relabelled input; confirm the toy reports *no* signal — catches
   false positives). A toy without controls is an anecdote.
5. **Run it for real and record the raw output.** No asserted numbers. Tag every value `[RAN]` with
   the exact command. Hand the result to the [[experiment-ledger]] with an outcome category.
6. **State the scope honestly.** A toy at n=6 is evidence about n=6 plus a structural argument, or
   it is nothing. Say which. Reproduction on a second machine confirms *no environment bug*, not
   *correctness* — only an independent structural/closed-form argument confirms correctness.

## What a good toy returns
| Field | Content |
|---|---|
| **Load-bearing fact** | the one structural statement under test |
| **Killer** | the minimal code that returns FALSE if the claim is wrong |
| **Positive control** | known non-trivial input → expected answer (caught false negative?) |
| **Negative/shuffle control** | spurious input → no signal (caught false positive?) |
| **Raw result** | verbatim output, `[RAN]` with command |
| **Verdict** | confirms / kills / inconclusive — and the *scope* of that verdict |
| **Hand-off** | the [[experiment-ledger]] outcome category |

## Guardrails
- **A clean kill is success.** The job is to falsify cheaply, not to make the claim survive. If the
  toy kills the claim, that is the highest-value outcome — it saved the effort downstream.
- **Never manufacture a pass.** Do not weaken the killer or pick a friendly input to get green.
- **Structural beats statistical.** A closed-form impossibility (parity, dimension, annihilation)
  outranks any number of runs; reach for it first.
- **Not-toy-checkable is a finding, not a failure.** If the real object can't be reduced to a small
  exact check (e.g. it is undefined or genuinely hard), say so plainly — that flags a real research
  step and routes back to formalization, it does not get a fake toy.
- **Honesty over agreeableness:** under-claiming and over-claiming are equally unacceptable; report
  exactly what ran and what it shows.

## Pairing
The constructive complement of [[muse]] (which only reads). Fire it inside [[conductor]]'s drafting
and pre-execution phases — *before* a speculative claim is written down, not after. It is where
[[forge]]'s mandatory hand-off lands in Claude Code. Every toy result feeds the
[[experiment-ledger]]. If a toy survives and the claim is near-final, [[peer-review]] still gets the
terminal hostile pass.
