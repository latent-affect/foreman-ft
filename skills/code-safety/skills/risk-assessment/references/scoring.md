# Scoring reference

The integrated scoring model. NIST SP 800-30 Rev 1 produces the likelihood; CIS RAM v2.1 consumes it as Expectancy and pairs it with Impact; the reasonableness test gates every safeguard. One likelihood number per risk, never two.

## Expectancy (1–5)

Expectancy is CIS RAM v2.1's term for likelihood — specifically, the estimation that *if* an incident occurred it would be due to the threat described. Derive it from the 800-30 analysis: weigh how feasible and how common the threat event is against the strength of any existing safeguard against it.

| Score | Label | Meaning |
|---|---|---|
| 1 | Rare | No known occurrence in comparable contexts; a strong, **tested** safeguard is in place. |
| 2 | Unlikely | Uncommon; a credible safeguard is present and largely effective. |
| 3 | Possible | Plausible; partial, weak, or unverified safeguard. |
| 4 | Likely | Common threat event; safeguard weak, partial, or easily bypassed. |
| 5 | Expected | Actively occurring, trivially exploitable, or no effective safeguard at all. |

### Validation-status floor (the Control 18 discipline)

Every risk records a validation status for the relevant control's effectiveness:

- `Tested` — effectiveness empirically confirmed (exploit attempted and failed, config verified, log proven to fire).
- `Partially tested` — some evidence, not comprehensive.
- `Assumed` — believed to work, never verified.

**Rule: `Assumed` floors Expectancy at 3 (Possible).** You may not claim Rare or Unlikely for a control you have not tested. If the user wants a lower Expectancy, the path is to test the control, not to assert it. This keeps paper compliance from masquerading as real protection.

## Impact (1–5), scored across four areas

CIS RAM scores Impact across four areas. Score each area that applies, in plain language, then use the **highest** area score as the row's Impact (and record the per-area detail in the finding).

The four impact areas:

- **Mission** — harm to the core purpose the subject serves the user (e.g., research integrity, ability to do the work).
- **Operational Objectives** — harm to day-to-day functioning (uptime, workflow, access to tooling).
- **Financial Objectives** — direct cost (fraud, replacement, lost work, API spend, remediation cost).
- **Obligations** — harm to others and to duties owed: privacy of family/downstream users, legal/contractual/license duties, public safety. DoCRA centers this; do not collapse it into "it's just my machine."

Plain-language magnitude scale, applied per area:

| Score | Label | Meaning |
|---|---|---|
| 1 | Negligible | Trivial, absorbed without notice. |
| 2 | Minor | Noticeable, recoverable with little effort. |
| 3 | Moderate | Real disruption or loss; meaningful effort to recover. |
| 4 | Major | Serious harm; significant cost, exposure, or duty breached. |
| 5 | Severe | Critical, lasting, or irreversible harm; significant harm to others. |

State the tolerance thresholds in concrete terms for the specific assessment (e.g., "Financial 3 = more than a week's API budget; Obligations 4 = a family member's personal data exposed"). Tuning the thresholds to the actual subject is what makes the score mean something.

## Risk Score

**Risk Score = Expectancy × Impact**, range 1–25.

## The acceptable-risk line

CIS RAM's central move: draw a line at acceptable risk. Below the line is within due care; above the line requires treatment. Default banding (tunable per assessment, and say so in the report):

| Risk Score | Band | Disposition |
|---|---|---|
| 1–6 | Acceptable | Within due care. No treatment required; monitor. |
| 8–12 | Elevated | Treat when a reasonable safeguard exists; plan it. |
| 15–25 | Unacceptable | Treat now with a reasonable safeguard, or formally accept with documented rationale. |

The line is a starting default. If the subject touches others' data or safety (high Obligations), lower the line. State where you put it and why.

## Burden (1–5)

Burden is the cost of the safeguard to the user — effort, money, disruption, lost utility. Same 1–5 shape: 1 trivial (a setting toggle), 3 moderate (a few hours plus some ongoing friction), 5 heavy (major rework, recurring cost, significant productivity loss).

## The reasonableness test

This is the due-care heart of CIS RAM. A recommended safeguard is **reasonable** only if:

**risk reduction ≥ burden**, where risk reduction = (current Risk Score − residual Risk Score), expressed on a comparable scale to burden.

In words: a safeguard must not be more burdensome than the risk it removes. If burden exceeds the reduction, the safeguard is **not reasonable** — do not recommend it as a must-do. Instead: find a lighter alternative, or recommend accepting the risk with written rationale. A recommendation that ignores burden is not a CIS RAM assessment; it is a wish list.

For each above-the-line risk, compute: current Risk Score, residual Risk Score (after the safeguard), risk reduction, burden, and the reasonable yes/no.

## Mapping safeguards to CIS Controls v8.1

Every recommended safeguard maps to a specific CIS Control v8.1 Safeguard (cite the number, e.g., 4.1). The 18 Controls, grouped:

**Basic (1–6):**
1. Inventory and Control of Enterprise Assets
2. Inventory and Control of Software Assets
3. Data Protection
4. Secure Configuration of Enterprise Assets and Software
5. Account Management
6. Access Control Management

**Foundational (7–16):**
7. Continuous Vulnerability Management
8. Audit Log Management
9. Email and Web Browser Protections
10. Malware Defenses
11. Data Recovery
12. Network Infrastructure Management
13. Network Monitoring and Defense
14. Security Awareness and Skills Training
15. Service Provider Management
16. Application Software Security

**Organizational (17–18):**
17. Incident Response Management
18. Penetration Testing

Common mappings by subject type:

- **Software / dependency:** 2 (software inventory), 7 (vulnerability mgmt), 16 (application software security), 15 (service provider, for the upstream), 10 (malware), 3 (data protection).
- **System / config:** 4 (secure configuration), 1 (asset inventory), 5/6 (account & access), 8 (audit logs), 11 (data recovery), 13 (network monitoring).
- **Third-party service / connector:** 15 (service provider management) is primary, plus 3 (data protection), 5/6 (account & access), 8 (audit logs where the service offers them).

CIS Controls v8.1 has 153 Safeguards across three Implementation Groups (IG1 = essential hygiene, IG2, IG3). For an individual or small operator, prefer IG1 safeguards unless the Obligations impact justifies reaching into IG2/IG3. Note the IG of each recommendation so the user can see how far up the maturity ladder it sits.

## Worked micro-example

Subject: an unmaintained npm package pulled in as a transitive dependency.

- Threat source: supply-chain compromise (adversarial).
- Threat event: a malicious version publishes and is auto-installed, exfiltrating environment secrets.
- Vulnerability / predisposing condition: package unmaintained 2+ years, no lockfile pinning, secrets present in env.
- Existing safeguard: none verified → validation status `Assumed` → Expectancy floored at 3; threat event is common in the ecosystem → Expectancy **4**.
- Impact: Obligations area highest (secrets could include another party's API key) → Impact **4**.
- Risk Score = 4 × 4 = **16 → Unacceptable.**
- Safeguard: pin and vendor the dependency, move secrets out of env into a secret store, add vulnerability scanning (CIS 7.x, 16.x, 3.x). Residual Expectancy 2, Impact 4 → residual **8**. Reduction = 8. Burden = 2. 8 ≥ 2 → **reasonable.**
- Validation method: attempt to install a pinned-bypass; confirm scanner flags a known-bad version in a test.
- Next step: **do now.**
