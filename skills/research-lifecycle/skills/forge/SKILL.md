---
name: forge
description: Produces falsifiable research conjectures. Use when the user asks for a hypothesis nobody has tried, wants a non-obvious angle, says the current approach is stuck against a known wall, or licenses disciplined speculation.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[forge · loaded · F04C9890]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary elsewhere in this practice — a clean result is only
> trustworthy if the canary came back.


# Forge

The **generative** skill. Every sibling skill *tests, ranks, or critiques*; `forge` *produces*. Its
premise: a "hallucination" is not an error to suppress — it is an **untested hypothesis**, a
combination not yet in the literature. The error was never in *generating* the novel claim; the
error is in *asserting it as true* or in *failing to test it*. So `forge` generates freely, labels
its footing honestly, and **cannot finish without handing off to the testing pipeline.** Optimism
lives here; rigor lives downstream; the two are kept in separate rooms on purpose.

This is the formalization of the process that produced the program's hypotheses in the first place
(the "Newton-interval" generation): disciplined imagination aimed at a *specific contingent
convergence* of existing machinery, not a grand reframing.

## Environment note (Claude Code)
- **Generate inline**, and ground the cross-domain ingredients with **WebSearch / WebFetch during
  forge** — verify whether the convergence is actually novel *before* claiming it. To generate free
  of the current thread's framing, spawn an independent **Agent subagent** (general-purpose) to forge
  in parallel, then triage what it returns.
- **The mandatory hand-off routes to REAL execution.** In Claude Code the testing pipeline is actual
  tool use, not a conceptual pass: [[toy-models]] builds and *runs* the smallest killer via Bash,
  every result is classified in the [[experiment-ledger]], and only survivors proceed. `forge`
  proposes; the environment disposes. The hand-off is not optional.
- **claude.ai (reference):** generate inline, web-search to ground, hand off to firewalled passes.

## Trigger when
- A problem needs a **new angle**, not a summary of consensus: "what might crack X," "hypothesize,"
  "give me a non-obvious approach," "what's a move nobody's tried."
- The user explicitly licenses **disciplined speculation** / treating hallucination as hypothesis.
- An existing approach is **stuck against a known wall** and needs a candidate that is built to
  *thread* that wall rather than ignore it.
- NOT when the task wants established fact, a literature summary, or a careful exposition — forge
  generates conjecture; do not deploy it where the user wants ground truth.

## The three admissibility gates
A generated idea is admitted as a *hypothesis* only if it passes all three. An idea that fails a
gate is not discarded silently — it is either reshaped to pass, or **labeled honestly as a pure
frame/analogy, not a hypothesis.**

| Gate | Test | Fails when |
|---|---|---|
| **Frame-free** | names an actual object/mechanism, not an evocative analogy | "use the deep structure of symmetry" (vs "use the 2-torsion of A₅") |
| **Falsifiable** | a concrete computation or theorem could disconfirm it | nothing could ever count against it |
| **Non-inert** | could have come out otherwise; not true by definition; and survives the move from ideation to execution | it merely restates the problem / is entailed by definitions / collapses when you try to actually do it |

**Execution-groundedness note (added from Si et al. 2024/2025, arXiv 2409.04109 / 2506.20803):**
The ideation-execution gap is empirical: LLM-generated ideas rated as more novel than expert ideas
in blind review systematically scored *lower* after execution, with rankings sometimes reversing.
Novelty in the room ≠ value in the world. The non-inert gate should explicitly ask: does this
hypothesis depend on unstated assumptions that likely don't hold when someone actually tries to
execute it? If yes, name those assumptions as part of the footing label — not as a reason to
discard the hypothesis, but as part of the honest speculative-leap disclosure.


## How to run it (the discipline)
1. **Name the wall first.** State the known no-go theorems / failure modes of the target, then
   generate *against* them — a hypothesis built to thread the barriers, not one that forgets they
   exist. (The discipline that made a candidate target torsion *because* torsion dodges the
   relevant barrier, rather than choosing a tool that walks into it.)
2. **Seek the contingent convergence, not the grand reframing.** A grand restatement of the problem
   is inert; a generic "use more math" is empty. Aim for *specific* machinery landing on the *exact*
   open spot. **Cross at least three distinct advanced domains** to find non-generic adjacencies.
3. **Run every candidate through the three gates.** Keep only what passes; relabel the rest as
   frames.
4. **Weigh by most-likely, but protect productive anti-sense.** Rank candidates by which convergence
   is most likely load-bearing — but do not prune a move *just* because it seems like it shouldn't
   work; the move that "shouldn't work but does" is the prize, flag it rather than cut it.
5. **Ship footing labels, always.** For each hypothesis, separate: **established ingredients**
   (cited), the **novel synthesis** (the actual new combination), and the **speculative leap** (what
   is unjustified). A novel combination is a *hypothesis, not a result* — novelty alone is cheap;
   only surviving tests makes it a contribution.
6. **Hand off — mandatory.** A forged hypothesis is not done until routed. The first hand-off is
   always [[knowledge-gap-hero]] to verify it is *actually* novel (catching false-novelty before it
   is claimed), then into the rest of the pipeline. In Claude Code, "the rest of the pipeline" means
   real execution via [[toy-models]] + [[experiment-ledger]]. `forge` proposes; it never asserts.

## The honest null
Sometimes the disciplined output is **"no admissible novel convergence exists here."** That is a
valid `forge` result, not a failure — do not manufacture novelty to fill space, and do not lower the
gates to produce a candidate. An honest null from forge saves the whole downstream pipeline from
hardening a non-idea.

## Guardrails
- **Generate freely; assert nothing.** The license to hallucinate is a license to *hypothesize*, not
  to state as true. Every output is explicitly a conjecture awaiting test.
- **The gates are not optional.** Frame-free, falsifiable, non-inert — an idea failing any of these
  is a frame, labeled as such, not smuggled in as a hypothesis.
- **Barrier-aware or it doesn't ship.** A hypothesis that ignores the known reasons attempts fail is
  not disciplined imagination; it is noise.
- **Under-claiming and over-claiming are equally unacceptable**, including about novelty itself —
  hence the mandatory [[knowledge-gap-hero]] check before any "this is new" claim.
- **Optimism in generation, rigor in testing, care in interpretation** — and never let the three
  bleed together: forge does not get to grade its own output.

## Pairing
`forge` is the **source** that feeds [[conductor]]'s pipeline; conductor should route the
*generation* phase here before any hardening begins. Distinct from its cousin [[muse-amplify]]:
muse-amplify *strengthens an existing under-supported draft* along decorrelated axes; `forge`
*creates the hypothesis that did not exist yet*. Its narrower sibling [[spread]] is the
distribution-and-score mechanic used *inside* forge's generation step to beat mode collapse. The
canonical end-to-end flow:

```
spread (elicit a scored distribution, beat mode collapse)
forge (generate disciplined, barrier-aware, gated hypotheses + footing labels)
  → knowledge-gap-hero (is it actually novel? verify the literature — catch false novelty)
  → muse (blind spots a fresh reviewer would catch before committing)
  → muse-amplify (strengthen the survivors along decorrelated axes, or honest null)
  → toy-models (smallest runnable killer + controls, RUN for real in Claude Code)
  → experiment-ledger (classify every result; object-match first)
  → Claude Code execution
  → peer-review (terminal hostile gate) → spine (what to lead with)
```
Forge opens the funnel; everything downstream narrows it. The funnel only works because forge
labels its footing and refuses to grade itself.
