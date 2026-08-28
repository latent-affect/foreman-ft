---
name: muse
description: Surfaces the questions a plan has not thought to ask, using a fresh reviewer with no stake in the current framing. Use when the user asks what is missing, what is not being asked, what fresh eyes would notice, or is about to act on a working hypothesis.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[muse · loaded · 2366BB68]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary in `security-audit/verify.sh` — a clean result is only
> trustworthy if the canary came back.


# Muse

Read-only meta-skill: surface the knowledge gaps you haven't thought to ask about
yet. Where [[knowledge-gap-hero]] fills *known* gaps (you flagged X, go research X),
muse finds the *unknown* gaps — questions a senior investigator with fresh eyes
would notice that the current chain of reasoning has been glossing over.

Muse delegates to a subagent because the goal is to break the inertia of the
current session's framing. The same Claude that's been deep in a thread has the
same blind spots throughout; a fresh subagent reading a brief is more likely to
notice "you haven't asked about X" than self-reflection from inside the work.

## Trigger when

- A complex investigation has produced a working hypothesis and you're about to
  act on it — and you want a sanity-check on what the current frame might be missing.
- The user says "what am I missing?" / "what aren't we asking?" / "what blind spots?"
- After a major finding lands and *before* committing to a disclosure, packet, or
  reversible action — the moment when surfacing missed angles is highest-leverage.
- A long chain of findings has reinforced a single hypothesis and confirmation
  bias is a risk — a fresh subagent re-reads with no investment in the conclusion.

## What muse does NOT do

- It does NOT answer the questions it surfaces. That's [[knowledge-gap-hero]]'s job
  (for factual gaps) or yours (for methodology/judgment gaps).
- It does NOT write to the filesystem, persist memory, or modify state.
  Output is conversational only.
- It does NOT replace adversarial review or red-teaming. It's pre-research framing —
  "what should we be asking?" not "what's wrong with our answer?"

## Workflow

1. **Compose a tight brief** for the subagent. Include:
   - One-paragraph investigation summary (what's being investigated, why, what stakes)
   - The current working hypothesis or interpretation
   - The 3-5 key findings that have led there
   - Any prior research already done (so the subagent doesn't re-tread)
   - Explicit ask: *enumerate unasked questions and unexplored angles. Do NOT answer them. Target 2x what the main agent would surface unaided.*

2. **Spawn the subagent** via the `Agent` tool. Use `subagent_type: general-purpose`
   for broad questions, or a more specific agent if one fits (e.g., `code-reviewer`
   for code-investigation contexts). Do NOT use `Explore` — muse is not a code-search
   task; it's a framing-broadening task.

3. **Receive and triage** the subagent's list:
   - **Factual gaps** ("we don't know X about Y") → flag for [[knowledge-gap-hero]]
   - **Methodological gaps** ("the way you measured Z assumes...") → user judgment
   - **Alternative hypotheses** ("if not A, could it be B?") → adversarial thinking
   - **Missing artifacts** ("we haven't looked at Q which would distinguish R from S")
     → next collection step

4. **Report back to the user** with the full triaged list. Don't filter to what
   feels safe or comfortable; the value is in the gaps that sting.

5. **Suggest a sequence**: which gaps to fill first, and through which mechanism
   (knowledge-gap-hero / user input / new evidence collection).

## Guardrails

- **READ-ONLY**: never write to disk, never persist memory, never modify state.
  All output is conversational. The user can choose to persist findings via
  knowledge-gap-hero or their own notes.
- **Brief, don't dump**: the subagent gets a tight summary, not the entire
  transcript. The goal is fresh eyes, not full context absorption.
- **Don't filter the output**: if the subagent surfaces something uncomfortable
  for the current hypothesis, surface it verbatim. Confirmation bias is the
  failure mode muse exists to counter.
- **Don't double-count**: if the subagent surfaces questions you already raised
  in the session, note that as "already on the radar" but don't pad the list.
- **Match domain depth to subagent**: a general-purpose subagent will surface
  generalist gaps. For deep-specialist domains, prepend a context primer to the
  brief or note the limitation.
- **Cite the subagent**: when you report back, clearly attribute the gaps to muse
  ("muse surfaced..."), so the user knows these came from outside the main
  session's frame.

## Output format to the user

```
## Muse — gaps surfaced for review

**Factual gaps** (→ knowledge-gap-hero candidates)
- [question]
- [question]

**Methodological gaps** (→ your judgment)
- [question]
- [question]

**Alternative hypotheses** (→ adversarial thinking)
- [hypothesis]

**Missing artifacts** (→ next collection step)
- [what + why it would distinguish what]

**Already on the radar** (subagent re-raised, not new)
- [item]

**Recommended sequence**: [your synthesis]
```

## Pairing with knowledge-gap-hero

Natural pipeline:
1. **Muse** surfaces what to ask about (read-only)
2. **knowledge-gap-hero** answers what's askable from authoritative sources (writes
   findings to memory)
3. User decides what to do with what doesn't have an answer

Run muse *before* knowledge-gap-hero when starting a new investigative thread.
Run muse *after* knowledge-gap-hero when you've absorbed new facts and want to
re-check what the new picture might be missing.
