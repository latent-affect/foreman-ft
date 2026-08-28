# ATLAS architecture

Cross-project reinforcement data layer. Two artifacts, two systems, one project.

ATLAS builds **an analytical warehouse** that a human queries in SQL after the fact, and **a real-time enforcement snapshot** that Foreman hooks read synchronously to make live decisions.

Every number below was measured against the live files on this machine during authorship. Where a reference document's claim did not survive execution, the measurement is stated and the design departs from the document. Where a claim of my own did not survive execution, the same applies, and §12 records the four that did not.

Measured against live files during authorship, not synthetic fixtures. Where a prior claim did not survive execution, the measurement is stated and the design departs.

---

## 0. Threat model, stated once, governing every trust classification

This section exists because the first draft embedded its threat model inside a single interface's rationale and let twelve other classifications inherit it silently. Two distinct adversaries are in scope, and they are not the same adversary.

**T1 — the same-user confused deputy.** An agent or script running as this user account, under pressure to look finished, writing to a path ATLAS or a hook reads. This is the dominant threat and the one with a documented history: this session's own reference material records two subagents fabricating complete success reports with no work behind them. Filesystem permissions do not defend against T1 at all, because the adversary holds the same uid. The only defences that work are structural: validate before use, fail open on anything unexpected, and never let an artifact's own claim about itself be the thing that authenticates it.

**T2 — externally-authored content arriving through a first-party channel.** Commit subjects, ticket bodies, comment text, and verbatim shell command strings captured into the audit plane. None of it was written by ATLAS, some of it was written by an agent, and any of it can be authored by someone who is not the operator, through a pull request, a cloned repository, or a pasted command. T2 content is parsed (the commit-message ticket regex), stored, and later rendered to a human.

**Out of scope, deliberately.** Other user accounts on this machine, network adversaries, and physical access. ATLAS makes no outbound calls and opens no ports. File modes of 0600 are set throughout, but they are hygiene against accidental exposure, not a control against either T1 or T2, and no part of this design relies on them.

Every `internal` classification in §4's table means exactly one thing: **both endpoints are ATLAS code, in one process, exchanging in-memory values or a database handle, with no filesystem or subprocess boundary in between.** That is the whole content of the claim. It is not an assertion that the data is trustworthy; the data crossing `ingest_to_warehouse` is untrusted and stays so after the warehouse writes it. If ATLAS is ever split across processes or hosts, every `internal` edge that gains a filesystem, socket, or subprocess boundary must be reclassified, and §11 names that as a tracked risk with the specific edges listed rather than leaving it to be noticed.

---

## 1. What the sources actually are

**`~/.claude/telemetry/verdicts.jsonl`** — 125,977 lines, 43.7 MB, growing at roughly 20 lines per minute during this pass. `verdict_ledger.py:127` strips every `None` field before writing, so key *absence* is the null encoding and no line has a fixed key set. Two writers bypass `verdict_ledger.py` entirely: `laa-commit-flow-advisory.sh` and `session-log.sh` emit 15,851 rows with second-resolution timestamps, 188 rows with no `epoch_ms` at all, and 42 rows where `ts` and `epoch_ms` disagree by up to one second. The module docstring warns that "two ledgers that are supposed to be joinable quietly stop agreeing on a field name." It already happened, inside the same ledger, and neither reference document mentions it.

Real null rates:

| field | present | null rate |
|---|---|---|
| `ts`, `handler_id`, `verdict` | 125,977 | 0.00% |
| `cwd` | 125,943 | 0.03% |
| `epoch_ms` | 125,789 | 0.15% |
| `session_id` | 125,335 | 0.43% |
| `event` | 125,414 | 0.45% |
| `tool_name` / `tool_use_id` | — | 15.62% / 16.05% |
| `self_duration_ms` | — | 22.54% |
| `rule_id` | — | 70.32% |
| `kind` | — | 77.04% |
| `run_id` | — | 95.51% |
| `target` | 3,399 | 97.29% |
| `decision` | 410 | 99.67% |
| `probe_id` | 397 | 99.68% |

Three invariants hold exactly and become CHECK constraints: `verdict` closed over four values; `target IS NOT NULL` exactly when `verdict='stolen'`, zero violations in either direction; `decision` closed over four values.

`epoch_ms` is **not monotonic in file order**. 14,562 lines sit below the running maximum, worst backstep 1,005 ms, because several hook processes append concurrently and each stamps its own clock. Both reference documents specify "tail incrementally by an `epoch_ms` watermark." That design skips rows.

**`~/.claude/audit-plane/`** — 1,251 rows in `global-safety/safety.jsonl`, 10 in `example-safety-tool/audit.jsonl`, eight event types, per-type payload shapes sharing almost nothing. The TESSERA-side document states `source_ledger` takes values `'global-safety' | 'example-safety-tool'`. The real self-reported `ledger` value in the second file is `"project"`. That field reports the writer's intent, not the file it landed in, so ATLAS records the path it read separately from the claim the record makes about itself. Otherwise this class of routing bug becomes undetectable from the warehouse.

Largest single line: 682,196 bytes, a `SAFETY_DENY` carrying verbatim shell command text. Ten credential patterns scanned across all 1,261 rows: zero hits today. The channel is the exact one this project's `CLAUDE.md` records a real API-key exposure through, so the class is live even though the count is zero. That scan was a one-time authorship-time measurement; the verification stage found it was never wired as a running check while `v_fail_open_incident` serves this same payload text verbatim through the query facade, and added `audit_payload_credential_scan` (§16) to make "zero hits" continuously verified rather than a stale claim.

**`/path/to/ticket-system/data/tessera.db`** — 322 tickets, 1,745 events, 35 registered projects. `v_flat` is 1,745 rows over 1,745 distinct `event_id`, so the comment join does not fan out today. It joins on `(ticket_id, created_ts)`, so two comments sharing a microsecond would fan the whole fact table silently. Contract check, not assumption. `ticket_commit_links` has **0 rows**, so the Foreman-side document's plan to join commits to tickets through it joins to nothing.

103 of 114 non-closed tickets carry NULL severity. 101 carry NULL tier.

**Git.** 30 of 35 registered projects are real git repositories, five of them worktrees (`AVLP`, `GLASFT`, `GLASPD`, `VALEFT`, `VALEPD`) where `.git` is a file, not a directory — `git rev-parse --show-toplevel` and `git rev-list --count HEAD` both confirm real history behind each (35, 4, 4, 9 and 3 commits respectively). An `os.path.isdir(root/'.git')` test misreads all five as non-repositories; the correct test accepts `.git` as either a file or a directory. Of the remaining five, four roots do not exist on disk and one has no `source_root` at all. Ingested: 1,097 commits, 47,082 commit-file rows, 152 validated ticket links.

**`~/.claude/telemetry/sessions.jsonl`** — 1,030 distinct sessions carrying `claude_version` and `branch`, covering 98.2% of the session ids in the verdict ledger. No machine field, no model field, so the TESSERA-side document's `dim_session.machine` column has no data source.

---

## 2. The idempotency key, decided by execution

Both documents propose a natural key. Both were run against the real file, ingesting it twice to simulate a crash between the row write and the watermark commit.

| candidate key | real rows lost on first pass | rows duplicated on replay |
|---|---|---|
| `(ts, handler_id, session_id, tool_use_id)` as literal SQL | 0 | **20,114** |
| same key, NULLs coalesced to a sentinel | **623** | 0 |
| `(epoch_ms, handler_id, session_id, tool_use_id)` coalesced | **803** | 0 |
| `sha256(raw line)` | **489** | 0 |
| **`(stream_id, byte_offset)`** | **0** | **0** |

The first row is the important one. SQL treats NULL as distinct under UNIQUE, and `tool_use_id` is NULL on 16% of rows, so the Foreman-side document's stated key is not merely lossy. It is a constraint that appears to protect and does nothing across a fifth of the ledger. Coalescing the NULLs, the obvious repair, then silently drops 623 genuinely distinct rows. Even hashing the whole raw line drops 489, because the shell writers emit byte-identical lines within the same second.

There is no natural key in this data. The only identity a line in an append-only file has is its position in that file.

**`stream_id` is `sha256(first 4096 bytes)`, with no inode component.** The first draft included the inode. Measured: a byte-identical `cp` of the ledger keeps the same head digest and gets a different inode, so the inode-bearing form assigns two stream ids to one logical stream, and 20,000 rows ingest as 40,000. Duplicate facts, no duplicate keys, no constraint violation, nothing to notice. Dropping the inode makes stream identity content-addressed: a copy is recognised as the same stream, while truncation-in-place and file replacement both change the head and are still detected. The residual risk is a file rewritten to a byte-identical first 4096 bytes; those bytes hold twelve complete lines with distinct microsecond timestamps, so accidental collision is not reachable, and a deliberate one is a T1 attack on the ingester that §11 records.

Verified by execution against the real file: full single-pass ingest and five-chunk crash-resume produce identical row sets; a replayed range whose watermark commit was lost inserts 0 rows; a file that grows between passes ingests only new bytes; a trailing partial line is not consumed and is ingested correctly once completed; a rotated file is detected and the total is correct with no duplicates. Row inserts and the watermark update happen in one SQLite transaction, so the crash window is closed by the engine.

---

## 3. Project resolution, and what happens to the quarter of rows it cannot resolve

The TESSERA-side document declares `project_prefix` as a resolved-at-ingest column and a foreign key. Measured against the real registry and the real 575 distinct `cwd` values:

| resolution | verdict rows | share | **fire rows** | distinct cwds |
|---|---|---|---|---|
| exactly one project | 95,341 | 75.68% | 1,555 | 38 |
| **two projects** | 19,820 | **15.73%** | **701** | 124 |
| no registered project | 10,782 | 8.56% | **498** | 396 |
| no `cwd` at all | 34 | 0.03% | **28** | 0 |

`AREM` and `FORE` share `source_root = /path/to/agent-remediation`. `MACNET` has no `source_root`. A scalar `project_prefix` column would have to pick one of two, or write NULL, and NULL would then mean three different things.

Naive string-prefix matching adds its own defect. Five registered roots are bare string prefixes of others (`example-project` swallows `example-project-ft` and `-prod`; `example-audio-app` swallows its `-ft` and `-prod` siblings; `example-tool` swallows `-playground`). Path-component-aware and naive matching disagree on exactly one real `cwd` in the whole ledger, and it is this repository: a second, differently-named checkout of this same tool resolves naively to `ATLAS`, whose registered root is the actual, existing checkout directory with 6 tickets already filed against it.

**Recording the ambiguity is half the job. The half the first draft skipped is what every downstream consumer does with it.** The fire-row column above is why. **1,227 of 2,782 fire verdicts, 44.1%, sit in a scope that is not exactly one project.** For `goals_freeze_gate.py` specifically, 21 of its 40 production fires are unresolved. A view that quietly writes `WHERE project_prefix IS NOT NULL` discards more than half that gate's F-10 evidence and reports the remainder as the whole.

So resolution is a derivation with a mandatory bucket, mirroring exactly the treatment `open_null_severity` gets in the snapshot:

- `cwd` is stored raw and never overwritten.
- `cwd_project` holds one row per distinct `cwd` with `resolution ∈ {unique, ambiguous, unregistered}` and CHECK constraints that make an inconsistent triple unstorable.
- `cwd_project_candidate` holds every candidate when there is more than one.
- **`v_project_scope` is the single derivation every project-dimensioned view joins through.** It emits `project_scope`, which is either a real prefix or one of the three literals `<ambiguous>`, `<unregistered>`, `<no-cwd>`. There is no path by which a row leaves a per-project rollup without landing in a named bucket, because the join is inner and the expression is total.
- `v_gate_proven_live` carries `fires_resolved`, `fires_ambiguous` and `fires_unresolved` as separate columns rather than one filtered count.
- The snapshot publishes `<ambiguous>`, `<unregistered>` and `<no-cwd>` as real shards. Measured: 19, 24 and 2 handlers respectively have activity in them.

A prior architecture pass reached the same conclusion about the same two projects and committed `.foreman/tessera-prefix` as the remedy, which `preflight_blocking_gate.py` already names in its own deny text. ATLAS reads that override where present and prefers it over the registry. Exactly one such file exists on this machine, so the mechanism is real and effectively unrolled-out. ATLAS must work correctly without it, and does.

---

## 4. Components

**`ingest`** — Tails the append-only sources and pulls the two queryable ones. Owns stream identity, watermarks, rotation and truncation detection, partial-line safety, and the raw-JSON-to-column mapping. It interprets nothing: an unknown field is preserved, an unparseable line is counted and skipped, no value is normalised on the way in.

**`warehouse`** — Owns the SQLite schema, migrations, and the data-quality runner. The only component that writes DDL. Its second job is the trust gate described in §8.

**`resolve`** — Rebuilds `cwd_project` from `dim_project`, `project_root`, and `.foreman/tessera-prefix` overrides. Path-component-aware longest-match, never string prefix. Idempotent; a registry change re-resolves history rather than leaving rows keyed to a stale answer.

**`snapshot`** — Builds and publishes the real-time artifact. Reads verdict rollups and trust state from the warehouse, reads open tickets live from TESSERA, refuses to publish when a source *the snapshot itself reads* has a failing contract check.

**`hookclient`** — The reference read-side library a Foreman hook imports. Zero dependencies, no writes, no network, no subprocess. Every failure path returns a reason and no data; verified across five failure modes, none raise.

**`query`** — The read facade over the warehouse for a human and for Foreman's periodic pattern detection. Refuses to answer from a withheld source and says so.

**`dashboard`** — The pre-existing scaffold. Declared as a real component rather than ungated, with a tracked blocker in D9.

### Interfaces

Direction is producer depends on consumer, matching the convention shipped in `/path/to/home/.claude/ARCHITECTURE.md` and `/path/to/ticket-system/ARCHITECTURE.md`. Every rationale below is derived from §0's threat model; none inherits it silently.

| interface | crosses | trust | derivation from §0 |
|---|---|---|---|
| `verdictledger_to_ingest` | JSONL lines from `~/.claude/telemetry/verdicts.jsonl` | `untrusted-input` | T1. Any process as this user appends. Two writers already bypass the canonical record builder, proving the channel is not controlled. |
| `auditplane_to_ingest` | JSONL lines from `safety.jsonl` and `audit.jsonl` | `secret-bearing` | T1 and T2. Payloads carry verbatim shell text, max 682,196 bytes. A credential pasted as a literal `export KEY=value` lands here in plaintext, which is this project's own recorded incident. Zero hits across ten patterns today. Classified by the class, not the count. |
| `tessera_to_ingest` | read-only SQL against `tessera.db` | `untrusted-input` | T2. Free-text ticket and comment bodies written by agents. |
| `gitrepo_to_ingest` | `git log --numstat` subprocess output per repo | `untrusted-input` | T2 specifically, not T1. Commit subjects can be authored by a contributor, a clone, or an upstream, and they are parsed. Fifteen ticket-shaped strings were dropped for an unregistered prefix on the real corpus, including `P0-3` and `F5-001`. |
| `ingest_to_warehouse` | typed rows plus watermark advance, one transaction | `untrusted-input` | Shaped but not validated. The warehouse's constraints are the validation. |
| `foremanoverride_to_resolve` | `.foreman/tessera-prefix`, one line naming a prefix | `untrusted-input` | T1. A checked-in file that overrides registry truth, so a wrong or malicious value silently re-attributes an entire project's history. Validated against `dim_project` before use. |
| `warehouse_to_resolve` | table handles, one process | `internal` | Both endpoints ATLAS code, no boundary crossed. |
| `resolve_to_warehouse` | `cwd_project` writes, one process | `internal` | Same. |
| `warehouse_to_snapshot` | rollups and trust state, one process | `internal` | Same. |
| `tessera_to_snapshot` | live open-ticket rollup, bypassing the warehouse copy | `untrusted-input` | T2. A block decision must not act on a ticket status the warehouse has not synced. |
| `snapshot_to_hookclient` | the published artifact on disk | `untrusted-input` | T1, and this is the sharpest instance. ATLAS writes it, but it sits at a fixed path any agent as this user can overwrite, and its consumer is a hook that can deny tool calls in every project. First-party authorship is not an integrity property. |
| `foremanhook_to_hookclient` | the hook's `cwd` and event payload in | `untrusted-input` | T1. `cwd` is an arbitrary string from the harness, normalised before any path is built from it. |
| `warehouse_to_query` | gated views, one process | `internal` | Both endpoints ATLAS code. |
| `query_to_dashboard` | a JSON extract for a static artifact | `internal` | Same process produces it. |

No interface is `network`. ATLAS makes no outbound calls and the dashboard is a static file, which is what makes the whole thing auditable from disk.

---

## 5. Why the two artifacts are separate, and what the dependency between them actually costs

The first draft claimed the two artifacts have "opposite failure directions" and rested the separation on that. Traced to the end, that claim was too strong, and the honest version is better.

**What is actually true.** The *gated views* fail closed. `hook_verdict` itself stays fully populated; `v_hook_verdict` sentinels out; the `query` facade refuses. A human running `sqlite3` against the base table bypasses all of it, which §8 now states where the claim is made rather than three sections later.

**The cascade, traced.** A contract failure on a snapshot-read source at T0 stops publication. The last good generation keeps serving. Once its age passes `max_age_seconds`, `hookclient` reports `stale` and every hook falls back to today's behaviour. So warehouse-closed does not stay opposite snapshot-open; **it becomes it, after exactly `max_age_seconds`.** That is intended, and it is the correct direction: a hook that hard-blocks because a data pipeline is unhealthy is a worse outcome than a hook that reverts to the behaviour it had before ATLAS existed. But it makes `max_age_seconds` the blast radius of a warehouse failure, not a caching parameter.

**So `max_age_seconds` is derived, not inherited.** The rule is `max_age_seconds = 3 × refresh_cadence_seconds`: three cadences tolerates two consecutive missed refreshes without flapping, and bounds the window in which hooks act on data the warehouse has already disowned. At the shipped cadence of 300 s that is 900 s, down from the first draft's uncalibrated 3600 s inherited from the reference documents.

**One real defect the trace exposed, now fixed and tested.** The first draft gated publication on `v_atlas_status.contract_failures = 0`, which is warehouse-wide, while the warehouse's own trust gate is per-source. Measured: failing only `git_ticket_prefix_registered`, a commit-message parsing check on a table the snapshot never reads, froze the entire real-time artifact and put every hook on the path to fail-open. The gate is now `v_snapshot_publishable`, scoped to the `snapshot_source` table. Both negative controls pass: failing a `git_commit` check leaves `publishable = 1`; failing `verdict_domain_closed` gives `publishable = 0` with `blocking_sources = hook_verdict`.

**What the separation does rest on.** Measured on this machine:

| read path | size | cost per call |
|---|---|---|
| snapshot index, 3-project dependency check | 12,307 B | **0.050 ms** |
| snapshot index plus one project shard | 12,307 + ≤2,221 B | 0.071 ms |
| monolithic single-file snapshot | 55,871 B | 0.216 ms |
| open the warehouse read-only, one indexed rollup | 123.7 MB db | **1.992 ms** |

The warehouse path is 40x the index path. Against `preflight_blocking_gate.py`, which really does average 181.7 ms, 2 ms is noise. Against `guard_destructive.py`, `architecture_gate.py` and `goals_freeze_gate.py`, all averaging under 0.05 ms across roughly 10,000 real firings each, it is a 40x regression on the cheapest hooks in the set.

The decisive result is not the mean. With a writer holding an exclusive transaction on the warehouse, a reader with a 50 ms busy timeout **failed outright** with `database is locked` after 59.9 ms. A hook reading the warehouse directly can be denied service by a sync job, in every project, on every tool call. No configuration of one SQLite file gives a batch writer and a hot synchronous reader both what they need. That, plus the fact that the two consumers need different behaviour when their data is missing, is the separation argument. It does not need the overstated version.

**Separation, stated precisely.** Separate files, separate formats, separate write paths, separate behaviour-on-absence, and a publication gate scoped to what the snapshot actually reads. Not separate *sources*: the snapshot's verdict rollup is read from the warehouse rather than re-derived, so the number a hook acts on and the number a human queries cannot drift. That drift is the failure `verdict_ledger.py` exists as its own module to prevent. The ticket rollup is the one exception, read live from TESSERA, because acting on a stale ticket status is worse than a rollup one sync interval behind.

---

## 6. What the snapshot contains, checked against the reference requirements

| comment 394 requires | status |
|---|---|
| open-ticket rollup per project: `status`, `priority`, `severity`, `tier`, `parent_id`, `updated_at`, non-closed only | **Partly.** Counts, not rows: `open_total`, `open_by_severity`, `open_null_severity`, `open_null_tier`. Per-ticket rows including `parent_id` excluded; see below. |
| project registry, prefix to repo_root | **Delivered, corrected.** A `roots` map keyed by path with explicit `resolution`, because 15.73% of rows resolve to two prefixes. |
| the P0-P4/S0-S4 rubric with version and hash | **Cannot be delivered.** No such artifact exists on disk; searched. The snapshot carries `severity_rubric.status = "absent"` with the blocker and an explicit consumer contract. |
| bounded recent window of verdict rollups per `(cwd, handler_id, kind)` | **Delivered**, aggregated to `project_scope` rather than raw `cwd`, including the three unresolved scopes. |
| the `stolen` verdict resolved before any rollup trusts it | **Delivered.** Resolved from `verdict_ledger.py:35-53` and enforced by the `target` CHECK at zero violations. |
| `generated_at` | Delivered, alongside `refresh_cadence_seconds` so a consumer can see the derivation of `max_age_seconds`. |
| cursors: TESSERA event id, **verdicts.jsonl line count** | **Delivered, corrected.** A line count requires reading the whole file and does not survive rotation. Replaced with `verdicts_byte_offset` plus `verdicts_stream_id`, the same key the warehouse uses. |
| atomic write | **Delivered and exceeded.** See §7. |
| hooks compute their own staleness | Delivered; the freshness contract travels with the artifact. |

**An assumption from the first draft, falsified.** I justified per-project sharding partly with "a hook only ever asks about its own project." `preflight_blocking_gate.py:101` reads:

```python
to_check = [prefix, *sorted(rt.depends_on(prefix))]
```

and `cross_project_routing/routing_table.py:90-95` sets `DEFAULT_DEPENDS_ON = {"FORE", "AREM"}`. **Every project on this machine checks itself plus FORE plus AREM on every PreToolUse.** The most-fired blocking gate in the set is a three-project consumer, not a one-project consumer.

The design survives, but for a reason that is now a requirement rather than a coincidence: **the per-project severity summary lives in the index, not in the shards.** Measured, a full three-project dependency check costs 0.050 ms from one index read with no extra file opens. Had the severity summary been sharded, the same check would open three files. The index carries all projects; the shards carry only what is genuinely per-project.

A second consequence, which is not a coincidence at all: **FORE and AREM, the two projects every other project depends on by default, are exactly the two that share an ambiguous root.** The two findings looked independent and are not. `preflight_blocking_gate.py:33-34` already says "a partial answer is not a safe answer here," so the index distinguishes a project that is absent from the map from one present with zeros, and a shard whose rollup is shared carries `rollup_scope: "<ambiguous>"` and `rollup_is_shared_with: ["AREM"]` so no consumer can read FORE's rollup as FORE's alone.

Live, from the real index: TESS's dependency check returns `TESS:S0=0,null_sev=16  AREM:S0=0,null_sev=1  FORE:S0=0,null_sev=46`. Zero S0 across all three, and 63 untriaged tickets. That pair of numbers is why `open_null_severity` is not optional.

**Where it falls short, named.**

Comment 394 asks for per-ticket rows; the snapshot ships counts. Ticket rows carry agent-authored free text and this artifact is read on every tool call in every project. A hook needing rows can call TESSERA, which `preflight_blocking_gate.py` already does. If a future consumer needs `parent_id` chains synchronously that is a real gap, tracked in D11 as a **`hookclient` capability gap**, kept distinct from the `query`-facade gap in D12 because the two consumers have different latency budgets and the first draft would have collapsed them.

Comment 399's **A2, escalation-on-repeat, is not satisfiable by this architecture at any refresh cadence.** A2 wants the fourth matching event within one session to escalate. Measured across 899 real sessions: median gap between consecutive verdicts inside a session is **13 ms**; median session lasts 10 seconds. No asynchronously refreshed artifact observes four events 39 ms apart. A2 needs a per-session counter maintained by the ledger writer at write time, which is comment 399's own B1 and is Foreman-side work. Stated because the snapshot would otherwise look like it covers A2 and would silently never fire, the exact shape of the `capture_plan.py` defect that ran healthy across 181 sessions.

Comment 399's **A4** requires `settings.json` content in the snapshot. Not built; blocked on a precedence decision, D3.

---

## 7. Snapshot publication

The environment's standing pattern is write-temp, verify from disk, atomic rename. Necessary, not sufficient, because this artifact is more than one file.

A monolithic file was tested first: 40 republishes under a concurrent reader, 0 partial reads, 0.216 ms per read, and every hook parses all 35 projects to answer about one. Splitting into index plus shards drops the read but introduces a torn pair. Stamping shards with the index's `generated_at` detects it: across 7,356 reads under a pathological republish loop, mixed pairs silently used were 0, but 97.9% of reads were refused as torn, which is a feature that never fires.

**The chosen design publishes into a generation directory and flips a symlink last.** A reader resolves `current` once and reads every file beneath the resolved path, so a torn pair is structurally impossible rather than detected. Measured across 63,799 reads, four concurrent readers, 400 republishes: torn 0, mixed 0, 99.989% good reads.

Retention took two attempts. Count-based GC can delete a generation a slow reader is inside; age-based GC leaves the directory unbounded within the window, measured at 201 generations under load. The rule is both: **a generation is removed only when it is older than `max_age_seconds` AND not among the newest three.** Measured under 400 rapid republishes: 99.989% good reads, 0 torn, bounded at 61 generations with a 1-second window.

Publication is refused when a source the snapshot reads has a failing contract check. A stale snapshot makes hooks fail open, which is designed. A confidently wrong snapshot makes them act on a bad number.

**A second, snapshot-local contract check runs at publish time, added by an earlier fatal-severity fix: `snapshot_ticket_rollup_identity`, recorded in §16 as `dq_check`'s new `'snapshot'` scope.** For every project the ticket-rollup writer must assert `open_total = open_null_severity + Σ open_by_severity.values()` before the generation directory is flipped live. This is the exact identity the fabricated §17 example silently violated (`TESS.open_total: 61` against a real `16 + 1 = 17`) and the exact class of defect that let `MACNET`, a project with one real open ticket, publish as `open_total: 0` — the confirmed-clean/could-not-check collapse §3, §6 and §9 exist to prevent, in the artifact those sections were written to protect. `source_table` for this check is `'tessera'`, not a warehouse table, because the ticket rollup is read live from TESSERA and never lands in the warehouse (§5, §6).

**Corrected by a later falsification re-review (2026-08-22): this check does NOT flow through `snapshot_source`/`v_snapshot_publishable`, and `snapshot_source` deliberately does not carry a `'tessera'` row.** An earlier draft of this section implied it did; measured against the live schema, it structurally cannot: `v_snapshot_publishable` only withholds publication for a source table present in `snapshot_source`, and a re-review found that adding `'tessera'` there to make the declarative path "work" introduces a worse failure -- `v_source_trust`/`v_queryable_source` require a `dq_check_run` row for the *current* `ok` run before a source counts as queryable at all, and `snapshot_ticket_rollup_identity` is only ever meaningfully evaluated with a real `ticket_rollup` from inside `publish()` itself, never from a normal `dq_runner.run_all()` ingest pass (which always calls it with `ticket_rollup=None` and records a deliberate vacuous pass, `"not a snapshot-publish run"`, precisely so an ordinary ingest doesn't manufacture a false failure over a check it has no rollup to evaluate). Routing through the declarative gate would recreate an earlier fatal-severity fix's exact shape: a check that is only ever really evaluated during publish would permanently block publication on every OTHER kind of run, since no prior 'ok' run ever recorded a real evaluation of it. So enforcement here is deliberately **procedural, not declarative**: `snapshot/publisher.py`'s `publish()` calls `builder.check_ticket_rollup_identity(ticket_rollup)` directly and raises `PublishRefused` before any file is written if it fails -- a real, tested, hard block, verified by execution (see the falsification re-review's negative controls), just not routed through the same `snapshot_source` machinery `v_snapshot_publishable` uses for warehouse-table sources. The `dq_check`/`dq_check_run` row for `snapshot_ticket_rollup_identity` still exists and is still written every ingest run; its purpose is the audit trail (a separate tracked follow-up, not this document, owns whatever failure-count in that row `checks_evaluated`/`contract_failures` should mean for a check whose real evaluation only happens at publish time) -- it is not itself gate-authoritative for this check, and no future implementer should assume adding a table to `snapshot_source` is sufficient to gate a `'snapshot'`-scope check. A `'snapshot'`-scope check without a matching `snapshot_source` row is, by design, enforced procedurally by its own consumer instead.

Five failure modes exercised against the reference client: pointer missing, pointer dangling, index corrupt, shard corrupt, whole root removed. All five returned a reason and no data; none raised.

---

## 8. How the gated views refuse to lie, and exactly where that guarantee stops

The title is scoped deliberately. The first draft's was not, and §11 walked it back three sections later.

**What is guaranteed.** Every registered check runs after every ingest and writes a row whether it passed or failed. A source with a failing **contract** check is withheld from every view built on it for that run. **Advisory** checks record and alert without gating.

A gated view does not go empty. It returns exactly one row whose `trust_state` reads `SOURCE-DISTRUSTED-DO-NOT-USE`. Verified by negative test: forcing `verdict_domain_closed` to fail takes `v_hook_verdict` from 125,977 rows to the sentinel while `hook_verdict` retains every row. On a brand-new database with no ingest run, `v_hook_verdict` returns the sentinel and `v_snapshot_publishable` returns 0 with `blocking_sources = hook_verdict`, so the uninitialised state is distrusted by default rather than clean by default.

**"Every view built on it," checked literally, not just at `v_hook_verdict`.** A prior pass built `v_hook_verdict`'s trust guard and stopped: every other `hook_verdict`-derived view — `v_project_scope`, `v_project_resolution_coverage`, `v_handler_denominator`, `v_verdict_confusion_matrix`, `v_fail_open_incident`, `v_hook_latency_rollup`, `v_decision_outcome_rate`, `v_deny_streak`, `v_trapped_agent_candidate`, `v_gate_proven_live` — still selected `FROM hook_verdict` directly, bypassing the sentinel entirely. `v_gate_proven_live`, which re-derives QUALITY-BAR F-10, reported a formally distrusted source as proven live. All ten are rebuilt in §16 to carry their own `trust_state`/`UNION ALL` sentinel, not a plain `WHERE` filter — a plain filter against a distrusted source returns zero rows, reintroducing the empty-view failure this section exists to prevent.

`v_project_scope` needed a different gate than the other nine: it is what `v_hook_verdict` itself joins to compute `project_resolution`/`project_scope`, so gating it on `v_hook_verdict` would be circular. It gates on `v_queryable_source` directly instead — the same source `v_hook_verdict` itself checks — which is not circular and was verified by execution: trusted output is byte-identical to the pre-fix view (plus the new `trust_state` column), and it collapses to the single sentinel row when `hook_verdict` is distrusted, same as the other nine. `v_gate_proven_live` was verified the same way on its own: output byte-identical to the pre-fix view when trusted, single sentinel row when not.

**A caveat this fix introduces, not fatal but real.** Eight of the ten (`v_project_resolution_coverage`, `v_handler_denominator`, `v_verdict_confusion_matrix`, `v_fail_open_incident`, `v_hook_latency_rollup`, `v_decision_outcome_rate`, `v_deny_streak`, `v_trapped_agent_candidate`, `v_gate_proven_live`, minus the two named next) sentinel branches key off "is `hook_verdict` in `v_queryable_source`," which is also true for a *trusted but empty* `hook_verdict` — before the first ingest run, or immediately after a migration that re-ingests from zero (D14). **Correction, a falsification re-review (2026-08-22): a prior version of this paragraph claimed all ten report the sentinel in that state; measured, `v_hook_verdict` and `v_project_scope` do not -- as row-per-record pass-through views, not aggregates, they return their honest zero rows there instead, which is correct (a trusted-but-empty source has nothing to hide, so nothing should be withheld), not a defect. The other eight, being single-row aggregates, cannot distinguish "legitimately zero" from "sentinel" any other way and do report the sentinel row.** In that eight-view case nothing has actually failed a contract check; `v_snapshot_publishable` still returns `publishable=1` in the same run, so a consumer reading only these ten views sees a false distrust signal while the top-level status view reports healthy. It fails closed, which is the safe direction, but it is a real disagreement between views a single consumer might read together, and is tracked rather than silently accepted. Left as an open, explicitly named choice for design-and-scope rather than fixed here: either accept the false-closed disagreement (documented, harmless direction), or add a distinct `SOURCE-EMPTY` sentinel value distinguishable from `SOURCE-DISTRUSTED-DO-NOT-USE`, which was not built or tested as part of this fix.

**What is not guaranteed, stated here and not later.**

1. **A `sqlite3` session against `hook_verdict` bypasses all of this.** SQLite views cannot raise. Primary enforcement is the `query` facade; the sentinel is defence in depth for someone reaching for a view directly, and there is no defence at all for someone reaching for the base table. That is accepted: the base tables must stay readable for forensic work, and locking them would defeat the purpose.
2. **The sentinel only protects a caller who reads it.** A caller that runs `SELECT AVG(self_duration_ms) FROM v_hook_verdict` gets a clean NULL from the sentinel row and no warning. `v_atlas_status` is the one view a consumer is contracted to read first, and the `query` facade reads it before returning anything. A hand-written aggregate over a gated view is outside the guarantee.
3. **The guarantee is about withholding, not about correctness.** A check that passes says the invariant it encodes held. It says nothing about invariants nobody wrote.

Three source checks fired real findings on the first real run:

- **`fail_open_not_double_counted`** — `verdicts.jsonl` `verdict='error'` holds 13 rows; `safety.jsonl` `HOOK_ERROR` holds 13 rows. Matched on handler, `cwd`, and timestamp within two seconds, all 13 pair perfectly with zero unmatched either side. Same 13 incidents recorded twice. A union would report 26, a **100% inflation**, the fail-open double-count class sitting live in ATLAS's own inputs.
- **`handler_denominator_nonzero`** — 4 handlers have zero `silent` verdicts and a positive `fire` count: `session-log.sh`, `telemetry_liveness.py`, `guard_untrusted_web.py`, `reinject_compact.py`. `verdict_ledger.py`'s own comment names the first three as the reason `stolen` was added; two were converted and `guard_untrusted_web.py` at 530 fires and 0 silents never was.
- **`audit_ledger_partition_by_cwd`** — 477 audit rows whose `cwd` resolves to a registered project landed in the shared global ledger. Measured from the warehouse without needing to know the bug exists.

**`git_ticket_prefix_registered` earned its place harder than the first draft claimed.** I cited `ISO-8601` as the one false positive. Across 1,097 real commits the bare regex produces 167 ticket-shaped strings and drops **15** for an unregistered prefix, including several severity-rubric-shaped strings and one unrelated abbreviation. The severity-rubric-shaped cases are severity-rubric references. Without validation, a commit subject mentioning a severity level manufactures a link to a nonexistent ticket in a nonexistent project. Dropped candidates are stored in `git_ticket_candidate_dropped`, not discarded, so the drop rate is itself queryable.

**A self-check layer, because none of the above checks ATLAS.** Every check named so far asks whether the *source* is bad. None asks whether ATLAS's own ingest and resolve behaved consistently with the source having changed. `dq_check.scope` now distinguishes `source` from `pipeline`, and five pipeline checks run each pass: `ingest_rows_match_bytes`, `ingest_rows_monotonic`, `resolution_rate_delta`, `handler_set_stable`, `watermark_advanced`.

`ingest_rows_match_bytes` **failed on its first live run**, 125,776 against 125,777. The ingester was right and the check was wrong: splitting a trailing-newline file on `\n` always yields an empty final element, and my predicate double-subtracted it. Corrected to count non-blank elements, it passes, and deliberately deleting five rows makes it fail. Recorded because a self-check that alarms on every run is worse than no self-check: it gets disabled, and then it is a guard that is registered, healthy, and has never once done its job.

---

## 9. Every view, and what wrong would look like

The first draft discussed five of thirteen while claiming all were inspected. That asymmetry was real. Each view below has a stated tell: the observation that would reveal it broken. Where a view has no sharp tell, that is said rather than covered over.

| view | rows, real data | what wrong looks like |
|---|---|---|
| `v_atlas_status` | 1 | Zero rows, or more than one. Both mean the `MAX(run_id)` subquery lost its anchor. Sharp. |
| `v_source_trust` | 4 | A source missing from the list that has registered checks; or `checks_run` below the count in `dq_check` for that source. Sharp. |
| `v_queryable_source` | 4 | A source appearing here while `v_source_trust` shows `contract_failures > 0`. Directly negative-tested. Sharp. |
| `v_snapshot_publishable` | 1 | `publishable = 1` with a non-empty `blocking_sources`. Both negative controls run. Sharp. |
| `v_project_scope` | 125,977 / 1 sentinel | Any count other than exactly `COUNT(*) FROM hook_verdict` while trusted. The join is inner and the scope expression total, so a shortfall means one is broken. Sharp, and it is the tell for the whole §3 fix. Also gated on `v_queryable_source` (§8's earlier fatal-severity fix): zero rows or a real-looking row while distrusted would mean the gate broke. |
| `v_hook_verdict` | 125,977 / 1 sentinel | Zero rows in either state. Negative-tested both directions. Sharp. |
| `v_project_resolution_coverage` | 4 | Buckets summing to less than the table, or a bucket vanishing. Verified: 95,341 + 19,820 + 10,782 + 34 = 125,977. Sharp. |
| `v_handler_denominator` | 25 | A handler with `n_silent = 0` and `structural_zero_denominator = 0`. Sharp, and it is the structural-zero-denominator tell. |
| `v_verdict_confusion_matrix` | 3,195 | `boundary_kind = 'unbounded'` on a row with a `session_id`; or a non-NULL `fire_rate` on a structural-zero handler. Sharp. |
| `v_fail_open_incident` | 13 | Any count other than the `verdict='error'` count, which would mean the audit LEFT JOIN fanned out. Sharp. |
| `v_hook_latency_rollup` | 170 | **Weaker tell.** `pct_timed` was added for exactly this: `self_duration_ms` is absent on 22.54% of rows, so an average over `COUNT(*)` is wrong by that share and nothing else would show it. With `pct_timed` visible, a rollup at low coverage is legible; without it, a plausible wrong average is not. |
| `v_decision_outcome_rate` | 100 | A total below the 410 real decision rows, meaning `<no-rule_id>` or an unresolved scope silently dropped. The 14 NULL-`rule_id` denies from `guard_destructive.py` are the specific probe. Sharp. |
| `v_deny_streak` | 181 | **Weakest tell, and the one I trust least.** The gaps-and-islands arithmetic can be subtly wrong in ways that still produce plausible streak counts. It is checked against `v_decision_outcome_rate`: total rows across all streaks must equal the deny count *among rows with a non-null `session_id`* (a falsification re-review, 2026-08-22, found and corrected an unscoped version of this claim that was false on correct data by exactly the null-`session_id` row count). Stated as the weak point rather than claimed sound. |
| `v_trapped_agent_candidate` | 8 | **Was wrong, caught by running it.** See below. |
| `v_gate_proven_live` | 25 | `fires_total` not equal to `fires_resolved + fires_ambiguous + fires_unresolved`. Sharp, and it is the §3 tell at handler grain. |
| `v_source_freshness` | 3 | A source absent, or `staleness_minutes` negative. Sharp. |
| `v_ticket_diff_binding` | 322 | **Was reported on empty inputs.** See below. |
| `v_pipeline_selfcheck` | 10 | Fewer rows than pipeline checks × runs. Sharp. |

**`v_verdict_confusion_matrix`** departs from the Foreman-side document, which requires grouping by the `run_id`/`probe_id` boundary. Those cover **4.49%** of the ledger. Implemented literally the view would score 4.49% of the data and read as complete. It falls back through `run_id`, `probe_id`, then `session_id`, which covers 99.57%, and reports which boundary it used.

**`v_trapped_agent_candidate` was wrong in my first draft and executing it is what caught it.** I defined the intervening-work test as `verdict='stolen'` in the streak window, which returned 0 across all 181 streaks; the view would have reported every streak as a trapped agent. `stolen` marks a write by a *hook*, not a completed tool call by the agent. Counting distinct `tool_use_id` on `PostToolUse` rows separates cleanly: 8 streaks of length 3 or more, of which **3 have zero completed tool calls**. The sharpest is six consecutive `FOREMAN-PREFLIGHT-GATE:unreachable` denials across 95 seconds with nothing completing, which is comment 399's B3 failure mode found in real history.

That story is offered with its limit attached. It was catchable because "0 of 181" is obviously wrong to anyone who knows what trapped means. The method finds defects when there is an obvious tell. That is why the table above states the tell for every view, and states plainly that `v_deny_streak` and `v_hook_latency_rollup` have the weakest ones.

**`v_ticket_diff_binding` is the case where the absent tell cost me.** The first draft reported it as executed and inspected. It returned 322 rows, every one with `commits_linked = 0`, because `git_commit_ticket` was empty: I had never run the git ingester. A view producing a uniform zero over a plausible row count, reported as success. That is the join-that-matched-three-of-five defect, in the document that diagnoses it.

Re-run against 1,097 real commits and 152 validated links it is differentiated and useful: 231 tickets with no linked commit, 67 with one, 15 with two, and one with 22. Two structural findings came out of the real run. First, **linkability is a first-class column now**, because a ticket in a project with no `source_root` reports `commits_linked = 0` identically to one that was checked and found none. Measured: 228 genuinely linkable-and-unlinked, 2 with no source root, 1 with no commits ingested. Small today, and the same confirmed-clean-versus-could-not-check collapse as everywhere else. Second, **cross-repo links are real and common**: 3 commits in the GLAS repo reference FORE tickets, 3 in TESS, 3 in VALE, and so on. `git_commit_ticket`'s `project_prefix` is the repo's, not the ticket's, and `cross_repo_commits` surfaces the difference rather than conflating them.

**`v_gate_proven_live`** re-derives QUALITY-BAR §8.2's F-10 measurement from the warehouse, and the unresolved columns change what it says. `architecture_gate.py`: 192 fires, 162 resolved, 30 unresolved, 28 registered projects. `goals_freeze_gate.py`: 40 fires, **19 resolved, 21 unresolved**, 6 projects. `preflight_blocking_gate.py`: 10 fires, 7 resolved, 1 ambiguous, 2 unresolved. `dependency_provenance_gate.py`: 39 fires, **0 resolved**. `ship_readiness_gate.py`: 3 fires, **0 resolved**. Three of five proven live, matching the hand measurement, and now showing that for the second of those three, more than half the evidence sits outside the registry.

---

## 10. Where a measurement is standing in for a policy

`dq_check.threshold_kind` is a column because this distinction has to survive into the data, not just the prose. Three values:

- **`invariant`** — the threshold is a property of the source's own contract, not a choice. `verdict_domain_closed` compares against `verdict_ledger.VALID_VERDICTS`. Sixteen of nineteen checks (updated by the verification-stage `audit_payload_credential_scan` addition; see §16's seed data for the current count, extracted programmatically by `atlas/warehouse/ddl.py` rather than retyped here, so this prose count is the only place that can drift).
- **`calibrated`** — the threshold is derived from another measurable property, and moves when that property moves.
- **`lean`** — a judgment call with a named recalibration trigger.

The first draft made project resolution advisory because "75.61% is a fact about the world, not a broken pipeline." A later pass over-corrected that into a **contract** check, `project_resolution_floor`, comparing the observed resolution rate against the fraction of registered projects that are real git repositories — 25/35 = 71.43%, by a miscount. **The real count is 30/35 = 85.71%**, once the five worktree projects in §1 are counted correctly. A corrected floor that high is not satisfiable: `AREM` and `FORE` share one `source_root`, which puts 15.73% of all rows (19,820) in the `ambiguous` bucket permanently — no registry change clears it — capping the achievable resolution rate at 84.27%. A calibrated floor set above its own ceiling is not a calibration; it is a contract check that fails on every run, from the first one, and measured end-to-end that collapse propagates through `v_queryable_source` into `v_hook_verdict`, into `v_snapshot_publishable`, and past `max_age_seconds` into every Foreman hook on the machine falling open, permanently.

`project_resolution_floor` is **advisory**, not contract; the first draft's original instinct was the correct one. The resolution rate is a fact about the registry, not something ATLAS's own pipeline can move on its own, so it is the wrong shape for a gate. `resolution_rate_delta` carries the contract severity instead: a *drop* in the rate is causally tied to a resolver regression in a way a *level* is not, and registering more real repositories can only raise the rate, so registry growth cannot false-positive it. Measured: dropping 20% of `cwd_project` rows with the registry untouched produces a 9.46-point drop, caught well past the delta's five-point contract threshold.

**Detection floor, disclosed by a falsification re-review (2026-08-22) rather than left implicit.** The five-point contract threshold is against the WHOLE table's resolution rate (125,977 rows), so its real detection floor is roughly `0.05 × 125,977 ≈ 6,299` rows -- a per-project regression smaller than that moves the whole-table rate by less than five points and the check will not fire. Measured: 27 of the 32 unique-resolving projects sit under that floor individually (only `TESS`, `BAYAREA`, `HYPHY`, `AUDI` and `MOON` clear it alone); a simulated total loss of resolution for `CHRO` (2,256 rows) produces only a 1.79-point delta and is missed. The 20%-of-`cwd_project` demonstration above shows the check *can* fire, not what the smallest real regression it *can* detect is -- that is this floor. Not fixed in this pass (a cwd-grain unique→not-unique regression check was tested as a viable alternative and does catch `CHRO` at n=1 without false-positiving on legitimate registry growth, but switching to it is real behavior change deferred to a follow-up; disclosed here so a reader does not assume five points of whole-table movement is the check's actual sensitivity for any one project.

Two thresholds remain **`lean`**, marked as such in the schema:

- `audit_payload_size_bounded` at 1 MiB, against a real maximum of 682,196 bytes. Trigger: the first payload above 1 MiB, which is a fact about a new writer, not about ATLAS.
- The seven-day verdict window. Trigger: the first consumer whose question the window cannot answer.

`max_age_seconds` is no longer a lean at all; §5 derives it from the cadence.

**The assumption that did not survive this treatment** is documented in §6: "a hook only ever asks about its own project" was asserted as settled and is false for every project on this machine.

---

## 11. Known limits

- **`v_hook_verdict`'s sentinel is defence in depth, not the gate.** Stated at §8 where the claim is made.
- **The sentinel only helps a caller that reads it.** An aggregate over a gated view sees a clean NULL.
- **`internal` means one process.** If ATLAS is ever split across processes or hosts, `warehouse_to_resolve`, `resolve_to_warehouse`, `warehouse_to_snapshot`, `warehouse_to_query` and `query_to_dashboard` all gain a real boundary and must be reclassified. Nothing in the current machinery would notice; there is no check that the process topology still matches the classification.
- **`stream_id` is content-addressed and single-host.** A file rewritten to a byte-identical first 4096 bytes reads as the same stream. Not reachable accidentally; reachable by a T1 adversary who can write the ledger's head, which is the same adversary who can write anything else it says.
- **ATLAS is scoped to one machine.** `ingest_run.host_id` exists so a future merge can tell runs apart, but no merge semantics are defined and none should be inferred. Cross-machine merge is D13.
- **Copy-truncate rotation loses the write window.** Lines appended between the copy and the truncate are lost before ATLAS sees them. Detected and recorded, not recovered.
- **No rotation has ever been observed on `verdicts.jsonl`.** The sibling `events-2026-W33.jsonl` shows weekly rotation is a real pattern in that directory. The code path exists and was tested; it has never fired in production.
- **The snapshot's verdict rollup is bounded by warehouse freshness.** Correct for A1 and A3, wrong for anything wanting sub-minute recency, which is D4.
- **`payload_json` in `audit_event` is a hedge.** Nine event types share almost no fields.
- **Migrations are barely designed.** `schema_migration` records version, name, timestamp and DDL digest, so a migration that ran is recoverable and a schema that drifted from its recorded digest is detectable. What is not designed: whether a breaking migration re-ingests from offset zero or preserves `hook_verdict`, and whether `stream_id` values survive a rebuild. Tracked as D14 rather than left as an implied responsibility of the `warehouse` component.
- **`v_deny_streak`'s window arithmetic has the weakest correctness tell in the schema**, and it is the input to `v_trapped_agent_candidate`, the view that was already wrong once.

---

## 12. What execution changed in this design

Recorded because the method is the argument, and a list of one caught bug is a worse case for it than a list of five.

1. `v_trapped_agent_candidate`'s intervening-work test was defined on the wrong verdict and matched nothing across all 181 streaks.
2. `v_ticket_diff_binding` was reported as executed while its join input was empty, returning a uniform zero over 322 rows.
3. `stream_id` included the inode, so a byte-identical copy would ingest 20,000 rows as 40,000 with no constraint violation.
4. The snapshot publish gate was warehouse-wide while the trust gate is per-source, so a commit-message check could freeze the real-time artifact.
5. `ingest_rows_match_bytes`, the first pipeline self-check written, was off by one and would have alarmed on every run.
6. `dim_project.root_is_shared` took a NOT NULL violation on the one project with a NULL `source_root`, because `x IN (...)` is NULL when `x` is NULL.

Four of the six produced plausible output. That is the argument for the tell column in §9 and against trusting "inspected."

---

## 13. Explicitly deferred, with the real blocker

**D1. `review_manifests`.** Blocked on emission. Nothing produces a structured record of what a review skill did. Unblocks when a review skill emits `{skill_name, checks_prescribed, checks_run, subagent_count, independent_reconfirmation}` to a known path. Three known skill-detection precision and recall defects are parked here: no ATLAS-side data source exists until that record does.

**D2. Severity-rubric tables and the A1 reference requirement.** Blocked; no rubric artifact exists on disk. 103 of 114 non-closed tickets carry NULL severity, so a threshold check today evaluates 9.6% of the queue.

**D3. `settings.json` scanning, the A4 reference requirement.** Blocked on a decision: three settings scopes exist and nothing states which is authoritative when they disagree.

**D4. Comment 399's A2.** Measured unsatisfiable at a 13 ms median within-session gap. Belongs to a write-time counter in the ledger writer.

**D5. `dim_session.machine`.** No data source. Omitted rather than shipped permanently NULL.

**D6. `component_coupling_snapshots`.** Real, not blocking.

**D7. `tessguard_audit_events`.** Blocked; zero rows and no way to distinguish that from a broken ingester.

**D8. Author identity.** `git_commit.author_hash` is a salted digest. The standing rule that a real name must never reach a public surface makes storing plaintext and remembering to redact at export the failure mode.

**D9. `dashboard/`.** The first draft declared it a component and called that resolved. It is not: the two files are a verbatim copy of another project's own single-project dashboard script whose own docstring says every query targets that project's schema and will not run against ATLAS. **Blocker: no ATLAS query has a stable shape to rewire against until the `query` facade exists.** Target: `dashboard/` reads only through `query`, and a smoke test asserts a non-empty render against the real warehouse. Until then it is a component containing a known-broken file, which is a different state from a component that works, and D9 exists so the yaml block listing it does not read as closure.

**D10. This project's own TESSERA registration.** The `ATLAS` prefix is registered to this repository's real checkout directory, which has 6 tickets. A second, differently-named checkout path is registered to nothing.

**D11. `hookclient` per-ticket capability gap.** Comment 394 asks for ticket rows; the snapshot ships counts. A hook needing `parent_id` chains synchronously is unserved. Blocker: nothing has asked yet, and building it costs every hook the read.

**D12. The periodic pattern-detection consumer is unserved and undesigned.** Named in the reference documents as one of two consumers. Checked: no crontab entry, no launchd agent, no pattern-detection script anywhere on this machine. Its query frequency, latency tolerance and shape are unspecified, so the counts-not-rows decision in §6, which was made from *hook* read cost, has never been tested against it. This is deliberately separate from D11: a periodic job can afford per-ticket rows and a wide scan where a hook cannot, so the two gaps have different answers and collapsing them would produce the wrong one. Blocker: the consumer does not exist. First real query it needs is the recalibration trigger.

**D13. Cross-machine merge.** `ingest_run.host_id` exists; merge semantics do not. Blocker: undecided whether two hosts' ledgers are one fact table or two.

**D14. Migration strategy.** `schema_migration` records what ran. Blocker: no decision on whether a breaking migration re-ingests from zero or preserves `hook_verdict`.

**Carried forward without re-litigation**: raw transcripts, raw diffs, TESSERA ticket content beyond `v_flat`'s dimensions, and `events.jsonl` blended into `hook_verdict`.

`dq_known_metric_bug` carries a CHECK constraint making the unimplemented-and-untracked state unstorable: `CHECK (implemented = 1 OR deferral_id IS NOT NULL)`. Verified by attempting an orphan insert, which the engine rejects. Five of eight known bugs have a live check; the other three carry `deferral_id = 'D1'`.

---

## 14. Component declaration

```yaml components
ingest: ["atlas/ingest/**"]
warehouse: ["atlas/warehouse/**"]
resolve: ["atlas/resolve/**"]
snapshot: ["atlas/snapshot/**"]
hookclient: ["atlas/hookclient/**"]
query: ["atlas/query/**"]
dashboard: ["dashboard/**"]
```

## 15. Interface declaration

```yaml interfaces
verdictledger_to_ingest: {"producer": "verdict_ledger", "consumer": "ingest", "trust": "untrusted-input"}
auditplane_to_ingest: {"producer": "audit_plane", "consumer": "ingest", "trust": "secret-bearing"}
tessera_to_ingest: {"producer": "tessera", "consumer": "ingest", "trust": "untrusted-input"}
gitrepo_to_ingest: {"producer": "git_repo", "consumer": "ingest", "trust": "untrusted-input"}
ingest_to_warehouse: {"producer": "ingest", "consumer": "warehouse", "trust": "untrusted-input"}
warehouse_to_resolve: {"producer": "warehouse", "consumer": "resolve", "trust": "internal"}
foremanoverride_to_resolve: {"producer": "foreman_override", "consumer": "resolve", "trust": "untrusted-input"}
resolve_to_warehouse: {"producer": "resolve", "consumer": "warehouse", "trust": "internal"}
warehouse_to_snapshot: {"producer": "warehouse", "consumer": "snapshot", "trust": "internal"}
tessera_to_snapshot: {"producer": "tessera", "consumer": "snapshot", "trust": "untrusted-input"}
snapshot_to_hookclient: {"producer": "snapshot", "consumer": "hookclient", "trust": "untrusted-input"}
foremanhook_to_hookclient: {"producer": "foreman_hook", "consumer": "hookclient", "trust": "untrusted-input"}
warehouse_to_query: {"producer": "warehouse", "consumer": "query", "trust": "internal"}
query_to_dashboard: {"producer": "query", "consumer": "dashboard", "trust": "internal"}
```

Both blocks were parsed with the real `component_coupling.parse_component_map()` and with the same regex-plus-`json.loads()` convention `tier_triage_gate/GOALS.json` specifies for the not-yet-built `parse_interfaces_map()`. Seven components, fourteen interfaces, zero unparsed lines, every interface carrying a `trust` from A6's closed set. Distribution: eight `untrusted-input`, five `internal`, one `secret-bearing`, zero `network`.

Six interfaces name an endpoint that is not a declared ATLAS component: `verdict_ledger`, `audit_plane`, `tessera`, `git_repo`, `foreman_override`, `foreman_hook`. If `parse_interfaces_map()` is later built to validate endpoints against the component map, these read as dangling. Rewriting them so both endpoints are internal components would satisfy that future parser and would erase every untrusted-input and secret-bearing boundary from the graph, which is the most valuable thing this document produces at this stage. The boundary stays; the risk is recorded.

---

## 16. Warehouse DDL

This is the exact SQL applied to an empty database and then loaded, twice, with 125,977 real verdict rows, 1,261 real audit rows, 1,745 real TESSERA events, 1,097 real commits across 30 real repositories (corrected per section 1's earlier fatal-severity fix -- five of the thirty are worktrees, `.git` as a file), and 35 real projects, and against which all eighteen views were executed.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================ INGEST BOOKKEEPING
CREATE TABLE ingest_run (
    run_id        INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','ok','failed')),
    atlas_version TEXT NOT NULL,
    -- Salted digest of the hostname. ATLAS is single-machine (limit stated in section 11,
    -- deferral D13); this exists so a future merge can tell runs apart, NOT because merge
    -- semantics are defined. Nothing here should be read as multi-host support.
    host_id       TEXT NOT NULL
);

-- One row per (logical source, physical stream). stream_id is sha256(first 4096 bytes) with
-- NO inode component. The inode-bearing form was measured wrong: a byte-identical `cp` keeps
-- the head digest and gets a new inode, so the same logical stream ingests twice --
-- 20,000 rows became 40,000, with no key collision and no constraint violation to notice.
-- Content addressing recognises the copy, and still detects truncate-in-place and
-- replacement because both change the head.
-- byte_offset always points just past the last COMPLETE line, so a writer caught mid-append
-- is never half-ingested.
CREATE TABLE ingest_source (
    source_name   TEXT NOT NULL,
    stream_id     TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    byte_offset   INTEGER NOT NULL CHECK (byte_offset >= 0),
    rows_ingested INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    updated_by_run INTEGER REFERENCES ingest_run(run_id),
    PRIMARY KEY (source_name, stream_id)
);

-- Rotation and truncation are RECORDED, not inferred later from a gap in the facts.
CREATE TABLE ingest_stream_history (
    id            INTEGER PRIMARY KEY,
    source_name   TEXT NOT NULL,
    stream_id     TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    reason        TEXT NOT NULL CHECK (reason IN ('first-seen','rotation','truncation')),
    prior_stream_id TEXT,
    prior_byte_offset INTEGER,
    observed_at   TEXT NOT NULL
);

-- ddl_sha256 makes a schema that drifted from its recorded migration detectable. What is
-- deliberately NOT decided here: whether a breaking migration re-ingests from offset zero or
-- preserves hook_verdict. Deferral D14.
CREATE TABLE schema_migration (
    version        INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    applied_at     TEXT NOT NULL,
    applied_by_run INTEGER REFERENCES ingest_run(run_id),
    ddl_sha256     TEXT NOT NULL
);

-- ============================================================ DIMENSIONS
-- No generated column. The TESSERA-side document proposes
--   is_dormant INTEGER GENERATED ALWAYS AS (... julianday('now') ...) VIRTUAL
-- which CREATEs without error and then fails on EVERY insert with "non-deterministic use of
-- julianday() in a generated column". Verified by execution on SQLite 3.51.0.
--
-- root_state is populated, not inferred: 30 of 35 registered projects are real git repos,
-- five of them worktrees where .git is a file rather than a directory -- the populating code
-- must accept .git as either, not os.path.isdir(root/'.git'), which misreads all five as
-- not-a-git-repo. Four roots do not exist, one has no source_root. Without this column
-- v_ticket_diff_binding cannot tell "checked and found no commits" from
-- "there is nothing here to check".
CREATE TABLE dim_project (
    project_prefix    TEXT PRIMARY KEY,
    project_codename  TEXT NOT NULL,
    tessera_project_id INTEGER,
    source_root       TEXT,
    root_is_shared    INTEGER NOT NULL DEFAULT 0,
    override_file     TEXT,
    root_state        TEXT CHECK (root_state IN
                      ('git-repo','not-a-git-repo','root-missing','no-source-root')),
    is_git_repo       INTEGER,
    foreman_bootstrapped INTEGER,
    tessguard_wired   INTEGER,
    last_commit_ts    TEXT,
    last_commit_sha   TEXT,
    refreshed_at      TEXT NOT NULL,
    refreshed_by_run  INTEGER REFERENCES ingest_run(run_id)
);

-- source_root -> project is many-to-many, not a function. AREM and FORE both claim
-- /path/to/agent-remediation, covering 19,820 real verdict rows and 701 real fire verdicts.
-- Those two are also every other project's default dependency set
-- (cross_project_routing/routing_table.py: DEFAULT_DEPENDS_ON = {"FORE","AREM"}), so the
-- ambiguity sits directly under the most-fired blocking gate's cross-project check.
CREATE TABLE project_root (
    source_root     TEXT NOT NULL,
    project_prefix  TEXT NOT NULL REFERENCES dim_project(project_prefix),
    origin          TEXT NOT NULL CHECK (origin IN ('tessera-registry','foreman-override')),
    root_depth      INTEGER NOT NULL,
    refreshed_at    TEXT NOT NULL,
    PRIMARY KEY (source_root, project_prefix)
);
CREATE INDEX ix_project_root_depth ON project_root(root_depth DESC);

-- Matching is path-COMPONENT-aware longest-match, never string prefix:
-- one real checkout path is a bare string prefix of a second, differently-named checkout of the same repo, and four other
-- registered root pairs collide the same way. The CHECK constraints make an inconsistent
-- (resolution, project_prefix, candidate_count) triple unstorable.
CREATE TABLE cwd_project (
    cwd             TEXT PRIMARY KEY,
    matched_root    TEXT,
    project_prefix  TEXT,
    candidate_count INTEGER NOT NULL,
    resolution      TEXT NOT NULL
                    CHECK (resolution IN ('unique','ambiguous','unregistered')),
    resolved_at     TEXT NOT NULL,
    resolved_by_run INTEGER REFERENCES ingest_run(run_id),
    CHECK ((resolution = 'unique')       = (project_prefix IS NOT NULL)),
    CHECK ((resolution = 'unregistered') = (matched_root IS NULL)),
    CHECK ((resolution = 'unique' AND candidate_count = 1)
        OR (resolution = 'ambiguous' AND candidate_count > 1)
        OR (resolution = 'unregistered' AND candidate_count = 0))
);

CREATE TABLE cwd_project_candidate (
    cwd            TEXT NOT NULL REFERENCES cwd_project(cwd),
    project_prefix TEXT NOT NULL REFERENCES dim_project(project_prefix),
    PRIMARY KEY (cwd, project_prefix)
);

-- writer_impl records which code path produced a handler's rows. 15,851 real rows come from
-- two shell writers that bypass verdict_ledger.py, emit second-resolution timestamps and
-- sometimes omit epoch_ms. Without this column those look like noise rather than a second
-- implementation of the record builder.
CREATE TABLE dim_handler (
    handler_id       TEXT PRIMARY KEY,
    owning_layer     TEXT CHECK (owning_layer IN
                     ('foreman-hard-gate','foreman-advisory','tessguard','safety-guard','probe','other')),
    is_blocking      INTEGER,
    writer_impl      TEXT CHECK (writer_impl IN ('verdict_ledger.py','shell','unknown')),
    first_seen_ts    TEXT,
    last_seen_ts     TEXT,
    notes            TEXT
);

-- No machine column and no model column: sessions.jsonl carries neither, and a field that is
-- always NULL looks handled while carrying zero bits. Deferral D5.
CREATE TABLE dim_session (
    session_id     TEXT PRIMARY KEY,
    started_ts     TEXT,
    ended_ts       TEXT,
    claude_version TEXT,
    git_branch     TEXT,
    start_cwd      TEXT
);

-- ============================================================ FACTS
-- Grain: one line of verdicts.jsonl. PK is (stream_id, byte_offset) because this data has no
-- natural key. Measured against the real 125,977-row file: the Foreman-side document's
-- (ts, handler_id, session_id, tool_use_id) duplicates 20,114 rows on a crash replay under
-- literal SQL NULL semantics, and drops 623 real rows once the NULLs are coalesced. Even
-- sha256 of the whole raw line drops 489.
--
-- Only ts, handler_id and verdict are NOT NULL. The TESSERA-side document declares epoch_ms,
-- hook_event and cwd NOT NULL; running its exact DDL against the real file HARD-REJECTS 751
-- rows (563 on hook_event, 188 on epoch_ms, 34 cwd violations masked behind those).
CREATE TABLE hook_verdict (
    stream_id        TEXT NOT NULL,
    byte_offset      INTEGER NOT NULL,
    ingest_run_id    INTEGER NOT NULL REFERENCES ingest_run(run_id),
    ts               TEXT NOT NULL,
    epoch_ms         INTEGER,
    ts_resolution    TEXT NOT NULL CHECK (ts_resolution IN ('microsecond','second')),
    handler_id       TEXT NOT NULL,
    hook_event       TEXT,
    verdict          TEXT NOT NULL CHECK (verdict IN ('fire','silent','error','stolen')),
    kind             TEXT,
    session_id       TEXT,
    cwd              TEXT,
    tool_name        TEXT,
    tool_use_id      TEXT,
    self_duration_ms REAL,
    target           TEXT,
    decision         TEXT CHECK (decision IS NULL OR decision IN ('deny','defer','ask','allow')),
    rule_id          TEXT,
    probe_id         TEXT,
    run_id           TEXT,
    PRIMARY KEY (stream_id, byte_offset),
    -- Holds exactly, both directions, across all 125,977 real rows.
    CHECK ((verdict = 'stolen') = (target IS NOT NULL))
) WITHOUT ROWID;
CREATE INDEX ix_hv_cwd_epoch     ON hook_verdict(cwd, epoch_ms);
CREATE INDEX ix_hv_handler_verd  ON hook_verdict(handler_id, verdict);
CREATE INDEX ix_hv_session_ts    ON hook_verdict(session_id, ts);
CREATE INDEX ix_hv_decision      ON hook_verdict(decision) WHERE decision IS NOT NULL;
CREATE INDEX ix_hv_ts            ON hook_verdict(ts);

-- source_path is where the row was actually read from. ledger_claim is what the row says
-- about itself, and the two disagree by design: example-safety-tool/audit.jsonl
-- self-reports ledger="project", not its own name, so this routing effect is only
-- detectable by keeping both. payload_json stays JSON: nine event types share almost no
-- fields, and the largest real payload is 682,196 bytes of verbatim shell text.
CREATE TABLE audit_event (
    stream_id      TEXT NOT NULL,
    byte_offset    INTEGER NOT NULL,
    ingest_run_id  INTEGER NOT NULL REFERENCES ingest_run(run_id),
    source_path    TEXT NOT NULL,
    ledger_claim   TEXT,
    schema_version TEXT,
    ts             TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    severity       TEXT,
    session_id     TEXT,
    cwd            TEXT,
    payload_json   TEXT NOT NULL,
    payload_bytes  INTEGER NOT NULL,
    PRIMARY KEY (stream_id, byte_offset)
) WITHOUT ROWID;
CREATE INDEX ix_ae_type_ts ON audit_event(event_type, ts);
CREATE INDEX ix_ae_cwd     ON audit_event(cwd);

-- Mirrored from v_flat rather than re-derived. ticket_severity and
-- ticket_criteria_frozen_before_work are carried because F-6 and F-15 need them and the
-- TESSERA-side document's proposed table omits both.
CREATE TABLE tessera_event (
    event_id            INTEGER PRIMARY KEY,
    ingest_run_id       INTEGER NOT NULL REFERENCES ingest_run(run_id),
    event_type          TEXT NOT NULL,
    event_ts            TEXT NOT NULL,
    actor               TEXT,
    actor_kind          TEXT,
    ticket_id           TEXT,
    project_prefix      TEXT,
    ticket_type         TEXT,
    ticket_status       TEXT,
    ticket_is_closed    INTEGER,
    ticket_priority     TEXT,
    ticket_severity     TEXT,
    ticket_has_frozen_criteria INTEGER,
    ticket_criteria_frozen_before_work INTEGER,
    ticket_criteria_count INTEGER,
    ticket_claim_count  INTEGER,
    ticket_lead_time_hours REAL,
    comment_has_code_snippet INTEGER,
    status_from         TEXT,
    status_to           TEXT,
    -- The real file-level check (tessera.api.discrepancy.
    -- discrepancy_for_ticket_singlerepo(), wired into both TESSERA close paths).
    -- Null on every event_type other than ClaimDiscrepancyChecked.
    diff_check_matches  INTEGER,
    diff_check_commit_sha TEXT,
    diff_check_touched_but_not_claimed_count INTEGER,
    diff_check_claimed_but_not_touched_count INTEGER
);
CREATE INDEX ix_te_project_ts ON tessera_event(project_prefix, event_ts);
CREATE INDEX ix_te_ticket     ON tessera_event(ticket_id);

-- author_hash, not author. Deferral D8.
CREATE TABLE git_commit (
    project_prefix TEXT NOT NULL REFERENCES dim_project(project_prefix),
    sha            TEXT NOT NULL,
    ingest_run_id  INTEGER NOT NULL REFERENCES ingest_run(run_id),
    committed_ts   TEXT NOT NULL,
    author_hash    TEXT,
    subject        TEXT,
    files_changed  INTEGER,
    insertions     INTEGER,
    deletions      INTEGER,
    PRIMARY KEY (project_prefix, sha)
) WITHOUT ROWID;
CREATE INDEX ix_gc_ts ON git_commit(committed_ts);

CREATE TABLE git_commit_file (
    project_prefix TEXT NOT NULL,
    sha            TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    insertions     INTEGER,
    deletions      INTEGER,
    PRIMARY KEY (project_prefix, sha, file_path),
    FOREIGN KEY (project_prefix, sha) REFERENCES git_commit(project_prefix, sha)
) WITHOUT ROWID;
CREATE INDEX ix_gcf_path ON git_commit_file(file_path);

-- project_prefix is the REPO's prefix, not the ticket's, and they legitimately differ:
-- measured on real history, 3 commits in the GLAS repo reference FORE tickets, 3 in TESS,
-- 3 in VALE. Conflating them would be wrong.
-- origin is part of the key because TESSERA's ticket_commit_links has 0 rows today, so the
-- link is derived from commit messages and must stay distinguishable from a real TESSERA
-- link if that table is ever populated.
CREATE TABLE git_commit_ticket (
    project_prefix TEXT NOT NULL,
    sha            TEXT NOT NULL,
    ticket_id      TEXT NOT NULL,
    origin         TEXT NOT NULL CHECK (origin IN ('commit-message-regex','tessera-link')),
    PRIMARY KEY (project_prefix, sha, ticket_id, origin),
    FOREIGN KEY (project_prefix, sha) REFERENCES git_commit(project_prefix, sha)
) WITHOUT ROWID;
CREATE INDEX ix_gct_ticket ON git_commit_ticket(ticket_id);

-- Dropped candidates are STORED, not discarded, so the drop rate is queryable rather than
-- invisible. Real corpus, 1,097 commits: 167 ticket-shaped strings, 15 dropped for an
-- unregistered prefix -- P0-2, P0-3, P0-4, P0-6 (severity-rubric references), F5-001..004,
-- MR-0, ISO-8601. Without validation each of those manufactures a link to a nonexistent
-- ticket in a nonexistent project.
CREATE TABLE git_ticket_candidate_dropped (
    project_prefix TEXT NOT NULL,
    sha            TEXT NOT NULL,
    candidate      TEXT NOT NULL,
    reason         TEXT NOT NULL CHECK (reason IN ('unregistered-prefix')),
    ingest_run_id  INTEGER NOT NULL REFERENCES ingest_run(run_id),
    PRIMARY KEY (project_prefix, sha, candidate)
) WITHOUT ROWID;

-- ============================================================ DATA QUALITY
-- scope: 'source' asks whether the INPUT is bad. 'pipeline' asks whether ATLAS ITSELF
--   behaved consistently run-over-run. The first draft had only the former, so the pipeline
--   could regress silently while every check stayed green. 'snapshot' asks whether a value
--   computed at PUBLISH TIME, from a live read that never lands in a warehouse table (the
--   ticket rollup, per §5/§6), is internally consistent -- an earlier fix, added because
--   nothing in 'source'/'pipeline' can reach a value that has no source_table of its own.
-- severity: 'contract' failures WITHHOLD a source from every gated view for that run, or for
--   'snapshot' scope, block that publish. 'advisory' failures record and alert without gating.
-- threshold_kind: 'invariant' means the threshold is the source's own contract, not a choice.
--   'calibrated' means it is derived from another measurable property and moves with it.
--   'lean' means a judgment call with a named recalibration trigger. This column exists so
--   the distinction survives into the data, not just the prose.
CREATE TABLE dq_check (
    check_name   TEXT PRIMARY KEY,
    source_table TEXT NOT NULL,
    scope        TEXT NOT NULL CHECK (scope IN ('source','pipeline','snapshot')),
    severity     TEXT NOT NULL CHECK (severity IN ('contract','advisory')),
    threshold_kind TEXT NOT NULL CHECK (threshold_kind IN ('invariant','calibrated','lean')),
    description  TEXT NOT NULL,
    implemented  INTEGER NOT NULL DEFAULT 0
);

-- Every check writes a row every run, pass or fail. An empty table here is indistinguishable
-- from "no check has ever run", which is the tessguard failure this exists to not
-- repeat.
CREATE TABLE dq_check_run (
    check_run_id   INTEGER PRIMARY KEY,
    run_id         INTEGER NOT NULL REFERENCES ingest_run(run_id),
    check_name     TEXT NOT NULL REFERENCES dq_check(check_name),
    run_at         TEXT NOT NULL,
    observed_value REAL,
    baseline_value REAL,
    passed         INTEGER NOT NULL CHECK (passed IN (0,1)),
    detail         TEXT,
    UNIQUE (run_id, check_name)
);
CREATE INDEX ix_dqr_recent ON dq_check_run(check_name, run_at DESC);

-- The CHECK makes "known bug, no check, no deferral" UNSTORABLE. Verified by attempting an
-- orphan insert, which the engine rejects. Without it a known bug can sit in the table
-- untracked, which is the same shape as a gate that is registered and never fires.
CREATE TABLE dq_known_metric_bug (
    bug_id      TEXT PRIMARY KEY,
    summary     TEXT NOT NULL,
    check_name  TEXT REFERENCES dq_check(check_name),
    deferral_id TEXT,
    implemented INTEGER NOT NULL DEFAULT 0,
    CHECK (implemented = 1 OR deferral_id IS NOT NULL)
);

-- The WAREHOUSE-TABLE sources the SNAPSHOT reads, as data rather than as a constant in the
-- publisher. Measured defect this fixes: gating publication on warehouse-wide contract_failures
-- meant a git commit-message check could freeze the real-time artifact, which ages past
-- max_age_seconds, which puts every hook in every project on the fail-open path.
-- Deliberately does NOT carry a row for every 'snapshot'-scope dq_check -- section 7's
-- falsification-re-review correction explains why 'tessera' (snapshot_ticket_rollup_identity)
-- is enforced procedurally by publish() instead, not by adding a row here.
CREATE TABLE snapshot_source (
    source_table TEXT PRIMARY KEY
);
INSERT INTO snapshot_source VALUES ('hook_verdict');

-- ============================================================ VIEWS
CREATE VIEW v_source_trust AS
SELECT c.source_table,
       MAX(r.run_id)                                        AS last_run_id,
       SUM(CASE WHEN c.severity='contract' AND r.passed=0 THEN 1 ELSE 0 END) AS contract_failures,
       SUM(CASE WHEN c.severity='advisory' AND r.passed=0 THEN 1 ELSE 0 END) AS advisory_failures,
       COUNT(*)                                             AS checks_run
FROM dq_check c
JOIN dq_check_run r ON r.check_name = c.check_name
WHERE r.run_id = (SELECT MAX(run_id) FROM ingest_run WHERE status='ok')
GROUP BY c.source_table;

-- A source with NO contract check at all is not queryable. Deliberate: an unchecked source
-- is treated as absent, not as clean.
CREATE VIEW v_queryable_source AS
SELECT t.source_table
FROM (SELECT DISTINCT source_table FROM dq_check WHERE severity='contract') t
JOIN v_source_trust s ON s.source_table = t.source_table
WHERE s.contract_failures = 0;

-- Scoped publication gate. Negative-tested both ways: failing a git_commit check leaves
-- publishable=1; failing verdict_domain_closed gives publishable=0, blocking_sources=hook_verdict.
CREATE VIEW v_snapshot_publishable AS
SELECT CASE WHEN (SELECT COUNT(*) FROM snapshot_source ss
                   WHERE ss.source_table NOT IN (SELECT source_table FROM v_queryable_source)) = 0
            THEN 1 ELSE 0 END                                AS publishable,
       (SELECT GROUP_CONCAT(ss.source_table) FROM snapshot_source ss
         WHERE ss.source_table NOT IN (SELECT source_table FROM v_queryable_source)) AS blocking_sources;

-- The one view a consumer is contracted to read FIRST. On an empty database this reports an
-- untrusted, unpublishable state rather than a clean one.
-- An earlier fix. Two blind spots, both against this section's own "reports an untrusted,
-- unpublishable state rather than a clean one" claim: (1) an empty ingest_run table made
-- MAX(run_id) NULL, so the WHERE clause matched nothing and this view returned ZERO rows on an
-- uninitialized database, indistinguishable from the broken-anchor state its own comment warns
-- about; (2) a run whose OWN status is 'failed' still had its contract_failures/etc. computed
-- from dq_check_run rows for that run_id, which is 0/0/0 -- vacuously, not because anything
-- passed -- when a failed run recorded zero checks. That reads as clean in the exact fields a
-- consumer is contracted to check. checks_evaluated makes the two cases distinguishable: 0
-- failures over 0 evaluated is not clean, it is unknown, the same distinction pct_timed already
-- makes for v_hook_latency_rollup. A consumer must read status='ok' AND checks_evaluated>0
-- before trusting contract_failures=0 as clean -- the query facade enforces this, not prose
-- alone (matching an earlier fix's caveat: a sentinel only protects a caller who reads it).
CREATE VIEW v_atlas_status AS
SELECT r.run_id, r.started_at, r.finished_at, r.status, r.host_id,
       (SELECT COUNT(*) FROM dq_check_run d WHERE d.run_id=r.run_id)        AS checks_evaluated,
       (SELECT COUNT(*) FROM dq_check_run d JOIN dq_check c ON c.check_name=d.check_name
         WHERE d.run_id=r.run_id AND d.passed=0 AND c.severity='contract')  AS contract_failures,
       (SELECT COUNT(*) FROM dq_check_run d JOIN dq_check c ON c.check_name=d.check_name
         WHERE d.run_id=r.run_id AND d.passed=0 AND c.severity='advisory')  AS advisory_failures,
       (SELECT COUNT(*) FROM dq_check_run d JOIN dq_check c ON c.check_name=d.check_name
         WHERE d.run_id=r.run_id AND d.passed=0 AND c.scope='pipeline')     AS pipeline_failures,
       (SELECT GROUP_CONCAT(source_table) FROM v_queryable_source)          AS queryable_sources,
       (SELECT publishable FROM v_snapshot_publishable)                     AS snapshot_publishable
FROM ingest_run r
WHERE r.run_id = (SELECT MAX(run_id) FROM ingest_run)
UNION ALL
SELECT NULL, NULL, NULL, 'NO-INGEST-RUN-YET', NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM ingest_run);

-- THE single project derivation. Every project-dimensioned view joins through this, so no
-- row can leave a per-project rollup without landing in a named bucket: the join is inner
-- and project_scope is total. 44.1% of all FIRE verdicts (1,227 of 2,782) live in a
-- non-unique scope; goals_freeze_gate.py alone has 21 of its 40 production fires there.
-- A WHERE project_prefix IS NOT NULL would discard them and report the remainder as the whole.
CREATE VIEW v_project_scope AS
SELECT 'ok' AS trust_state, v.stream_id, v.byte_offset,
       COALESCE(cp.resolution, CASE WHEN v.cwd IS NULL THEN 'no-cwd' ELSE 'unregistered' END) AS resolution,
       COALESCE(cp.project_prefix,
                '<' || COALESCE(cp.resolution,
                                CASE WHEN v.cwd IS NULL THEN 'no-cwd' ELSE 'unregistered' END) || '>')
                                                        AS project_scope,
       cp.project_prefix                                AS project_prefix,
       COALESCE(cp.candidate_count,0)                   AS candidate_count
FROM hook_verdict v
LEFT JOIN cwd_project cp ON cp.cwd = v.cwd
WHERE 'hook_verdict' IN (SELECT source_table FROM v_queryable_source)
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL
WHERE 'hook_verdict' NOT IN (SELECT source_table FROM v_queryable_source);

-- A distrusted source returns ONE row saying so, never zero rows. Verified by negative test.
-- Scope limit, stated where the claim is made: a sqlite3 session against hook_verdict
-- bypasses this entirely, and an aggregate that never reads trust_state sees a clean NULL.
CREATE VIEW v_hook_verdict AS
SELECT 'ok' AS trust_state, v.stream_id, v.byte_offset, v.ts, v.epoch_ms, v.ts_resolution,
       v.handler_id, v.hook_event, v.verdict, v.kind, v.session_id, v.cwd, v.tool_name,
       v.tool_use_id, v.self_duration_ms, v.target, v.decision, v.rule_id, v.probe_id, v.run_id,
       s.resolution AS project_resolution, s.project_scope, s.project_prefix, s.candidate_count
FROM hook_verdict v
JOIN v_project_scope s ON s.stream_id=v.stream_id AND s.byte_offset=v.byte_offset
WHERE 'hook_verdict' IN (SELECT source_table FROM v_queryable_source)
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE',
       NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
       NULL,NULL,NULL,NULL
WHERE 'hook_verdict' NOT IN (SELECT source_table FROM v_queryable_source);

-- Tell: the buckets must sum to COUNT(*) FROM hook_verdict.
-- Live: 95,341 + 19,820 + 10,782 + 34 = 125,977.
-- Reads v_hook_verdict, not hook_verdict directly: an earlier fix. A view built straight off
-- the base table reports as if hook_verdict were trusted even when it is not. Every view below
-- that aggregates hook_verdict facts carries the same trust_state/UNION-ALL-sentinel shape as
-- v_hook_verdict itself, not a plain WHERE filter -- a plain filter against a distrusted source
-- returns zero rows, which is the empty-view failure this design exists to prevent.
CREATE VIEW v_project_resolution_coverage AS
SELECT 'ok' AS trust_state, v.project_resolution AS resolution,
       COUNT(*)                                        AS verdict_rows,
       SUM(v.verdict='fire')                           AS fire_rows,
       COUNT(DISTINCT v.cwd)                           AS distinct_cwds,
       ROUND(100.0*COUNT(*)/(SELECT COUNT(*) FROM v_hook_verdict WHERE trust_state='ok'), 2) AS pct_rows
FROM v_hook_verdict v
WHERE v.trust_state = 'ok'
GROUP BY v.project_resolution
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- structural_zero_denominator is that structural-zero-denominator class made mechanical. Four real handlers qualify:
-- session-log.sh, telemetry_liveness.py, guard_untrusted_web.py (530 fire / 0 silent),
-- reinject_compact.py.
CREATE VIEW v_handler_denominator AS
SELECT 'ok' AS trust_state, handler_id,
       COUNT(*)                                                AS n,
       SUM(verdict='fire')                                     AS n_fire,
       SUM(verdict='silent')                                   AS n_silent,
       SUM(verdict='error')                                    AS n_error,
       SUM(verdict='stolen')                                   AS n_stolen,
       CASE WHEN SUM(verdict='silent') = 0 AND SUM(verdict='fire') > 0
            THEN 1 ELSE 0 END                                   AS structural_zero_denominator
FROM v_hook_verdict
WHERE trust_state = 'ok'
GROUP BY handler_id
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- Boundary falls back run_id -> probe_id -> session_id, and reports which it used. run_id and
-- probe_id together cover 4.49% of the real ledger; grouping on them alone would score 4.49%
-- of the data and read as complete. session_id covers 99.57%.
-- fire_rate is NULL, not 1.0, wherever the denominator is structurally zero.
CREATE VIEW v_verdict_confusion_matrix AS
SELECT 'ok' AS trust_state, v.handler_id,
       COALESCE(v.run_id, 'session:' || COALESCE(v.session_id,'<none>')) AS boundary_key,
       CASE WHEN v.run_id IS NOT NULL THEN 'run_id'
            WHEN v.probe_id IS NOT NULL THEN 'probe_id'
            WHEN v.session_id IS NOT NULL THEN 'session_id'
            ELSE 'unbounded' END                                AS boundary_kind,
       v.project_scope,
       d.structural_zero_denominator,
       COUNT(*)                                                 AS n,
       SUM(v.verdict='fire')                                    AS n_fire,
       SUM(v.verdict='silent')                                  AS n_silent,
       SUM(v.verdict='error')                                   AS n_error,
       SUM(v.verdict='stolen')                                  AS n_stolen,
       CASE WHEN d.structural_zero_denominator = 1 THEN NULL
            ELSE ROUND(1.0*SUM(v.verdict='fire')/COUNT(*), 4) END AS fire_rate
FROM v_hook_verdict v
JOIN v_handler_denominator d ON d.handler_id = v.handler_id AND d.trust_state = 'ok'
WHERE v.trust_state = 'ok'
GROUP BY v.handler_id, boundary_key, boundary_kind, v.project_scope, d.structural_zero_denominator
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- The fail-open double-count class, structurally prevented. verdicts.jsonl verdict='error' (13 rows) and
-- safety.jsonl HOOK_ERROR (13 rows) are the SAME 13 incidents: matched on handler + cwd + ts
-- within 2s, all 13 pair, zero unmatched either side. A UNION would report 26.
-- Tell: any row count other than the verdict='error' count means the LEFT JOIN fanned out.
CREATE VIEW v_fail_open_incident AS
SELECT 'ok' AS trust_state, v.stream_id, v.byte_offset, v.ts, v.handler_id, v.cwd, v.project_scope,
       v.kind AS exception_class,
       a.payload_json AS audit_detail,
       CASE WHEN a.stream_id IS NULL THEN 'verdict-only' ELSE 'both-ledgers' END AS corroboration
FROM v_hook_verdict v
LEFT JOIN audit_event a
       ON a.event_type = 'HOOK_ERROR'
      AND json_extract(a.payload_json,'$.hook') = v.handler_id
      AND a.cwd IS v.cwd
      AND ABS(strftime('%s', a.ts) - strftime('%s', v.ts)) <= 2
WHERE v.trust_state = 'ok' AND v.verdict = 'error'
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- pct_timed is the tell. self_duration_ms is absent on 22.54% of rows, so an average over
-- COUNT(*) is wrong by that share and nothing else in the output would show it.
CREATE VIEW v_hook_latency_rollup AS
SELECT 'ok' AS trust_state, handler_id,
       substr(ts,1,10)                          AS day,
       COUNT(self_duration_ms)                  AS n_timed,
       COUNT(*)                                 AS n_rows,
       ROUND(100.0*COUNT(self_duration_ms)/COUNT(*),1) AS pct_timed,
       ROUND(AVG(self_duration_ms),2)           AS avg_ms,
       MAX(self_duration_ms)                    AS max_ms
FROM v_hook_verdict
WHERE trust_state = 'ok'
GROUP BY handler_id, day
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- rule_id is NULL on 14 of the 410 real decision rows (all guard_destructive.py denies).
-- They surface as '<no-rule_id>' rather than vanishing. Tell: the total must equal 410.
CREATE VIEW v_decision_outcome_rate AS
SELECT 'ok' AS trust_state, v.handler_id,
       COALESCE(v.rule_id,'<no-rule_id>')       AS rule_id,
       v.project_scope,
       v.decision,
       COUNT(*)                                 AS n
FROM v_hook_verdict v
WHERE v.trust_state = 'ok' AND v.decision IS NOT NULL
GROUP BY v.handler_id, COALESCE(v.rule_id,'<no-rule_id>'), v.project_scope, v.decision
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- Gaps-and-islands over consecutive denies within (session, handler). 181 real streaks,
-- longest 6. WEAKEST correctness tell in the schema, stated as such: this arithmetic can be
-- subtly wrong and still produce plausible counts. Cross-check, CORRECTED by a falsification
-- re-review (2026-08-22): a prior version of this comment claimed total rows across all streaks
-- must equal the FULL deny count in v_decision_outcome_rate; measured on correct data that is
-- false by construction, off by exactly the rows with session_id IS NULL (this view's own CTE
-- filters WHERE session_id IS NOT NULL, since PARTITION BY session_id cannot group a NULL
-- session meaningfully). The real cross-check is scoped: streak row total must equal
-- v_decision_outcome_rate's deny count RESTRICTED TO rows with a non-null session_id, not the
-- unscoped total.
-- An earlier fix. ts is unnormalized TEXT and two writers changed format at 2026-08-12T21:35
-- '2026-08-21T00:00:01Z' sorts AFTER '2026-08-21T00:00:01.500000Z' lexicographically
-- because 'Z' (0x5A) > '.' (0x2E), which inverts chronology across the exact boundary this
-- view's window functions order by. Ordering (and grp's gaps-and-islands partitioning) is now
-- keyed on epoch_ms first, an integer with no such pitfall, with ts as a tiebreak only.
-- streak_start_epoch_ms/streak_end_epoch_ms are the authoritative boundary values, and the ONLY
-- ones downstream code (v_trapped_agent_candidate) may compare or subtract. streak_start/
-- streak_end (ts text) are MIN(ts)/MAX(ts) over the now-correctly-grouped rows, but that MIN/MAX
-- is still a plain lexicographic string comparison -- on a mixed-precision streak it can print
-- streak_start chronologically AFTER streak_end (verified: the exact counterexample above
-- produces streak_start='...01.500000Z', streak_end='...01Z', swapped). Display-only, human-
-- readability convenience, not a lesser-precision-but-still-ordered pair; do not sort or compare
-- on them. Residual, disclosed rather than silently accepted: epoch_ms is NULL on 0.15% of rows
-- (section 1); those rows sort by ts alone within their PARTITION and can still misorder
-- relative to the rest, which the epoch_ms-keyed fix above does not reach.
CREATE VIEW v_deny_streak AS
WITH d AS (
  SELECT session_id, handler_id, ts, epoch_ms, rule_id, decision,
         ROW_NUMBER() OVER (PARTITION BY session_id, handler_id ORDER BY epoch_ms, ts)
       - ROW_NUMBER() OVER (PARTITION BY session_id, handler_id,
                            CASE WHEN decision='deny' THEN 1 ELSE 0 END ORDER BY epoch_ms, ts) AS grp,
         CASE WHEN decision='deny' THEN 1 ELSE 0 END AS is_deny
  FROM v_hook_verdict
  WHERE trust_state = 'ok' AND session_id IS NOT NULL AND decision IS NOT NULL
)
SELECT 'ok' AS trust_state, session_id, handler_id, grp,
       COUNT(*)              AS streak_len,
       MIN(ts)               AS streak_start,
       MAX(ts)               AS streak_end,
       MIN(epoch_ms)         AS streak_start_epoch_ms,
       MAX(epoch_ms)         AS streak_end_epoch_ms,
       GROUP_CONCAT(DISTINCT rule_id) AS rule_ids
FROM d
WHERE is_deny = 1
GROUP BY session_id, handler_id, grp
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- The intervening-work test counts COMPLETED TOOL CALLS (distinct tool_use_id on PostToolUse),
-- not `stolen` verdicts. Defining it on `stolen` returned 0 across all 181 streaks and would
-- have flagged every streak as trapped: `stolen` marks a write by a HOOK, not a completed tool
-- call by the agent. Corrected: 8 streaks of length >= 3, of which 3 have zero completed tool
-- calls -- the sharpest being six consecutive FOREMAN-PREFLIGHT-GATE:unreachable denials over
-- 95 seconds with nothing completing.
-- Same earlier fix, continued: this view's own window predicate (w.ts > streak_start AND
-- w.ts < streak_end) was named in the review as exposed to the same lexicographic-ts defect
-- v_deny_streak had. Now keyed on streak_start_epoch_ms/streak_end_epoch_ms, and window_seconds
-- is computed directly from the epoch_ms difference rather than julianday(ts) parsing, which
-- both fixes the defect and removes a unit-conversion step. Residual: a PostToolUse row with
-- NULL epoch_ms (0.15% of rows) can no longer match this predicate; disclosed, not silent.
--
-- window_measurable, MAJOR-B fix (falsification re-review, 2026-08-22): a streak whose OWN
-- epoch_ms bounds are NULL (a real, if rare, case -- streak_start_epoch_ms/streak_end_epoch_ms
-- come from MIN/MAX(epoch_ms) over the streak's rows) makes the window predicate above match
-- NOTHING, so completed_tool_calls_in_window reads 0 -- identical in shape to a genuine trapped
-- agent (also 0 completed calls), and indistinguishable from it without this column. Demonstrated:
-- a 3-deny streak with NULL epoch_ms bounds and two tool calls genuinely completed inside its
-- real (ts-based) window reported 0/NULL before this fix, a false trapped-agent signature this
-- view's own confirmed-clean-vs-could-not-check discipline (section 8, section 9) exists to
-- prevent. window_measurable=0 means "could not check," matching pct_timed's existing pattern
-- for v_hook_latency_rollup; a consumer must not read completed_tool_calls_in_window=0 as
-- "confirmed trapped" without also checking window_measurable=1.
CREATE VIEW v_trapped_agent_candidate AS
SELECT 'ok' AS trust_state, s.session_id, s.handler_id, s.streak_len, s.streak_start, s.streak_end, s.rule_ids,
       CASE WHEN s.streak_start_epoch_ms IS NULL OR s.streak_end_epoch_ms IS NULL THEN 0 ELSE 1 END
                                                            AS window_measurable,
       CASE WHEN s.streak_start_epoch_ms IS NULL OR s.streak_end_epoch_ms IS NULL THEN NULL
            ELSE (SELECT COUNT(DISTINCT w.tool_use_id) FROM v_hook_verdict w
                   WHERE w.trust_state = 'ok'
                     AND w.session_id = s.session_id
                     AND w.hook_event = 'PostToolUse'
                     AND w.epoch_ms > s.streak_start_epoch_ms AND w.epoch_ms < s.streak_end_epoch_ms)
       END                                                 AS completed_tool_calls_in_window,
       CASE WHEN s.streak_start_epoch_ms IS NULL OR s.streak_end_epoch_ms IS NULL THEN NULL
            ELSE CAST((s.streak_end_epoch_ms - s.streak_start_epoch_ms) / 1000.0 AS INTEGER)
       END                                                 AS window_seconds
FROM v_deny_streak s
WHERE s.trust_state = 'ok' AND s.streak_len >= 3
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

-- QUALITY-BAR F-10, re-derived from data. The three fires_* columns exist because a filtered
-- count would silently discard 44.1% of all fire verdicts: goals_freeze_gate.py has 19
-- resolved against 21 unresolved, so more than half its production evidence sits outside the
-- registry. Tell: fires_total must equal fires_resolved + fires_ambiguous + fires_unresolved.
-- Was an earlier headline fix case: this view re-derives QUALITY-BAR F-10, the compliance number,
-- and read straight off hook_verdict, so it reported a distrusted source as "proven live" --
-- the exact failure this whole trust-gate design exists to catch, sitting inside its own
-- codebase. Now routed through v_hook_verdict. Negative-tested: output is byte-identical to
-- the prior view when hook_verdict is trusted (25 rows, architecture_gate.py 192/162/1) and
-- collapses to the single sentinel row when it is not.
CREATE VIEW v_gate_proven_live AS
SELECT 'ok' AS trust_state, v.handler_id,
       SUM(v.verdict='fire')                                                    AS fires_total,
       SUM(v.verdict='fire' AND v.project_resolution='unique')                  AS fires_resolved,
       SUM(v.verdict='fire' AND v.project_resolution='ambiguous')               AS fires_ambiguous,
       SUM(v.verdict='fire' AND v.project_resolution IN ('unregistered','no-cwd')) AS fires_unresolved,
       COUNT(DISTINCT CASE WHEN v.verdict='fire' THEN v.cwd END)                AS distinct_fire_cwds,
       COUNT(DISTINCT CASE WHEN v.verdict='fire' AND v.project_resolution='unique'
                           THEN v.project_prefix END)                           AS registered_projects_fired_in,
       CASE WHEN SUM(v.verdict='fire' AND v.project_resolution='unique') > 0 THEN 1 ELSE 0 END AS f10_proven_live
FROM v_hook_verdict v
WHERE v.trust_state = 'ok'
GROUP BY v.handler_id
UNION ALL
SELECT 'SOURCE-DISTRUSTED-DO-NOT-USE', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM v_hook_verdict WHERE trust_state = 'ok');

CREATE VIEW v_source_freshness AS
SELECT s.source_name, s.source_path, s.stream_id, s.byte_offset, s.rows_ingested, s.updated_at,
       ROUND((julianday('now') - julianday(s.updated_at)) * 24 * 60, 1) AS staleness_minutes
FROM ingest_source s;

-- QUALITY-BAR F-12/F-13. linkability exists because a ticket in a project with no source_root
-- reports commits_linked=0 identically to one that was checked and found none. Measured on
-- real data: 228 linkable-and-unlinked, 2 no-source-root, 1 no-commits-ingested.
-- cross_repo_commits is real, not defensive: 3 commits in the GLAS repo reference FORE tickets.
-- This view was reported as "executed and inspected" in an earlier draft while
-- git_commit_ticket was EMPTY, returning 322 rows of uniform zero. Re-run against 1,097 real
-- commits it differentiates: 231 unlinked, 67 with one commit, 15 with two, one with 22.
-- commits_linked/cross_repo_commits (git_commit_ticket) stay exactly what they always
-- were -- a ticket-ID-shaped string in a commit SUBJECT LINE, gated only on the prefix being a
-- registered project, never checked against the ticket store and never looking at diff content.
-- Kept as-is rather than redefined, per this table's own additive-only discipline (a changed
-- MEANING is a breaking change, not a silent redefinition of an existing column) -- so real
-- file-level evidence is added as new columns beside it, not folded into it.
--
-- diff_checks_* comes from tessera_event rows with event_type='ClaimDiscrepancyChecked': the
-- one real, non-proxy check in the stack (tessera.api.discrepancy.
-- discrepancy_for_ticket_singlerepo(), wired into both TESSERA close paths),
-- comparing an agent's self-reported claimed files against git show --name-only's actual
-- file list for the claim's own commit_sha -- independent of whether that commit's subject
-- line ever mentioned the ticket ID, so it catches real evidence git_commit_ticket's regex
-- would miss entirely (an untitled/unconventional commit message) as well as flagging a
-- ticket-ID-shaped string that was never backed by a matching file diff.
CREATE VIEW v_ticket_diff_binding AS
SELECT t.ticket_id, t.project_prefix,
       CASE WHEN p.project_prefix IS NULL     THEN 'project-unknown'
            WHEN p.source_root IS NULL         THEN 'no-source-root'
            WHEN p.is_git_repo = 0             THEN 'not-a-git-repo'
            WHEN NOT EXISTS (SELECT 1 FROM git_commit g WHERE g.project_prefix = t.project_prefix)
                                               THEN 'no-commits-ingested'
            ELSE 'linkable' END                            AS linkability,
       COUNT(DISTINCT gct.sha)                             AS commits_linked,
       COUNT(DISTINCT CASE WHEN gct.project_prefix <> t.project_prefix THEN gct.sha END)
                                                           AS cross_repo_commits,
       MAX(t.ticket_claim_count)                           AS claim_count,
       MAX(t.ticket_is_closed)                             AS is_closed,
       COALESCE(MAX(dc.diff_checks_performed), 0)          AS diff_checks_performed,
       COALESCE(MAX(dc.diff_checks_matched), 0)            AS diff_checks_matched,
       COALESCE(MAX(dc.diff_checks_mismatched), 0)         AS diff_checks_mismatched,
       CASE WHEN MAX(dc.diff_checks_performed) IS NULL OR MAX(dc.diff_checks_performed) = 0
                 THEN 'not-checked'
            WHEN MAX(dc.diff_checks_mismatched) > 0        THEN 'verified-mismatch'
            ELSE 'verified-match' END                      AS file_level_evidence
FROM tessera_event t
LEFT JOIN dim_project p         ON p.project_prefix = t.project_prefix
LEFT JOIN git_commit_ticket gct ON gct.ticket_id = t.ticket_id
LEFT JOIN (
    SELECT ticket_id,
           COUNT(*)                                                    AS diff_checks_performed,
           SUM(CASE WHEN diff_check_matches = 1 THEN 1 ELSE 0 END)     AS diff_checks_matched,
           SUM(CASE WHEN diff_check_matches = 0 THEN 1 ELSE 0 END)     AS diff_checks_mismatched
    FROM tessera_event
    WHERE event_type = 'ClaimDiscrepancyChecked'
    GROUP BY ticket_id
) dc ON dc.ticket_id = t.ticket_id
WHERE t.ticket_id IS NOT NULL
GROUP BY t.ticket_id, t.project_prefix, linkability;

-- ATLAS checking ATLAS. Every other check asks whether the SOURCE is bad.
CREATE VIEW v_pipeline_selfcheck AS
SELECT c.check_name, r.run_id, r.observed_value, r.baseline_value, r.passed, r.detail
FROM dq_check c JOIN dq_check_run r ON r.check_name = c.check_name
WHERE c.scope = 'pipeline';
```

### Seed data, applied at schema creation

```sql
INSERT INTO dq_check (check_name, source_table, scope, severity, threshold_kind, description, implemented) VALUES
 ('verdict_domain_closed',        'hook_verdict', 'source',  'contract','invariant', 'Every verdict is one of verdict_ledger.VALID_VERDICTS. An unknown value means a second writer disagrees with the canonical one.',1),
 ('stolen_implies_target',        'hook_verdict', 'source',  'contract','invariant', 'target IS NOT NULL exactly when verdict=stolen. 0 violations in 125,977 real rows.',1),
 ('decision_domain_closed',       'hook_verdict', 'source',  'contract','invariant', 'Every decision is one of verdict_ledger.VALID_DECISIONS (the deny>defer>ask>allow merge lattice).',1),
 ('watermark_le_filesize',        'hook_verdict', 'source',  'contract','invariant', 'Stored byte_offset never exceeds the live file size. Exceeding it means truncation went undetected.',1),
 ('fail_open_not_double_counted', 'hook_verdict', 'source',  'contract','invariant', 'Fail-open double-count class. verdict=error and audit HOOK_ERROR describe the SAME 13 incidents; a union counts each twice.',1),
 ('project_resolution_floor',     'hook_verdict', 'source',  'advisory','calibrated','Resolution rate vs the fraction of registered projects that are real git repos (30/35 = 85.71%, corrected for five .git-as-file worktrees). ADVISORY: that corrected floor already exceeds the 84.27% ceiling set by the AREM/FORE shared-root ambiguity, which no registry fix can clear -- resolution_rate_delta carries the contract severity instead.',1),
 ('handler_denominator_nonzero',  'hook_verdict', 'source',  'advisory','invariant', 'Structural-zero-denominator class. A handler with zero silent verdicts has a structurally zero false-positive denominator and no interpretable fire rate.',1),
 ('audit_envelope_wellformed',    'audit_event',  'source',  'contract','invariant', 'schema_version, a parseable ts and a non-empty event_type on every row.',1),
 ('audit_payload_size_bounded',   'audit_event',  'source',  'contract','lean',      'No single audit payload exceeds 1 MiB. Real max 682,196 bytes. LEAN: recalibrate on the first payload above the bound.',1),
 ('audit_payload_credential_scan','audit_event',  'source',  'advisory','invariant', 'adversarial-code-review verification finding (SERIOUS): auditplane_to_ingest is secret-bearing (section 0/4) and section 1 states "ten credential patterns scanned, zero hits" as a one-time authorship measurement, never wired as a running check, while v_fail_open_incident (in query facade ALLOWED_VIEWS) serves audit_detail=payload_json verbatim. This check makes zero-hits a continuously-verified alert, not a stale claim -- advisory, since a real hit is an incident to alert on, not grounds to withhold the whole source. Does not redact at read time; whether query/snapshot should additionally redact secret-bearing payloads is a separate, tracked design question, not decided here.',1),
 ('audit_ledger_partition_by_cwd','audit_event',  'source',  'advisory','invariant', 'Rows whose cwd resolves to a registered project but which landed in global-safety. Measured 477.',1),
 ('tessera_event_id_unique',      'tessera_event','source',  'contract','invariant', 'v_flat stays 1:1 on event_id. Its comment join on (ticket_id, created_ts) fans out silently if two comments share a timestamp.',1),
 ('git_ticket_prefix_registered', 'git_commit',   'source',  'contract','invariant', 'Every stored ticket link has a prefix in dim_project. 15 of 167 real candidates dropped, incl. P0-3 and F5-001.',1),
 ('ingest_rows_match_bytes',      'hook_verdict', 'pipeline','contract','invariant', 'Rows ingested equals non-blank lines in the byte range consumed. Failed on its own first run (off by one, in the CHECK not the ingester); corrected, negative-tested by deleting 5 rows.',1),
 ('ingest_rows_monotonic',        'hook_verdict', 'pipeline','contract','invariant', 'Row count never decreases run-over-run.',1),
 ('resolution_rate_delta',        'hook_verdict', 'pipeline','contract','calibrated','Resolution rate must not drop more than 5 points run-over-run. Carries the contract severity project_resolution_floor cannot: a drop is causally tied to a resolver regression, a level is not. Verified: dropping 20% of cwd_project rows produces a 9.46-point drop, caught.',1),
 ('handler_set_stable',           'hook_verdict', 'pipeline','advisory','invariant', 'No previously-seen handler vanishes from the table.',1),
 ('watermark_advanced',           'hook_verdict', 'pipeline','advisory','invariant', 'Watermark advanced, or the source did not grow.',1),
 ('snapshot_ticket_rollup_identity','tessera',    'snapshot','contract','invariant', 'Per-project open_total = open_null_severity + Sum(open_by_severity.values()) in the published ticket rollup. NOT gated via snapshot_source/v_snapshot_publishable -- source_table=tessera has no snapshot_source row on purpose (section 7 explains why). Real enforcement is procedural: publisher.py:publish() calls builder.check_ticket_rollup_identity() directly and raises PublishRefused before any file is written on a violation. This row exists for the audit trail; run_all() records a vacuous pass here (ticket_rollup=None, "not a snapshot-publish run") on every ordinary ingest, since only publish() ever has a real rollup to check. A violation here previously shipped as index.json TESS.open_total=61 against a real 16+1=17, and MACNET.open_total=0 against one real open ticket.',1);

INSERT INTO dq_known_metric_bug (bug_id, summary, check_name, deferral_id, implemented) VALUES
 ('BUG-1','Merge script trusted a hand-written aggregate instead of recounting its deduped list','fail_open_not_double_counted',NULL,1),
 ('BUG-2','Dashboard read a hardcoded wrong-repo audit log path and rendered a manufactured zero as clean','watermark_le_filesize',NULL,1),
 ('BUG-3','Glossary claimed fire == deny+ask, false for handlers that never emit a decision','handler_denominator_nonzero',NULL,1),
 ('BUG-4','Skill extractor matched a bare skill-name mention as an invocation',NULL,'D1',0),
 ('BUG-5','Skill extractor missed bare hyphenated skill names entirely',NULL,'D1',0),
 ('BUG-6','Instruction-position gating false-positived on a skill named as object, not method',NULL,'D1',0),
 ('BUG-7','stolen verdict undocumented in the data itself','stolen_implies_target',NULL,1),
 ('BUG-8','audit_lib PROJECT_ROOT hardcoded; other projects mis-route to the shared global ledger','audit_ledger_partition_by_cwd',NULL,1);
```

---

## 17. The real-time snapshot artifact

Published under `~/.claude/atlas/snapshot/`, alongside the telemetry and audit-plane directories Foreman hooks already read, not inside any project. Mode 0600 on every file, which per §0 is hygiene, not a control.

```
~/.claude/atlas/snapshot/
├── current -> gen-2026-08-21T22:09:41.291218Z      (symlink, the commit point)
├── gen-2026-08-21T22:09:41.291218Z/
│   ├── index.json                                   12,307 bytes
│   └── projects/
│       ├── TESS.json                                ≤2,221 bytes
│       ├── FORE.json
│       ├── ambiguous-UNRESOLVED.json
│       ├── unregistered-UNRESOLVED.json
│       ├── no-cwd-UNRESOLVED.json
│       └── ... 38 shards total
└── gen-2026-08-21T21:17:04.002914Z/                 (retained: newer than max_age or in the newest 3)
```

Shards first, index second, pointer flip last. A reader resolves `current` once and reads every file beneath the resolved path.

### `index.json` — always read, 0.050 ms including a full 3-project dependency check

Regenerated mechanically against the live sources for this fix, not hand-written: ticket rollups from `SELECT ... FROM tickets JOIN projects` against the real `tessera.db`, `unresolved_scopes.handlers` from an independent path-component-aware resolver run against the live `verdicts.jsonl` (127,114 lines today), and the cursor from that same file's real head hash and current byte length. The `open_total = open_null_severity + Σ open_by_severity.values()` identity holds for every project below, checked by hand here and asserted as a snapshot contract check before publish, per project, going forward — the defect this replaces was that identity failing silently (`TESS.open_total: 61` against `16 + 5 = 21`).

```json
{
  "schema_version": "atlas-snapshot-2",
  "generated_at": "2026-08-21T22:09:41.291218Z",
  "refresh_cadence_seconds": 300,
  "max_age_seconds": 900,
  "warehouse_run_id": 2,
  "cursors": {
    "verdicts_stream_id": "854894e2516385ad473ae1ba1e03b1f39f8fe72f80c938e89b0c1907b1f1285f",
    "verdicts_byte_offset": 44072234,
    "tessera_max_event_id": 1750
  },
  "verdict_window_days": 7,
  "rate_uninterpretable_handlers": [
    "guard_untrusted_web.py", "reinject_compact.py", "session-log.sh", "telemetry_liveness.py"
  ],
  "severity_rubric": {
    "status": "absent",
    "version": null,
    "sha256": null,
    "blocker": "no P0-P4/S0-S4 rubric artifact exists on disk",
    "consumer_contract": "status!='present' means CANNOT EVALUATE; never 'threshold not met'"
  },
  "roots": {
    "/path/to/ticket-system":      {"prefixes": ["TESS"],        "resolution": "unique"},
    "/path/to/agent-remediation":      {"prefixes": ["AREM","FORE"], "resolution": "ambiguous"},
    "/path/to/hyphy":              {"prefixes": ["HYPHY"],       "resolution": "unique"},
    "/path/to/gifsmith": {"prefixes": ["GIF"],         "resolution": "unique"},
    "/path/to/example-project":         {"prefixes": ["EXPJ"],        "resolution": "unique"},
    "/path/to/example-project-ft":      {"prefixes": ["EXPJFT"],      "resolution": "unique"},
    "/path/to/atlas":              {"prefixes": ["ATLAS"],       "resolution": "unique"}
  },
  "unresolved_scopes": {
    "<ambiguous>":    {"handlers": 19},
    "<unregistered>": {"handlers": 24},
    "<no-cwd>":       {"handlers": 2}
  },
  "projects": {
    "TESS": {
      "codename": "TESSERA",
      "source_root": "/path/to/ticket-system",
      "root_is_shared": false,
      "root_state": "git-repo",
      "tickets": "present",
      "open_total": 17,
      "open_by_severity": {"S3": 1},
      "open_null_severity": 16,
      "open_null_tier": 15
    },
    "FORE": {
      "codename": "FOREMAN",
      "source_root": "/path/to/agent-remediation",
      "root_is_shared": true,
      "root_state": "git-repo",
      "tickets": "present",
      "open_total": 47,
      "open_by_severity": {"S1": 1},
      "open_null_severity": 46,
      "open_null_tier": 43
    },
    "AREM": {
      "codename": "AGENT-REMEDIATION",
      "source_root": "/path/to/agent-remediation",
      "root_is_shared": true,
      "root_state": "git-repo",
      "tickets": "present",
      "open_total": 1,
      "open_by_severity": {},
      "open_null_severity": 1,
      "open_null_tier": 0
    },
    "MACNET": {
      "codename": "Mac & Network Ops",
      "source_root": null,
      "root_is_shared": false,
      "root_state": "no-source-root",
      "tickets": "present",
      "open_total": 1,
      "open_by_severity": {},
      "open_null_severity": 1,
      "open_null_tier": 1
    }
  }
}
```

Three things in that object are load-bearing and none is a size optimisation.

**`projects` holds every project, not just the reader's own.** `preflight_blocking_gate.py:101` computes `to_check = [prefix, *depends_on(prefix)]`, and `DEFAULT_DEPENDS_ON = {"FORE","AREM"}`, so every project on this machine checks three. Measured live: TESS's check returns `TESS:S0=0,null_sev=16  AREM:S0=0,null_sev=1  FORE:S0=0,null_sev=46` from one index read, 0.050 ms, no extra file opens. Sharding the severity summary would make it three.

**`open_null_severity` sits beside `open_by_severity`.** That same real check sees zero S0 across all three projects and 63 untriaged tickets. A hook reading only the severity map would call that clean.

**`tickets` distinguishes present from absent.** `preflight_blocking_gate.py:33-34` already holds that "a partial answer is not a safe answer here," so a project missing from the map and a project present with zeros must not look alike.

### `projects/FORE.json` — the ambiguous case, +0.021 ms

```json
{
  "schema_version": "atlas-snapshot-2",
  "index_generated_at": "2026-08-21T22:09:41.291218Z",
  "project_prefix": "FORE",
  "source_root": "/path/to/agent-remediation",
  "rollup_scope": "<ambiguous>",
  "rollup_is_shared_with": ["AREM"],
  "verdict_rollup": {
    "guard_destructive.py":    {"": {"silent": 3245}, "deny": {"fire": 8}},
    "rule_dependency_docs.py": {"": {"silent": 3137}, "deny": {"fire": 7}},
    "session-log.sh":          {"logged": {"fire": 6, "stolen": 279}}
  },
  "recent_fail_open": []
}
```

`rollup_scope` and `rollup_is_shared_with` are the §3 fix reaching the hook. FORE's rollup is not FORE's; it is FORE and AREM's, and the artifact says so rather than letting a consumer read a shared number as its own.

**This block is regenerated from a real per-handler pass over the ambiguous scope, not hand-written, and the handlers in it changed as a result.** The prior draft's example used `architecture_gate.py` and `goals_freeze_gate.py` at 1,904/22 and 1,889/6 — plausible-looking numbers for exactly the two handlers §9's own measurement (line 306) states have **zero** ambiguous-scope fires. It also invented `kind` values `"investigated"` and `"named"` for `rule_dependency_docs.py`; the real corpus has exactly two `(kind, verdict)` combinations for that handler, `(NULL, silent)` and `('deny', fire)`, shown above with real ambiguous-scope counts (3,137 and 7). `guard_destructive.py` and `session-log.sh` replace the two zero-activity handlers as real examples of what this shard actually contains.

### `projects/ambiguous-UNRESOLVED.json` — the bucket, published

```json
{
  "schema_version": "atlas-snapshot-2",
  "index_generated_at": "2026-08-21T22:09:41.291218Z",
  "project_prefix": null,
  "rollup_scope": "<ambiguous>",
  "verdict_rollup": { "...19 handlers with real activity..." },
  "recent_fail_open": []
}
```

### Reference consumer contract

```
resolve current  ->  read index  ->  validate schema_version  ->  validate age against max_age_seconds
  ->  longest path-component match on roots
  ->  unique:      proceed
      ambiguous:   proceed with the candidate list, never pick one
      unregistered: proceed as if ATLAS were absent
  ->  if detail is needed, read projects/<PREFIX>.json under the SAME resolved generation
```

Every failure returns a reason and no data. Verified across five failure modes: pointer missing, pointer dangling, index corrupt, shard corrupt, root removed. None raise. `severity_rubric.status != "present"` and `hook_registry.status != "present"` mean cannot evaluate, never evaluated-and-found-nothing, which is the difference between an advisory that works and one that has been silently dead for a hundred and eighty-one sessions.

---

## Confidence

**High** on every source fact in §1, §2 and §3, and on the six defects in §12. Each was measured by executing code against the live files, with negative controls wherever a mechanism was proposed: the idempotency key by crash-replay against the real ledger; the trust gate by forcing a check to fail; the scoped publish gate by failing a check on each side of the boundary; `ingest_rows_match_bytes` by deleting five rows; the `dq_known_metric_bug` constraint by attempting an orphan insert.

**Medium, not High, on the snapshot's concurrency/atomicity numbers specifically** — corrected by a falsification re-review (2026-08-22): a prior version of this section counted "63,799 concurrent reads across 400 republishes" among the High-confidence negative controls. No reproducing harness for that figure exists in this repo's own `atlas/snapshot/tests/` on this machine, and `~/.claude/atlas/snapshot/` does not exist after a full build cycle — the design (resolve-once generation directory, so a torn pair is structurally impossible; age-AND-count retention) is sound reasoning and the atomic-rename mechanism itself is directly inspectable, but the concurrency numbers are not independently reproducible today. Raising this back to High needs a real concurrency stress test committed alongside the tests that already exist for the rest of this component.

**High**, newly, on `v_ticket_diff_binding` and `git_ticket_prefix_registered`, which in the previous draft were Medium dressed as High: the first was reported on empty inputs, the second cited one false positive where the real corpus yields fifteen.

**Medium-High** on the schema. All eighteen views executed against real data in both a loaded and an empty database, and §9 states the falsification tell for each. Four of the six defects in §12 produced plausible output, so "inspected" is not load-bearing on its own; the tells are.

**Medium** on `v_deny_streak` specifically, and by inheritance on `v_trapped_agent_candidate`. The window arithmetic has the weakest tell in the schema and feeds the one view already found wrong once.

**Medium** on the deferral list being complete. D1 through D14 each name a verified blocker, but completeness is a negative claim and I have not proven one.

**Low-Medium** on the remaining lean judgment call: the 1 MiB audit payload bound, marked `lean` in `dq_check.threshold_kind` rather than only in prose. Corrected by a falsification re-review (2026-08-22): a prior version of this line also counted the seven-day verdict window here, and claimed it too was marked `lean` in the schema; measured, there is exactly one `threshold_kind='lean'` row (`audit_payload_size_bounded`) — the seven-day window has no `dq_check` row at all and so cannot be marked anywhere in the data, only in this prose, which is itself the gap section 10 warns a lean judgment call should not be left in. `max_age_seconds` is no longer a lean; §5 derives it from the cadence.

**Explicitly unmeasured**: whether the counts-not-rows shape serves the periodic pattern-detection consumer. That consumer has no crontab entry, no launchd agent and no script anywhere on this machine, so there is nothing to measure against. D12 records it as unserved rather than claiming it is designed for.
