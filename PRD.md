# PRD — dev-harness Quality, Security and Efficiency Remediation

**Stage:** `foreman:product-requirements` (Run 2, stage 1 of 3)
**Author:** Priya Desai (staff PM persona), session `dev-harness-run2`, branch `build-brief-run2`
**Date:** 2026-09-02
**Input:** `/Users/m5/Downloads/dev-harness-BUILD-BRIEF_2.md` (293 lines, corrected edition)
**Scope:** R1 through R22 of that brief, plus DEVH-2 and two gaps found while verifying it.
**Out of this document's scope:** R0 (closed by Run 1; cited here, never reopened).
**Next stage:** `foreman:architecture` (Clint Eastwood), then `foreman:design-and-scope` (Priya).

---

## 0. How this document was built, and what that changes

The brief is an input, not a source. Every requirement below was checked against the live
tree at `/Users/m5/dev/dev-harness-run2` and against the report artifacts in
`/Users/m5/dev/dev-harness` before it was written down. That pass moved eight items.

| Brief item | What the brief said | What the artifact says | Effect |
|---|---|---|---|
| R1 | Three files where CC *and* MI both flag | Only `store.py` meets that criterion. `skill_broadscan.py` MI=51.35 (A), `component_coupling.py` MI=41.45 (A) | Criterion split; see R1 |
| R4 | Four SQL sites, "unconfirmed" whether safe | All four parameterize values with `?` and interpolate only identifiers from a module constant or a validated allowlist | Closed on the vulnerability question; R4 becomes annotation plus a regression test |
| R5 | Hex high-entropy string, real secret or not | Lines 44-48 are `EXPECTED_SHIM_SHAS`, SHA-256 digests of git hook shims, used for tamper detection | Closed; R5 becomes a suppression with recorded justification |
| R13(4) | OS sandboxing is "deepest fix, most cost" | `bollard/lib/deny_keychain.sb` plus `neutralize_credentials.sh` already implement a working, empirically verified Seatbelt deny-profile on this machine | Cost estimate is too high; a reference implementation exists |
| R14/R15 | Validate `layered_command_guard.py` | That file does not exist anywhere under `/Users/m5` outside `Library` | Both requirements are unverifiable as written; see R14 |
| R16 | Un-defer ATLAS D5 (model column) | D5's stated reason is *no data source*: `sessions.jsonl` carries no model field (`docs/atlas-architecture.md:63`, DDL comment at line 600) | Two-part requirement; the data source is the blocking half |
| R18 | Churn reads 1 almost everywhere; probably an ATLAS gap | The repo has 7 commits total. `get_git_churn` counts all history with no window. Churn of 1 is arithmetically correct | Not an ATLAS gap. The real defect is downstream: see R18 |
| — | not in the brief | The three report scripts every number here rests on are untracked (`??`) in git | New requirement R23 |

Confidence notation follows the operator's standing rule: **High** = verified against an
independent artifact; **Medium** = internally consistent, not independently confirmed;
**Low** = single observation or assumption.

---

## 1. Problem statement

dev-harness ships a gate system whose whole premise is that a claim gets checked before it is
trusted. Three classes of gap in the repo itself contradict that premise, and each one is
observable today.

**It does not run its own guards.** `bollard/` ships eight gate scripts and zero of the three
security guards (`guard_destructive.py`, `guard_prodconfig.py`, `guard_untrusted_web.py`) that
its own documentation, tests and code comments treat as live components. The shipped
`skills/foreman/config/settings.json.template` registers six gates and no guards. The install
script's fail-closed preflight (`scripts/install-dev-harness.sh:19`) lists eight required hooks
and none of the three. An operator who installs dev-harness gets the design gates and none of
the destructive-command, production-config or untrusted-web protection.

**It detects after the fact where its own stated principle says scrub at capture.**
`atlas/ingest/audit.py:36` stores `payload_json` verbatim at ingest. The credential check
(`atlas/warehouse/dq_runner.py:260`) runs after storage, is advisory, and its own comment states
plainly that it does not redact. A real Gemini API key already reached persistent storage
through this channel, recorded in the project's own `CLAUDE.md`. Detection is the wrong shape
for a guarantee an operator only discovers has failed by going and looking for it.

**Its measurements are not reproducible.** The health score, the hotspot ranking and the
security convergence report that this whole remediation is scoped against come from three
scripts that are untracked in git. There is no commit to diff a "before" against, and the
before/after comparison R3 asks for cannot currently be made honest.

None of these is a performance problem or a taste problem. Each is a checkable statement about
the system that is currently false.

---

## 2. Goals

Outcomes, not checks. The requirements in section 3 are what gets verified; these are why.

**G1 — dev-harness holds itself to the bar it sells.** A protection the repo documents as
shipped is one an installer actually receives and one this repo actually runs.

**G2 — Sensitive content is removed where it is written, not where it is read.** Detection and
serve-time redaction stay as layers on top, never as the mechanism the guarantee rests on.

**G3 — Every number in a remediation claim can be re-derived by someone who was not in the
room.** Tooling committed, inputs stated, scope stated, output re-runnable.

**G4 — Remediation goes where the evidence points.** Selection by converging independent signal,
not by a ranking that is single-signal underneath.

**G5 — A capability that cannot be reached cannot be evaded.** Text-matching hooks are audit,
not boundary. Where a boundary is required, it removes capability.

**G6 — Requirement to ticket to evidence stays traceable.** Nothing in scope is closed by
assertion.

---

## 3. Requirements

Numbering continues the brief's R-series so downstream references stay valid. R6, R10 and R11
are recorded in section 8 as already closed and carry no requirement. Each requirement states
its verification, its supporting evidence, and its confidence.

### Quality remediation

**R1 — Complexity and maintainability remediation, with the selection criterion corrected.**

The brief names three files under a single criterion that only one of them satisfies. Split it.

*R1a (criterion met).* When `foreman_quality_baseline.py` is re-run against the repo root after
remediation, `tessera/store/store.py` shall report a maintainability index at or above the
pinned target, having started at 4.78 (radon grade C, the only C-grade file in the analyzed
tree), max cyclomatic complexity 32, SLOC 1443.

*R1b (criterion not met).* `monitoring/compliance-cron/skill_broadscan.py` (max_cc 36, MI 51.35,
grade A) and `bollard/component_coupling.py` (max_cc 27, MI 41.45, grade A) do not satisfy
"complexity *and* maintainability both flag." Either they get a separate stated justification
recorded at design-and-scope, or they leave scope. They shall not be carried under R1a's
criterion, for the same reason the brief itself removed `cli.py` from scope at R6.

- **Verification:** `/Users/m5/.venv/bin/python3 foreman_quality_baseline.py` from the repo root;
  compare `quality_baseline_report.json` before and after. R1a passes on the MI threshold. R1b
  passes when each remaining file carries a written justification or is removed from the backlog.
- **Blocking precondition (unresolved, carried from the brief):** the target number is not
  pinned. The brief says so, and this document does not invent one. **Proposed default, needs
  operator confirmation at design-and-scope:** `store.py` MI ≥ 20 (radon grade A floor) and
  SLOC-weighted health ≥ 7.5 against the 6.86 baseline. Implementation shall not start on R1
  until a number is recorded in `GOALS.json`.
- **Evidence:** `/Users/m5/dev/dev-harness/quality_baseline_report.txt`, hotspot and MI tables.
  173 files, 1165 functions, simple health 8.36, SLOC-weighted 6.86.
- **Confidence: High** on the measured values (read directly from the report). **Medium** on
  whether an MI move on `store.py` alone shifts the weighted score meaningfully; `store.py` is
  1443 SLOC of a 173-file tree and the weighting is by SLOC, so the effect is plausible but
  unmodelled.

**R2 — Characterization tests before refactoring, proven to fail.**

Given a file entering R1 remediation, when its characterization tests are written, then those
tests shall be run against a deliberately mutated copy of the pre-refactor file and shall fail.
A characterization suite that passes against a broken input is not evidence.

- **Verification:** for each file touched, a recorded run showing the suite red against the
  mutant and green against the original, before any refactoring commit lands.
- **Rationale:** this project's own standing rule on self-written test code, and the "weak-oracle
  / structurally-unfalsifiable tests" gap the convergence scan names as invisible to every tool
  it runs.
- **Scope:** files actually touched, starting with `store.py`. Not a repo-wide coverage push.
- **Confidence: High** (the requirement is a procedure, not a measurement).

**R3 — Second baseline run, from a context that did not do the remediation.**

After R1 lands, the baseline shall be re-run and compared. The comparison run shall be executed
by a session that did not author the refactor, per the project's separate-context review rule.

- **Verification:** two dated `quality_baseline_report.json` files, each recording the commit SHA
  of the tool and of the tree, plus a diff.
- **Depends on:** R23. Without it there is no tool SHA to record and the "before" is not fixed.
- **Confidence: High.**

**R4 — SQL construction in `store.py`: annotate and pin, the vulnerability question is closed.**

Read directly this pass. All four flagged sites parameterize every *value* with `?` and
interpolate only *identifiers*:

| Line | Construct | Identifier source |
|---|---|---|
| 799 | `f"UPDATE tickets SET {field_name}=?, updated_at=? WHERE ticket_id=?"` | `field_name` rejected at line 781 unless in `PRIORITY_LIKE_FIELDS` (line 777, a two-key dict constant) |
| 1651 | `f"SELECT {col_list} FROM {table} ORDER BY {col_list}"` | `table` from `schema.PROJECTION_TABLES` (`schema.py:266`, module tuple); `col_list` from `canonical_columns_internal()`, PRAGMA over a scratch DB built from `schema.DDL` |
| 1663 | same construct, live-projection path | same |
| 1806 | `f"UPDATE tickets SET {column}=?, updated_at=? WHERE ticket_id=?"` | `column` is a literal ternary, `"priority"` or `"severity"` |

No actor-supplied or ticket-supplied string reaches an interpolation point. This matches the
`sql_query.py` `get_schema()` precedent the brief cites.

What remains is that the four sites are indistinguishable at a glance from a real injection, and
the safety at line 799 rests on a guard fourteen lines away that a future edit could widen.

- **Requirement:** each of the four sites shall carry an inline suppression with a written
  justification naming its identifier source, and a regression test shall assert that
  `set_priority_like` raises `ValueError` for a `field_name` outside `PRIORITY_LIKE_FIELDS`.
- **Verification:** `bandit` reports zero unsuppressed `hardcoded_sql_expressions` in
  `tessera/store/store.py`; the new test fails when the line 781 guard is removed.
- **Also:** one risk-register row recording the pattern and the widening risk.
- **Confidence: High** (direct code read of all four sites and both identifier sources).

**R5 — `tessera/tessguard/config.py` detect-secrets finding: false positive, suppress with reason.**

Lines 44-48 define `EXPECTED_SHIM_SHAS`, three SHA-256 digests of the `pre-commit`, `pre-push`
and `commit-msg` git hook shims, pinned so a one-file edit to a shim is caught. Not a
credential. The in-file comment already states the purpose.

- **Requirement:** record an audited detect-secrets baseline entry for these lines with the
  justification, so the finding does not recur as an open item on the next scan.
- **Verification:** a detect-secrets run over the repo reports zero *unaudited* findings in
  `tessera/tessguard/config.py`.
- **Confidence: High** (direct read; the values are hex digests of known files and the
  surrounding code uses them for comparison, not authentication).

### Security and privacy

**R7 — MCP trust-boundary review, narrower than the brief assumed.**

`atlas/mcp/server.py` is 85 lines, wraps `QueryFacade` unchanged, adds no safety logic of its
own, and at line 22 already excludes `v_fail_open_incident` from `MCP_VIEWS`. The
payload-bearing view is therefore not reachable through the MCP tool surface. It remains
reachable through `QueryFacade` directly, which is R8's problem, not R7's.

- **Requirement:** a written STRIDE pass over this file's trust boundary, dispatched to Nadia
  Osei at the validation stage, producing a risk-register row with a scored disposition.
- **Verification:** the risk-register row exists and names a decision, not a "monitor" placeholder.
- **Evidence:** the convergence scan lists MCP tool poisoning as a category with no mature free
  scanner, needing manual review. This is that review, not a tool run.
- **Confidence: High** on the narrowing (read `server.py:22` and `facade.py:14` directly).
  **Low** on residual risk magnitude until the STRIDE pass runs.

**R8 — Credential scrubbing at audit ingest, fail-closed.**

While the audit-plane ingest path is processing a line, when that line's serialized payload
matches any pattern in `CREDENTIAL_PATTERNS`, the ingest shall redact the match before the row
is written, and shall fail the ingest run if the redaction step raises.

- **Verification, and it must be this shape:** given a synthetic audit line containing a
  distinct planted match for each of the ten patterns in
  `atlas/warehouse/dq_runner.py:246-257`, when the line is ingested, then `payload_json` **read
  back from the database on disk** contains none of the ten. Reading back the in-memory value the
  scrub function just returned does not satisfy this. Additionally: with the scrub function
  forced to raise, the ingest run shall exit non-zero and shall write no row.
- **Constraint the architecture stage must respect:** the warehouse is a derived store. The raw
  source (`safety.jsonl`) is unchanged on disk, so redaction here is recoverable and is not a
  destructive operation on the system of record. Redaction of the source itself is not in scope.
- **Open question from the brief, answered:** redact-at-write, *and* raise the check from
  advisory to contract. Not either/or. The brief's own R8 text already states serve-time
  handling is defense in depth rather than a substitute; this document adopts that.
- **Evidence:** `atlas/ingest/audit.py:36` (`"payload_json": json.dumps(obj)`, no scrub);
  `dq_runner.py:233-245` (the check's own comment: "It does NOT redact payloads at read time");
  largest observed line 682,196 bytes carrying verbatim shell text; `v_fail_open_incident` in
  `ALLOWED_VIEWS` at `facade.py:14`.
- **Confidence: High** on the gap (read every link in the chain). **High** on the incident being
  real, not hypothetical (recorded in the project's own `CLAUDE.md`).

**R9 — Cross-project ledger routing, write-time.**

Same shape as R8 at smaller scale. `check_audit_ledger_partition_by_cwd` counts rows whose `cwd`
resolves to a registered project but whose `source_path` puts them in the shared global-safety
ledger. Advisory today.

- **Requirement:** rows shall be routed by resolved `cwd` at write time, and the check shall be
  promoted from advisory to contract once the observed count reaches zero.
- **Verification:** `check_audit_ledger_partition_by_cwd` returns `observed_value=0` over the
  live warehouse, and its registration tuple in `test_schema.py:39` reads `contract`, not
  `advisory`.
- **Blocking precondition:** the brief's figure of 477 misrouted rows **could not be verified in
  this pass** and no warehouse database was queried. A current measured count is required before
  this requirement is sized. **Confidence: Low** on 477; **High** on the mechanism and on the
  check existing as described.

**R22 — Inventory of every verbatim-persistence point, not just `audit_event`.**

R8 and R9 are the two known violations of the scrub-at-capture principle. The requirement is an
enumeration, not a spot fix.

- **Requirement:** produce an inventory of every location where raw or verbatim externally
  sourced content is persisted, each row classified as scrubbed-at-write, scrubbed-at-read, or
  unscrubbed, with the classification traceable to a specific line.
- **Must include, because the convergence scan names it as invisible to every tool it runs:**
  PII in TESSERA ticket free-text fields (`comments`, `repro_steps`, `environment`,
  `description`). That content lives in the ticket database, not in source code, so the source
  scan cannot see it. This is not currently a numbered item anywhere in the brief.
- **Verification:** the inventory exists, every entry cites a file and line, and the architecture
  stage is not closed while any entry is unclassified.
- **Confidence: High** that the inventory is incomplete today (the scan's own KNOWN GAPS section
  names at least one blind spot outside `audit_event`).

### Hook and gate evasion

**R13 — Capability removal is the primary control; detection layers are never the only control.**

Absorbs R12, which is a premise rather than a checkable requirement.

- **Requirement:** given a command-string evasion of the `su""do` class, when every
  detection-layer guard is disabled, then the action shall still be denied by a
  capability-scoping or allowlist layer.
- **Ordering, from the brief's scored pass, retained:** scoped capability by construction;
  allowlist inversion; friction and confirmation; OS-level sandboxing. Detection candidates
  (semantic resolution, anomaly detection, LLM judge, pattern feed) are additive only.
- **Cost correction:** the brief treats OS-level sandboxing as the most expensive layer on the
  assumption that Seatbelt tooling would be built from nothing. This repo already ships
  `bollard/lib/deny_keychain.sb`, a working `sandbox-exec` deny-profile with recorded empirical
  verification against a real binary, driven by `bollard/lib/neutralize_credentials.sh` which is
  itself fail-closed by design. The profile pattern, the invocation wrapper, and the method for
  distinguishing "denied" from "coincidentally absent" all exist and are proven on this machine.
  Layer 4's real cost is lower than the brief's ranking assumes. Architecture should re-rank
  with that in hand.
- **Confidence: High** on the sandbox tooling existing and being verified (read the profile and
  its recorded 2026-08-23 trial results). **Medium** on how far that pattern transfers from
  credential denial to command-capability scoping; the mechanism is the same, the policy is not.

**R14 — `layered_command_guard.py` cannot be validated because it cannot be found. Resolve the
artifact before this requirement is actionable.**

Absorbs R15. A full-depth search of `/Users/m5` excluding `Library` returned no file, directory
or reference matching `layered_command_guard*`. `OSSandboxGuard`, `SemanticResolutionGuard`,
`AllowlistGuard` and `PatternFeedGuard` are named in the brief as classes in that file, including
a disclosure R15 describes as "stated in the file itself." Neither the file nor the disclosure is
locatable.

- **Requirement:** before R14 or R15 enters a backlog, the artifact shall be located and its path
  recorded, or the requirements shall be rewritten as build requirements rather than validation
  requirements.
- **Verification:** a stated absolute path that exists, or a rewritten pair of requirements.
- **What R15 becomes if the file is found:** `SemanticResolutionGuard` shall fold
  `chr(a)+chr(b)` addition, verified by the chr-built `rm` case being caught by that guard alone
  with the other three disabled.
- **What R14 becomes if the file is found:** `OSSandboxGuard` shall deny the `su""do`
  reproduction on this machine, using the verification method `deny_keychain.sb` already
  documents.
- **Confidence: High** that it is not present under the searched path. **Low** on why; it may
  live in `agent-remediation` or another checkout not searched. This is a hand-off question for
  the orchestrator, not a finding of fabrication.

### ATLAS and TESSERA

**R16 — Model identity in `dim_session`, data source first.**

The brief asks to un-defer ATLAS deferral D5. D5's stated reason is not deprioritization, it is
absence of a source: `docs/atlas-architecture.md:63` records that `sessions.jsonl` carries "no
machine field, no model field," and the DDL comment at line 600 states a permanently-NULL column
"looks handled while carrying zero bits." Un-deferring without a source reproduces exactly the
failure D5 was raised to avoid.

- **R16a:** a data source for per-session model identity shall be identified and shown to
  populate for a real session before any schema change.
- **R16b:** once R16a holds, `dim_session` shall carry a `model` column, added to the DDL block
  in `docs/atlas-architecture.md` section 16 (which `atlas/warehouse/ddl.py` extracts at import
  time) and to `EXPECTED_TABLES` coverage in `atlas/warehouse/tests/test_schema.py`.
- **Verification:** R16a, a query returning a non-null model for at least one real session.
  R16b, `test_schema.py` green and the column non-null for sessions ingested after the change.
- **Business justification, retained from the brief:** the Sonnet-baseline scalability case
  cannot be evaluated without knowing which model produced a verdict.
- **Confidence: High** on the blocker (read both the doc line and the DDL comment).

**R17 — Concurrent-session view, buildable from existing columns.**

Confirmed. `dim_session` as declared at `docs/atlas-architecture.md:602` carries `session_id`,
`started_ts`, `ended_ts`, `claude_version`, `git_branch`, `start_cwd`. Every column R17 needs is
present. No new ingestion.

- **Requirement:** a view reporting concurrent sessions per project per time window shall exist
  as a permanent warehouse view, replacing the ad hoc computation in
  `token_bloat_diagnostic.py`'s launch-cluster mode.
- **Verification:** the view name appears in `EXPECTED_VIEWS` in `test_schema.py:20`, the schema
  test is green, and the view returns rows over the live warehouse.
- **Confidence: High** (all four required columns read directly from the DDL block).

**R18 — Fix the hotspot signal, not ATLAS. The churn reading is correct.**

Settled this pass, and the brief's proposed direction was wrong. `get_git_churn`
(`foreman_quality_baseline.py:218`) runs `git log --pretty=format: --name-only` over all
history with no window and counts occurrences. This repository has **7 commits total**;
`git log -- tessera/store/store.py` returns 1. A churn of 1 is the correct answer for a squashed
distribution tree, not a defect, and not an ATLAS capability gap. ATLAS does not compute this
number at all.

The real defect is one line downstream. `hotspot_signal = max_cc * git_commits_touching_file`
(line 405). With churn pinned at 1 across the tree, the signal reduces to `max_cc`. The ranking
is presented as "complexity x git churn" and is single-signal underneath. R1's selection rests
on that ranking.

- **Requirement:** either churn shall be sourced from a repository where this code has real
  history, or the churn factor shall be dropped and the output relabelled to state that the
  ranking is complexity-only on repositories with degenerate history. A signal that names two
  inputs shall not silently carry one.
- **Verification:** either the report shows churn values greater than 1, or the report header and
  the JSON field name state complexity-only, and a test asserts the relabelling holds when every
  file has churn 1.
- **Confidence: High** (`git log --oneline | wc -l` = 7; per-file counts read directly; the
  multiplication read at line 405).

### Process

**R19 — Concurrent sessions message each other, with a stated review expectation.**

Zero references to `SendMessage` or `ListAgents` exist anywhere in this repository. Not built.

- **Requirement:** given two concurrent Claude Code sessions on the same project, when one writes
  code, then the other shall receive a message naming the artifact and stating that the write is
  subject to review.
- **Verification:** a recorded transcript from the receiving session showing the message, with
  the artifact path in it.
- **Constraint:** this uses the session-to-session messaging primitive called from inside a
  session's own tool-use loop. It is not a network API a background process can invoke, and the
  architecture stage should not design one.
- **Confidence: High** that it does not exist (grep over the whole tree, zero hits).

**R20 — One TESSERA ticket per requirement, at design-and-scope.**

- **Requirement:** every R-number carried forward from this document shall have its own DEVH
  ticket, one ticket per requirement, not one ticket per component.
- **Verification:** the ticket count equals the count of live requirements in section 3, and each
  ticket cites its R-number.
- **Timing:** design-and-scope, per R20's own text. Not this stage. Flagged here so it is not
  dropped in the hand-off.
- **Explicitly permitted:** a ticket may declare a dependency on post-release field-trial data.
  The dependency must be visible on the ticket, not assumed.
- **Confidence: High.**

**R21 — Quality scoring becomes a standing KPI system, not a report.**

- **Requirement:** baseline runs shall be scheduled and their results retained as a time series
  that can be diffed across dates, rather than regenerated ad hoc.
- **Verification:** at least two dated, retained baseline records exist and a diff between them
  is producible by command.
- **Open, and deliberately not decided here:** whether the target is one score for the codebase
  or a separate target per category (complexity, security, maintainability). The brief states
  this is design-and-scope's job. This document does not pre-empt it. What this document does
  require is that the decision is recorded before R1's target number is pinned, because the two
  choices are the same choice.
- **Depends on:** R23 and R18. A KPI series built on an untracked tool with a degenerate signal
  would formalize the wrong number.
- **Confidence: Medium** (the requirement is clear; the metric shape is genuinely undecided).

### New requirements found while verifying the brief

**R23 — The measurement tooling shall be version-controlled.**

`git status` in `/Users/m5/dev/dev-harness` reports `foreman_quality_baseline.py`,
`token_bloat_diagnostic.py` and `security_privacy_convergence.py`, plus all six of their `.json`
and `.txt` outputs, as untracked. `git ls-files` matching those names returns zero. Every
quantitative claim in this PRD and in the brief traces to one of those three files, and none of
them exists in the branch this work is being done on.

- **Requirement:** all three scripts shall be tracked in git, and each generated report shall
  record the tool commit SHA and the analyzed tree SHA in its own output.
- **Verification:** `git ls-files` returns all three paths; a fresh report contains both SHAs.
- **Why this is load-bearing rather than hygiene:** R3 asks for a before/after comparison. An
  untracked "before" can change or vanish between the two runs with no diff to show it, which
  makes the comparison unfalsifiable in exactly the way this project's own literature calls out.
- **Confidence: High** (`git status --short` output read directly).

**R24 — dev-harness shall run and ship its own security guards. (DEVH-2)**

Three findings, one requirement.

1. `bollard/` contains no `guard_destructive.py`, `guard_prodconfig.py` or
   `guard_untrusted_web.py`. The repository nonetheless treats all three as real components:
   `bollard/hook_common.py:46` describes a corruption that "took out guard_destructive,
   guard_install, guard_prodconfig, scan_write"; `bollard/architecture_gate.py:8` cites
   `guard_prodconfig.py` as precedent; `docs/atlas-architecture.md` carries measured
   per-handler statistics for all three, including `guard_destructive.py` at 3,245 silent and 8
   deny fires.
2. `skills/foreman/config/settings.json.template`, the settings file an installer receives,
   registers six design gates and zero guards.
3. `scripts/install-dev-harness.sh:19` fails closed on eight required hooks and names none of
   the three, so a guard-free install is a clean install by the script's own standard.

- **Requirement:** the three guards shall be present in `bollard/` with tests, registered in the
  shipped settings template, and added to the install script's required-hook preflight so a tree
  missing them refuses to install.
- **Verification:** `scripts/test_install_fail_closed.py` extended to assert the three files
  exist and that removing any one causes the installer to exit non-zero; the substituted template
  registers all three; a destructive-command probe is denied in a fresh install.
- **Confidence: High** on all three findings (each read directly from the named file and line).

---

## 4. Non-functional requirements

**N1 — Fail-closed on every privacy or redaction guarantee.** R8 and R24 both make claims of the
class the operator's standing rule governs. Write to a temp location, verify by reading back
from disk, publish by atomic rename, and hard-fail the whole operation on any error in that
chain. No partial success, no log-and-continue.

**N2 — Hook latency budget.** `docs/atlas-architecture.md:181` records `guard_destructive.py`,
`architecture_gate.py` and `goals_freeze_gate.py` averaging under 0.05 ms across roughly 10,000
real firings each, against `preflight_blocking_gate.py` at 181.7 ms. Anything R24 adds to the
`PreToolUse` path shall stay inside the sub-millisecond class those three occupy. A 40x
regression on the cheapest hooks is the failure mode that document already names.

**N3 — Redaction shall not silently degrade the audit trail.** R8 removes content from a
persisted record. The redacted row shall remain wellformed against
`check_audit_envelope_wellformed`, and the fact that redaction occurred shall be recorded on the
row rather than being inferable only from absence.

**N4 — No new runtime dependency without provenance.** `dependency_provenance_gate.py` already
gates this. The current manifest is 32 pinned packages, all queried against OSV with zero
vulnerabilities found. That state is the baseline to hold.

**N5 — Every threshold in this document is re-derivable.** Any number a requirement is checked
against shall name the command that produces it. A requirement whose threshold cannot be
re-derived is not verifiable, whatever it looks like.

**N6 — Separate context for verification.** R3's re-run, and the falsification pass over
`GOALS.json` at design-and-scope, shall be executed by a context that did not perform the
work being checked.

---

## 5. Out of scope

Each with the reason, because an unnamed non-goal is scope creep with a head start.

- **R0, context-size reduction.** Closed by Run 1. Its original 2.3x-3.1x claim is retracted as
  an unscoped-aggregation artifact. Cited in this document, never reopened.
- **The Alice/Bob QA architecture.** A separate project, gated on this one. R13 is
  command-string evasion defense and is adjacent, not the same system, and the architecture stage
  should keep the language distinct.
- **Anything duplicating `foreman-context-balancer`'s cross-session capacity view or advisory
  routing.** FCLB has its own PRD and 214 passing tests, blocked on Postgres, not on design. R17
  builds a warehouse view over existing columns and stops there.
- **CIS RAM scoring inside Nadia's per-build gate.** Evaluated and rejected. STRIDE/DREAD suits
  a cheap per-build dispatch; CIS RAM suits a heavier periodic audit. A periodic trigger, if
  wanted, is a new mechanism, not a change to Nadia.
- **Further token and cache mechanics investigation.** Exhausted. Cache hit rate 95-99% across
  every window tested, concurrent-session sharing 97% effective.
- **Blanket refactor of all 1,165 analyzed functions.** R1 targets convergent signal only.
- **`cli.py`'s CC-51 score as an active risk.** A flat command dispatcher matching NIST 500-235's
  named exception. The report supports this independently: MI 33.1, grade A. Complexity flags,
  maintainability does not.
- **Photo and image EXIF remediation.** Handled externally.
- **Redaction of `safety.jsonl` itself.** R8 covers the derived warehouse. The append-only source
  is a separate decision with different reversibility.
- **Presidio-based PII scanning of source code.** Not installed, and the convergence report is
  explicit that source is the wrong target anyway. R22 points the PII question at the ticket
  database, where the content actually lives.

---

## 6. Success metrics

How the project is judged, not how a single requirement passes. Each has a threshold.

| # | Metric | Threshold | Measured by |
|---|---|---|---|
| M1 | Guard dogfooding | All three guards present in `bollard/`, registered in the shipped template, and enforced by the install preflight. A tree missing any one fails to install | `scripts/test_install_fail_closed.py` |
| M2 | Write-time scrub | Zero of ten planted credential patterns readable from `payload_json` on disk after ingest; forced scrub failure yields a non-zero exit and zero rows | The R8 verification harness |
| M3 | Reproducibility | Three tools tracked; every report records tool SHA and tree SHA | `git ls-files`; report header |
| M4 | Signal honesty | The hotspot ranking either carries real churn variance or states complexity-only in its output | `quality_baseline_report.json` |
| M5 | Quality movement | `store.py` MI at or above the pinned target; SLOC-weighted health above 6.86 by the pinned margin | Baseline re-run, R3, separate context |
| M6 | Traceability | Every live requirement has one DEVH ticket citing its R-number | TESSERA query |
| M7 | Inventory completeness | Every verbatim-persistence point classified with a file and line; none unclassified | R22 inventory |

M5 is not final until R1's blocking precondition is closed. Stating a metric whose threshold is
still open is better than stating one that sounds settled and is not.

---

## 7. Falsification pass

Required by this skill's own gate criteria: for each requirement, could a plausible broken
implementation still satisfy the stated verification? Findings, and what was changed.

- **R8 originally read "credential patterns are scrubbed at ingest."** A scrub function that
  returns a cleaned string while the insert still writes the original would pass a verification
  that checks the return value. Rewritten to require reading the row back from disk, and to
  require a forced-failure case. This is the same trap the operator's fail-closed rule names.
- **R2 originally read "add characterization tests."** A test suite asserting `assert result is
  not None` passes against almost any mutation. Rewritten to require the suite be proven red
  against a deliberate mutant first.
- **R24 originally read "add the guards to the settings template."** Files copied into a template
  that the installer never validates would pass. Rewritten to require the install preflight fail
  when any of the three is absent, which is the property that actually protects an installer.
- **R1 originally read "target a meaningful move off 6.86."** "Meaningful" is not checkable and
  two readers would disagree. Rewritten with a proposed number, marked as needing confirmation,
  and with implementation blocked until a number is recorded.
- **R17 survives unchanged.** A view that returns rows but computes concurrency wrongly would
  pass "returns rows." Accepted, and flagged for the architecture stage: the view needs a
  correctness case with known overlapping sessions, which is design detail, not a PRD threshold.
- **R19 survives unchanged**, weakly. A message sent and ignored satisfies "the other session
  receives a message." Whether the review expectation is *acted on* is not checkable at this
  stage and should not be faked into a threshold here.

---

## 8. Closed before this stage, recorded for traceability

| Item | Disposition | Evidence |
|---|---|---|
| R0 | Closed by Run 1. Original growth claim retracted | Brief's own correction section; DEVH-3 exit report |
| R6 | No action. `cli.py` is a flat dispatcher, NIST 500-235 named exception | Independently supported: MI 33.1, grade A |
| R10 | No change to Nadia. Confirmed intentional | Brief section "what this is not," item 3 |
| R11 | Retired, folded into R8. Number not reused | Brief |
| R12 | Absorbed into R13 as its premise | This document |
| R15 | Absorbed into R14, pending artifact resolution | This document |
| DEVH-4, DEVH-5 | Two script bugs filed during Run 1 | Run 1 |

---

## 9. Open decisions carried into architecture

Four, three of which block something.

1. **R1's target number.** Blocks R1 implementation. Coupled to R21's static-versus-per-category
   choice; the same decision made once.
2. **Where `layered_command_guard.py` lives.** Blocks R14 and R15. A hand-off question for the
   orchestrator, resolvable by naming a path.
3. **A current measured count for R9.** Blocks sizing. The 477 figure is unverified here.
4. **Docker and Postgres on the FCLB build machine.** Carried from the brief. Not blocking
   anything in this document's scope, since nothing here depends on FCLB. Recorded so it is not
   lost.

The brief's fourth open question, whether R0 blocks an autonomous pass, is moot: R0 closed in
Run 1.

---

## 10. Gate status

`PRD.md` exists at the project root. Every requirement in section 3 states a verification or is
explicitly marked blocked with a reason. The out-of-scope section is non-empty. The falsification
pass in section 7 has run and changed four requirements.

This stage is **not mechanically enforced**. No hook checks for `PRD.md` the way
`architecture_gate.py` checks for `ARCHITECTURE.md`. Nothing prevented this document from being
skipped, and nothing will prevent the next one from being skipped either. Stating that plainly
is the only enforcement available at this stage.
