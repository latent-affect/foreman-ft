# PRD — dev-harness Quality, Security and Efficiency Remediation

**Stage:** `foreman:product-requirements` (Run 2, stage 1 of 3)
**Author:** Priya Desai (staff PM persona), session `dev-harness-run2`, branch `build-brief-run2`
**Date:** 2026-09-02
**Input:** `/Users/m5/Downloads/dev-harness-BUILD-BRIEF_2.md` (293 lines, corrected edition)
**Scope:** R1 through R22 of that brief, plus DEVH-2 and two gaps found while verifying it.
**Out of this document's scope:** R0 (Run 1's item. Its ticket closed 2026-09-02 after this
document's correction; the underlying measurement remains unsettled. Cited here, never reopened
or re-scoped by this document. See section 8.1).
**Next stage:** `foreman:architecture` (Clint Eastwood), then `foreman:design-and-scope` (Priya).

---

## 0. How this document was built, and what that changes

The brief is an input, not a source. Every requirement below was checked against the live
tree at `/Users/m5/dev/dev-harness-run2` and against the report artifacts in
`/Users/m5/dev/dev-harness` before it was written down. That pass moved eight items. A ninth and
a tenth row were added later, after the concept gate challenged one of this document's own
claims and a second session re-checked the artifact behind it.

| Brief item | What the brief said | What the artifact says | Effect |
|---|---|---|---|
| R1 | Three files where CC *and* MI both flag | Only `store.py` meets that criterion. `skill_broadscan.py` MI=51.35 (A), `component_coupling.py` MI=41.45 (A) | Criterion split; see R1 |
| R4 | Four SQL sites, "unconfirmed" whether safe | All four parameterize values with `?` and interpolate only identifiers from a module constant or a validated allowlist | Closed on the vulnerability question; R4 becomes annotation plus a regression test |
| R5 | Hex high-entropy string, real secret or not | Lines 44-48 are `EXPECTED_SHIM_SHAS`, SHA-256 digests of git hook shims, used for tamper detection | Closed; R5 becomes a suppression with recorded justification |
| R13(4) | OS sandboxing is "deepest fix, most cost" | `bollard/lib/deny_keychain.sb` plus `neutralize_credentials.sh` already implement a working, empirically verified Seatbelt deny-profile on this machine | Cost estimate is too high; a reference implementation exists |
| R14/R15 | Validate `layered_command_guard.py` | Neither the file nor any of its four named classes exists anywhere on this machine, across two independent searches | Both requirements blocked on an existence question, not a path question. See 0.1 |
| R16 | Un-defer ATLAS D5 (model column) | D5's stated reason is *no data source*: `sessions.jsonl` carries no model field (`docs/atlas-architecture.md:63`, DDL comment at line 600) | Two-part requirement; the data source is the blocking half |
| R18 | Churn reads 1 almost everywhere; probably an ATLAS gap | No analyzed source file has more than one commit touching it. `get_git_churn` counts all history with no window, so churn of 1 is arithmetically correct | Not an ATLAS gap. The real defect is downstream: see R18 |
| — | not in the brief | The three report scripts every number here rests on are untracked (`??`) in git, and one of them overwrites its own prior output on reuse | New requirement R23, sequenced first |
| R0 | "Closed by Run 1" (this document's own earlier claim) | `DEVH-3` read `open` in TESSERA when checked; closed 2026-09-02, after this correction. Four readings of one unstable metric, not two disagreeing measurements. Well-sampled days (n=9) run median 1.93, all above target | Corrected in 8.1 after the concept gate challenged it |

Confidence notation follows the operator's standing rule: **High** = verified against an
independent artifact; **Medium** = internally consistent, not independently confirmed;
**Low** = single observation or assumption.

## 0.1 Provenance risk, high severity — read before scoping R12 through R15

The brief describes `layered_command_guard.py` as existing code, in specific detail, down to a
named AST-folding limitation disclosed "in the file itself" and a four-guard layered design in
which three guards independently caught a case the fourth missed. **No such file, and none of
its four named classes, exists anywhere on this machine.**

Two sessions searched independently, with no shared context between them, across
`/Users/m5` at full depth excluding `Library`, plus `/Users/m5/dev/grok` and every scratchpad
location. Five distinct search terms: the filename, `OSSandboxGuard`, `SemanticResolutionGuard`,
`AllowlistGuard`, `PatternFeedGuard`. A third pass over this repository added `su""do`,
`layered_command`, `allowlist inversion` and `capability by construction`.

**Precise statement of the result, and it is deliberately an invariant rather than a file
list.** Zero hits in executable code anywhere in that scope. Every hit, without exception, is a
narrative document describing or discussing the guard rather than implementing it: the three
successive revisions of the source brief, this pipeline's own PRD, architecture, design-and-scope
and handoff records, and a muse enumeration written the same night.

**The count is not stable and must not be quoted as one.** Two earlier revisions of this section
have already been wrong about it. The first claimed "zero hits on any term in either search",
which was false — `/Users/m5/Downloads` and `/Users/m5/agent-remediation` are inside the stated
scope and both produced hits. The second corrected that but enumerated six specific paths, which
went stale within the hour: the filename search actually returned eight, and two of the
additions (`DESIGN-AND-SCOPE.md`, a muse enumeration) exist *because this pipeline kept writing
documents about the search*. Every document produced in the course of investigating this question
becomes another hit in it. This document is itself on the list.

So the claim is stated as the property that survives that feedback loop: **no executable
implementation exists anywhere in the searched scope, and the set of prose mentions grows
monotonically as this investigation is written up.** A reader re-running the search will find
more files than any snapshot here names, and that is expected rather than a discrepancy. This is
the same correction R18 needed for the same reason — a snapshot count that this project's own
output keeps invalidating.

That precision matters here more than anywhere else in the document, because this sentence is the
sole evidentiary basis for the highest-severity operator escalation in it.

One thing the corrected result adds rather than removes: the design is described in three
successive revisions of the source brief, and no implementation appeared alongside any of them.
That is consistent with both explanations below and settles neither.

Two explanations fit. A scratchpad artifact was written and cleaned up before landing anywhere
durable, which matches a real pattern from other sessions. Or the implementation was described
rather than built. **This document takes no position on which**, and the question is the
operator's to settle, not a reviewer's to assume. What matters for scoping is that both
explanations have the same consequence right now: there is no code to validate, so R14 and R15
cannot be validation requirements, and nothing in R12-R15 that rests only on the brief's
narration can be relied on while planning.

The precedent is on the record and is the reason this is flagged at severity rather than noted
in passing. R0's original 2.3x-3.1x context-growth claim came from the same source, was stated
with the same specificity, and was retracted after independent verification. That is one
confirmed instance of a confidently stated quantitative claim from this brief's lineage not
surviving a check. This would be a second, of a different kind.

**What survives in that section regardless.** Exactly one claim, and it was found in the repo
rather than taken from the brief: `bollard/lib/deny_keychain.sb` and
`bollard/lib/neutralize_credentials.sh` are real, present, and carry a recorded empirical
trial. R13's *judgment* also stands on its own merits, since capability removal beating text
matching is an argument, not a measurement. The scored-candidate pass behind R13's ordering
("12 candidates, six-way tie at the top") has no locatable artifact and should be treated as an
unverified input to Clint's re-ranking, not a result to inherit.

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

**Its measurements are not reproducible, and one of them erases its own evidence.** The health
score, the hotspot ranking and the security convergence report that this whole remediation is
scoped against come from three scripts that are untracked in git. There is no commit to diff a
"before" against. Worse, `token_bloat_diagnostic.py` writes to a fixed filename in the working
directory, so running it twice from one place destroys the first result, which has already
happened to the only dev-harness-scoped run anyone can point at (section 8.1). A measurement
tool that overwrites prior evidence on ordinary reuse is not a hygiene problem, and R23 is
sequenced ahead of every requirement whose verification is a before/after comparison.

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
- **Ordering, carried forward but not inherited as settled:** scoped capability by construction;
  allowlist inversion; friction and confirmation; OS-level sandboxing. Detection candidates
  (semantic resolution, anomaly detection, LLM judge, pattern feed) are additive only. The
  scored pass this ordering came from ("12 candidates, six-way tie at the top") has no locatable
  artifact, per section 0.1. The ordering is defensible on its own argument and Clint should
  re-derive it rather than adopt the numbers.
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

**R14 — BLOCKED. `layered_command_guard.py`'s existence is unconfirmed. Do not treat it as
code that exists.**

Absorbs R15. This is not a missing path. It is a missing artifact, and the distinction changes
what the requirement can say.

Two independent searches ran. This session searched `/Users/m5` at full depth excluding
`Library` for the filename. A second session, with no shared context, searched the same tree
plus `/Users/m5/dev/grok` and every scratchpad location, for the filename **and** for each of
the four class names separately (`OSSandboxGuard`, `SemanticResolutionGuard`, `AllowlistGuard`,
`PatternFeedGuard`). Five search terms, two contexts, zero hits. A third search over this
repository for those terms plus `su""do`, `layered_command`, `allowlist inversion` and
`capability by construction` returns only this file.

- **Requirement:** R14 and R15 shall not enter a backlog, and shall not be sized, until the
  operator confirms whether this code was ever written. The two outcomes need different
  requirements, not a different path.
- **If it existed and was lost:** R15 becomes `SemanticResolutionGuard` shall fold
  `chr(a)+chr(b)` addition, verified by the chr-built `rm` case being caught by that guard alone
  with the other three disabled. R14 becomes `OSSandboxGuard` shall deny the `su""do`
  reproduction, using the verification method `deny_keychain.sb` already documents.
- **If it was never written:** both are build requirements, sized from nothing, and the R12-R15
  section's other technical claims inherit the same doubt. See section 0.1.
- **Confidence: High** that no such file or class exists on this machine, from two independent
  searches over five terms. **Not assessed** on why, and this document takes no position on it.

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

**R17 — Concurrent-session view. Same blocked shape as R16, corrected 2026-09-02.**

An earlier revision of this requirement read "buildable from existing columns... no new
ingestion", on the strength of the DDL declaring every column needed. The columns are declared,
and that half stands: `dim_session` at `docs/atlas-architecture.md:602` carries `session_id`,
`started_ts`, `ended_ts`, `claude_version`, `git_branch`, `start_cwd`. **But nothing populates
the table.** Raised by a separate ATLAS scoping pass and confirmed independently here against
this repository:

- No warehouse database exists in this tree at all.
- `dim_session` appears in exactly one place in the whole of `atlas/`: the `EXPECTED_TABLES` set
  in `atlas/warehouse/tests/test_schema.py:15`. No module reads it and no module writes it.
- `sessions.jsonl`, the file `docs/atlas-architecture.md:63` names as the source for these
  columns, is not referenced anywhere under `atlas/`.

So R17 is blocked on exactly the shape R16 is: schema declared, data source absent. That is the
same mistake this document catches R16's brief text making, reproduced here in my own
requirement — I verified the columns were declared and treated that as verifying the requirement
was buildable. The confidence annotation below was correctly scoped to the columns; the sentence
above it was not, which is how the two came apart.

- **R17a:** an ingest path shall populate `dim_session` from a real source, and at least one row
  shall be shown present, before the view is designed. **Blocked.**
- **R17b:** once R17a holds, a view reporting concurrent sessions per project per time window
  shall exist as a permanent warehouse view, replacing the ad hoc computation in
  `token_bloat_diagnostic.py`'s launch-cluster mode.
- **Verification:** R17a, `SELECT count(*) FROM dim_session` returns non-zero against a real
  warehouse. R17b, the view name appears in `EXPECTED_VIEWS` (`test_schema.py:20`), the schema
  test is green, and the view returns correct rows over known overlapping synthetic sessions —
  not merely "returns rows", per this document's own falsification note.
- **Open question for the architecture stage:** whether `docs/atlas-architecture.md` describes
  this repository's warehouse or another instance. If another, R17a may already be satisfied
  somewhere this tree cannot see, and the answer changes who owns the blocker.
- **Confidence: High** that the columns are declared and that nothing in this tree populates
  them, both read directly. **Low** on whether a populated warehouse exists elsewhere.

**R18 — Fix the hotspot signal, not ATLAS. The churn reading is correct.**

Settled this pass, and the brief's proposed direction was wrong. `get_git_churn`
(`foreman_quality_baseline.py:218`) runs `git log --pretty=format: --name-only` over all history
with no window and counts occurrences. A churn of 1 is the correct answer for a squashed
distribution tree, not a defect, and not an ATLAS capability gap. ATLAS does not compute this
number at all.

The finding is stated as an invariant rather than a commit count, because a commit count is a
moving target and this document's own commits move it. **No analyzed Python source file in this
tree has more than one commit touching it.** The three highest-churn paths in the whole
repository are `scripts/install-dev-harness.sh`, `README.md` and `PRD.md`, at 3 each, all
documents or release plumbing, none of them analyzed source. `tessera/store/store.py` reads 1.
That invariant, not the tree's total, is what the requirement rests on, and it holds across
every re-measurement so far.

The real defect is one line downstream. `hotspot_signal = max_cc * git_commits_touching_file`
(line 405). With churn pinned at 1 across every analyzed file, the signal reduces to `max_cc`.
The ranking is presented as "complexity x git churn" and is single-signal underneath. R1's
selection rests on that ranking.

- **Requirement:** either churn shall be sourced from a repository where this code has real
  history, or the churn factor shall be dropped and the output relabelled to state that the
  ranking is complexity-only on repositories with degenerate history. A signal that names two
  inputs shall not silently carry one.
- **Verification:** either the report shows churn values greater than 1 for analyzed source, or
  the report header and the JSON field name state complexity-only, and a test asserts the
  relabelling holds when every file has churn 1.
- **Confidence: High.** Per-file counts read directly and the multiplication read at line 405.
  Re-measured three times as the tree grew (7, then 9 at the concept gate, then 10), and
  `store.py` read 1 every time. The concept gate independently confirmed the same conclusion at
  9 commits. **Snapshot values in this document are stale by design**; the invariant is the
  claim.

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
- **And on two open bugs closing first (concept gate condition C3).** `DEVH-4` (UTC day
  bucketing) and `DEVH-5` (day-over-day ratio comparing a fixed historical day against an
  in-progress today) are both `open` against `token_bloat_diagnostic.py`, one of the three
  scripts a KPI series would be built on. R21 shall not formalize anything on that tool while
  either is open. Section 8.1 shows what that costs when ignored: a surviving figure generated
  by the exact computation DEVH-5 names as unstable.
- **Confidence: Medium** (the requirement is clear; the metric shape is genuinely undecided).

### New requirements found while verifying the brief

**R23 — The measurement tooling shall be version-controlled, and its outputs shall stop
overwriting each other. Sequence this first.**

Two halves. The second was found after the first was written and is the reason this requirement
moved to the front of the queue.

*R23a, provenance.* `git status` in `/Users/m5/dev/dev-harness` reports
`foreman_quality_baseline.py`, `token_bloat_diagnostic.py` and
`security_privacy_convergence.py`, plus all six of their `.json` and `.txt` outputs, as
untracked. `git ls-files` matching those names returns zero. Every quantitative claim in this
PRD and in the brief traces to one of those three files, and none of them exists in the branch
this work is being done on.

*R23b, evidence destruction on ordinary reuse.* `token_bloat_diagnostic.py` writes to
`Path.cwd() / "token_bloat_diagnostic.json"` and the matching `.txt` (lines 503-504). Fixed
name, no timestamp, no run id, no scope marker. Running it twice from one directory destroys
the first result, and that has already happened to the only dev-harness-scoped run anyone can
point at. Section 8.1 has the demonstration. This is not a hypothetical failure mode being
guarded against, it is a completed one being reported.

- **Requirement:** all three scripts shall be tracked in git; each generated report shall record
  the tool commit SHA and the analyzed tree SHA in its own output; and no run shall be capable
  of overwriting a prior run's output, whether by run-scoped filenames or by refusing to write
  over an existing file.
- **Verification:** `git ls-files` returns all three paths; a fresh report contains both SHAs;
  and two consecutive runs from one directory with different arguments leave two readable
  results, not one.
- **Sequencing, and this is the point:** R23 shall land before R1, R3 or R21 produce any number
  intended to be relied on. A tool that is untracked *and* destroys prior evidence on normal use
  cannot support a before/after comparison, and R3 and R21 are both nothing but before/after
  comparisons. Treating this as hygiene and deferring it is how R0 ended up with two
  disagreeing scoped numbers and complete raw output for neither.
- **Confidence: High** on both halves. R23a from `git status --short` read directly; R23b from
  the script's own lines 503-504 and docstring line 45, plus the surviving evidence of the
  overwrite in section 8.1.

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

- **R0, context-size reduction.** Run 1's item, not this document's. DEVH-3 is now `closed`
  (verified 2026-09-02T09:32Z), transitioned by the Run 1 session after this document's
  correction reached it. Out of scope here because it belongs to another run. Note the ticket
  closing is not the same as the measurement resolving; see 8.1. Its original 2.3x-3.1x claim is separately and genuinely
  retracted. Section 8 states the real status.
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

## 8. Disposition of items not carried as requirements

| Item | Disposition | Evidence |
|---|---|---|
| R6 | No action. `cli.py` is a flat dispatcher, NIST 500-235 named exception | Independently supported: MI 33.1, grade A |
| R10 | No change to Nadia. Confirmed intentional | Brief section "what this is not," item 3 |
| R11 | Retired, folded into R8. Number not reused | Brief |
| R12 | Absorbed into R13 as its premise | This document |
| R15 | Absorbed into R14, pending the existence question in 0.1 | This document |
| DEVH-4, DEVH-5 | Two script bugs filed during Run 1. Both still `open` | TESSERA, verified 2026-09-02 |

### 8.1 R0's ticket was open, not closed. Correcting this document's own earlier claim.

An earlier revision of this PRD recorded R0 as "Closed by Run 1," sourced to a Run 1 exit report
that said so in prose. The concept gate challenged it and was right. I verified the ticket
myself rather than accept either account: `DEVH-3` read **`open`** in TESSERA
(`/Users/m5/dev/ticket-system/data/tessera.db`, queried read-only 2026-09-02). A run reporting
itself finished is not a transition, and I should have checked before writing the row. This is
the same self-report-versus-artifact gap section 0.1 exists to warn about, appearing in my own
document, which is the reason it gets a correction here rather than a silent edit.

**Subsequent update, 2026-09-02T09:32Z.** The Run 1 session was told about this and transitioned
`DEVH-3` to `closed`, with a comment recording what actually produced the retracted number. The
ticket status above is left as it was found, because it is the evidence for the correction, not a
live status field. Verified independently by re-querying rather than taken from that session's
own report of having done it — which is the whole point of this subsection. **`DEVH-4` and
`DEVH-5` remain `open`**, and that matters more than DEVH-3's state: they are the two bugs that
make the surviving 1.16x figure untrustworthy, so closing R0's ticket did not resolve R0's
measurement. A closed ticket and a settled number are different things here.

The honest status is not "closed" and not "no data exists." It is unsettled across two
incomplete framings.

**Framing one, single-project, and the reason no reading of it settles anything.** An earlier
revision of this subsection described "two scoped numbers that disagree", which framed them as
two competing measurements of one stable quantity. That framing was wrong, and it came from an
attribution another session later retracted as an unstated inference. Corrected:

| Reading | Source | Raw output |
|---|---|---|
| **0.96x**, under target | Reported in the brief's own R0 correction | None. The brief says plainly it "was never shown here, only summarized" |
| **1.1x**, at target | A second Run 1 terminal run, same session, ~22:44 | Not retained |
| **1.16x**, over target | `/Users/m5/dev/investigation/dev-harness-token-bloat-diagnostic-scoped-20260901.txt`, 297 bytes | Console summary only; its JSON was overwritten |
| **0.64x**, far under target | The current `token_bloat_diagnostic.json`, verified by reading it in this session | Present, but **not dev-harness-scoped** — `project_hint` is `m5-dev-`, 149 session files |

Four readings, not two, and they are not disagreeing measurements. They are one unstable metric
sampled at four different moments, which is what DEVH-5 already says about it: the day-over-day
ratio compares a fixed historical day against an in-progress one.

**The evidence is inside the surviving JSON's own per-day series, and it needs stating carefully
because the obvious version of the argument does not hold.** An earlier revision of this
subsection pointed at the raw spread — 1.0, 2.5, 0.25, 1.34, 2.09, 2.11, 1.75, 0.5, 1.82, 1.25,
1.93, 2.14, 1.24, 1.96, 1.78, 1.78, 0.64 — and called a tenfold range decisive. It is not. The
three lowest ratios sit on the three thinnest days in the file (47, 42 and 106 turns), so a
skeptic discounts them as small-sample noise and the spread collapses toward twofold. That
objection is correct and the claim was overstated.

The form that survives it: restrict to well-sampled days, at least 1000 turns, and read what is
left. n=9, verified from the file in this session:

| Day | Turns | Ratio |
|---|---|---|
| 2026-08-15 | 1,793 | 2.09 |
| 2026-08-16 | 1,840 | 2.11 |
| 2026-08-19 | 3,805 | 1.82 |
| 2026-08-20 | 1,212 | 1.25 |
| 2026-08-21 | 2,194 | 1.93 |
| 2026-08-22 | 1,003 | 2.14 |
| 2026-08-26 | 4,632 | 1.96 |
| 2026-08-27 | 5,254 | 1.78 |
| 2026-08-28 | 3,067 | 1.78 |

Median 1.93. Every well-sampled day sits above the 1.1 target and none is near parity. Meanwhile
the headline 0.64x that reads as comfortably under target is a **106-turn partial day**, and the
`day_one` every ratio is divided by is itself only **262 turns**. That is DEVH-5's mechanism
shown rather than asserted: a two-point comparison between two arbitrary days, either of which
can be thin, which is exactly why no single reading closes anything.

**The honest limit, named rather than left implicit:** none of this says *why* well-sampled days
run high. A real workload change and a metric defect are both live explanations and this data
cannot separate them. **Confidence: High** on the arithmetic and the turn counts, read directly
from the file. **Medium** on 1000 turns being the right cutoff — it is a reasonable line, not a
derived one.

That same file also carries `day_one: 2026-08-06`. dev-harness's own transcripts do not begin
until 2026-08-23, a point the brief's own R0 correction makes independently. So the baseline day
every one of those ratios is computed against belongs to a different project's data.

Put plainly, and this is the sentence worth keeping: **R0 was closed on a single favorable
reading of a metric whose own open ticket says it is not stable enough to close anything on.**
DEVH-4 and DEVH-5 are both still `open`, verified in this session.

**Why the second one's raw output is gone, and why that is a finding rather than an accident.**
`token_bloat_diagnostic.py` writes its results to a fixed filename in the current working
directory: `Path.cwd() / "token_bloat_diagnostic.json"` and the matching `.txt` (lines 503-504;
the module docstring at line 45 says so outright, "next to wherever you run it"). No timestamp,
no run id, no scope in the filename. Every run from the same directory overwrites the last one.
Both runs were made from `/Users/m5/dev/dev-harness`, so the later `/dev`-wide run destroyed the
scoped run's JSON. This is demonstrated, not inferred: that file now carries
`project_hint: "m5-dev-"` over 149 session files, which is the broader run's output, not the
scoped one's.

**And the surviving number is produced by a computation with an open bug against it.** DEVH-5,
`open`: "day_over_day ratio compares a fixed historical day against an in-progress 'today',
producing an unstable number." A latest-day-versus-day-one ratio is exactly that computation.
DEVH-4, also `open`, adds UTC bucketing that misfiles evening Pacific sessions into the next
day. So 1.16x is over target and is not a number that can be read as settled even on its own
terms.

*One correction to how this reached me:* it was relayed as a hardcoded output path. It is not
hardcoded, it is cwd-relative with a fixed name, which is a slightly different mechanism with
the same consequence. Recorded because the mechanism is what a fix has to address.

**Framing two, cross-project, also incomplete.** The operator redirected the investigation after
that run, calling the single-project framing "missing the point," and asked for a pre- versus
post-Foreman-adoption comparison across all of `/dev` instead. That work was not completed; it
still lacks the operator's actual cutover date, which is an input only he holds.

**Effect on this document: none.** No requirement in section 3 depends on R0's status, and R0 is
another run's item. The correction matters for traceability, not for scope. The one thing it
does change is section 9's disposal of the brief's fourth open question.

---

## 9. Open decisions carried into architecture

Five, three of which block something. The first is for the operator, not the architecture stage,
and it is the highest-severity open item in this document.

1. **HIGH — Was `layered_command_guard.py` ever written?** Blocks R14 and R15 entirely, and
   sets how much of R12-R15 can be trusted while planning. Not answerable by search: four
   independent passes over five terms have now returned nothing, the fourth run by the concept
   gate in a context that authored neither this PRD nor the brief. Needs the operator's own
   recall or a pointer to a machine nobody has searched. Full statement in section 0.1.
   **Default if unanswered by architecture freeze, set by the concept gate (C1) and adopted
   here:** treat it as never written. R14 and R15 become build-from-nothing requirements sized
   at design-and-scope, and every R12-R15 technical claim resting only on the brief's narration
   gets re-derived rather than inherited. The default exists so an unanswered question cannot
   silently become an assumed yes.
2. **R1's target number.** Blocks R1 implementation. Coupled to R21's static-versus-per-category
   choice; the same decision made once.
3. **A current measured count for R9.** Blocks sizing. The 477 figure is unverified here.
4. **Docker and Postgres on the FCLB build machine.** Carried from the brief. Not blocking
   anything in this document's scope, since nothing here depends on FCLB. Recorded so it is not
   lost.
5. **Whether R0 blocks the start of an autonomous pass.** The brief's own fourth open question.
   An earlier revision called it moot because R0 had closed, which was wrong at the time. R0's
   ticket has since genuinely closed (2026-09-02T09:32Z), but its measurement has not settled and
   DEVH-4/DEVH-5 are still open, so the question was never actually answered on the merits. It
   was settled for this run by decision: the operator gave explicit go-ahead for Run 2 with R0
   unresolved. That answers it once, not as a standing rule, so it stays on the list.

---

## 10. Gate status

`PRD.md` exists at the project root. Every requirement in section 3 states a verification or is
explicitly marked blocked with a reason. The out-of-scope section is non-empty. The falsification
pass in section 7 has run and changed four requirements.

**Concept gate: GO with three conditions.** Package at
`/Users/m5/dev/dev-harness-run2/.foreman/tpm-gate-concept.json`, revision 2, graded against
commit `808558c`. All three conditions are discharged in this document: C1's default is adopted
verbatim at section 9 item 1, C2's correction is section 8.1, C3's sequencing is R23's
sequencing clause and its two-open-bugs dependency is on R21.

C2 was this document's own error: an unverified "R0 closed by Run 1" row in section 8. Corrected
with the ticket status checked directly against TESSERA rather than taken from the Run 1 report
or from the gate's account of it. Four other statements resting on that assumption were tracked
down and fixed in the same pass (the header, the out-of-scope entry, the section 8 table, and
section 9's disposal of the brief's fourth open question, which is now item 5 and live again).

The gate also caught a stale number: section 0 stated a 7-commit tree, already 9 when the gate
read it and 10 now, three of those this document's own. R18 is rewritten to rest on an invariant
that survives the drift (no analyzed source file has more than one commit touching it) rather
than on a snapshot count. Other snapshot figures in this document are stale by the same
mechanism and should be read that way.

Worth recording rather than quietly fixing: the error was mine, it was the one class of error
this document's own section 0.1 was written to warn about, and it took an outside context to
catch it. A reviewer who has read this far is entitled to weigh that when deciding how much of
the rest to take on trust.

This stage is **not mechanically enforced**. No hook checks for `PRD.md` the way
`architecture_gate.py` checks for `ARCHITECTURE.md`. Nothing prevented this document from being
skipped, and nothing will prevent the next one from being skipped either. Stating that plainly
is the only enforcement available at this stage.
