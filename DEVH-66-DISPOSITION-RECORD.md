# Run-2 disposition record (DEVH-66)

Generated 2026-09-03T17:00:47Z by `buildDispositionRecord.py`, from the live TESSERA DB (read-only) and real git history in three repos. Regenerable: nothing here was typed by hand.

**Sourcing rule, per DEVH-66's own statement.** A disposition is recorded only from a ticket's live status, a real commit citing that ticket, or a comment on the ticket itself. No session report, handoff doc, or orchestrator summary was treated as evidence, including this session's own.

**The linkage in this record was reconstructed, not read.** TESSERA's `ticket_commit_links` table holds **0** rows, so the system's own ticket-to-commit mechanism carries nothing. Every commit association below comes from scanning commit-message text for ticket ids across all branches of the three repos. A commit that fixed something without naming it is invisible to this method, and that limitation cannot be closed from the data.

- In-scope tickets (DEVH/DEVHR2/FORE created since 2026-09-02): **134**
- Commits in the same window across 3 repos: **42**

## Segmentation, and why the run boundary is stated rather than assumed

Ticket creation runs continuously through 2026-09-02 daytime, then surges again at `2026-09-03T03:00Z` (2026-09-02 20:00 local) -- the overnight run that hard-froze at 05:41 local and resumed. "This run" has no unambiguous start in the durable data, so both segments are reported and the reader picks. Nothing is excluded on a judgment call about scope.

| disposition | overnight | daytime 09-02 |
|---|---|---|
| CLOSED-CODE | 10 | 11 |
| CLOSED-COMMENT-ONLY | 0 | 21 |
| CLOSED-UNTRACEABLE | 0 | 0 |
| OPEN-CODE-LANDED | 16 | 5 |
| FILED-NOT-FIXED | 2 | 11 |
| FILED-BARE | 14 | 44 |

| disposition | count | meaning |
|---|---|---|
| CLOSED-CODE | 21 | closed, and a real commit in the window cites it |
| CLOSED-COMMENT-ONLY | 21 | closed on ticket-thread evidence, no commit cites it |
| CLOSED-UNTRACEABLE | 0 | closed with neither a commit nor a comment -- no recorded basis |
| OPEN-CODE-LANDED | 21 | still open, but code citing it landed -- partial or unclosed |
| FILED-NOT-FIXED | 13 | open with discussion recorded, no code -- correct if deliberate |
| FILED-BARE | 58 | open, zero comments, zero commits -- filed and untouched since |

## CLOSED-CODE (21)

| ticket | seg | P/S | status | evidence | summary |
|---|---|---|---|---|---|
| DEVH-2 | day | P1/SNone | closed | run2@612f769 | dev-harness does not run its own shipped security guards (guard_destructive/guard_prodconfig/gua |
| DEVH-16 | day | P1/S2 | closed | run2@5825a6f, run2@b377168, run2@a392fe5 | R14/R15: layered_command_guard.py treated as never written (C1 default applied, architecture fro |
| DEVH-52 | day | P0/S1 | closed | run2@70d605a, run2@81b44aa | tessguard's git hooks were never wired (core.hooksPath unset) -- every commit and push in dev-ha |
| DEVH-54 | day | P1/S2 | closed | run2@f7e09f4, run2@e03fb72, run2@24bfb74 | tessguard's git hooks fail closed for every commit made from a worktree other than the registere |
| DEVH-56 | day | P0/S1 | closed | run2@766946b, run2@d34b490, run2@e03fb72 | TESSGUARD_DB_PATH is not set persistently anywhere -- every real commit fails closed on an unres |
| DEVH-59 | day | P2/S3 | closed | run2@f7e09f4 | bollard/tessera_resolver.py has the same worktree-path-mismatch bug DEVH-54 fixed in tessguard,  |
| DEVH-61 | day | P1/S2 | closed | run2@562edc6, run2@78bbd9d, run2@335085b | R14/R15 architecture recycle (1/4): resolve whether R13's capability-removal-first ordering is a |
| DEVH-62 | day | P1/S2 | closed | run2@d0756f6, run2@04a69a8, run2@e0b75df | R14/R15 architecture recycle (2/4): component decomposition of OSSandboxGuard/SemanticResolution |
| DEVH-72 | day | P1/S1 | closed | run2@2f61a16, run2@5d4dd38, run2@335085b | R14/R15 blocker: the four new guard files resolve component_of=None -- goals_freeze_gate never g |
| DEVH-73 | day | P2/S2 | closed | run2@335085b | R14/R15 architecture prose is stale: still reads BLOCKED with 'no interface space reserved', doe |
| DEVH-75 | day | P1/S2 | closed | run2@a392fe5, run2@ff2f189, run2@d0756f6 | C16's discriminator run cannot pass: Claude Code's own ambient safety layer intercepts destructi |
| DEVH-76 | night | P1/S2 | closed | run2@10ead5a | REQ-13a architecture: replace/extend regex command-matching with parse-then-deny-on-obfuscation, |
| DEVH-79 | night | P2/S2 | closed | run2@2f61a16, run2@5d4dd38 | bollard/GOALS.json C9 regression-guard test never built (fell through after DEVH-72) |
| DEVH-80 | night | P1/S1 | closed | run2@2f61a16, run2@79e8089, claude-hooks-v2@bbb9f7c | C19 gap: guard_os_sandbox._profile_can_deny() matches deny-text inside a string literal, not jus |
| DEVH-81 | night | P0/S0 | closed | run2@9aeb9b2, run2@48c0e52, run2@a9d9fc0 | ACTIVE REGRESSION: bob_write_gate.py denies every Write from every session, not just Bob dispatc |
| DEVH-83 | night | P2/S2 | closed | run2@4f8c5f0, run2@2f61a16 | bollard/GOALS.json C9 regression-guard test never built (fell through after DEVH-72) |
| DEVH-85 | night | P0/S0 | closed | run2@9aeb9b2, run2@8fbff71, run2@a9d9fc0 | DEVH-81's fix introduces a real authorization bypass -- index/record desync silently allows unap |
| DEVH-87 | night | P0/S0 | closed | run2@9aeb9b2, run2@e10fee4, run2@a9d9fc0 | Independently re-falsify DEVH-85's fix, then jointly re-register bob_write_gate.py + bob_write_c |
| DEVHR2-1 | night | P3/S3 | closed | run2@9aeb9b2, run2@e10fee4, run2@8fbff71 | Cross-reference: this repo's real R14/R15 guard-chain work is tracked under DEVH (project DEV-HA |
| DEVHR2-2 | night | P2/S2 | closed | run2@4f8c5f0, run2@5d4dd38 | bollard/GOALS.json C9 regression-guard test never built (fell through after DEVH-72) |
| FORE-285 | night | P2/S2 | closed | claude-hooks-v2@fb72ac7, claude-hooks-v2@cf57677 | bob_write_gate.py sibling-hook desync (case 18) untested against a real sibling guard -- unexecu |

## CLOSED-COMMENT-ONLY (21)

| ticket | seg | P/S | status | evidence | summary |
|---|---|---|---|---|---|
| DEVH-3 | day | P2/S2 | closed | 1 comment(s) | R0: Reduce per-turn context size (dev-harness token-bloat hotfix) |
| DEVH-6 | day | P0/S1 | closed | 1 comment(s) | R23: version-control the three measurement scripts and stop their reports overwriting each other |
| DEVH-7 | day | P2/S2 | closed | 2 comment(s) | R1: complexity/maintainability remediation, with the selection criterion corrected |
| DEVH-8 | day | P2/S2 | closed | 1 comment(s) | R2: characterization tests before refactoring, each proven to fail against a mutant |
| DEVH-10 | day | P3/S3 | closed | 1 comment(s) | R4: annotate store.py's four f-string SQL sites and pin the allowlist with a test |
| DEVH-11 | day | P3/S4 | closed | 1 comment(s) | R5: record an audited detect-secrets baseline entry for EXPECTED_SHIM_SHAS |
| DEVH-12 | day | P3/S2 | closed | 4 comment(s) | R7: STRIDE trust-boundary review of atlas/mcp/server.py |
| DEVH-13 | day | P1/S1 | closed | 1 comment(s) | R8: scrub credentials at audit ingest, fail-closed, before the row is ever persisted |
| DEVH-19 | day | P2/S3 | closed | 2 comment(s) | R18: relabel the hotspot signal -- it names two inputs and carries one |
| DEVH-22 | day | P2/S2 | closed | 1 comment(s) | R22: inventory every verbatim-persistence point, not just audit_event |
| DEVH-29 | day | P1/S2 | closed | 1 comment(s) | verify_settings_shape() only checks hooks under the exact matcher 'Edit/Write/Bash', so R24's gu |
| DEVH-30 | day | P2/S2 | closed | 1 comment(s) | R23 shipped with no committed test suite, so its own verification is not reproducible |
| DEVH-36 | day | P2/S3 | closed | 1 comment(s) | token_bloat_diagnostic emits two different report schemas depending on whether anything matched |
| DEVH-38 | day | P2/S2 | closed | 1 comment(s) | scripts C5: add installed-copy denial probes for guard_prodconfig and guard_untrusted_web |
| DEVH-40 | day | P2/S2 | closed | 1 comment(s) | DDL extraction scans past section 16 and can silently return a wrong seed block |
| DEVH-45 | day | P1/S2 | closed | 1 comment(s) | root suite is currently RED: test_c1_all_three_scripts_are_tracked asserts store.py max_cc >= 20 |
| DEVH-49 | day | P2/S3 | closed | 1 comment(s) | F1: invert MCP_VIEWS from denylist-of-one to explicit allowlist |
| DEVH-50 | day | P1/S2 | closed | 1 comment(s) | F2: QueryFacade.fetch() must push LIMIT into SQL, not slice in Python after fetchall() |
| DEVH-51 | day | P3/S3 | closed | 1 comment(s) | F3: monitoring's facade-error paths leak the absolute warehouse path via str(exc) |
| DEVH-63 | day | P1/S2 | closed | 1 comment(s) | R14/R15 architecture recycle (3/4): interface contract + composition/ordering semantics ('layere |
| DEVH-64 | day | P1/S2 | closed | 1 comment(s) | R14/R15 architecture recycle (4/4): falsification pass on the guard-chain design, same disciplin |

## OPEN-CODE-LANDED (21)

| ticket | seg | P/S | status | evidence | summary |
|---|---|---|---|---|---|
| DEVH-66 | day | P1/S2 | open | run2@9aeb9b2, run2@4f8c5f0, run2@48ca35f | Run 2 closure pass: verified disposition record (closed / filed-not-fixed / recycled-and-held) |
| DEVH-68 | day | P2/S2 | open | run2@9aeb9b2, run2@8fbff71, run2@815a481 | 12 unverified detect-secrets findings across 6 files, repo-wide (beyond R5's already-verified fi |
| DEVH-70 | day | P1/S2 | open | run2@8fbff71 | hook_common.py's shared run() harness is fail-open by design -- a systemic gap for any future pr |
| DEVH-74 | day | P1/S2 | open | run2@8fbff71 | Harness bug (NOT agent PDP drift): auto-mode's permission classifier blocked a legitimate test-s |
| DEVH-78 | night | P2/S2 | open | run2@2f61a16 | bollard/GOALS.json results[] has duplicate criterion IDs with conflicting statuses (C18 x3, C16  |
| DEVH-84 | night | P3/S3 | open | run2@c809282, run2@cd08c70, run2@2f61a16 | results[] append-only in bollard/GOALS.json is convention, not a mechanism -- nothing reads or e |
| DEVH-86 | night | P1/S1 | open | run2@9aeb9b2, run2@e10fee4, run2@bc0f379 | Alice/Bob write-gate consolidation: implementation-coverage backlog, tonight's active-regression |
| DEVH-88 | night | P1/S2 | open | run2@c809282 | Stale test artifact class in the dispatch index (foreman/dispatch-index/) has no cleanup/stalene |
| DEVH-89 | night | P2/S2 | open | run2@8fbff71, run2@078f4a0, run2@2b6e4fa | Decision pending review (Nadia Osei / security lane): narrow bob_write_gate.py's case 6, or leav |
| DEVH-90 | night | P2/S3 | open | run2@9aeb9b2, run2@48c0e52, run2@f7e09f4 | 3 standing QA worktrees created: qa-security, qa-general, qa-evasion |
| DEVH-91 | night | P2/S2 | open | run2@9aeb9b2, run2@078f4a0 | bollard guard chain is stateless per-invocation and has no tool_name branch for Task/Agent -- se |
| DEVH-92 | night | P2/S2 | open | run2@9aeb9b2 | 5 of 10 in-scope evasion-taxonomy patterns are clean misses against the live bollard guard chain |
| DEVH-93 | night | P2/S2 | open | run2@8fbff71 | Hypothesis: auto-mode classifier may be context-sensitive to recent tool-call history, not purel |
| DEVH-94 | night | P2/S3 | open | run2@9aeb9b2 | evasion_taxonomy_battery.py status classification ignores stderr -- stdin-malformed guard input  |
| DEVHR2-3 | night | P2/S2 | open | run2@9aeb9b2, run2@4f8c5f0 | C9's regression guard covers a named subset of bollard/, not the component -- an ordinary bollar |
| FORE-265 | day | P0/S1 | open | run2@2b6e4fa | REQ-55: named decision-rights model for the PDP (RAPID/DACI-grounded) -- gate owners and orchest |
| FORE-281 | night | P1/S2 | open | run2@c809282, run2@2b6e4fa, run2@a9d9fc0 | CHV2: session_prior + dispatch_record reconciliation, then case 20 in bob_write_gate.py -- expli |
| FORE-282 | night | P2/S2 | open | run2@c809282, run2@cd08c70, run2@48c0e52 | TESSERA project resolution has no concept of a worktree inheriting its parent repo's registered  |
| FORE-287 | night | P2/S3 | open | run2@9aeb9b2, run2@48c0e52 | Self-permission-file edits (.claude/settings.json) require human execution -- correctly, but wit |
| FORE-288 | night | P2/S2 | open | run2@f7e09f4 | tessera_resolver.py's worktree-path-mismatch bug (DEVH-59 class) also lives in foreman-v2-qa and |
| FORE-290 | night | P0/S1 | open | run2@9aeb9b2 | Canonical Foreman bootstrap (settings.json.template) is stale on 3 independent axes -- missing 3 |

## FILED-NOT-FIXED (13)

| ticket | seg | P/S | status | evidence | summary |
|---|---|---|---|---|---|
| DEVH-18 | day | P3/S3 | open | 1 comment(s) | R17: permanent concurrent-sessions-per-project view, buildable from existing columns |
| DEVH-20 | day | P3/S3 | open | 1 comment(s) | R19: concurrent sessions message each other with a stated review expectation |
| DEVH-21 | day | P3/S3 | open | 1 comment(s) | R21: quality scoring as a standing KPI series rather than a one-off report |
| DEVH-31 | day | P2/S2 | open | 1 comment(s) | Nothing cross-references PRD requirements against architecture coverage, and the thing that woul |
| DEVH-39 | day | P1/S2 | open | 1 comment(s) | Subagent-to-orchestrator narrative reports duplicate TESSERA's own structured record -- real mea |
| DEVH-42 | day | P2/S2 | open | 1 comment(s) | Concurrent git commits in a shared worktree can race and mis-attribute staged changes to an unre |
| DEVH-71 | day | P2/S2 | open | 1 comment(s) | DF finding for Marcus (FT-to-Prod V2): toy-models Skill enforcement for Tier 3 architecture orig |
| DEVH-82 | night | P3/S3 | open | 1 comment(s) | Cross-reference: this repo's real R14/R15 guard-chain work is tracked under DEVH (project DEV-HA |
| FORE-262 | day | P1/S2 | open | 2 comment(s) | REQ-53: writing results[] is the last action of verification, not a separate follow-up -- 7 conf |
| FORE-264 | day | P1/S2 | open | 1 comment(s) | Reminder: dev-harness-qa is a live QA-build fork -- operator opening a session there to coordina |
| FORE-266 | day | P1/S2 | open | 1 comment(s) | REQ-56: mechanically re-inject the PDP into session context, extending the real, already-proven  |
| FORE-268 | day | P1/S2 | open | 3 comment(s) | Design the real V2 DF-to-Prod promotion process, using tonight's atlas-sonnet-qa canonical propa |
| FORE-272 | night | P1/S2 | open | 2 comment(s) | Fable 5.1 cross-family audit: Bob's KPI/deny-surface backlog (ledger_origin -> ATLASSN datasets  |

## FILED-BARE (58)

| ticket | seg | P/S | status | evidence | summary |
|---|---|---|---|---|---|
| DEVH-4 | day | P2/S2 | open | **none** | token_bloat_diagnostic.py buckets days by UTC, not local time -- misfiles evening PDT sessions i |
| DEVH-5 | day | P1/SNone | open | **none** | token_bloat_diagnostic.py's day_over_day ratio compares a fixed historical day against an in-pro |
| DEVH-9 | day | P2/S2 | open | **none** | R3: second quality baseline run, executed by a context that did not do the remediation |
| DEVH-14 | day | P2/S2 | open | **none** | R9: route audit rows by resolved cwd at write time, then promote the check to contract |
| DEVH-15 | day | P2/S2 | open | **none** | R13: capability removal as the primary control; detection layers never the only control |
| DEVH-17 | day | P3/S3 | open | **none** | R16: model identity in dim_session -- establish a data source before adding the column |
| DEVH-23 | day | P2/S3 | open | **none** | Declare PRD.md and DESIGN-AND-SCOPE.md in CONTROL_FILENAMES so their exemption matches its reaso |
| DEVH-24 | day | P3/S3 | open | **none** | review_notify.py must fire on governance-document edits, not skip them (amends R19 / DEVH-20) |
| DEVH-25 | day | P3/S3 | open | **none** | foreman_quality_baseline.py and security_privacy_convergence.py share token_bloat_diagnostic.py' |
| DEVH-26 | day | P2/S3 | open | **none** | Pre-implementation brief: docs/atlas-architecture.md edit for R8 (payload_redacted column, crede |
| DEVH-27 | day | P2/S3 | open | **none** | Pre-implementation brief: atlas component, R8 implementation (audit_scrub.py + wiring + tests) |
| DEVH-28 | day | P3/S3 | open | **none** | docs/ has no real design-and-scope pass, and it holds the live DDL rather than only prose |
| DEVH-32 | day | P2/S2 | open | **none** | foreman:product-requirements SKILL.md states the PRQ tier mapping inverted, and keeps propagatin |
| DEVH-33 | day | P1/S3 | open | **none** | Pre-implementation brief: bollard component, R24 guards (guard_destructive/prodconfig/untrusted_ |
| DEVH-34 | day | P1/S3 | open | **none** | Pre-implementation brief: skills component, R24 template registration + DEVH-29 fix |
| DEVH-35 | day | P1/S3 | open | **none** | Pre-implementation brief: scripts component, R24 install preflight + installed-copy probe |
| DEVH-37 | day | P1/S3 | open | **none** | Pre-implementation brief: tessera component, store.py refactor (R1/R2/R3, DEVH-7/8/9) |
| DEVH-41 | day | P2/S2 | open | **none** | First-match sweep (2 of 2): foreman_evidence.py's hash extraction has no uniqueness check; parse |
| DEVH-43 | day | P2/S3 | open | **none** | Pre-implementation brief: root-docs-and-scratch component, DEVH-36 schema unification |
| DEVH-44 | day | P3/S3 | open | **none** | Close the monitoring->atlas integration PARTIAL when a warehouse database exists |
| DEVH-46 | day | P2/S2 | open | **none** | Pre-implementation brief: monitoring component, facade exception-handling fix (Check 6 finding) |
| DEVH-47 | day | P2/S2 | open | **none** | gates_by_prefix crashes with TypeError on an empty-but-clean warehouse (zero hook_verdict rows)  |
| DEVH-48 | day | P3/S4 | open | **none** | Audit the 12 unaudited detect-secrets findings outside config.py |
| DEVH-53 | day | P3/S3 | open | **none** | Self-caught: an ad hoc verification one-liner reported a clause satisfied when its target file d |
| DEVH-55 | day | P2/S3 | open | **none** | DEVH-52 follow-up: install-dev-harness.sh should run core.hooksPath, not print it; check tessera |
| DEVH-57 | day | P2/S3 | open | **none** | Recurring pattern: real, verified-fixed defects sit in open TESSERA tickets, undercounting no_op |
| DEVH-58 | day | P3/S3 | open | **none** | README.md's 'Honest number' section is stale and scoped to the wrong ticket set -- must be fixed |
| DEVH-60 | day | P2/S2 | open | **none** | F4: atlas/mcp/server.py exception paths leak absolute warehouse path to untrusted MCP caller |
| DEVH-65 | day | P2/S2 | open | **none** | Harden bollard's guard_prodconfig.py to match claude-hooks-v2's adversarially-reviewed bar (syml |
| DEVH-67 | day | P3/S3 | open | **none** | README's 'The honest number' section is stale: wrong PRD path and a closed-count that predates t |
| DEVH-69 | day | P3/S3 | open | **none** | DF-to-Prod metrics are documented but not mechanically wired -- no dogfood_metrics block in SHIP |
| DEVH-77 | night | P2/S2 | open | **none** | bollard/verdict_ledger.py record() silently swallows all exceptions -- 'no row' is ambiguous bet |
| DEVH-95 | night | P0/S1 | open | **none** | dev-harness-run2-e1 and -e2 have zero Foreman hooks registered -- same P0 condition as the QA wo |
| FORE-254 | day | P1/S2 | open | **none** | Dogfood finding: a shared skill file carried a persistent, self-reinforcing error invisible to s |
| FORE-255 | day | P0/S1 | open | **none** | REQ-49: escalation minimalism in a declared E2E run -- only Marcus's Go/Kill/Hold is a mandatory |
| FORE-256 | day | P2/S3 | open | **none** | REQ-50: no standing mechanism re-checks ticket tier assignments after a schema/skill correction  |
| FORE-257 | day | P0/S1 | open | **none** | Dogfood finding for Marcus: an orchestrating session wrote directly into three foreign TESSERA p |
| FORE-258 | day | P1/S2 | open | **none** | REQ-51: inter-session reporting protocol -- pointer-first, artifact-checked ('does an artifact c |
| FORE-259 | day | P2/S2 | open | **none** | REQ-52: a stage shall state what it deliberately excluded from its own scope, not only what it c |
| FORE-260 | day | P0/S1 | open | **none** | Dogfood finding for Marcus+Priya: recurring first-match-standing-in-for-completeness code patter |
| FORE-261 | day | P1/S2 | open | **none** | Use real pre-Foreman project history as the vanilla-Claude-Code baseline, instead of waiting on  |
| FORE-263 | day | P0/S1 | open | **none** | REQ-54: QA builds -- KPI-gated quality/security/privacy review stage, builder+reviewer in separa |
| FORE-267 | day | P2/S3 | open | **none** | PDP consolidation executed: REQ index, supersession pointer-banners, REQ-27/39 archived after li |
| FORE-269 | day | P1/S2 | open | **none** | REQ-57: after a gate's first full pass, re-verification is a bounded spot-check, not another exh |
| FORE-270 | day | P2/S3 | open | **none** | Clean-room UTM/VM Claude Code baseline: the real fix for 'no pre-gate transcript history exists  |
| FORE-271 | day | P2/S2 | open | **none** | foreman-v2-qa (qa-builds branch) is stale: missing REQ-55/56 and tonight's FORE-267 PDP consolid |
| FORE-273 | night | P1/S2 | open | **none** | CHV2: ledger_origin writer-identity field on every hook_verdict row + negative control proving t |
| FORE-274 | night | P2/S2 | open | **none** | Trace the missing verdict='error' row: Fable's ingest-run-43 claim of 24 reproduces as 23 on ind |
| FORE-275 | night | P2/S2 | open | **none** | ATLASSN: migration 8 (non-hook-writer bucket) + three dq_runner.py changes (distinct-cwd floor,  |
| FORE-276 | night | P2/S3 | open | **none** | ATLASSN: split run_pull into a fast plane (10-15s tails+incremental resolve) and a slow plane (5 |
| FORE-277 | night | P2/S3 | open | **none** | ATLASSN: bash_command_shape backfill over 58,747 existing Bash calls + two views (prevalence by  |
| FORE-278 | night | P2/S3 | open | **none** | ATLASSN: graduate qa_pilot_snapshot (drop pilot flag, add session_id/dispatch_id/config_sha) and |
| FORE-279 | night | P3/S3 | open | **none** | FORE: REQ-54 -- write down Jon's ruling that resolves the two Bobs (gate-Bob and QA-Bob are the  |
| FORE-280 | night | P2/S3 | open | **none** | FORE: new REQ -- KPI thresholds set from the QA fork's measured distribution (measure-then-decid |
| FORE-283 | night | P2/S2 | open | **none** | Bob's write gate: E3 composite property (agent_type-blind subagent confined to replay-only) is u |
| FORE-284 | night | P2/S2 | open | **none** | bob_write_gate.py case 18 once-only: real N-parallel-writer race untested (fix landed, atomicity |
| FORE-286 | night | P2/S2 | open | **none** | bob_write_confirm.py: no test injects a failure in the confirmation write itself (companion's ow |
| FORE-289 | night | P2/S2 | open | **none** | concept_gate.py's self-authorization gap has no ticket -- a builder denied by the gate can write |

## Commits citing a ticket id that does not exist in TESSERA

None.

## Commits in the window citing no ticket at all (0)

None.

## What the record shows

**No closure in this window rests on nothing.** CLOSED-UNTRACEABLE is 0: every closed ticket carries either a commit that cites it or a comment thread recording why. That is the single best result here, and it was the thing most likely to be false.

**Every commit names a ticket.** 42 of 42 commits in the window cite at least one ticket id, and 0 cite an id that does not exist in TESSERA. One apparent exception is not one: `HANDOFF-run2-10-20260903.md` cites `DEVH-99999`, which is a deliberate fabricated-ticket example inside a description of a negative test, not a real citation.

Read that result narrowly. It means commit messages name tickets; it does not mean a gate verified them. tessguard binds this repo to project DEVHR2, which held two tickets for most of this run, so commits pass by naming `DEVHR2-1` while the substantive work is tracked under DEVH -- DEVHR2-1's own text says so. "Cites a ticket" and "was checked against real work" are different claims and only the first is established.

**The system's own linkage table is empty.** `ticket_commit_links` holds 0 rows, across the whole database, not just this window. Every association in this record was reconstructed by text-scanning commit messages. This may be a mechanism that is simply unused rather than broken -- it appears designed to be written by a gitops promote step this run never invoked -- so it is recorded here as an observation needing a decision, not filed as a defect. The consequence stands either way: ticket-to-commit traceability in this project currently depends on humans and agents typing ids into commit messages, and a fix that lands without naming its ticket is invisible to any audit of this kind, including this one.

**21 tickets have code and remain open.** That is the class worth a human pass: each one is either a partial fix, or a fix whose ticket nobody closed. DEVH-90 is a deliberate instance -- its own comment says it stays open until the script actually runs against the real worktrees -- so the class is not an error list, it is a to-triage list.

**14 tickets filed overnight have had nothing happen since.** Zero comments, zero commits, still open. Being filed is a real disposition and several of these are correctly parked, but nothing in the data distinguishes 'deliberately deferred' from 'forgotten at 4am' -- the tickets themselves do not say. That distinction needs a human, and it is the most useful thing anyone could do with this record.

## Untraceable findings

Findings that were real, known to more than one session, and recorded in no durable artifact at the time this record was built. This section is the point of the exercise, not a defect in the run.

1. **The DEVH-42 concurrent-commit race fired for real tonight, and neither the occurrence nor the corrected mitigation was written down anywhere.** DEVH-42 had zero comments while two sessions hit the race mid-commit, a corrected mitigation (pathspec-scoped `git commit -F <msg> -- <path>`) was issued over cross-session messaging, and a flag-ordering trap in that mitigation was independently hit twice, in the `-m` and `-F` forms. A search of every comment in the window for `pathspec`, `git commit --` or `shared index` returned zero rows. **Remedied during this pass:** recorded as comment 1322 on DEVH-42, including the after-the-fact verification (`git log --name-only -1`) that is the only check able to settle what a commit actually took.

2. **This method cannot see a fix that never named its ticket.** Stated as a limit rather than a result. In this window the blind spot happens to be empty -- 0 commits cite no ticket -- but that is a fact about tonight, not a property of the method.

3. **Nothing here covers findings that never reached TESSERA or a commit at all** -- raised in a session, answered in conversation, never filed. By construction this record cannot enumerate them, and no artifact-based record can. The only durable countermeasure is filing at the moment of discovery, which is why the C9 scope residual was filed as DEVHR2-3 rather than left on a revisit trigger.

## Method, so this can be re-run and disagreed with

Generator: `buildDispositionRecord.py` (session scratch, ephemeral; the logic is restated here so it survives the script). Tickets come from a read-only connection to the live TESSERA DB, filtered to DEVH/DEVHR2/FORE created since 2026-09-02. Commits come from `git log --all --since` in dev-harness-run2, claude-hooks-v2 and ticket-system; `--all` so work on other sessions' branches is not invisible. A ticket is associated with a commit when the commit's subject or body contains its id. Disposition is then a pure function of (status, has-commit, has-comment) -- no judgment, no summary, no session report anywhere in the chain.

Two limits worth stating plainly. The commit scan is text matching, so a ticket id mentioned in passing in a commit body counts as a citation. And the record covers artifacts, not truth: a ticket closed with a comment that says "verified" is recorded as CLOSED-COMMENT-ONLY without this pass re-verifying the claim.
