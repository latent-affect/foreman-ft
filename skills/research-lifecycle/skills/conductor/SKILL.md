---
name: conductor
description: Routes research and argument work to the right hardening stage for the phase it is in. Use when the user is drafting a paper or plan and asks what check comes next, which stage applies now, or how to sequence the work through to submission.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[conductor · loaded · B51E79B7]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary elsewhere in this practice — a clean result is only
> trustworthy if the canary came back.


# Conductor (claude.ai edition)

Orchestration meta-skill: the **standing operating procedure** for argument-bearing research
work. Where [[peer-review]] is a *terminal* hardening run on a near-final draft, `conductor` is the
*lifecycle* router — it watches the arc of building a document or plan and fires the **single
cheapest right stage at the moment it's cheap**, so the hard checks don't pile up at submission
(the classic failure: write the whole paper, fact-check it the night before, find a load-bearing
claim went stale).

`conductor` does **not** do the work itself. It sequences. Each stage it routes to is one of the
sibling skills, which run their own firewalled fresh-eyes pass. Conductor's only job is **phase
detection + minimal-right-tool selection + escalation timing.**

## Environment note — claude.ai edition

No subagents here, so the stages conductor routes to run as **firewalled inline passes** (default)
or **fresh-chat runs** (high-rigor), per each skill's own environment note. Adversarial distance is
weaker than in Claude Code; say so when it matters, and push the user toward a fresh chat for the
high-stakes passes (notably the frame-rejecting skeptic in [[peer-review]]).

**Making it "always on" in claude.ai:** there's no `CLAUDE.md` here. The trigger is the skill's
own description (it fires when an argument-bearing task appears) plus, if you want it enforced every
chat, a one-line routing rule in **Settings → Profile → preferences** ("For research/argument-bearing
work, route hardening stages via conductor") or in a **Project's custom instructions** if the work
lives in a Project. The procedure lives here in the skill; the always-fires nudge lives in
preferences/project instructions.

## Trigger when
- The user starts, continues, or asks for help on a **paper, essay, manuscript, memo, review, or
  long-form argument** — at any phase: "help me outline," "let's draft section 3," "is this
  ready," "strengthen this," "what would a reviewer say."
- The user is about to **execute a plan** (including a Claude Code project plan) and wants its
  blind spots surfaced first → route to [[muse]].
- A **time-sensitive fact** (version, CVE, price, date, "latest X") gets asserted → fire
  [[knowledge-gap-hero]] now, don't bank the gap.
- A draft section **stabilizes** → cheap moment for a [[muse]] gap pass.
- A **load-bearing structural claim is about to be committed** ("X always has property P", "this
  metric tracks Y", "the obstruction is nonzero") → fire [[toy-models]] now — build the smallest
  killer before the claim is written down or built on.
- **Any toy, experiment, or Claude Code run returns a result** → log it to [[experiment-ledger]]
  (the **object-match** check FIRST) before anyone calls it a win or a loss.
- The user signals **submission/publication or any irreversible send** → escalate to full
  [[peer-review]].

## The phase → stage map
Detect where the work is in its life and route accordingly. Most turns touch ONE stage, not all.

| Phase | Signal | Route to | Why this is the cheap moment |
|---|---|---|---|
| **0. Scope** | "help me plan/outline/argue X" | conductor itself: name the load-bearing + time-sensitive claims up front | The claim map drives every later stage |
| **1. Blind spots** | a section, plan, or argument takes shape (also before executing any plan) | [[muse]] | Fresh-eyes gaps before more is built on it |
| **2. Facts** | any version/CVE/price/date/"latest" asserted — **at any phase** | [[knowledge-gap-hero]] | A stale fact caught at write-time is free; **re-fire every time a new time-sensitive claim appears** |
| **3. Broad + under-supported** | "this feels thin / strongest version?" | [[muse-amplify]] | Widen along decorrelated axes; expect the honest null; skip on an already-narrow thesis |
| **4. Load-bearing claim about to be committed** | "X always has property P", "this metric tracks Y", "the obstruction is nonzero" | [[toy-models]] | The smallest killer NOW kills a bad direction before it is built on — not weeks later |
| **5. A result came back** | any toy / experiment / Claude Code run returns | [[experiment-ledger]] | Classify into the five categories — **object-match first** — before it is believed; a proxy result is WRONG OBJECT, not evidence |
| **6. Execution** | "go build/run this" | Claude Code run | The actual work; its outputs feed the ledger too |
| **7. Near-final / pre-send** | "is this ready / harden it / beat the reviewer" | [[peer-review]] | One terminal hostile pass so the real referee finds ~nothing |
| **8. Writeup emphasis** | "what do I lead with / promote / cut" | [[spine]] | Turn hardened content into emphasis architecture — last, after peer-review |

## How to run it (the discipline)
1. **Detect phase, don't assume "run everything."** A one-paragraph ask or a half-formed outline
   does NOT warrant the full pipeline. Scale to the artifact — over-eager (peer-reviewing a stub)
   is as bad as under-eager (shipping unverified).
2. **Fire the minimal right stage**, name which one and why, then continue the actual work.
   Conductor should feel like a quiet co-author flagging the cheap check, not a gate.
3. **Don't double-run.** If a stage already ran on unchanged content, say "already covered."
4. **Respect each stage's contract.** The analysis skills are **read-only** — they surface, rank,
   or critique; they don't edit. The author revises.
5. **Toy before you trust; ledger before you believe.** A load-bearing structural claim gets a
   [[toy-models]] killer at the moment it's proposed, and every result is classified in the
   [[experiment-ledger]] (object-match FIRST) before it's a "win" or "loss" — a proxy result is
   WRONG OBJECT, not evidence about the claim.
6. **Escalate to [[peer-review]] before any irreversible send**, even unasked — that's the
   highest-leverage moment users most often skip. Flag it; let them decline.

## The natural sequence (end-to-end)
```
scope (name load-bearing + time-sensitive claims)
  → muse — surface blind spots / gaps first (reading pass; also before executing any plan)
  → knowledge-gap-hero — fill every time-sensitive fact as it lands (re-fire WHENEVER a new one appears)
  → muse-amplify IF the draft is broad + under-supported (else skip — never pad a narrowed thesis)
  → toy-models — build the smallest killer for each load-bearing structural claim, BEFORE committing it
  → experiment-ledger — log every toy result into one of the five categories (object-match first)
  → Claude Code run — the actual build/execution (its outputs also go to the ledger)
       ↳ knowledge-gap-hero AGAIN whenever a toy or run surfaces a fresh fact to verify
  → peer-review — terminal hostile gate before any irreversible send
  → spine — emphasis architecture for the writeup (lead / promote / demote / cut), last
```
A default, not a rail — enter at whatever phase the work is actually in, and re-fire
knowledge-gap-hero any time a fresh time-sensitive fact appears.

## Guardrails
- **Route, don't do.** Conductor selects and sequences; the sibling skills do the work.
- **Scale to the artifact.** Cheapest correct intervention wins.
- **Never pad.** If [[muse-amplify]]'s honest null is right, that's success. Skip amplify on an
  already-narrowed thesis (it concentrates rather than broadens).
- **Caught facts are dated and sourced** ([[knowledge-gap-hero]]); critiques and additions are
  attributed to their pass so facts, flags, and proposals never blur.
- **Honesty over agreeableness**: under-claiming and over-claiming are equally unacceptable, and
  unanswerability is a positive finding, not a gap to paper over.
- **Name the environment limit.** Say when adversarial distance is partial and a fresh chat would
  strengthen the pass.

## Pairing
`conductor` is the lifecycle layer above the others. Run the individual skills directly when you
already know which single stage you need; reach for [[peer-review]] directly when you just want the
terminal hostile pass. [[toy-models]] writes-and-runs the smallest falsifier during drafting /
pre-execution; [[experiment-ledger]] is the append-only record every toy and run feeds — and the
source [[spine]] and [[peer-review]] read to see which support is real, which is correlated, and
which verdicts were retracted.
