---
name: experiment-ledger
description: Classifies each experimental result into one of five outcomes, checking first whether the intended object was tested rather than a proxy. Use when a run returns a result, when the user is about to call something a win or a failure, or when a stand-in was measured.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[experiment-ledger · loaded · 4E327652]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary elsewhere in this practice — a clean result is only
> trustworthy if the canary came back.


# Experiment Ledger

A persistent, append-only record of every experiment the program runs, where each entry is forced
into **one of five outcome categories** before it can be believed. Its whole reason to exist is the
failure it prevents: a test came back negative, one instance reached for "this is a win," another
reached for "it's dead," and neither was right because **the test had measured a proxy, not the real
object.** The ledger makes that confusion structurally impossible by requiring an *object-match*
check on every entry.

"Win" and "loss" are **not** categories here. The categories are:

| Category | Meaning | The trap it catches |
|---|---|---|
| **PASS** | the intended object was tested and the claim survived | over-trusting a lucky run |
| **FAIL** | the intended object was tested and the claim was falsified | a real, clean negative — valuable |
| **AMBIGUOUS** | the object was tested but the result does not discriminate | reading noise as signal |
| **WRONG OBJECT** | a *proxy* was tested, not the intended object | logging a proxy failure as a real one |
| **PROMISING-BUT-UNDERFORMALIZED** | the intended object is not yet defined sharply enough to test | calling "untested" either "dead" or "alive" |

## Environment note
- **Claude Code:** the ledger is a committed `LEDGER.md` (or structured file), append-only, versioned
  with the code that produced each entry. This is its natural home.
- **claude.ai:** maintained inline as a running table in the working document; port it to a file when
  the work moves to Claude Code. The discipline is identical; only the persistence differs.

## Trigger when
- **Any experiment, toy, or computation returns a result** — before it is interpreted, it gets a
  ledger entry. Fire immediately after [[toy-models]] or after a Claude Code run.
- Someone is about to call a result a **"win" or a "failure"** → stop; classify it into one of the
  five categories first, which requires answering the object-match question.
- A **prior verdict is being relied on** downstream → check its ledger entry's category and
  confidence before building on it.

## The required fields per entry
Every entry must answer the object-match question explicitly; an entry missing it is incomplete.

| Field | Content |
|---|---|
| **Claim under test** | the hypothesis, stated falsifiably |
| **Intended object** | what we *meant* to test |
| **Object actually tested** | what the code/experiment *actually* operated on |
| **Object match?** | YES / NO — **if NO, category is WRONG OBJECT**, full stop |
| **Method** | command(s) to reproduce; every value `[RAN]` or `[ASSERTED]` |
| **Raw result** | verbatim output, no paraphrase of numbers |
| **Outcome** | one of the five categories |
| **Confidence** | high / medium / low, with the reason |
| **What would flip it** | the concrete result that would change the verdict |
| **Decorrelation** | does this share a failure mode with another entry? (correlated confirmations are weak) |

## How to run it (the discipline)
1. **Object-match first.** Before category, answer: did we test the intended object or a proxy?
   A proxy result is **WRONG OBJECT** regardless of whether it came back positive or negative —
   it is not evidence about the claim. This is the single most important rule.
2. **No "win," no "loss."** Those words are banned as verdicts. Force the five-way category; it
   carries the nuance those words erase.
3. **A clean FAIL is a real result, recorded as such** — and is distinct from AMBIGUOUS (didn't
   discriminate) and from WRONG OBJECT (didn't test the right thing). Keep the three separate.
4. **PROMISING-BUT-UNDERFORMALIZED is honest, not evasive.** Use it when the intended object isn't
   yet sharp enough to test — but pair it with the formalization step that would make it testable,
   so it cannot become a permanent parking spot for a favored conjecture.
5. **Confidence travels with the entry.** Downstream work cites the category *and* confidence, never
   a bare "we showed X." Reproduction on a second machine raises confidence in *no-environment-bug*,
   not in *correctness*.
6. **Append-only.** Corrections are new dated entries that supersede, not silent edits — the trail
   of retracted verdicts is itself evidence the ledger is honest (a record only ever revised toward
   the favored answer is worthless).

## Guardrails
- **The object-match check is mandatory and comes first.** It is the reason the ledger exists.
- **Under-claiming and over-claiming are equally unacceptable.** WRONG OBJECT and AMBIGUOUS exist
  precisely so a result is neither inflated to PASS/FAIL nor dismissed.
- **Do not let strong tooling inflate confidence in the conjecture.** A better-audited negative is
  still a negative; the ledger tracks the *method's* reliability separately from the *claim's* odds.
- **Every number is `[RAN]` with its command or `[ASSERTED]` and quarantined.** No unsourced values.

## Pairing
Downstream of [[toy-models]] (which generates entries) and of any Claude Code execution. Upstream of
[[spine]] and [[peer-review]], which should read the ledger to see which support is real, which is
correlated, and which verdicts were retracted. [[conductor]] consults the ledger to decide whether a
claim is solid enough to build on or needs another pass.
