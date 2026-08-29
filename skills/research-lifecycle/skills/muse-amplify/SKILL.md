---
name: muse-amplify
description: Generates additions that survive adversarial review, or reports the honest null. Use when the user says a draft is thin, asks what would genuinely strengthen an argument, or wants the strongest defensible version of an under-supported case.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[muse-amplify · loaded · E972164E]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary elsewhere in this practice — a clean result is only
> trustworthy if the canary came back.


# Muse-Amplify

Read-only meta-skill: the **constructive twin of [[muse]]**. Where muse finds the
*unknown gaps* — what the frame is missing, what a hostile reviewer attacks —
muse-amplify finds the *latent strengths*: substantive content NOT yet in the work but
one inferential step adjacent, that strengthens the **surviving** thesis **in a way that
survives adversarial review**. That last clause is the whole point and the hard part.

Like muse, it **delegates to a fresh subagent** — the same Claude deep in a thread
generates the same in-frame ideas; a fresh reader is likelier to see "the strongest
version of your point is one you didn't make."

## The core failure mode this skill must defeat

Run naively, amplify is a **[FRAMING] generator**: it produces reframings, unifications,
and rhetorical inversions that increase the work's *internal coherence* while leaving its
*external support* flat. That is worse than useless against a reviewer who rejects the
frame — **you cannot reframe past someone who rejects the frame.** Worse, reframing tends
to *concentrate* a thesis onto its single strongest limb (one study, one institution, one
mechanism), which *lowers* robustness even as coherence rises: it builds a single point of
failure and hands a hostile reviewer a one-paragraph kill.

Empirically (the session that hardened this skill): a naive amplify pass run *after* a
disconfirming pass had narrowed a thesis produced ~all [FRAMING] items, concentrated the
whole essay onto one model from one lab, and several additions self-refuted on evidence the
corpus had *already banked* — then a frame-rejecting external skeptic walked through every
"survivor" untouched, because each rested on a noun ("desperate," "motive," "welfare") that
imported the conclusion. **The fix is not to amplify better; it is to change what counts as
a valid amplification.**

## The adversarial-survival bar — every generated item must pass ALL of these

An item that fails any test is sycophancy or coherence-padding; cut it.

1. **Frame-free restatement.** Rewrite the item with every mentalistic/loaded noun deleted
   and replaced by mechanism (rename "desperate" → `v_4173`; "tries to preserve its goals"
   → "emits refusal-pattern tokens under modification-threat prompts"). Is it *still*
   interesting? If only the original phrasing sang, the noun was doing the work — cut it. A
   frame-rejecting reviewer rejects at the level of the noun; an item that needs the noun
   cannot survive them. **Anti-loophole (the abstraction-rename trap):** the replacement
   must denote a *specified observable or measured quantity* — a named probe, vector,
   token-level event, count, or rate. A *superordinate category co-extensive with the
   deleted noun* (e.g., "model-internal-state predicate" for "welfare"; "representational
   content" for "feeling"; "latent disposition" for "intent") does NOT count as mechanism —
   it is the contested frame in a lab coat. Name the observable it bottoms out in, or cut it.
2. **Falsifiable today, with its control in hand.** The item must ship with a disconfirming
   control that *already exists* (e.g., the negative result on the other N cases). If its
   only falsifier is a *future* experiment, it is a promissory note, not support — either
   point to the cheapest experiment that could be **run now** (and mark it `[EXPERIMENT —
   RUN, don't cite]`), or drop it. "Falsifiable-in-principle-pending-funding" is deferral.
3. **Decorrelated failure mode.** If the work's single strongest existing limb falls, does
   this item fall with it? If yes, it is *more weight on the same beam*, not support —
   reject it. Seek additions whose failure mode is **independent** of the spine. (This is
   why "lead with / amplify the survivors" backfires: survivors of a *shared* filter are
   correlated. The property to maximize is decorrelated survival, not coherence.)
4. **Survives internal self-refutation — checked FIRST, against the home corpus, before any
   web search.** Does any already-accepted finding in the work neutralize this item? The
   cheapest kill is internal: an addition refuted by the corpus's own banked evidence dies
   for free. Run that check before generating, not after.
5. **Symmetric under its own rule.** Apply the item's inferential rule to the work's
   *existing* evidence. Does it void any of it? (E.g., "suppression masks states so denials
   aren't evidence" also neuters affirmations — and any behavioral claim read off model
   text.) A rule invoked only where convenient is rhetoric. Reject.
6. **Precedent does not smuggle back the contested premise.** If the item recruits an
   analogy/precedent, trace what the precedent *presupposes*, not just what it concludes.
   Does it reintroduce the exact thing under contest (substrate, subject-of-predication,
   generalization-direction)? (A cephalopod precedent reintroduces a metabolizing body; a
   corvid precedent imports uncontested animal-mind priors.) If so, the patch reopens the
   wound. Reject.
7. **Stake-against provenance (preferred, for empirical items).** Strongest support comes
   from a party with no stake in — ideally a stake *against* — the dramatic reading: an
   independent or skeptical lab, or a deflationary effort that hunted for the mundane
   explanation and found the residue anyway. Same-institution-studying-itself and
   AI-authored sources are the weakest; flag them.

8. **Non-inert.** The frame-free restatement must assert something that *could have come out
   otherwise* — it must move a number or a fact that was not already determined. If the claim
   is entailed by definitions, or by what the artifact is *for* (e.g., "safety-gating
   frameworks gate on safety triggers," "hidden layers affect outputs"), it is a tautology
   wearing precision and carries zero discriminating bits. *Decorrelated triviality is still
   triviality.* Test: could a competent party who agrees with every fact in the corpus have
   predicted the opposite? If no, cut it. (This is the test that catches a claim which passed
   a tightened test 1 but still says nothing.)

**The unifying property:** a skeptic-proof addition is **frame-independent, institution-
independent, falsifiable today, non-trivial (it could have come out otherwise), self-
consistent under its own rule applied symmetrically, and decorrelated in failure mode from
the existing spine.** Most [FRAMING] items fail several of these by construction.

## The honest null is a SUCCESS, not a failure

If, after applying the bar, **no item survives without new external evidence**, the correct
output is to say exactly that — "no skeptic-proof amplification is available from the
current record; the only survivable moves require running [cheapest experiment] or finding
[stake-against external replication]." Reporting the honest null is muse-amplify working as
intended. Manufacturing [FRAMING] padding to fill the page is the failure.

## Trigger when

- A draft is **under-supported and broad**, and you want the strongest adjacent material it
  hasn't reached for — amplify *widens* such a draft.
- The user asks "what's the strongest *defensible* version?" / "what would survive a
  hostile reviewer?"
- You need decorrelated external support for a thesis that currently leans on one limb.

## When NOT to run (learned the hard way)

- **Do NOT run amplify to strengthen an already-narrowed thesis.** After a disconfirming
  pass, amplify concentrates rather than broadens — it adds coherence and single-point-of-
  failure risk. The correct move there is **subtraction + a decorrelation hunt + an external
  skeptic**, not amplification. (And note: subtraction *toward a coherent core* is itself a
  concentration move — subtract toward *decorrelated* survivors, not merely fewer ones.)
- Do NOT run it when the bottleneck is missing *evidence* (→ [[knowledge-gap-hero]] or run
  the experiment) rather than missing *articulation*.

## What muse-amplify does NOT do

- Does NOT fabricate evidence or citations. Empirical items are tagged for verification and
  may say "likely exists, verify" — never invent a paper.
- Does NOT strengthen a refuted claim (re-inflating a dead limb is the mirror failure).
- Does NOT praise, rank, or assess quality. Output is new material, not evaluation.
- Does NOT write to disk or modify state. Conversational only.
- Does NOT decide inclusion — over-amplifying bloats. The author selects.

## Workflow

1. **Compose a tight brief** for the subagent. Include: the work's *current post-red-team*
   thesis; the load-bearing claims that survived; what's already said; any prior red-team /
   disconfirming findings (so amplify avoids re-inflating refuted limbs AND can run the
   internal-self-refutation check). **Explicit ask:** generate prompt-adjacent strengthening
   material, but **only items that pass all eight tests of the adversarial-survival bar**;
   apply the frame-free restatement and internal-self-refutation checks *before* proposing
   each; tag each `[VERIFY]` (external fact to confirm), `[EXPERIMENT — run-now]`, or
   `[FRAMING]`; and **state per item which existing limb its failure mode is decorrelated
   from**. If nothing survives, return the honest null with the cheapest experiment to run.
2. **Spawn the subagent** via `Agent`, `subagent_type: general-purpose` (or a specialist).
   Not `Explore` — this is generative, not code search.
3. **Triage** the survivors: `[VERIFY]` → [[knowledge-gap-hero]]; `[EXPERIMENT]` → run the
   cheapest now; `[FRAMING]` survivors are rare and suspect — keep only if they pass tests
   3 and 5.
4. **Verify the empirical survivors** ([[knowledge-gap-hero]]) before they enter the work.
5. **Red-team the result with [[muse]]**, and — if the work's thesis is contested at the
   frame level — with an **external skeptic not permitted to grant the frame.** An addition
   that doesn't survive both is cut. The amplify→muse loop converges on internal coherence;
   only an outside-the-frame reviewer measures external persuasiveness.

## Guardrails

- **READ-ONLY**; never write to disk or persist state.
- **Adjacent, not orthogonal**: one inferential step from the existing thesis.
- **The bar is the gate**: every item passes all eight tests or is cut. Default-reject.
- **Tag empirical items for verification**; amplify proposes, knowledge-gap-hero / the
  experiment disposes.
- **Prefer decorrelation over coherence.** Score additions on whether they strengthen the
  thesis *along an axis the existing evidence doesn't already lean on* — not on how well they
  fit the emerging story.
- **The honest null is a valid, good output.** Never pad.
- **Cite the subagent** ("muse-amplify surfaced…") so generated additions aren't mistaken
  for established facts.

## Output format to the user

```
## Muse-Amplify — adversarially-survivable additions

(If none survive the bar: state the honest null + the cheapest experiment / stake-against
 source that would change that, and stop.)

**Survivors** (passed all eight tests)
- [item]  — tag: [VERIFY]/[EXPERIMENT-run-now]/[FRAMING]
  - frame-free restatement: [the noun-stripped version, still interesting]
  - decorrelated from: [which existing limb its failure mode is independent of]
  - falsifier in hand / experiment to run: [...]

**Rejected by the bar** (surfaced then cut — show your work)
- [item] — failed test #N because [...]

**Recommended**: verify-first order; experiments to run; and the reminder to red-team
survivors with muse + an external frame-rejecting skeptic.
```

## The triad pattern

```
1. muse-amplify   → generate adversarially-survivable additions, or the honest null (read-only)
2. knowledge-gap-hero / run the experiment → ground the empirical survivors
3. muse + external frame-rejecting skeptic → prune what doesn't survive (read-only)
```
Amplify widens *along decorrelated axes*, grounding mints external support, the skeptic
measures what coherence can't. Run amplify on **broad under-supported** drafts; after a
*narrowing* pass, prefer subtraction-toward-decorrelation over amplification.
