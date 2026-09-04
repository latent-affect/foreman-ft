# Bollard / verdict_ledger.py drift survey (FORE-316), read-only

Performed 2026-09-04 by dev-harness-run2 at agent-remediation-3c's request, following the FORE-316
finding (eleven independently-diverged `bollard` copies) surfaced during FORE-314. Read-only
throughout: no file listed here was edited, merged, or reconciled as part of this survey. Single
context, not parallelized, per explicit instruction (eleven copies edited concurrently produce a
twelfth variant; a survey run the same way produces a wrong one).

Scope, per the split: **code drift only** -- `bollard/` (11 known copies) and `verdict_ledger.py`
(13 files sharing that name). The skills/PDP/PRD/CLAUDE.md document-drift half went to
`dev-harness-run2-81` separately and is not covered here.

Method: `shasum -a 256` and `diff` against real files on disk, `git log`/`git worktree list`
against real repo history, direct reads of `.claude/settings.json` files. No claim below is asserted
without the command that produced it.

---

## Item 4 (sent separately, repeated here for the record)

**bob_write_gate.py has no drift risk.** Exactly one copy exists on this machine
(`/Users/m5/dev/claude-hooks-v2/hooks/bob_write_gate.py`), registered by that identical absolute
path in `dev-harness-run2` and its five worktree siblings (`e1`, `e2`, `qa-evasion`, `qa-general`,
`qa-security`) -- confirmed by reading each project's `.claude/settings.json` `PreToolUse` block
directly, not inferred. Not registered anywhere else (`dev-harness`, `atlas-sonnet-qa`,
`foreman-v2-qa` carry no `bob_write_gate.py` hook at all in their settings). If Alice/Bob arms in
any of those six, the code that fires is unambiguous and is tonight's code.

**bollard's five guards (`guard_destructive.py` etc.) are the opposite picture.** Every project
that registers them points at its own local `bollard/` copy (`.../dev-harness-run2/bollard/...` vs
`.../dev-harness-run2-e1/bollard/...`, byte-different command strings, confirmed per project).
Which copy executes there depends entirely on which project/worktree the session is in.

---

## 1. Canonicity, with evidence

**`dev-harness-run2` is the de facto canonical source, established by real commit history, not
path depth or file count.** Three of the eleven copies carry commits saying so explicitly:

```
dev-harness-qa    7f72500  2026-09-02  "Sync bollard/skills/scripts to dev-harness-run2's current, real state"
atlas-sonnet-qa   d760cda  2026-09-02  "ATLASQA-1: bring bollard/skills/scripts to parity with dev-harness-run2"
foreman-v2-qa     5672dbf  2026-09-02  "FOREQA-1: bring bollard/skills/scripts to parity with dev-harness-run2"
```

No copy, including `dev-harness-run2` itself, contains a README, GOALS.json note, or code comment
declaring it canonical. **The absence of that declaration is itself a finding**: canonicity here
exists only as a fact three sibling repos' commit history happened to record once, on 2026-09-02,
and nowhere does the project state it as a standing rule. A fourth copy needing a sync today has
nothing written down to sync *against*, only a convention to infer from grep.

`dev-harness` and `dev-harness-v2` never received an equivalent sync. Their most recent
`bollard/`-touching commits are about unrelated work (`DEVH-103` credential redaction;
`"QUALITY-BAR is the bar, not a session log"`), and `dev-harness-v2`'s is dated 2026-08-24 --
eleven days stale at the time of this survey.

## 2. Behind vs. forked

**Behind (propagation failure, fixable by copying):**

- `dev-harness-qa`, `atlas-sonnet-qa`, `foreman-v2-qa` -- synced to dev-harness-run2 once
  (2026-09-02), now missing everything landed since: `guard_allowlist.py`, `guard_semantic_
  resolution.py`, `guard_pattern_feed.py`, `guard_os_sandbox.py` are absent entirely (not
  outdated -- not present on disk at all), `hook_common.py` and `foreman_evidence.py` are on the
  pre-sync hash. This is the same file set, later in time; copying forward would close it.
- `dev-harness-run2-qa-evasion/-general/-security` -- have all five guards (unlike the three
  above) but are behind `dev-harness-run2` itself on `guard_destructive.py` and `guard_allowlist.py`
  specifically (different hash, worktree on its own QA branch).

**Forked (two sources of truth, not fixable by copying alone):**

- `dev-harness` and `dev-harness-v2` each carry a **unique** `foreman_evidence.py` hash found
  nowhere else among the eleven -- independent edits, not a snapshot behind the mainline. `dev-harness`
  and `dev-harness-v2` are also the two copies missing `guard_destructive.py` and
  `guard_prodconfig.py` entirely -- not behind on them, structurally without them.
- `dev-harness-run2` itself has forked from its own worktree siblings on `verdict_ledger.py`:
  see §3. `guard_destructive.py` also shows a three-way split even within the six-worktree family
  (`dev-harness-run2` alone; `e1`+`e2` together; the three `qa-*` together) -- each worktree sits on
  its own branch (`build-brief-run2`, `closure-e1-falsify-d1d3`, `closure-e2-falsify-d4-full`,
  `qa-evasion`, `qa-general`, `qa-security` -- confirmed via `git worktree list`), so this is
  branch divergence by design, not an accident, but it is still real divergence a reconciliation
  pass would have to account for.

## 3. Load-bearing ranking

**Highest -- `verdict_ledger.py`'s DEVH-77 fallback marker, present in exactly one of thirteen
files sharing that name.**

```
019582504b65...  10 files: dev-harness, dev-harness-qa, dev-harness-v2, atlas-sonnet-qa,
                  foreman-v2-qa, and dev-harness-run2's OWN worktree siblings e1, e2,
                  qa-evasion, qa-general, qa-security
76daa383bf4b...  1 file:  dev-harness-run2 ONLY
```

Behavior difference, not cosmetic: the fixed version distinguishes "the ledger write failed" from
"the guard correctly stayed silent" by writing a `ledger_write_failed` marker on a lost row (see
`_write_fallback_marker`, added for DEVH-77). Every other copy -- **including dev-harness-run2's
own git-history-sharing worktree siblings** -- still collapses those two states into
indistinguishable silence. This is the sharpest instance of the survey's whole finding: a fix made
directly in one worktree's working tree never reached even the repos that share its commit history,
because a worktree's own uncommitted or worktree-local commits don't propagate to siblings
automatically.

**A thirteenth `verdict_ledger.py` is not the same artifact at all.**
`/Users/m5/dev/claude-hooks-v2/hooks/verdict_ledger.py` shares the filename with the twelve bollard
files above and serves a structurally different purpose (a different docstring, a different
module -- claude-hooks-v2's own hook chain, not bollard's "coverage experiment" framing). Grepping
for `verdict_ledger.py` across this machine returns two unrelated lineages under one name. Flagged
as its own hazard: a future reader who finds one and assumes it is the other will reason about the
wrong system.

**High -- guard coverage gaps.** `dev-harness`, `dev-harness-v2`, `dev-harness-qa`,
`atlas-sonnet-qa`, `foreman-v2-qa` (5 of 11) are missing `guard_allowlist.py`, `guard_semantic_
resolution.py`, `guard_pattern_feed.py`, and `guard_os_sandbox.py` entirely -- not stale copies,
absent files. A session in any of these five has a structurally smaller Bash/Edit/Write guard
surface than one in the `dev-harness-run2` family, with nothing in the tree to signal that the gap
exists.

**High, policy-load-bearing -- `GOALS.json` diverges in all eleven copies; no two share a hash.**
Six distinct versions across eleven files. `dev-harness-run2`'s copy alone carries tonight's
DEVHR2-4 amendment (NotebookEdit's authorization class, confirmed by grep) -- no other copy has
any record of that decision. `GOALS.json` is where scope amendments and out-of-scope declarations
live; a copy without an amendment does not merely display stale text, it has no memory that the
decision was made.

**Low -- the internal `guard_destructive.py`/`guard_allowlist.py` three-way split inside the
`dev-harness-run2` worktree family.** Real, but attributable to the worktrees being on different
task-scoped branches by design (falsification/QA branches, not a shared mainline), and the guards'
own detection logic is unlikely to differ meaningfully between these particular hashes without
further diffing -- not chased further under this survey's time budget; named as a residual rather
than ranked with confidence.

---

## Evidence appendix

Full per-file hash-group table (11 bollard copies; `MISSING` = file absent from that copy's
`bollard/`):

```
guard_destructive.py
  8bc3b486f323: dev-harness-qa, qa-evasion, qa-general, qa-security, atlas-sonnet-qa, foreman-v2-qa
  MISSING:      dev-harness, dev-harness-v2
  7a24fc1224ec: dev-harness-run2-e1, dev-harness-run2-e2
  01828caa0f86: dev-harness-run2

guard_prodconfig.py
  00918cfe7f11: dev-harness-run2, e1, e2, qa-evasion, qa-general, qa-security
  759d734e9903: dev-harness-qa, atlas-sonnet-qa, foreman-v2-qa
  MISSING:      dev-harness, dev-harness-v2

guard_allowlist.py / guard_semantic_resolution.py / guard_pattern_feed.py / guard_os_sandbox.py
  present only in: dev-harness-run2, e1, e2, qa-evasion, qa-general, qa-security
  MISSING from:    dev-harness, dev-harness-qa, dev-harness-v2, atlas-sonnet-qa, foreman-v2-qa
  (guard_allowlist.py additionally splits 3-vs-3 within the present six: run2/e1/e2 vs the three qa-*)

guard_untrusted_web.py
  9411835297af: 9 of 11 (all but dev-harness, dev-harness-v2)
  MISSING:      dev-harness, dev-harness-v2

hook_common.py
  2d335561ea46: dev-harness-run2, e1, e2, qa-evasion, qa-general, qa-security
  10fb4f245aa8: dev-harness, dev-harness-qa, dev-harness-v2, atlas-sonnet-qa, foreman-v2-qa

foreman_evidence.py
  03ebb43f30e5: 9 of 11 (all but dev-harness, dev-harness-v2)
  9a837144c523: dev-harness (unique)
  0bfb5bf4e9ce: dev-harness-v2 (unique)

GOALS.json -- six distinct hashes, no two copies agree; see body.

isolate_verdict_ledger.py (FORE-314, built tonight) -- exists only in dev-harness-run2.
```

Repo/worktree HEADs at time of survey (`git worktree list`):
```
dev-harness                  bd5a018 [main]
dev-harness-qa                7f72500 [qa-builds]
dev-harness-run2             31291a0 [build-brief-run2]
dev-harness-run2-e1           3513903 [closure-e1-falsify-d1d3]
dev-harness-run2-e2           3513903 [closure-e2-falsify-d4-full]
dev-harness-run2-qa-evasion   078f4a0 [qa-evasion]
dev-harness-run2-qa-general   79e8089 [qa-general]
dev-harness-run2-qa-security  415ce69 [qa-security]
```
(`dev-harness-v2`, `atlas-sonnet-qa`, `foreman-v2-qa` are independent repos, not worktrees of this
one -- confirmed by their absence from this `git worktree list` output.)

## What this survey did not do

No file was edited, merged, backported, or deleted. No canonicity claim was applied -- `dev-harness-
run2` being the evidenced canonical source is a finding for the fix to act on, not something this
survey enacted. The internal three-way `guard_destructive.py`/`guard_allowlist.py` split inside the
run2 worktree family was not diffed line-by-line; flagged as open rather than characterized further.
