---
name: brief
description: Records a named persona's pre-implementation requirements for one component as a real, cited ticket, before any implementation file is written -- REQ-45's fix for the gap between "a gate opened" and "the agent about to write code actually holds the applicable requirements." Use when `pre_implementation_brief_gate.py` denies a write for a component with no brief recorded, or proactively once REQ-44's dispatch rule has picked the persona who will judge this component's eventual gate. Layered qualification, not a new persona -- this is a lightweight mode of an existing persona (Priya, Clint, Dana, Marcus, or Nadia, whichever REQ-44 names), not a standalone reviewer.
---

# foreman:brief

A gate that opens is not a briefing. `pre_implementation_brief_gate.py`'s own docstring is blunt
about the gap this closes: once `architecture_gate.py` opens, the implementing agent is trusted to
have correctly read and internalized however much of `ARCHITECTURE.md` and `QUALITY-BAR.md` apply
to the component it's about to touch, with nothing checking that the specific applicable
constraints are actually in its own working context. This skill is the checked step.

## What this is, and what it deliberately is not

**Communication only.** This is not a review. It renders no verdict, finds no defects, blocks
nothing by itself. It states, plainly and specifically, what the persona who will eventually judge
this component's gate is going to check — so the agent that writes the code holds the same
standard the agent that judges it will use.

**Not a new persona.** Run this as a lightweight mode of whichever persona REQ-44's dispatch rule
names for this component's eventual gate — Dana Okafor for a component whose next real gate is
execution/integration-test verification, Marcus Webb for one headed toward ship-readiness, Clint
Eastwood for one still at architecture, and so on. The brief and the eventual judgment must be the
same standard, stated once, not two texts that can drift apart — that's the whole reason this isn't
its own reviewer.

**May run as an in-process Agent-tool subagent**, unlike every review dispatch in this project.
`FORE-206`'s blanket deny on in-process dispatch exists because a subagent judging its own
session's work isn't independent judgment. A brief renders no judgment — it's the same context
stating a standard to a different (often later, sometimes different-session) implementer. The
independence concern doesn't apply, so the cheaper mechanism is the right one here. Judge mode,
when this same persona is actually dispatched to review the finished work, still requires a real
peer session, unchanged.

## What to actually do

1. Identify the component and confirm which persona REQ-44 names for its eventual gate.
2. Read that persona's own file in full, plus the sections of `ARCHITECTURE.md` and
   `QUALITY-BAR.md` that apply to this specific component — not a general summary, the actual
   constraints this component will be judged against.
3. File a real ticket stating those constraints plainly, in the persona's own voice — what must be
   true, not a restatement of the whole document. State it as a requirement the eventual review
   will check, since it is one.
4. Write `.foreman/briefs/<component>.json`:
   ```json
   {"ticket_id": "<REAL-TICKET-ID>", "persona": "<persona-file-name>"}
   ```
5. Update that component's `GOALS.json` to cite the same ticket ID somewhere in its frozen
   criteria. The gate checks this citation exists — it's what keeps the brief and the frozen
   criteria from becoming two documents that can silently disagree.

## What this does not do

It does not verify the eventual code conforms to the brief — that's the judging persona's real
review, later, same as it always was. A brief that was read and ignored is a different, more
visible failure than a brief that was never given; this skill only guarantees the second failure
mode can't happen silently. Do not treat a filed brief as evidence of conformance — it's evidence
the agent was told, nothing more.

Origin: REQ-45, `foreman-v2/docs/PRD.md`, 2026-08-29 — written the same night the gap was found
live reading `architecture_gate.py`'s own docstring, and the operator's explicit design call on
layered-qualification-over-new-persona and subagent-for-brief-mode-only.
