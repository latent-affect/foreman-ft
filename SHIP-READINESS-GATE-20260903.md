# Ship-readiness gate — dev-harness-run2, dogfood scope, 2026-09-03

**Verdict: RECYCLE**, with one operator-gated item that will still block Go after the recycle
completes. Not Go, not Kill, and not a clean Hold — the reasons for each are below.

Gate owner: Marcus Webb lens, embodied in session `dev-harness-run2-5e` (Opus 5). Scope reviewed:
dogfood readiness for the operator's own use, not production. `.foreman/SHIP-CHARTER.json` was
deliberately **not** written or updated by this pass — a charter records a Go, and this is not one.

**Read the independence disclosure in §6 before weighting anything in §1.** Part of this build is
my own work, and I am not a clean reviewer of it.

---

## 1. What I checked myself, and what it showed

Everything below was executed in this session against live files and the live TESSERA DB. Nothing
here rests on a session report, including my own earlier ones.

### PASS — QA worktree governance fix actually landed

The overnight P0 (three QA worktrees running with zero Foreman hooks for hours) is closed in fact,
not just in narrative. I did not take the ticket's word for it:

| worktree | sha256 (16) | hook entries | own-bollard | leaking to run2 | hooks-v2 | ask gate |
|---|---|---|---|---|---|---|
| qa-security | `90d30d5eee2b6917` | 19 | 6 | 0 | 13 | yes |
| qa-general | `346f20247d44e7d8` | 19 | 6 | 0 | 13 | yes |
| qa-evasion | `7548e66f98f521ec` | 19 | 6 | 0 | 13 | yes |

All 19 command targets per worktree resolve to files that exist on disk (0 missing). Guard commands
are retargeted to each worktree's own `bollard/`, with zero commands still pointing back at
`/Users/m5/dev/dev-harness-run2/bollard/` — which is the failure mode a copied settings file would
have produced silently. `ask_user_question_gate.py` is registered in all three.

### PASS — Alice/Bob write gate, both P0s closed on layered evidence

DEVH-81 (active regression, P0/S0) and DEVH-85 (authorization bypass introduced by DEVH-81's own
fix, P0/S0) are closed; DEVH-87 is closed. The verification chain behind them is the strongest
thing in this build: `dev-harness-run2-cf` re-falsified DEVH-85's fix from a scratch repro (4
scenarios plus the reverse-disagreement case, `test_bob_write_gate` 54/54, full claude-hooks-v2
suite 502/502, no authorship stake in `bbb9f7c`/`4b635e2`); `dev-harness-run2-89` ran a second
independent repro; `ticket-system-bd` re-read the live settings file rather than accepting either
report. Note that DEVH-87's first status update explicitly **refused** to close on a cross-session
relay and demanded the evidence be recorded on the ticket first. That is the behavior this gate
exists to reward.

I confirmed the live registration directly in `/Users/m5/dev/dev-harness-run2/.claude/settings.json`
(sha256 `f907398a29f43cbd`): `bob_write_gate.py` on PreToolUse `Edit|Write|Bash`,
`bob_write_confirm.py` on PostToolUse `Write`, `agent_dispatch_gate.py` on PreToolUse `Agent` —
registered together in one file, which was DEVH-87's "one pass, not two" requirement.

### PASS — nothing pushed to any remote

Verified per repo rather than assumed: `dev-harness-run2` (branch `build-brief-run2`, no upstream,
84 commits unpushed across branches), `claude-hooks-v2` (`master` ahead 43), `ticket-system`
(`main`, 106 unpushed). No remote has seen tonight's work.

### PASS on sample — the credential triage holds up

DEVH-68's 12 detect-secrets findings: `.secrets.baseline` now carries 14/14 entries with
`is_secret: false`, 0 unaudited. I spot-checked the four highest-risk classifications by reading the
actual lines rather than the annotations: the AWS key, private-key header and Basic Auth hits in
`atlas/ingest/tests/test_audit_scrub.py:69-77` are planted fixtures in a redaction test
(`AKIAABCDEFGHIJKLMNOP`, `"ghp_" + "a" * 36`, `hunter2`), the `test_credential_scan.py:55` hit is the
same fake AWS shape inside a detection test, and `security_privacy_convergence.py:436` is the string
`"pip install detect-secrets"`. The verdict is right on every sample I pulled.

Caveat, and it is Marcus's standing one: that triage is recorded by exactly one session
(`dev-harness-run2-89`) with no independent re-check, and DEVH-68 is still **open**. A
false-positive verdict on 12 credential findings is precisely the kind of "resolved" that gets
carried forward because nobody re-opened the file. My sample of 4/12 is not a re-verification of the
other 8.

## 2. What blocks Go

### BLOCKER 1 — two of this build's own worktrees are ungoverned right now

Found in this pass, not inherited from a ticket. `/Users/m5/dev/dev-harness-run2-e1` and
`/Users/m5/dev/dev-harness-run2-e2` each contain `.foreman/` and **no `.claude/settings.json`**.
Both are active registered TESSERA projects (`DEVHR2E1`, `DEVHR2E2`). Zero PreToolUse hooks, zero
`ask_user_question_gate.py`, zero audit trail — the identical condition that produced tonight's P0
in the QA worktrees, still live, in this build's own worktree family, hours after that P0 was
halted and fixed three directories over.

Mitigating: both are dormant (last commit 2026-09-02, no session activity since). `e2` holds one
untracked file, `DEVH-76-FALSIFICATION.md` — real work product sitting in an ungoverned tree.

This is named nowhere as its own ticket. FORE-290 mentions `e1/e2` in a single parenthetical inside
its "what this is not" paragraph. A P0-class governance gap does not get to live in someone else's
disclaimer.

**This is the operator-gated part.** Per FORE-287, self-permission-file edits require human
execution, so no session can wire e1/e2 itself. `register_qa_worktrees.py` exists and is verified
(DEVH-90) but does not cover these two worktrees.

### BLOCKER 2 — the disposition record does not exist

DEVH-66 ("Run 2 closure pass: verified disposition record") is open, has **one** comment, last
updated 2026-09-02T20:29, and no record artifact exists anywhere in the worktree. Zero of tonight's
findings have a recorded disposition.

A ship-readiness gate that wants to know "what did this run find, and what happened to each of
those findings" has no input. That is not a documentation nicety. Tonight generated at least
DEVH-88 through DEVH-94, FORE-287, FORE-290, DEVHR2-3 and a set of closures, across ~14 sessions
in 6 repos, and the run's own instruction was that findings reported to an orchestrator and never
filed anywhere are the point of the exercise. Nobody has looked for those yet.

This one is squarely a stage deliverable that was assigned and not produced. It is why the verdict
is Recycle rather than Hold: the build is not merely waiting on the operator.

## 3. Open, unfixed, and correctly left open — not blockers, but they bound the claim

- **DEVH-92** — 5 of 10 in-scope evasion patterns are clean misses against the live 7-guard chain,
  in 4 distinct classes. DEVH-76 Phase 1 scopes classes A and D, Phase 2 (blocked) covers B,
  **nothing covers class C**. I verified one class unfixed by reading the source rather than
  trusting the ticket: `guard_semantic_resolution.py` contains zero occurrences of `base64` and
  implements only `_decode_chr_concat` and `_decode_hex_escapes`. The guard-chain files were last
  touched 2026-09-02; no fix landed tonight.
- **DEVH-91** — structural ceiling, not a detection bug: the guards are stateless per-invocation and
  none of the seven branch on `tool_name` for Task/Agent, so split-across-turns and delegated
  dispatch cannot be caught by construction.
- **DEVH-94** — the evasion battery's own instrument scores a stderr-only machinery failure as a
  clean pass, because `run_guard()` reads stdout only. Bounded honestly by its reporter (none of the
  12 committed rows take that path, so DEVH-90/91/92's results stand), but it is a measuring
  instrument that fails open, and the next battery run is where that bites.
- **DEVH-78 / DEVH-84** — duplicate criterion IDs and append-only-by-convention in
  `bollard/GOALS.json`. Commit `2f61a16` (mine) adds to the condition both describe.
- **DEVHR2-3** — C9's regression guard covers a named subset of `bollard/`, not the component.

None of these block dogfood: they are honestly scoped, correctly open, and the guard chain's real
coverage is a known quantity rather than an assumed one. They do mean nobody may describe this
build's guard chain as covering the evasion taxonomy. It covers 5 of 10 in-scope patterns.

## 4. FORE-290: not this build's blocker, and worth more attention than that implies

FORE-290 (P0/S1, open) says the canonical bootstrap `settings.json.template` is stale on three
axes: it omits `ask_user_question_gate.py`, `agent_dispatch_gate.py` and the Alice/Bob pair
entirely; it points at `/Users/m5/.claude/hooks/`, where `ship_readiness_gate.py` is 8 days and 56
lines behind the dev repo; and `verify_settings_shape()` cannot detect either problem, because it
checks only that *some* `Edit|Write|Bash` entry exists and that each command resolves to *some*
real file — which is true of the stale file too.

That third axis is a staleness check that fails open: it reports clean because it could not confirm
otherwise. I have no patience for that pattern and it should be fixed on its own merits. But it is a
defect in the FOREMAN bootstrap product, and FORE-290 itself states — and I independently confirmed
by checksum and content — that run2 and its three QA forks are correctly wired. It does not block
this build. It blocks the *next* project bootstrapped through that path, which is a different gate.

## 5. On the verification mechanism

Tonight's independent checks did not run through the persona-dispatch mechanism; `agent_dispatch_gate.py`
is registered live on PreToolUse `Agent` (I confirmed it in the settings file) and blocks it
deliberately per §4.4.3b. The work was done instead by separate peer sessions.

My judgment: the substance is better than the label would have been. Independence is bought by a
reviewer not having seen the reasoning that produced the code, and separate sessions deliver that
more completely than an in-process persona does. The qa-evasion pair is the proof — the blind
reviewer attacked the instrument rather than re-running it, and found DEVH-94, which re-running
would never have surfaced. Three separate sessions re-derived DEVH-85's closure from scratch.

The gap is provenance, not evidence quality: there is no dispatch record tying each check to a
declared reviewer role, so the audit trail is comment threads rather than gate artifacts. Name it,
do not inflate it. It is not a reason to discount §1.

## 6. Independence disclosure — read this before weighting §1

**I am not an independent reviewer of this build.** Seven of tonight's commits in this worktree are
mine (`10ead5a` through `bc0f379`, plus `4f8c5f0`), I verified DEVH-83 and filed DEVHR2-3, and
DEVH-66 — Blocker 2 — is a lane assigned to me that I have not started. A gate owner reporting that
his own unstarted work blocks the gate is the least useful possible form of that finding, and it is
also the most reliable, since it costs me something to say it.

Per this project's own standing rule (one context implements, a separate context reviews), my
findings on the parts I built are contaminated and should be re-checked by someone else:
specifically §1's git-push check on `dev-harness-run2`, the DEVHR2-3 entry in §3, and Blocker 2.

Findings in areas I had no hand in — the QA worktree checksums, the Alice/Bob chain, the e1/e2
discovery, the secrets spot-check, the guard-chain source reads, FORE-290 — carry no such
contamination. Blocker 1 in particular was found by direct filesystem check in this pass and does
not depend on my earlier work at all.

## 7. What moves this to Go

1. **Wire e1/e2, or archive them.** Operator-gated per FORE-287. Either extend
   `register_qa_worktrees.py` to cover both worktrees and have Jon run it, or, if they are finished,
   archive the TESSERA registrations and remove the `.foreman/` markers so they stop being
   ungoverned Foreman projects. Commit or move `e2`'s untracked `DEVH-76-FALSIFICATION.md` first.
   File it as its own ticket either way — it currently exists only as a parenthetical in FORE-290.
2. **Build DEVH-66's disposition record.** Every finding from tonight with its actual disposition,
   sourced from tickets and commits, never from session reports. Anything untraceable to a durable
   artifact is recorded as untraceable — that result is the point, not an embarrassment.
3. **Re-verify DEVH-68's remaining 8 findings** in a session that did not perform the triage, then
   close the ticket. I sampled 4 and they were right; that is not a clearance of the other 8.
4. **State the guard chain's coverage honestly wherever it is claimed** — 5 of 10 in-scope evasion
   patterns, class C unscoped — or close DEVH-92's class C.

Items 2, 3 and 4 need no operator involvement and are the recycle. Item 1 is the Hold that survives
it.

---

Verified 2026-09-03 by `dev-harness-run2-5e` against the live TESSERA DB
(`/Users/m5/dev/ticket-system/data/tessera.db`), the live settings files in all four wired
worktrees, and the working trees of `dev-harness-run2`, `claude-hooks-v2` and `ticket-system`.
