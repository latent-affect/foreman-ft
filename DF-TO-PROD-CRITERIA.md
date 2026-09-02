# DF-to-Prod promotion criteria

Authored by Marcus Webb (session `dev-harness-7f`) during dev-harness-run2's ship-readiness gate
review, 2026-09-02, in direct response to FORE-213 ("No defined DF-to-Prod promotion process").
Scoped to answer FORE-213 generally, not only this one build's own gate -- but authored from this
build's real evidence, per this project's own standing rule against defining criteria in the
abstract.

## Why this exists

A Go-to-dogfood charter (`ship-readiness` SKILL.md, REQ-42) intentionally does not require prior
human usage -- that's the circularity REQ-42 exists to break. But nothing currently defines what a
dogfood *period* has to produce before a Go-to-Production charter is even attemptable. Without
that, "dogfood for a while" has no exit condition, and a build can sit in dogfood indefinitely, or
get promoted on vibes. This is that exit condition, named once rather than re-derived per build.

## Required evidence, four types, none substitutable for another

### 1. Bug/ticket rate threshold

The dogfood period must show a **declining or stable** new-ticket rate for the build under test,
measured week-over-week from real TESSERA data (`tessera list --project <PREFIX> --created-after
<date>`), not estimated. A rising rate mid-dogfood is not itself disqualifying -- it may mean
dogfooding is working as intended, surfacing real gaps -- but Production promotion requires the
rate to have turned over and stabilized for at least one full measurement window before promotion,
not merely "some tickets got filed and closed."

**Concrete bar for this build's own precedent:** 53 real DEVH tickets in one dogfood-adjacent
session is a high rate against a small population of real usage hours -- that is expected for a
build's first exposure to scrutiny, not a red flag on its own. What would be a red flag: the same
rate persisting once initial-exposure findings are exhausted.

### 2. Quality-score progression, real data only

The SLOC-weighted health score trend this build already tracks: 6.74 (clean baseline) -> 6.86
(dev-harness) -> 7.12 (dev-harness-run2, post-R0-R24, real measured data, not projected). A
Production charter requires this trend to be **monotonically non-decreasing** across the dogfood
period, re-measured from a context that did not do the remediation being scored (this project's
own standing precedent, `foreman_quality_baseline.py`, ideally run by a fresh session each time to
avoid the self-grading bias this project has named and corrected for elsewhere tonight).

### 3. Security findings closed with none reopened

Every security finding raised during the dogfood period (STRIDE findings, credential-exposure
incidents, or anything Nadia Osei's review surfaces) must be closed with independently re-verified
evidence -- re-run, not re-read, matching this project's own fail-closed verification standard --
**and none reopened** during the remainder of the dogfood window. A finding that gets closed and
then reopens is a stronger signal against promotion than one that stays open and disclosed; masking
a real reopen by closing it a second time without root-causing the first closure's failure is
exactly the letter-vs-intent gap this project's gaming research keeps finding elsewhere, and this
criterion exists to make that failure mode visible rather than laundered by a second green
checkmark.

### 4. User testing, named as its own required evidence type -- distinct from code/AI review

**Explicit operator instruction, not a discretionary addition:** user testing sits alongside the
three DF metrics above, not folded into or replaced by them. Code review, AI-persona review, and
automated test suites structurally cannot substitute for this -- this project's own standing
example is the trim-slider UX bug that was code-review-clean and only surfaced when a human
actually used the feature (`ship-readiness` SKILL.md's own `dogfood_pass` rationale).

Required, concretely: at least one documented real usage session by a human distinct from anyone
who authored the build under review, covering the build's actual primary workflows (not a smoke
test), with findings recorded in TESSERA the same way any other finding is -- not folded into a
narrative report that never reaches the ticket ledger, which is the exact gap DEVH-3's own
transition history demonstrated tonight.

## Two governance items, required per this task's own brief, answered here

### Persona/skill-file review scope (FORE-254)

**Confirmed and checked, not assumed.** This review's own scope included checking whether FORE-254's
concrete instance (the Tier 1/Tier 3 inversion in `product-requirements/SKILL.md`) is still live.
It is not: the installed, machine-wide copy at `/Users/m5/.claude/skills/foreman/skills/product-requirements/SKILL.md`
carries a dated correction (2026-09-02) matching TESSERA's real schema (`TIER_PAPER_QUAL=1`
lightest, `TIER_FULL_NPI=3` heaviest). Separately noted, not necessarily a defect: this repo's own
in-tree `skills/foreman/skills/` does not carry a `product-requirements` or `security-privacy-review`
directory at all -- unclear whether that is deliberate scope (this repo ships only the stages it
actively maintains) or a real gap; not resolved here, flagged for whoever owns the skills tree.

**Going forward:** Marcus Webb's ship-readiness scope for any Foreman-managed build should
explicitly include a check of persona/skill files the build's own sessions actually loaded and
used during the build, not only application code in the repo's own diff -- because a shared,
machine-wide file's defect is invisible to any single project's own diff review by construction,
and this project already has one confirmed live incident of exactly that shape.

### Cross-project TESSERA write pattern (FORE-257 / TESS-182)

**Judgment, as requested:** the pattern found tonight -- an AREM-scoped session (`agent-remediation-af`)
writing directly into FORE, ATLASSN, and DEVH tickets with no session ever opened in those repos --
is **not acceptable going forward and needs tightening**, not just disclosure. This is the same
principle FORE-28 already states for this project generally ("cross-project content contamination
is structurally prohibited") applied to the ticket ledger specifically. A ticket's `actor`/`reporter`
fields being free-text and self-reported, with no check against the acting session's real
cwd/repo, means TESSERA currently cannot even detect this pattern, let alone prevent it -- which is
exactly what TESS-182 already proposes to fix (compare the acting agent's real cwd/repo against the
target project's registered `source_root`). This reviewer's recommendation: TESS-182 should land
before this build (or any build using multi-session orchestration this way) is treated as a settled
process pattern, not an incidental convenience. Until it does, any cross-project write should be
treated as an exception requiring explicit, logged justification, not the default mode of
coordination.

## What this document does not do

Does not itself gate anything mechanically -- no hook reads this file yet. It is the written
answer FORE-213 asked for; wiring it into `foreman:ship-readiness`'s actual Production-charter path
(a real `dogfood_metrics` block in `SHIP-CHARTER.json`, checked the same way the five existing
checks are) is follow-on work, not done here, and should get its own ticket rather than being
silently assumed complete because this document exists.
