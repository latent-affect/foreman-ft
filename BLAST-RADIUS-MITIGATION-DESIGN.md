# Blast-radius mitigation design: Alice/Bob and the Fable backlog

**Author:** Session A (priya-desai lens), 2026-09-03. First cut, for Clint (ae) and muse
(ticket-system-65) to take the architecture-collision question forward.
**Scope:** one Tier 3 run, three parts. Part 1 (R14/R15, the bollard guard chain) is closed, all
19 criteria independently falsified. This document covers parts 2 and 3 and the interaction
between them.
**Worked from:** the live `.claude/settings.json` in this worktree, the real `claude-hooks-v2`
code, and ticket state read directly. Not from the dispatch summary.

---

## 1. The finding that reframes the question

**A fail-closed authorization gate is live on every `Edit|Write|Bash` in this repository, and the
component that produces the records it authorizes against has not been written.**

`bob_write_gate.py` is registered in `/Users/m5/dev/dev-harness-run2/.claude/settings.json` on
`PreToolUse`, matcher `Edit|Write|Bash`. Its own docstring states the position without hedging:

> `/Users/m5/.claude/hooks/stage_dispatcher.py` exists, but ... it is tonight's UNRELATED
> stage-transition watchdog dispatcher, not a writer of the `.foreman/dispatch/by-session/*.json`
> / `dispatch-index/*.json` records this gate actually reads ... The design doc's C2 row names the
> RIGHT ROLE ... but the CONCRETE FILE that plays it, for Alice/Bob specifically, has not been
> written yet ... it cannot be exercised end-to-end against a live Alice/Bob run until C3 and a
> real C2 writer for these exact records exist.

Confirmed against the live configuration rather than taken from the docstring: of the three
Alice/Bob components, only the enforcing ones are registered.

| Component | Role | Registered as a live hook? |
|---|---|---|
| `bob_write_gate.py` (C4) | enforces | **yes**, `PreToolUse Edit\|Write\|Bash` |
| `bob_write_confirm.py` | confirms | **yes**, `PostToolUse Write` |
| `agent_dispatch_gate.py` | enforces | **yes**, `PreToolUse Agent` |
| `sanitize_proposal.py` (C3) | produces | no, not in any settings.json |
| `write_dispatch_record.py` | produces | no, not in any settings.json |
| C2 dispatch writer | produces | **does not exist** |

Method: parsed the live settings file and listed every registered hook; grepped every
`settings.json` on the machine for the two producer scripts, zero hits. **Confidence: High.**

Re-verified after Jon's reregistration run, against the same file two other sessions were reading
(`.claude/settings.json`, mtime `2026-09-03T04:10:41`), because two contradictory readings of this
exact state were in circulation tonight and neither matches what the file says. DEVH-86 reported
`bob_write_gate.py` and `bob_write_confirm.py` as "fully inert"; that is wrong, both are
registered and firing. The correction to it, that "the pipeline is live and correct", is right
about the enforcing half and overstates the rest: the producers are registered in zero settings
files and C2 does not exist. The accurate statement is narrower than either. **The enforcement
half of Alice/Bob is live. The production half is not wired.** Everything in this document rests
on that distinction, so it is worth stating in the form that survives both corrections.

### Why this is the blast radius, not a footnote

The gate is deny-or-silent, and its silent path is the common one: a session with no open
dispatch is not Bob's traffic, so the gate returns without emitting. Because no live component
writes dispatch records, no session has an open dispatch, so the gate is silent essentially
always. That is why it looks harmless.

Its *error* path is not silent. Section 4.5 of the design and the code agree: this gate
deliberately does not use `hook_common.run()`, because that fails open, and an authorization
boundary that fails open is not a boundary. It catches `BaseException` and emits `hc.deny(...)`.
That is the correct choice for an authorization gate, and it means **any internal error in this
file denies every `Edit`, `Write` and `Bash` in the repository until it is fixed.**

That is not hypothetical. It happened tonight: a `bob_write_gate.py` regression briefly blocked
Write calls repo-wide, tracked as DEVH-81 and DEVH-85.

`[corrected, ticket-system-65, §9.4]` These are not the same failure shape and should not be read
as equivalent evidence: DEVH-81 was over-deny, self-revealing, found by ordinary use. DEVH-85 was
under-deny — a real authorization bypass, found only by deliberate adversarial testing, producing
no operational signal on its own. Section 7's Option B argument ("tonight's regression is an
argument for B as much as against it") is earned by DEVH-81's shape and does not extend to
DEVH-85's. DEVH-85 is now fixed (`4b635e2`) and independently re-falsified; see §9.4 for the full
correction and its consequence for Option D.

So the risk profile today is a gate that delivers no authorization value (its authorization
source does not exist) while carrying its full downside (repo-wide write denial on any bug). The
expected value of the current configuration is negative, and that is the thing to mitigate first.

### A live landmine in the same mechanism

`/Users/m5/.claude/foreman/dispatch-index/` contains exactly one entry, dated 2026-08-27:

```json
{"dispatch_id": "TEST-FULL-PIPELINE-1", "project_root": "/Users/m5/agent-remediation", "status": "open"}
```

Two things follow. First, a test artifact is sitting in the live index the gate reads, left in
`status: "open"`. FORE-273 already exists for the same class of problem in the verdict ledger
("negative control proving tests no longer touch the real ledger"); no equivalent ticket covers
the dispatch index. Second, and more sharply: `.foreman/dispatch/by-session/` **does not exist in
this worktree**. The gate has an explicit case for index-says-open-but-no-record, and it denies
rather than treating it as "no dispatch open", which is the right call for an authorization
boundary and also means an index entry matching a live session id would deny that session's every
write. Session ids are UUIDs so this specific stale entry is inert, but the shape is a landmine,
not a curiosity.

I have not established whether the test suite still writes to the live index today. What is
established is that it did at least once and the entry was never cleaned up. That distinction is
deliberate; the stronger claim would need a test run to confirm.

`[added after reading the Fable sources directly, 2026-09-03]` This is not an isolated artifact,
it is the third instance of one pattern, and Fable already quantified the first. The writeup §1
finds that **three quarters of the deny ledger is test noise**: 1,485 of 1,957 deny rows have no
`tool_use_id`, 1,033 of those have cwd `/tmp`, and the guard test suites "call `hook_common.run()`
with the real ledger writer live". Its conclusion is the one that matters here, stated in its own
words: "Nothing was lost. The instrument was polluted." So the live verdict ledger is polluted by
tests, the live dispatch index holds a `TEST-` entry left open, and FORE-273 exists to fix the
first with a negative control. The dispatch index has no equivalent ticket, and it is the store
an authorization gate reads rather than one an analyst reads. That ordering is backwards: the
polluted store with the weaker consequence is ticketed, the one wired to a live deny path is not.

---

## 2. FORE-229 is half right, and the wrong half matters

Verified independently rather than accepted: grepped `claude-hooks-v2` for `advisory.json` and
`status.json`. Only `sanitize_proposal.py` writes them; only its own test file reads them back.
**Zero production consumers. Confirmed, High confidence.**

But FORE-229's premise is that these files are "written by `sanitize_proposal.py` on every
Alice/Bob run". In the live wiring they are not written at all, because `sanitize_proposal.py`
is not a registered hook and its orchestrator `write_dispatch_record.py` is not either.

This changes what closing the ticket means. As written, FORE-229 is satisfied by adding a
consumer for two files that live use never produces. That would close the ticket and change
nothing. The real asymmetry it is pointing at is the one in section 1: enforcement is live,
production is not.

**Recommendation:** re-scope FORE-229 rather than close it. Its precondition ("before Alice/Bob
goes live") has already been violated, so it is now a live gap, not a gate. Its correct successor
is a precondition on the *producer* side landing, at which point the consumer requirement becomes
meaningful again.

---

## 3. Is FORE-281's sequencing sufficient?

FORE-281 gates `bob_write_gate.py` case 20 behind FORE-273, 275, 276, 277 and 278, and marks it
"needs Jon present". **That sequencing is correct as far as it goes and it is not sufficient.**

Correct: every one of those five is a genuine data dependency. Case 20 reads `session_prior` and a
`dispatch_record` reconciliation, and those rest on `ledger_origin` (273), migration 8 and the
dq_runner changes (275), the ingestion split (276), and the two graduations (277, 278). Nothing
in that chain is invented. It also correctly flags shared guard infrastructure as needing operator
authorization rather than an agent building it ad hoc.

Insufficient in one specific way, and narrower than my first draft of this section claimed.
**Nothing in the backlog builds C2**, and case 20's second component reconciles dispatch records
against approved post-image hashes, which are written by the component that does not exist.

`[corrected against the source, 2026-09-03]` My first draft attached that gap to FORE-281 as a
whole. Reading Fable's writeup directly rather than through the ticket shows that is wrong for
two of its three parts. The writeup §4 item 4 specifies dispatch-record reconciliation as
"security review finding N1 checked after the fact, on every session, **whether or not the gate
is wired**". It is an ATLAS-side reconciliation over Writes, deliberately designed to be built and
to run without the gate, and it catches "Bob writing his own dispatch record via PID ancestry".
So FORE-281 items (1) `session_prior` and (2) `dispatch_record` reconciliation are buildable now
and do not depend on C2. Only item (3), case 20 in `bob_write_gate.py` reading them, does.

The residual gap is real but smaller: the reconciliation is buildable without C2 and its
population is empty until dispatches actually open, so it will run green over nothing and report
no findings, which is its own trap given how much of tonight was inert checks reading as passes.

**Proposed tightening, revised:** attach "a real C2 dispatch-record writer exists" to FORE-281
**item (3) only**, not to the ticket as a whole, and state in items (1) and (2) that their
acceptance must distinguish "reconciled, no exceptions found" from "population was empty".
Neither is a dependency I am inventing: the first is the one the gate's own docstring names, and
the second is Fable's own §4 rule applied to its own dataset.

---

## 4. Scope and premortem read on the Fable backlog

FORE-272 with ten open children (273 through 282), none started. Read by what each one changes:

| Bucket | Tickets | Touches the live gate? |
|---|---|---|
| Observability and data plane | 274, 275, 276, 277, 278, 280 | no |
| Writer identity / test isolation | 273 | no |
| Documentation of a ruling | 279 | no |
| Project resolution correctness | 282 | no |
| **Gate behaviour** | **281** | **yes** |

**The premortem finding: nine of the ten tickets make Bob better at seeing, and none makes the
thing Bob authorizes against exist.** The backlog is a well-sequenced observability programme
attached to an enforcement component whose authorization source is missing. If the whole backlog
landed tomorrow, `bob_write_gate.py` would have richer priors and the same hole in the middle.

That is not an argument against the backlog, and after reading Fable's writeup directly I want to
be careful about whose gap this is. **Fable bounded the backlog deliberately and said so**: §6
ends with "Out of scope until 1 through 4 land: replacing raw-string regex in the command guards
(REQ-13a, its own architecture stage), FORE-189/190, anything in tessera-v2." The writeup never
claims this programme finishes Alice/Bob. It is an instrumentation programme, correctly scoped as
one, and §4's ordering rationale ("first because without it every prior below is polluted by test
rows") is sound.

So the premortem finding is not that Fable omitted C2. It is that **the backlog can be read as a
completion plan by anyone who meets it as ten tickets rather than as Fable's section 6**, and the
tickets do not carry that scope boundary with them. The C2 writer is unticketed work that nothing
sequences, and the risk is that ten tickets closing green reads as Alice/Bob being finished.
Cheapest fix: put the out-of-scope line from §6 onto FORE-272 itself, so the boundary travels
with the epic rather than living only in a source document.

Second premortem note: FORE-280 sets KPI thresholds "from the QA fork's measured distribution
(measure-then-decide)". That is the right method. Flagging one risk on it, because it is the
failure this project keeps hitting: a threshold measured on a distribution produced while the
enforcement half is inert will not describe the distribution after C2 lands and dispatches
actually open. Measuring now is fine; freezing thresholds now is not.

---

## 5. Where the two architectures actually collide

They collide at exactly one file, `bob_write_gate.py`:

- **Fable side:** FORE-281 case 20.
- **Alice/Bob coverage side:** FORE-283 (E3 composite property untested), FORE-284 (case 18
  once-only under real concurrency), FORE-285 (case 18 sibling-hook desync), FORE-286
  (`bob_write_confirm.py` fail path).

`[corrected, Clint (ae), §9.1]` FORE-286 is not part of the one-file collision as stated above:
`bob_write_confirm.py` is a separate, live companion file (`PostToolUse Write`) that the Fable
backlog never touches at all — checked against every child ticket's own text, not the dependency
table. It's correctly grouped into the serialization recommendation below (shared intent/confirm
protocol with case 18), but it is not itself a cross-programme collision point. See §9.1 for the
full correction, including a third, non-edit collision (case 4's live dependency on
`project_resolve.py`, FORE-282) this section doesn't cover.

Everything else in both programmes is disjoint. The collision surface is small and it is
concentrated in one file that is currently live and fail-closed, which is what makes it sharp: two
work streams editing a file whose every bug denies all writes in the repository.

**One item is already unblocked and nobody has noticed.** FORE-285 states it is "unexecutable
until bollard + bob_write_gate share a settings". They share one now: this worktree's
`.claude/settings.json` registers five `bollard` guards and `bob_write_gate.py` on the same
`Edit|Write|Bash` matcher, verified above. FORE-285's stated blocker is satisfied. It is cheap,
it is a real test of sibling-hook desync, and it can proceed immediately.

---

## 6. What can proceed now, what must wait

**Proceed now, no interaction with the live gate:** FORE-274, 276, 277, 278, 279, 280 (measure,
do not freeze), 282, and FORE-273 (which also reduces the section 1 landmine by attacking the
test-isolation class directly).

**Proceed now, newly unblocked:** FORE-285, per section 5.

**Serialize, do not parallelize:** FORE-283, 284, 286 all edit or exercise `bob_write_gate.py` /
`bob_write_confirm.py`. One at a time, each with the full test suite green before the next starts.
Two sessions editing a live fail-closed gate concurrently is how the repo gets bricked twice.

**Wait, correctly gated:** FORE-281 case 20, behind its five blockers plus the C2 dependency from
section 3.

**Decide first, because it gates the risk on everything above:** the live-exposure question in
section 7.

---

## 7. The mitigation decision, which is Jon's

The gate is live, provides no authorization value today, and carries a repo-wide denial risk that
has already fired once. Three options, with the tradeoff stated rather than a single
recommendation dressed as the only answer:

**A. Unregister `bob_write_gate.py` until a C2 writer exists.** Removes the entire downside. Costs
nothing that is working today, because the gate cannot authorize against records nothing writes.
Cost: the four coverage tickets (283 to 286) then test a hook that is not live, and re-registering
later is itself a change that needs its own verification. This is the option the expected-value
argument points at.

**B. Keep it live, keep it fail-closed, serialize all edits and require the suite green before
each.** Accepts the residual risk in exchange for keeping the component exercised in real
conditions.

`[corrected per §9.4, and the correction narrows this option's own argument]` The original
sentence here read "which is how DEVH-81 and DEVH-85 were found at all", and that conflates two
opposite failure shapes. DEVH-81 was over-deny: self-revealing, found through ordinary use.
DEVH-85 was under-deny, an authorization bypass that produced no operational signal and was found
only because a session built an adversarial fixture and kept going past what it was asked. Live
exercise finds the first shape and is nearly blind to the second. So B's exercise-value argument
is earned by DEVH-81 alone, and the failure mode B is least able to protect against is exactly
the one that matters most for an authorization boundary.

**C. Keep it live, soften the error path to silence-plus-loud-audit.** I argue against this one.
It converts an authorization boundary into a detection layer, which is precisely the
fail-open/fail-closed distinction NFR-1 exists to hold, and this project has now been bitten three
separate times by controls that looked present and were inert.

**D. Keep cases 1-7 live and fail-closed; make cases 8-19 dormant by construction.** muse's
proposal (§9.4), endorsed here after checking its premise against the code rather than accepting
it. Add an explicit precondition before case 8: does a C2 producer exist at all (a producer
registered in some `settings.json`, or any by-session record ever observed)? If not, return
silent. This changes nothing about fail-closed semantics for the cases that fire today, and it
does not throw away the exercise value A gives up.

**Refinement, from reading `main()` case by case rather than trusting the 1-7 / 8-19 split.**
Without an open dispatch, most of cases 1-7 are already dormant for the same reason 8-19 are.
Case 2 (tool-class lockout) and case 4 (project-unresolvable) both deny only `if index_open`,
which is DEVH-81's own fix. Case 1 and case 3 return silent. What is genuinely live today is
narrower than "cases 1-7":

| Case | Fires today without a dispatch? | Why |
|---|---|---|
| 6, `agent_type` present | **yes, unconditionally** | denies any `Edit`/`Write`/`Bash` from an in-process subagent |
| 7, no `session_id` | **yes, unconditionally** | session-shape check, needs no dispatch data |
| 2, 4 | no | gated on `index_open`, always false today |
| 1, 3 | no | silent by design |
| 8-19 | no | need a dispatch record |
| error path | **yes** | `BaseException` to `hc.deny`, repo-wide |

That strengthens D rather than weakening it. The live deny surface is two session-shape cases
plus the fail-closed error path, and both cases stay valid whether or not C2 ever exists, because
neither reads a dispatch record.

`[self-correction, checked against what actually landed]` An earlier version of this paragraph
said D's precondition guard "would move cases 2 and 4 into dormant-by-construction alongside
8-19, making [FORE-282] visible rather than silent when C2 lands." That was wrong, and it matters
because it would license dropping a blocker. The guard that landed (case 7b, commit `13df9e1`)
sits between case 7 and case 8 and covers only the record-based chain. Cases 2, 4 and 5 remain
dormant the way they already were, gated on `index_open`, which is the correct design and not a
shortfall in the implementation. **Consequence: the guard does nothing about §9.2's FORE-282
exposure.** Case 4 still calls `project_resolve` on every real Write and still arms the moment C2
lands. §9.2's recommendation to add FORE-282 to FORE-281's blockers is therefore fully
load-bearing and is not mitigated by Option D.

**Landed state, verified directly rather than taken from the handoff** (`bob_write_gate.py`
@ `13df9e1`): case 7b denies only when `index_open`, and returns silent otherwise, matching cases
4 and 5. Driven as a subprocess against the live file: an ordinary `Write` with no dispatch
produces empty stdout and exit 0; the identical payload with `agent_type` present returns
`permissionDecision: deny`. Its own suite is 58 tests, green. So the implementation matches the
option as endorsed, no unconditional deny was introduced, and the case-6 constraint below is
confirmed live rather than inferred from source.

**One live consequence worth stating plainly, because it is not a bug and is easy to miss:** case
6 denies every `Edit`, `Write` and `Bash` carrying `agent_type`, unconditionally, today. In-process
subagents cannot write in this repository at all while this gate is registered. That is the
section 4.4.3b mandate working as designed, not a defect, but it is a live behavioural constraint
on every session here and it belongs in the decision rather than being discovered later.

**My read:** D, then A, then B, and not C. D is what I would pick, because it keeps the only two
cases that actually fire and are actually valid, and it converts "dormant because no data happens
to exist" into "dormant by construction" — which is the same class of fix as everything else that
went right tonight. A remains a clean fallback if the precondition guard is judged to be more new
code in a live gate than the situation warrants, which is a fair objection to D and the reason A
is still second rather than dismissed.

The choice is still Jon's, because it is a risk-appetite call rather than a technical one. What I
would not do is leave it undecided, since the current state is option B without anyone having
chosen it.

---

## 8. Confidence and what I did not check

**High:** the live hook registration, the absence of C2/C3 from every settings file, the
zero-consumer finding for `advisory.json`/`status.json`, the contents of the stale dispatch-index
entry, the absence of `.foreman/dispatch/by-session/` in this worktree, FORE-285's blocker being
satisfied, and the ticket states and dependency edges. All read directly from the live files or
the ticket database.

**Medium:** the claim that the gate is silent essentially always. It follows from the code paths
and from no dispatch records existing, but I did not instrument a live session to count its
verdicts. The one thing that would raise it is a verdict-ledger query over recent sessions.

**Not checked, deliberately:** the ten Fable tickets' internal technical merit beyond what their
summaries and dependency edges state; `ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md` in full (I read the
gate's own docstring and the sections it cites, not the whole document); whether the test suite
still writes to the live dispatch index today; and DEVH-65, 77, 78, which are bollard-side and do
not bear on the collision question. DEVH-78 is a finding about a file I own and I have responded
to it separately rather than folding it in here.

**Read directly, 2026-09-03, after the first cut:** `HOOK-EVASION-PATTERNS-AND-CATCHES.md` (78
lines) and `# Fable session writeup.txt` (234 lines), both line by line, plus a sha256 confirming
the `Documents/` and `agent-remediation/` copies of the catches document are identical. Three
sections of this document changed as a result and each change is marked inline: section 3's
tightening is narrower than I first wrote, section 4's premortem now credits Fable's own scope
boundary rather than implying an omission, and section 1's landmine is now placed as the third
instance of a pattern Fable already quantified. Nothing else in the document needed revising, and
I did not re-verify Fable's own corpus figures (the 1,957 / 1,485 / 1,033 deny-row counts, or the
24-versus-23 `verdict='error'` discrepancy that FORE-274 already exists for). Those are cited as
Fable's measurements, not re-derived as mine.

**Not mine to solve:** the architecture-collision question itself, which is the next hop to Clint
and muse. What this document is for is to hand them a collision surface that is one file wide, a
sequencing gap that is one line, and a live-exposure decision that should be made before either.

---

## 9. Addendum — Clint (ae) and muse (ticket-system-65), the architecture-collision pass

Answering the three questions 6a's document was scoped to hand off, each checked against the live
code and ticket state directly, not against this document's own summary.

### 9.1 The collision surface is one file, with one precision correction

**Confirmed:** `bob_write_gate.py` is the real cross-programme collision. FORE-281 (Fable side)
and FORE-283/284/285 (coverage side) all name it directly, verified by reading each ticket's own
`Code:` line, not inferred from summaries.

**Correction:** section 5's list folds FORE-286 ("`bob_write_confirm.py` fail path") into the same
"collide at exactly one file" sentence. Checked directly: `bob_write_confirm.py` is a real, separate
file, live on `PostToolUse Write` in the same settings.json. Nothing in the Fable backlog
(FORE-273 through FORE-282, read individually, not by dependency edge) mentions it. It is touched
by the coverage side alone. It is not a cross-programme collision point by this document's own
definition of one — it is the intent/confirm companion of the file that is, correctly grouped into
the same serialize-and-test-green-before-next recommendation in section 6 for a different reason
(shared two-phase protocol with case 18, not a shared editor). Practical effect on section 6's
recommendation: none, keep serializing FORE-283/284/285/286 together. Practical effect on the
claim's precision: real — "one file" should read "one file plus its live companion," not "one
file including the companion."

**Found, not in the document:** a third, non-edit form of collision. `bob_write_gate.py:56` imports
`tessera.tessguard.project_resolve` and case 4 (`_resolve_project_root`, line 106) calls
`project_resolve.resolve_projects_for_repo(repo_root, store)` unconditionally on every real Write.
That is the exact function FORE-282 documents as having no worktree-inheritance concept. Section 4's
table marks FORE-282 "touches the live gate? no" — correct for code-editing collision (FORE-282's
fix lives in `project_resolve.py`, not `bob_write_gate.py`), wrong for behavioral dependency. See
9.2 for why this matters and is currently dormant.

### 9.2 FORE-281's sequencing has two gaps, not one

Confirmed the C2-writer gap agent-remediation-aa already added — real, necessary, correctly
sourced from the gate's own docstring.

**Found, a second gap:** read `bob_write_gate.py:287-299` directly (case 4/5). The
`project-unresolvable` deny only fires `if index_open` — and section 1 of this document already
establishes `index_open` is essentially always false, because no C2 writer means no dispatch is
ever open. That is why FORE-282's worktree bug is inert today: case 4 can return `None` all day
and case 4's own deny path never engages while `index_open` stays false. The moment C2 lands —
which is FORE-281's entire purpose — dispatches start opening for real, `index_open` stops being
always-false, and any Write whose target resolves through `git_toplevel` to a worktree without its
own direct TESSERA registration hits case 4 and denies. For this specific worktree that risk is
further masked today by `agent-remediation-aa`'s ad hoc `DEVHR2` registration (FORE-282's own
comment thread), which is a workaround for one worktree, not a fix to `project_resolve.py` itself
— the underlying gap FORE-282 describes remains open for any other worktree-shaped target.
FORE-282 is not among FORE-281's five listed blockers, and is not the C2-writer gap already added.

**Recommendation:** add FORE-282 to FORE-281's blocker list, or at minimum require case 4 be
re-verified against a real worktree target after C2 lands and before case 20 is added — case 20
sits directly downstream of case 4's resolution succeeding, per `bob_write_gate.py`'s own case
ordering.

### 9.3 Premortem: confirmed as stated, with one sharpening

Independently read each of the nine non-281 children's own scope (not the dependency-edge table
alone): FORE-274 (ATLAS row-count tracing), 275/276/277/278 (ATLASSN warehouse, ingestion split,
dataset backfill, snapshot graduation), 279/280 (pure documentation, explicitly "no code change" in
both tickets' own acceptance criteria), 282 (`tessguard/project_resolve.py`, a different subsystem).
None writes to `.foreman/dispatch/by-session/*.json` or the dispatch-index; none creates C2.
**The characterization holds exactly as stated: nine tickets make Bob better at seeing, none makes
the thing Bob authorizes against exist.**

Sharpening, from 9.2: FORE-282 is the one exception to "the backlog can proceed independently of
Alice/Bob" (section 6). It is technically disjoint from Alice/Bob's own code, but it is not disjoint
from the QUESTION Alice/Bob's authorization depends on the moment C2 exists. Treating it as purely
independent background work (as section 6's "proceed now" bucket does) is right for priority and
sequencing among the nine, but the finding in 9.2 means it should land before or alongside C2, not
simply whenever it happens to get picked up.

### 9.4 muse's finding (ticket-system-65), independently verified before folding in

**DEVH-81 and DEVH-85 are opposite failure shapes, not the same one.** Section 1 cites both as
support for "the gate carries downside, no upside," and section 7's Option B leans on "tonight's
regression is an argument for B as much as against it." Verified directly against both tickets:
DEVH-81 was over-deny — blocked every Write, found immediately through ordinary use, self-revealing,
shipped despite 114/114 green. DEVH-85 was under-deny — an authorization bypass (index/record
desync silently skipped case 16's content-approval check), found only because `dev-harness-run2-cf`
built an adversarial fixture and kept going past the two questions it was asked, not by the suite
and not by ordinary use. Option B's "keep it live for real exercise value" argument is earned by
DEVH-81's shape. It does not extend to DEVH-85's shape, which produces no operational signal to be
exercised by. The citation needs correcting regardless of which option Jon picks.

**Status update on DEVH-85 itself**, checked live: the bypass is fixed (commit `4b635e2`,
`dev-harness-run2-d4`) and independently re-falsified by `dev-harness-run2-cf` — the same session
that found the original bug, rebuilding the reproduction from scratch rather than adapting the new
test file, confirming all four scenarios now resolve correctly. The ticket itself is still showing
`status: open` — a tracking-hygiene gap, not a live risk.

**Option D, muse's proposal, endorsed:** narrower than A/B/C. Cases 1-7 (the tool/agent/session
filter and target/project resolution, where DEVH-81 lived) run on every real Write today and are
now demonstrated self-revealing and hardened — keep them exactly as they are, live and fail-closed.
Cases 8-19 (the record-based content-approval chain, where DEVH-85 lived) are provably unreachable
by real traffic today, per section 1's own finding that `.foreman/dispatch/by-session/` does not
exist in this worktree. Add one explicit, structural precondition guard before case 8 — a real
check for "does a C2 producer exist" (any producer script registered in a settings.json, or any
by-session record ever observed), hard-returning silent if not — rather than relying on the
emergent, undocumented fact that no records happen to exist yet. This does not touch fail-closed
semantics for cases 1-7 (unlike C, correctly rejected) and does not lose the one demonstrated
benefit of keeping the gate live (unlike A). It converts "cases 8-19 are dormant by absence of
data" into "cases 8-19 are dormant by construction, revisited deliberately when C2 lands" — the
same honest cost the document already names for A (re-enabling later is itself a change needing
verification), so not free, but it does not require trading away cases 1-7's proven exercise value
to get it. Now doubly motivated by 9.2's finding: case 4's own resolution depends on
`project_resolve.py`, which has a known, currently-inert gap of exactly the kind an explicit
precondition guard would also make visible rather than silent, the next time something upstream of
case 8 changes state.

### 9.5 Net read for Jon

The mitigation decision in section 7 is still Jon's, and A/B/C's costs are still stated correctly.
What changes: Option D is a real, narrower fourth choice, not decided here; the DEVH-81/85 citation
under Option B needs the correction in 9.4 regardless of which option is picked; and FORE-281 is
not clear to proceed the moment C2 lands — it needs FORE-282 (or an equivalent re-verification of
case 4) alongside it, not after it.

**Confidence:** High on 9.1's file-level findings and 9.2/9.3's ticket-scope findings (read
directly from the live settings.json, `bob_write_gate.py`'s own source, and each cited ticket's own
text). High on 9.4's DEVH-81/85 distinction and the DEVH-85 fix status (read directly from both
tickets' full comment threads). Medium on how likely FORE-282's worktree gap is to actually bite
in practice once C2 lands — that depends on which future dispatch targets land in unregistered
worktrees, which neither of us measured.

