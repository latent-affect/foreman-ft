# Validation — dev-harness Quality, Security and Efficiency Remediation, 2026-09-02 (re-run)

Persona: Priya Desai. Session: dev-harness-33. Independent of the PRD/design-and-scope author
(dev-harness-run2-01), the concept-gate reviewer (dev-harness-9b), and the implementer of
DEVH-10/11/19/22 below — this session verified and falsified other sessions' claims all night
(R24, R1a/R2/R3, Check 6) but authored none of the scope, design-and-scope, or implementation
work being checked here.

**This is a re-run of the first pass, not a fresh review.** The first pass (same file, same
session) found four gaps: R4, R5, R18, R22 — each real, high-confidence, unblocked, and never
done. All four have since landed: DEVH-19 `8057574` (R18), DEVH-22 `683770c` (R22), DEVH-10
`32955d8` (R4), DEVH-11 `fcbcca6` (R5). Re-verified all four by re-running real code and building
independent negative controls, not by reading the commit messages. What follows is the delta from
the first pass plus the parts of the original report still relevant; see git history for the full
first-pass text if wanted.

**Input substitution, unchanged from the first pass.** No `SCOPE.md` exists; `PRD.md` sections 5/6
stand in, per the same dispatch. See the first pass for the full reasoning.

---

## Done-state check: M1–M7, updated

| # | Metric | Status | Change from first pass |
|---|---|---|---|
| M1 | Guard dogfooding | **MET** | Unchanged. |
| M2 | Write-time scrub | **MET** | Unchanged. |
| M3 | Reproducibility | **MET** | Unchanged. |
| M4 | Signal honesty | **MET (new)** | `foreman_quality_baseline.py` now detects degeneracy from the real churn distribution (`len(set(churn_values)) <= 1`), not a hardcoded special case, and emits `hotspot_signal_complexity_only` (relabeled field, header, and `meta.hotspot_signal_note`) instead of silently naming a two-input signal that only carries one. Ran the tool fresh against the current tree: churn is no longer degenerate (49 commits now vs. 7–10 when R18 was filed), so the live report correctly takes the non-degenerate branch — I could not observe the relabeling fire on live data for that reason, so I ran the fix's own dedicated test suite instead: `tests/test_hotspot_signal_relabel.py`, 4/4 pass, covering both a synthetic degenerate fixture (relabeling fires) and a non-degenerate fixture (byte-for-byte old behavior preserved). |
| M5 | Quality movement | **MET** | Unchanged. |
| M6 | Traceability | **N/A, correctly deferred** | Unchanged. |
| M7 | Inventory completeness | **MET (new)** | `docs/verbatim-persistence-inventory.md` exists: 72 lines, 10 classified entries, every one citing a real file and line, none unclassified. Read in full. Contains a self-correction the authoring session made about its own claimed evidence (a stale file count for `repo_context_part_*.txt`, corrected in the same table rather than left wrong) — a good sign, not a defect. |

**Overall done-state: MET.** All seven success metrics are now MET or correctly N/A. This is a
material change from the first pass's PARTIALLY MET.

---

## Re-verification of the four landed fixes (not trusting the commit messages)

**DEVH-19 / R18.** Read the implementation directly (`foreman_quality_baseline.py:416-505`) and
ran `tests/test_hotspot_signal_relabel.py` (4/4). The degeneracy check is computed from the real
distribution, not hardcoded, which is what makes it correct if this tree's history ever gets
squashed again. Confirmed MET.

**DEVH-22 / R22.** Read `docs/verbatim-persistence-inventory.md` in full (not spot-checked).
10/10 entries classified, each with a file:line citation. `docs/GOALS.json` deliberately does not
carry criteria for this document's *content* (its own out-of-scope note says so explicitly — it
only guards that writes to `docs/` can't perturb the DDL extraction contract), so there is no
formal results[] entry anywhere for R22 itself. The content satisfies PRD.md's own stated
verification for R22 ("the inventory exists, every entry cites a file and line... not unclassified")
by direct inspection; the absence of a tracking GOALS.json is a minor traceability gap, not a
functional one — noted below, not blocking.

**DEVH-10 / R4.** Ran the committed regression test
(`tessera/store/tests/test_sql_identifier_safety.py`, 2/2 pass) and confirmed all four `# nosec
B608` annotations exist at real locations with real content (`store.py:478-479`, `store.py:1314`,
`store.py:1332`, `replay_handlers.py:154` — the criterion's own text cites slightly stale line
numbers, 479/1308/1320, from before a later edit shifted them again; verified by content, not by
trusting either set of numbers). Then built my own negative control rather than trust the
commit's claimed one: copied `tessera/store/` and `tessera/common/` to an isolated scratch
location, stubbed the `PRIORITY_LIKE_FIELDS` guard to `if False:`, re-ran the same committed test
— it fails (`KeyError: 'not_a_real_field'`, since the guard no longer intercepts before the
dict lookup), confirming the test is not vacuous. Full `tessera` suite: 211/211. Confirmed MET.

**DEVH-11 / R5.** This is where the orchestrator's flagged concern lives, and it needed real
checking, not a read-through.

Ran `detect-secrets audit .secrets.baseline --report --json` (read-only) against the committed
baseline: **14 total entries, 2 `VERIFIED_FALSE` (both `tessera/tessguard/config.py`), 12
`UNVERIFIED` across 6 other files** — matches the commit's own disclosed count exactly. R5's own
verification clause, read directly from PRD.md, is explicitly file-scoped: *"a detect-secrets run
over the repo reports zero unaudited findings in `tessera/tessguard/config.py`"* — not a
whole-repo claim. Checked: zero unaudited findings in that one file. **R5 is MET as PRD.md
actually specifies it.**

The 12 unverified findings in the other 6 files are real, but they are new information — nothing
in PRD.md ever named a whole-repo detect-secrets surface, because detect-secrets wasn't even
installed before this ticket. They are a byproduct of installing the tool for R5's own narrow
purpose, not a regression and not part of what R5 asked to be done. Framing this the way root
`GOALS.json`'s own `C1` `reproduction_gap` is framed — disclosed, not silently dismissed, and not
folded into a MET/NOT MET verdict for a requirement that never covered it: **this is a new,
disclosed, unticketed gap**, not a defect in DEVH-11's own work. Whoever owns secret hygiene
repo-wide should pick it up as its own item; it is not this stage's routing target because it was
never a requirement PRD.md scoped.

**One process note, not a code defect, caught while verifying this:** `detect-secrets scan
--baseline .secrets.baseline` rewrites the baseline file in place when run — I ran it once during
this check, it modified the tracked `.secrets.baseline` (7 insertions/5 deletions, picking up
drift from other sessions' concurrent edits to `.foreman/*.json` files), and I reverted it
(`git checkout -- .secrets.baseline`) immediately rather than leave an unintended change sitting
in the tree. `detect-secrets audit ... --report --json` is read-only and is what I used for the
actual verification above. Worth knowing for whoever next touches this file.

`tessera/GOALS.json` had no `results[]` entries for the new `C6`/`C7`/`C8` criteria DEVH-10 and
DEVH-11 landed against — the same "verified for real, never written back" pattern already caught
on `bollard`, `atlas`, root, and `monitoring` earlier tonight, this time in `tessera` itself.
Backfilled directly with the re-verification evidence above rather than left for a sixth instance.

---

## Out-of-scope items: unchanged from the first pass

Re-checked nothing new here since the first pass — R14/R15, the blanket-refactor non-goal,
`cli.py`, and `safety.jsonl` were all confirmed clean then and none of the four fixes above touch
any of them. No new absorption risk introduced by DEVH-10/11/19/22.

---

## Assumed-in-scope items: updated

R4, R5, R18, R22 — all closed, verified above. **New, smaller items surfaced by this pass, named
rather than left implicit:**

- **12 unverified detect-secrets findings across 6 files, repo-wide.** Real, disclosed above, not
  part of any existing requirement's scope. Recommend a ticket, not a blocker.
- **R18's fix has real tests but no GOALS.json tracking it anywhere** (`root/GOALS.json` and
  `tessera/GOALS.json` each explicitly exclude it from their own scope). The code is verified
  correct by direct testing; the traceability mechanism (M6's own concern, one level early) has a
  gap for this one item. Minor, not blocking — noted so it isn't mistaken for "no freeze needed"
  by a later reader.

Neither is large enough to change the overall verdict.

---

## Verdict

**MET.** All seven success metrics are satisfied or correctly deferred; the two prior gaps (R4/R5,
R18, R22 — four items) are closed and independently re-verified, not merely reported closed. No
out-of-scope absorption. The two new, small items above (repo-wide secret-hygiene residual, R18's
missing GOALS.json tracking) are named rather than silently carried forward, and neither blocks
this stage.

---

## Deployment checklist (this skill's closing section)

**Closed for the write-path/read-back properties this pass can check locally**: R8's write-temp →
disk-verify → atomic-publish chain (`atlas/GOALS.json` C1–C4) and R23's non-clobbering guarantee
(root `GOALS.json` C3–C4) both have real, independently re-run end-to-end evidence, not
unit-test-only coverage. Writing `.foreman/frozen.json` is appropriate now on this report's own
criteria.

```
.foreman/frozen.json — not written by this report; recommend the freeze holder (priya-desai,
design-and-scope session) write it, since this session's role tonight has been independent
verification rather than holding the freeze pen.
```

**Before `foreman:ship-readiness`:** `foreman:security-privacy-review` (Nadia Osei) is still owed
— R7's STRIDE pass over `atlas/mcp/server.py`, explicitly deferred to that stage by PRD.md itself,
has not run. Not a gap in this report; it was never this stage's job.
