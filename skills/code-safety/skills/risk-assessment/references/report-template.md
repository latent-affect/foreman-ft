# Report template and output bundle

Defines the deliverable. Produce both a zip bundle and a standalone Markdown report. Keep the prose direct; this is a working document, not a brochure.

## Report section structure

Use this order. It mirrors the method: frame → analysis → decision → next steps.

```
# Security & Privacy Risk Assessment: [Subject]

**Date:** [today, with year]
**Subject type:** [software / system / service]
**Assessor:** Claude (framework-grounded; not a substitute for a licensed professional engagement)
**Frameworks:** NIST CSF 2.0 · NIST SP 800-30 Rev 1 · CIS RAM v2.1 · CIS Controls v8.1

## 1. Executive summary
- Overall posture in 3–5 sentences, framed by CSF Functions.
- The top 3–5 risks by score, named plainly.
- The single most important next step.

## 2. Scope and characterization
- Subject, version/identifier, purpose.
- Data handled and where it flows.
- Trust boundaries and permissions.
- Interested parties (who is harmed if this fails).
- Where the acceptable-risk line was set, and why.

## 3. Coverage map (CSF 2.0)
- Table of the six Functions: covered / partial / gap / N-A, one line each.
- Named blind spots.

## 4. Knowledge-gap findings
- CVEs, advisories, maintenance status, with dates and sources.
- "None found as of [date]" is a valid and important result — state it.

## 5. Risk register
- The table (columns below). Every threat event is one row.

## 6. Findings detail
- One subsection per above-the-line risk: the threat narrative, the per-area Impact reasoning, the recommended safeguard with its CIS Safeguard number and IG, residual score, burden, reasonable y/n, and validation method.

## 7. Next steps / remediation roadmap
- Prioritized list, grouped: Do now · Plan · Accept with rationale · Watch.
- Each item: action, CIS Safeguard, effort, residual risk, validation method.

## 8. Methodology and limitations
- The three-layer method in two sentences.
- The single-likelihood integration (800-30 produces it, CIS RAM consumes it).
- The validation-status floor.
- Honest limitations: what was assumed, what could not be verified, what would change the picture.
- Note that this is a structured analysis, not a penetration test or a legal due-care opinion.
```

## Risk register columns

CSV with these columns, in this order:

```
ID, Subject/Asset, CSF Function, Threat Source, Threat Event, Vulnerability/Condition,
Impact Area (highest), Expectancy (1-5), Impact (1-5), Risk Score, Band,
Validation Status, Acceptable (Y/N), Recommended Safeguard, CIS v8.1 Safeguard, IG,
Residual Expectancy, Residual Impact, Residual Risk Score, Burden (1-5), Reasonable (Y/N), Next-step Class
```

- `Band` is Acceptable / Elevated / Unacceptable.
- `Next-step Class` is Do now / Plan / Accept / Watch.
- Leave residual and safeguard columns blank for rows that are already Acceptable (no treatment needed); say so rather than inventing a safeguard.

## Bundle layout

Assemble in a working directory, then zip:

```
[subject-slug]-risk-assessment/
├── risk-assessment.md        # the full report (section structure above)
├── risk-register.csv         # the register
└── methodology.md            # short note: frameworks, versions, scoring model, date
```

Then:

1. Write the standalone `risk-assessment.md` to `/mnt/user-data/outputs/` as well (the portable copy for Claude Code).
2. Write the zip to `/mnt/user-data/outputs/[subject-slug]-risk-assessment.zip`.
3. `present_files` with the **zip first**, then the standalone Markdown.

Keep the slug short and filesystem-safe (lowercase, hyphens, no spaces or quotes).

## Tone

Score honestly. A low-risk verdict stated plainly is more useful than inflated findings, and an unavoidable high risk named clearly is more useful than a reassuring hedge. If the honest result is "this is fine, adopt it," say that and show the work. If it is "do not run this," say that too.
