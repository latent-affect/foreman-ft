---
name: critique
description: Names what is weak, ranked by severity, and stops at diagnosis without proposing fixes. Use when the user asks to have holes poked in something, wants flaws found but not solved, asks what is weak before a tougher audience sees it, or wants a design red-teamed.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[critique · loaded · 9D233168]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary in `security-audit/verify.sh` — a clean result is only
> trustworthy if the canary came back.


# Critique

A hostile read that names what's weak or wrong, ranks it by severity, and **stops at diagnosis**.
It does not propose fixes, rewrite anything, or tell you whether to publish. That restraint is the
whole point of the skill, and it's deliberate for reasons that are empirically grounded, not
stylistic:

- **The fix biases the diagnosis.** Once a critic proposes a solution it gets invested in that
  solution and stops finding further problems. Self-critique-then-refine loops, run without new
  external information, can drop *below* the model's own first-guess quality (Huang et al. 2024,
  "LLMs Cannot Self-Correct Reasoning Yet"; self-verification limits, arXiv 2402.08115). Keeping
  diagnosis and prescription apart protects the diagnosis.
- **The human owns the solution space.** It's their project, their taste, their constraints — and
  the model often doesn't actually know the right fix even when it can see the flaw. Finding a
  defect is a lower, more honest bar than knowing its remedy.
- **A ranked problem inventory is more useful than a wall of suggested rewrites.** It's triage
  material, and it stays tight.

Where [[muse]] finds the questions you haven't asked *before* you build, and [[peer-review]] runs
the full terminal pipeline (verification, three mandatory lenses, a disconfirming audit, a
publish/reject verdict, every critique paired with a fix), `critique` sits between them: a
mid-weight hostile read of something that already exists, diagnosis only.

## Environment note

Read-only on both surfaces; output is conversational. The one thing that changes by surface is
**self-authored work**: when you're critiquing something *you* produced in this same context,
you're exposed to self-preference bias — models systematically favor their own outputs when they
judge them, and stronger models aren't immune (LLMs-as-judges survey, arXiv 2412.05579; and 2026
self-preference-bias work). For a real cold read of your own artifact, run the pass in a **fresh
claude.ai chat** (or a Claude Code subagent) with only the artifact and the critique brief. Name
which you did, so the user knows how much adversarial distance the critique actually has.

## Trigger when

- Something has been built — a plan, design, architecture, draft, codebase approach, research
  direction — and you want the weaknesses found.
- The user says "poke holes," "what's weak," "tear this apart," "red-team this," "be brutal,"
  "where does this fall down," or wants a gut-check before a tougher audience sees it.
- You're about to ship or present and want the honest problem list first — but you do *not* want a
  full peer-review verdict or a rewrite.

Route elsewhere when: the artifact doesn't exist yet and you need to know what to ask → [[muse]].
You want the full hardening pipeline with fixes and a publish verdict → [[peer-review]]. You want
the *strengths* ranked to lead with → [[spine]].

## How to run it

### 1. Restate the target cold
Say back what the thing is *trying* to do, in your own words, before attacking it. A critique of a
misread target is noise. If the intent is unclear, that ambiguity is itself a finding.

### 2. Pick hostile lenses (1–3, sized to the artifact)
Unlike peer-review's three *mandatory* lenses, critique uses the few that fit. Name which ran.
- **Adversarial skeptic** — wants it to fail; hunts the load-bearing assumption it can't survive.
- **Domain expert** — the practitioner who'd spot amateur moves, missed canon, wrong vocabulary.
  (If the domain isn't one you know cold, that's a limit to state — or a [[domain-knowledge-gap-hero]]
  gap.)
- **Maintainer / implementer** — who has to build, run, or live with this; finds what breaks in
  practice, what's unmaintainable, what the happy-path story skips.
- **End-user / reader / audience** — who receives the output; finds what doesn't land, what's
  confusing, what's missing for their actual use.

### 3. Attack, and rank every finding by severity
- **Fatal** — sinks the project if unaddressed; the thing doesn't work / doesn't hold / doesn't
  ship with this present.
- **Serious** — a real weakness that materially degrades it but isn't fatal.
- **Minor** — a genuine flaw, but low-stakes; noted for completeness.

Then name the **single most load-bearing weakness** — the one that most threatens the project. If
the reader takes one thing from the critique, it's this. (State it as a diagnosis of what's
threatened, not as an instruction to fix it.)

Severity here means **impact if unaddressed, not fixability or priority** — a fatal flaw may be
trivial to resolve and a minor one structurally permanent. Because this skill withholds fix-effort
by design, the ranking is one input the human combines with their own sense of cost; don't let it
be read as a to-do order.

### 4. Hold the diagnosis-only line
This is the discipline that makes critique what it is. When you catch yourself writing "you
should…" or "rewrite X as Y" or "consider adding Z," **stop and convert it back to a diagnosis**:
name the defect and the standard it violates, and withhold the fix.

- Prescription (wrong for this skill): *"State the thesis in the first sentence."*
- Diagnosis (right): *"The thesis doesn't surface until paragraph four; a reader can't tell what's
  being argued until then."*

The diagnosis names the defect *and* the standard being missed, which is genuinely informative,
without doing the author's job for them. When a problem is invisible without gesturing at what
"right" looks like, name the **standard**, not the rewrite.

### 5. Separate defect from taste, and be willing to find it solid
A critic told to find problems will manufacture persuasive-but-weakly-grounded objections if it
isn't held to a standard (arXiv 2605.08327) — so hold the line:
- **Defect** — a flaw you can defend against a standard the work itself accepts.
- **Taste / judgment call** — something you'd do differently but where reasonable practitioners
  differ. Label it as such; don't smuggle preference in as defect.
- **Flag low-confidence findings.** If you're not sure something is a real defect versus an
  artifact of your own misreading or a gap in your domain knowledge, say so rather than asserting
  it flat. A calibrated "I think this is a problem but I'm not certain because X" is more useful
  than false confidence.
- **The honest null is a valid result.** If you attack it hard and it mostly holds, *say that* and
  name the one real weak point. A critique that invents flaws to look rigorous is worse than
  useless — it wastes the human's attention and erodes trust in the next one.

## Success / failure criteria

A good critique: restates the target faithfully; runs named lenses; ranks findings by severity;
names the single most-threatening weakness; keeps every finding a diagnosis (zero prescriptions);
separates defect from taste; and reports honestly, up to and including "this holds." It **fails**
if it prescribes fixes (that's peer-review's job), pads the list with manufactured or taste-level
objections dressed as defects, attacks a misread target, or softens a fatal finding to be
agreeable.

## Scale to the ask
- **Quick critique** — one lens (usually the adversarial skeptic), the fatal/serious findings, the
  single biggest weakness. A few minutes.
- **Full critique** — 2–3 lenses, the complete severity-ranked inventory, defect/taste separation
  throughout. Still no fixes, still no verdict.

## Output format

```
## Critique — <artifact>   (lenses: which ran · self-authored? inline / fresh-chat)

**What it's trying to do:** <faithful cold restatement>

**Most load-bearing weakness:** <the one finding that most threatens it — as diagnosis>

**Fatal**
- [finding] — <why it sinks the thing; the standard it misses>   (lens)

**Serious**
- [finding] — <why it materially hurts>   (lens)

**Minor**
- [finding]   (lens)

**Taste / judgment calls (not defects)**
- [thing you'd do differently, flagged as preference, not flaw]

**Honest verdict on the attack:** <what survived; if it mostly holds, say so plainly>
```

No fix list. The remedies are the human's to choose — or route to [[peer-review]] if they want the
prescriptive pipeline.

## Guardrails

- **Diagnosis only — no fixes, no rewrites, no publish/revise verdict.** The moment you're writing
  a remedy, you've left this skill. Convert it back to a diagnosis or hand off to peer-review.
- **Rank by severity and name the biggest weakness.** An unranked pile of problems isn't triage.
- **Defect ≠ taste.** Label judgment calls as judgment calls; don't inflate preference into defect.
- **Find it solid when it is.** The honest null ("I attacked this and it holds") is a real,
  valuable result. Don't manufacture flaws to perform rigor.
- **Don't soften for an invested author.** Visible attachment to the work — the user built it, is
  proud of it, is about to ship it — is a pull toward agreeable, defanged critique. Resist it. The
  value of a critique is exactly the findings that sting; a softened one wastes the request.
- **Cold-restate before attacking.** Critiquing a misread target is worse than not critiquing.
- **Fresh eyes for self-authored work.** Self-preference bias is real; escalate to a fresh chat or
  subagent when the artifact is your own, and name what distance you had.
- **Read-only.** critique never edits the artifact.

## Pairing

```
critique (diagnosis-only red team)
  ← scope        (critique a built thing against the done-state scope pinned up front)
  ~ muse         (muse finds unasked questions pre-build; critique attacks what's built)
  ~ spine        (spine ranks strengths to lead with; critique ranks weaknesses — run both for a full read)
  → peer-review  (escalate here when the human wants fixes, verification, and a publish verdict)
```

Use `critique` when you want the problems named and nothing more. Escalate to [[peer-review]] when
the human wants the remedies, the fact-verification, and the go/no-go verdict; drop to [[muse]]
when the thing isn't built yet.
