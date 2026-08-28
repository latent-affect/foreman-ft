---
name: conceptualize
description: Points a Foreman-track project at `scope` for its pre-architecture conceptualization pass, and closes the one real gap in that hand-off — `scope`'s own Pairing diagram doesn't name `foreman:architecture` as a downstream target. Use when a Foreman-track project has a rough idea and no `SCOPE.md` yet, or when you're about to reach for a bespoke conceptualization step because it looks like Foreman doesn't have one. Does not replace, wrap, or reimplement `scope` or `muse` — it routes to them.
---

# foreman:conceptualize

This skill was asked for as a "Foreman-flavored conceptualization conductor" on the premise that
`foreman:foreman` routes rough ideas straight into implementation stages with no formalized
pre-architecture step. That premise doesn't survive reading `scope`'s own `SKILL.md`: **`scope`
already runs `muse` internally, as step 5 of its own frame** ("Run muse on the scope-so-far...
fold real gaps back into the frame before locking it"). `foreman:foreman`'s routing table already
sends "I want to build X / rough idea, no scope yet" to `scope`, and its own Pairing diagram opens
with `scope (front-end project router) -> foreman:architecture`.

So the scope+muse combination that produced an earlier brief wasn't an ad hoc pairing someone
forgot to formalize. It's what `scope` already does when it's run properly. There is no missing
conceptualization engine to build.

**The one real gap:** `scope`'s own Pairing diagram (in its `SKILL.md`) lists its downstream
hand-offs as `conductor / storytelling / footing / creative` — the research/fiction/creative
lifecycle routers. It does not mention `foreman:architecture`, even though `foreman:foreman`
documents the reverse direction correctly. A session that reads `scope`'s docs alone, without
already knowing about Foreman, has no way to discover that a Foreman-track scope brief hands off
to `foreman:architecture` next. This skill exists to close that one hand-off, nothing else.

## Trigger when

- A Foreman-track project (`.foreman/` marker present, or about to be created via `foreman:init`)
  has a rough idea and no `SCOPE.md` yet.
- You catch yourself about to design a bespoke pre-architecture process because "Foreman doesn't
  seem to have one" — that instinct is the thing this skill exists to correct.
- `foreman:foreman`'s own routing table sent you here, or you're deciding what comes before
  `foreman:architecture`.

## Method

1. **Run `scope`.** Not a summary of it, not a lighter version — the real skill, full frame if the
   project is genuinely uncertain, right-sized per its own guardrails if it's small. Its step 5
   (muse) is not optional and not this skill's job to re-run separately.
2. **Persist the brief as `SCOPE.md`** in the project root, per `scope`'s own Claude Code
   environment note.
3. **Hand off to `foreman:architecture`.** That's the Foreman-specific step `scope`'s own docs
   don't name — say so explicitly when you do it, so the next session (or the next reader of
   `SCOPE.md`) doesn't have to rediscover this hand-off from scratch.

## Guardrails

- **Do not reimplement `scope` or `muse`.** This skill has no frame of its own. If you find
  yourself writing scope-like questions here instead of invoking `scope`, stop.
- **Do not treat this as a second muse pass.** `scope` step 5 already ran one. Re-running muse
  here would be redundant, not thorough.
- **This is a pointer, not a stage.** If `scope`'s own behavior ever changes such that it no
  longer includes a blind-spot sweep, or Foreman needs conceptualization behavior `scope` can't
  provide, that's a real design gap worth reopening — but it isn't this skill's job to guess at
  that gap preemptively.

## Origin

2026-08-21. Built after confirming, by reading `scope`'s and `muse`'s actual
`SKILL.md` files rather than inferring from `foreman:foreman`'s description of them, that the
originally-scoped "new pre-architecture conceptualization skill" was solving a gap that doesn't
exist. Recommendation on record in the original design discussion: treat the open Skill A
question as resolved by this thin bridge, not by a heavier standalone skill.
