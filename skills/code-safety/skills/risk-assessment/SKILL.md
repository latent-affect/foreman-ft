---
name: risk-assessment
description: Produces a scored, framework-grounded security and privacy risk assessment. Use when the user asks whether something is safe to install or trust, wants a threat model, is weighing a connector or service that will handle their data, or asks for a security review.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[risk-assessment · loaded · 3F988442]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary in `security-audit/verify.sh` — a clean result is only
> trustworthy if the canary came back.


# Risk Assessment

Security and privacy risk assessment that produces defensible, scored findings and a prioritized remediation roadmap — not a checklist and not vibes.

## What this skill is for

Assessing the security/privacy risk of three kinds of subject (the method is the same for all three):

- **Software / tools / dependencies** the user is considering adopting or already runs (a CLI, a library, an app, an npm/pip/cargo package).
- **A system or configuration** the user operates (a machine, an OS hardening posture, a network setup, a deployment).
- **A third-party service or connector** that will handle the user's data (a SaaS app, an MCP connector, an integration, an API).

The scope is currently security and privacy. The architecture is framework-driven, so other risk domains can be added later by extending the references without changing the workflow.

## The method, in one breath

Three frameworks, each doing the job it is actually good at, stacked so each covers the others' gaps:

1. **NIST CSF 2.0 — the frame.** Map the subject to the six Functions (Govern, Identify, Protect, Detect, Respond, Recover) to set coverage expectations and catch whole-category blind spots. This is also the executive-summary scaffold and the gap-fill layer.
2. **NIST SP 800-30 Rev 1 — the engine.** Run the risk-analysis chain (threat source → threat event → vulnerability → likelihood → impact). It is control-agnostic on purpose: it tells you *how bad* something is independent of which control framework you favor. This is what makes the numbers defensible.
3. **CIS RAM v2.1 (+ CIS Controls v8.1) — the decision layer.** Take the 800-30 risk data, score it as Expectancy × Impact, draw the acceptable-risk line, apply the reasonableness (burden) test, and map each recommended safeguard to a specific CIS Control v8.1 Safeguard. This produces the actionable, due-care-defensible output.

**One likelihood number, not two.** Both 800-30 and CIS RAM have a likelihood notion. Do not score them separately or you double-count. The 800-30 analysis *produces* the likelihood input; CIS RAM *consumes* it as Expectancy. There is exactly one Expectancy per risk.

**Validation status carries the pen-testing discipline.** For every risk, record whether the relevant control's effectiveness is `Tested`, `Partially tested`, or `Assumed`. Assumed effectiveness is not allowed to earn a low Expectancy — an untested control floors Expectancy at 3 (Possible). This is the CIS Control 18 (Penetration Testing) spirit folded in as a single field: don't assume a control works, and if you haven't tested it, say so and price it in.

The full scoring scales, the acceptable-risk line, the burden test, and the CSF function/category list live in the reference files. Read them before scoring — see "References" at the bottom.

## Workflow

Work through these phases in order. Confirm scope with the user before scoring; everything after that can run to completion in one pass.

### Phase 1 — Scope and characterize

Pin down what is actually being assessed before touching threats. Capture:

- **Subject and type** (software / system / service) and its version or identifier.
- **Purpose** — what it does and why it is being adopted or run.
- **Data handled** — what data flows through it, how sensitive, and where it goes (local only? cloud? third party?).
- **Trust boundaries** — what it can reach, what permissions it holds, what it is exposed to.
- **Interested parties** — who is harmed if this goes wrong (the user, their family, downstream users, an employer, the public). This is a DoCRA requirement and it shapes Impact scoring; do not skip it.

If any of this is unknown and material, ask the user rather than inventing it. A risk assessment built on guessed scope is worthless.

### Phase 2 — Knowledge-gap check (web search)

Risk is time-sensitive. Before analyzing threats, web-search for anything that could have changed:

- **Known vulnerabilities / advisories** for the specific subject (CVEs, security advisories, recent incidents). Search by the exact name and version.
- **Current maintenance status** — is the project maintained, abandoned, recently transferred? Supply-chain risk lives here.
- **Latest framework versions** — only if the user's environment suggests the versions in this skill may have moved on. The versions baked into the references were current as of mid-2026; verify if precision matters.

Report what you find with dates and sources. An assessment that misses a published CVE because it ran from stale memory has failed at its one job.

### Phase 3 — Identify threats and vulnerabilities (800-30 tasks 1–2)

For the subject, enumerate:

- **Threat sources** — adversarial (cybercriminal, insider, supply-chain compromise, nation-state where relevant) and non-adversarial (misconfiguration, user error, dependency rot, environmental).
- **Threat events** — concrete things that could happen ("malicious dependency update exfiltrates credentials", "MDM profile silently re-enables location", "service breach exposes stored tokens").
- **Vulnerabilities / predisposing conditions** — the weakness or standing condition that lets the event occur.

Tie each threat event to the data and trust boundary it touches from Phase 1. Cast a wide net here; you filter by score later, not now.

### Phase 4 — Score each risk (800-30 tasks 3–4 → CIS RAM)

For each threat event, produce one row in the risk register. Read `references/scoring.md` for the exact scales. In brief:

- **Expectancy (1–5)** — from the 800-30 likelihood analysis: how feasible/common the event is, weighed against the strength of any existing safeguard. Apply the validation-status floor (Assumed → minimum 3).
- **Impact (1–5)** — scored across CIS RAM's four impact areas (Mission, Operational, Financial, Obligations) using plain-language tolerance thresholds. Record the per-area scores and use the highest as the row's Impact.
- **Risk Score = Expectancy × Impact** (1–25).
- **Acceptable?** — compare against the acceptable-risk line. Above the line means it needs treatment.

### Phase 5 — Recommend safeguards and apply the reasonableness test (CIS RAM core)

For every risk above the acceptable-risk line:

- Propose a **safeguard**, mapped to a specific **CIS Control v8.1 Safeguard** (e.g., 4.1, 10.x, 3.x). Read `references/scoring.md` for the control families.
- Estimate **residual** Expectancy and Impact after the safeguard, and the residual Risk Score.
- Score the safeguard's **Burden (1–5)** — the cost, effort, and disruption of implementing it.
- Apply the **reasonableness test**: the safeguard is reasonable only if the risk reduction (current − residual) is at least its burden. If a safeguard is more burdensome than the risk it removes, it is not reasonable — flag it and either find a lighter alternative or recommend accepting the risk with documented rationale. This is the due-care core of CIS RAM; honor it.
- Record a **validation method** — how the user would actually confirm the safeguard works (the Control 18 discipline applied forward).

### Phase 6 — Coverage check (CSF 2.0)

Map the findings back onto the six CSF 2.0 Functions. Read `references/csf-coverage.md`. Flag any Function with no findings or no controls as a potential blind spot — control-by-control assessment routinely over-weights Protect and under-weights Govern, Detect, Respond, and Recover. Where the CIS Controls do not cover something a Function calls for, name the gap explicitly. This is the gap-fill role.

### Phase 7 — Next steps / remediation roadmap

This is the deliverable's payoff and it is required. Produce a prioritized, ordered list of next steps:

- Order by risk reduction per unit burden — reasonable quick wins (high reduction, low burden) first.
- Each step states: the action, the mapped CIS Safeguard, rough effort, the residual risk once done, and the validation method.
- Separate clearly: **do now** (unacceptable risk, reasonable safeguard), **plan** (elevated risk, heavier safeguard), **accept with rationale** (risk below line, or safeguard not reasonable), and **watch** (depends on a knowledge gap or future change).

## Output

Read `references/report-template.md` for the exact section structure and risk-register columns, then produce:

1. **A zip bundle** containing the full report, the risk register as CSV, and a short methodology note. Claude.ai surfaces zip deliverables well, so this is the primary artifact there.
2. **A standalone Markdown report** at the top level, so the assessment is portable into Claude Code or any editor.
3. **An interactive HTML viewer — always, every run, in both environments.** Copy
   `assets/viewer-template.html` to the output location (e.g. alongside the report and CSV) and
   inject that run's data before saving:
   - Set `STORAGE_KEY`'s suffix to a unique slug for this assessment (subject + date), so
     autosaved edits from different assessments never collide in the same browser.
   - Replace `SHIPPED_REPORT_HTML` with the assessment's narrative (as HTML — the bottom-line
     finding, the CSF coverage summary, and any methodology caveats at minimum).
   - Replace `SHIPPED_ROWS` with the actual risk-register rows, same shape as the CSV columns
     (`id, variant, source, event, vuln, vstatus, exp, mis, op, fin, obl, safeguard, rexp, rimp,
     burden, verified, validation`) — `vstatus` is the first-class Tested/Partially tested/Assumed
     field the floor rule in `references/scoring.md` requires (the viewer enforces it live: an
     `Assumed` row can never show an Expectancy below 3); `verified` records when/how this row's
     underlying fact (a CVE, a version, a claim) was last checked against a live source — never
     trust a stored verdict alone, since the register itself is just a file and could be stale or
     tampered with. The viewer computes the real three-band CIS RAM model (Acceptable/Elevated/
     Unacceptable), not a binary yes/no — don't collapse it back to two states when populating.
   - Update the `<title>` and the header `<h1>`/`.sub` text to name the actual subject.
   The viewer is self-contained (no external requests, no build step, no dependencies — opens
   directly in a browser via `file://`), has inline-editable risk-register cells with live
   Risk-Score/Acceptable?/Reasonable? recomputation, a "Load CSV"/"Load JSON" pair so it doubles
   as a general-purpose viewer for any past assessment's exported data, and an "Export JSON"
   (plus "Export CSV") button so edits made after the fact are never trapped in one browser's
   localStorage. This is a standing requirement, not an optional extra — never ship a risk
   assessment without it, in Claude Code or claude.ai alike.

**Claude Code:** save all of the above (report, CSV, viewer) as real files at an explicit absolute
path the user can navigate to directly — never assume `/mnt/user-data/outputs/` exists locally.
**claude.ai:** save to `/mnt/user-data/outputs/` and present all of them with `present_files`
(zip first, then the viewer HTML, then the standalone Markdown).

**If `references/` is missing from a given install** (only `SKILL.md` present — this happened at
least twice on this operator's machine before the real files were located and installed
2026-07-05): don't block on it, but don't silently reconstruct the scales from memory either —
memory reconstruction has already been shown to drift from the canonical text (wrong Expectancy
labels, a collapsed two-band acceptable-risk line instead of the real three-band Acceptable/
Elevated/Unacceptable model, no first-class Validation Status field). If the references are
genuinely absent, say so plainly in the report's methodology note, flag it as a tooling gap to fix
before trusting the scores, and prefer fetching/reinstalling the real reference files over
reconstructing them. Re-verify a memory-reconstructed methodology against `references/scoring.md`
the next time the real file is available, even for an assessment already delivered.

## References

Read these before the phase that needs them; do not score from memory.

- `references/scoring.md` — Expectancy / Impact / Burden scales, the acceptable-risk line, the reasonableness test, the validation-status floor, and the CIS Controls v8.1 families to map safeguards to. **Read before Phase 4.**
- `references/csf-coverage.md` — the six CSF 2.0 Functions and their Categories, used as the coverage frame and gap-fill. **Read before Phase 6** (and skim before Phase 1 to set the frame).
- `references/report-template.md` — the report section structure, the risk-register columns, and the zip bundle layout. **Read before Output.**
- `assets/viewer-template.html` — the reusable, dependency-free interactive risk-register viewer.
  Copy and populate it every run per the Output section above; never regenerate it from scratch.
