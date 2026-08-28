# Architecture — TESSERA

Chosen at the project's one spread checkpoint: **Candidate 6, the SQLite event-log synthesis**
(scored 19/20 on the locked rubric — see the checkpoint record in the build conversation for
the full 6-candidate spread and scoring).

**Revision note:** this is the third post-review revision, following four review passes total.
Pass 1 (CHANGES REQUIRED) found four defects in the original design; the fixes for those
(`BEGIN IMMEDIATE`, `UNIQUE(prev_hash)`, store-side idempotency, rowid-polled SSE, store-layer
workflow/cycle enforcement) were verified correct in pass 2 by an independently re-derived
harness, not just re-read. Pass 2 (also CHANGES REQUIRED) found three new defects — one of them
(the write-lock-on-rejection stall) created by pass 1's own fix — plus ten secondary findings.
Pass 3 (also CHANGES REQUIRED) verified pass 2's fixes and found two more defects — a
cross-process race in the `docs/` write path, and a busy-error justification measured under the
wrong configuration a second time — plus nine further secondary findings (re-entrancy policy,
the rollback-guard-against-masking fix, the orphan-`prev_hash` trigger, a named chain-
verification test, a corrected unowned-paths claim, `stage_heads`' storage and event-sourcing,
and the SSE-ordering dependency note). Pass 4 verified pass 3's fixes and returned **SHIP WITH
FIXES**: six findings, all specification-completeness or accuracy corrections rather than
design defects (a `stage_heads` rebuild-scope gap, a wrongly-attributed SSE-ordering mechanism,
an unspecified retry bound with a real tuning trap, an under-specified exception-discrimination
requirement at the idempotency catch site, this note's own pass-count being wrong, and an
uncommitted working tree). All are addressed below; see the build's review history for all four
verdicts in full.

This note tracks the **Storage model** section's own review history specifically (pre-dates
`tessguard`). The `tessguard` component below has a separate, later review history of its own —
six passes so far, findings tagged F1–F6, N1–N8, P1–P5, Q1–Q5, and R1–R8, all recorded in full in
`ARCHITECTURE-REVIEW.md` — tracked there rather than folded into this count, since the two
components were reviewed independently and conflating their pass numbers would misstate either
history. (This count has itself gone stale three times before — pass 3's M16, pass 5's M-l, pass
6's R4 — so it is worth treating as a live number to check, not a fact to trust on sight, the next
time this section is touched.)

## Storage model

A single SQLite file, WAL mode. `store` opens connections with `isolation_level=None`
(autocommit off by default; transactions are managed explicitly, never left to Python's
`sqlite3` legacy implicit-transaction heuristic) and **every write transaction begins with
`BEGIN IMMEDIATE`**, not `BEGIN DEFERRED` and not an implicit transaction — stated here because
A concurrency harness proved the implicit/deferred case forks the hash chain under real concurrency
(12 fork points, 15 chain tips, at SCOPE.md's 20×50 bar) while `BEGIN IMMEDIATE` on the same
harness produced zero forks, one tip, zero corruption. `busy_timeout` is set on every connection, AND wrapped in an application-level bounded
retry/backoff in `store`. This is **load-bearing, not defense-in-depth** — stated plainly after
two rounds of measuring it under the wrong configuration. `BEGIN IMMEDIATE` removes the
non-retryable busy-*upgrade* failure mode (a `SELECT` under `BEGIN DEFERRED` racing to promote
to a write lock), which is a real fix and is why an earlier number (44 hard errors/1000,
measured against a deferred/implicit-transaction design) doesn't transfer. But `BEGIN
IMMEDIATE` does not remove ordinary write-write contention, and a second measurement that
varied reader count while holding writer count at 1 wrongly attributed a zero-busy-error result
to `BEGIN IMMEDIATE` itself rather than to the writer count. Re-measured varying writer count
directly, `busy_timeout=5000`, at SCOPE.md's own 20-process bar, with an in-transaction work
duration matching what a real workflow/hierarchy/cycle check costs (10–50ms, not a no-op):

| Writers | In-txn work | Committed/1000 | Hard `SQLITE_BUSY` |
|---|---|---|---|
| 1 | ~0ms | 1000 | 0 |
| 20 | 10ms | 984 | 16 |
| 20 | 50ms | 911 | 89 |

Hard busy errors appear at exactly the concurrency level and transaction shape this system
actually runs at, once the write transaction does the real work pass 2's fix put inside it
(workflow/hierarchy/cycle checks). The retry wrapper is what turns those failures back into
successful writes; without it they surface to the caller as failed creates, which SCOPE.md's
"zero lost writes" bar does not allow. Counter/event counts matched committed-row counts with
zero duplicate IDs in every row of this table — the id-assignment design is sound under this
same contention, independent of the retry wrapper. A retry, when it fires, restarts the
**entire** transaction from its first read, never just the failed statement — retrying only the
tail statement reproduces the hash-chain fork pass 1 found.

**Retry bound, stated explicitly rather than left implicit:** up to **15 attempts** with
exponential backoff against a **5000ms** `busy_timeout`. Measured at the document's own
worst-case row (20 writers, 50ms in-transaction work): a plausible-looking bound of 6 attempts
still lost 8 writes per 1000 — under SCOPE.md's zero-lost-writes bar — while 15 attempts lost
zero. **The obvious tuning move is wrong and is recorded here so nobody rediscovers it by
shipping it:** shortening `busy_timeout` in order to retry more often inside the same wall-clock
budget makes outcomes strictly *worse* (measured: `busy_timeout=1000` with 15 attempts lost 17
writes per 1000; `busy_timeout=250` with 40 attempts lost 11) — the timeout, not the attempt
count, is what's protecting correctness, because a shorter timeout increases the chance of
exhausting attempts while still contended rather than one attempt just waiting longer. Do not
lower `busy_timeout` to make retries "faster" without re-measuring against the full 20×50 bar.

**This has a real latency cost, named honestly instead of glossed as "slightly delayed":**
p99 create latency at the realistic 10ms-in-transaction row was measured at 7.1s, and 28.9s at
the 50ms/15-attempt row. Zero lost writes is bought with tail latency, not free — a caller
(including `reviewui`'s UI-save path) needs to treat a create as capable of taking several
seconds under real contention, not as an operation with negligible latency variance.

**The concurrency evidence run itself must use realistic in-transaction work, not a no-op
create.** Measured: at ~0ms in-transaction work, hard `SQLITE_BUSY` stayed at zero across every
writer count from 1 to 20 — a concurrency test built around a trivial create would pass at any
retry bound, including one that loses writes once the real workflow/hierarchy/cycle checks are
in the transaction. SCOPE.md's 20×50 concurrency proof is required to exercise `store`'s actual
write path (real checks inside the transaction), not a simplified stand-in, or the proof
verifies nothing.

Every `store` write transaction is opened through one context manager
(`with store.write_txn_internal() as txn:`) that issues `BEGIN IMMEDIATE`, yields, and **always** runs
`ROLLBACK` in a `finally` block unless the body reached `COMMIT` — a validation failure
(illegal workflow transition, hierarchy violation, link cycle, anything `store` rejects) is a
`ROLLBACK`, not a bare exception with the transaction left open. `finally` guards the `ROLLBACK`
itself with `if conn.in_transaction:` — SQLite can already have torn the transaction down on
its own (a triggered `ON CONFLICT ROLLBACK`, `SQLITE_FULL`, some I/O errors), and an
unconditional `ROLLBACK` in that state raises `OperationalError: cannot rollback - no
transaction is active`, which would mask the real error rather than propagate it. This is
stated explicitly because review found that moving workflow/cycle/hierarchy checks into the
write transaction (below) means a rejected write now happens *with the RESERVED lock held*;
measured without the rollback, one rejected writer stalled every other writer in the process
for as long as that worker thread lived (8s hold → 8s stall on the next writer; unbounded in a
real thread pool). `busy_timeout` does not protect against this — it's what makes the next
writer wait quietly instead of erroring, which makes the stall invisible rather than absent.

`write_txn_internal()` is not re-entrant — a nested `with store.write_txn_internal():` inside another one
raises (`cannot start a transaction within a transaction`), and fails loud with no lock leak
(the outer `finally` still fires). Composite operations that need more than one `store` write
(e.g. creating a ticket and recording its initial links) are internal `store` methods that
accept an already-open connection/transaction rather than opening their own — only the
outermost public `store` call opens `write_txn_internal()`. `store`'s idempotency-conflict handling
follows this same discipline concretely: the `IntegrityError` from a duplicate
`idempotency_key` propagates out of the `with` block (triggering the `finally`'s `ROLLBACK`,
which is what keeps the ticket-ID counter from advancing on a doomed attempt — see below), and
is caught by the *calling* `store` method, outside the `with`, which then re-reads and returns
the already-created ticket. The catch site is outside the context manager on purpose, not
incidentally — catching inside the `with` body and falling through to a normal return would
let the block commit instead of roll back, silently reintroducing the counter-burn bug this
design exists to close. **That catch must discriminate on which constraint fired, not catch
`IntegrityError` bare.** `events` now carries three distinct constraints that all raise the
same exception type: the idempotency partial-unique-index, `prev_hash`'s `UNIQUE`, and the
orphan-`prev_hash` `BEFORE INSERT` trigger. A bare `except IntegrityError` at the idempotency
catch site would treat a genuine hash-chain fork or an orphan-`prev_hash` bug as an idempotent
duplicate and paper over it by returning some unrelated existing ticket — swallowing exactly
the failure this schema's constraints exist to make impossible to miss. The catch must inspect
the constraint (SQLite's error message distinguishes them: `events.idempotency_key` vs.
`events.prev_hash` vs. the trigger's own message) and re-raise anything that isn't the
idempotency case.

Two tables written in the same transaction, always:
- `events` — append-only (`BEFORE UPDATE`/`BEFORE DELETE` triggers raise), hash-chained
  (`event_hash` = sha256 of the canonical event content, `prev_hash` = the prior event's hash).
  **`events.prev_hash` is `TEXT NOT NULL UNIQUE`** — every row, including the first, has a
  non-NULL `prev_hash`; `store` writes a fixed sentinel (`"GENESIS"`) as the first event's
  `prev_hash` at schema creation. This is stated explicitly because SQLite's `UNIQUE` does not
  constrain `NULL` values (multiple `NULL`s are treated as distinct) — a nullable `prev_hash`
  left exactly the genesis event unprotected by the constraint the rest of the document argues
  must be structural, not disciplinary, and review measured that gap directly (a nullable
  genesis produced 4 chain roots/4 tips under one workload; the non-NULL sentinel produced 1).
  `NOT NULL UNIQUE` bounds *forks* (two events sharing a parent) but not *orphans* (an event
  whose `prev_hash` names no real prior event, e.g. a bug that writes an arbitrary string) — a
  `BEFORE INSERT` trigger requiring `prev_hash` to equal the sentinel (first row only) or match
  an existing `event_hash` closes that gap structurally too, and is cheap (same mechanism
  already used for the append-only triggers); included as one of `store`'s
  `foreman:design-and-scope` criteria rather than asserted as already built here. Either way,
  `store` must expose a named chain-verification check — exactly one root, exactly one tip,
  every non-sentinel `prev_hash` resolves, every `event_hash` matches a fresh recomputation —
  as an actual test, not left as something only a reviewer's ad hoc harness checks; both
  architecture-review passes that found chain defects found them by counting roots and tips,
  and that count needs to live in the test suite, not in review transcripts.
- A read-optimized projection (`tickets`, `ticket_fields`, `comments`, `ticket_links`,
  `stage_heads`) —
  ordinary mutable rows, written in the identical transaction as the `events` insert, so they
  cannot drift out of sync with each other (same-transaction atomicity is real and SQLite
  guarantees it). What same-transaction writing does **not** guarantee, and what it was
  previously mis-stated as removing the need for: a bug in the projection-writing code is still
  a bug, and would still silently produce a projection that's an incorrect function of the
  events even though the two never desynchronize from each other. **`store` exposes
  `rebuild_projection()`**, which replays `events` from scratch into a fresh projection, and the
  test suite runs `assert rebuild_projection() == live_projection` — this is the actual
  correctness check event sourcing is supposed to buy, and it was missing from the first draft.

Ticket-ID assignment: `UPDATE counters SET value = value + 1 WHERE name = ? RETURNING value`
inside the same `BEGIN IMMEDIATE` transaction as the `TicketCreated` event and its projection
row. A rolled-back transaction rolls the counter back with it — no gap between id-reservation
and body-write.

Idempotent create: `store` (not `api`) holds a partial `UNIQUE` index on
`events(idempotency_key) WHERE idempotency_key IS NOT NULL`. A retried create with the same key
hits the constraint inside the write transaction; `store` catches the `IntegrityError`,
**rolls back this attempt** (via the same `write_txn_internal()` context manager — a caught constraint
violation is still a rollback, not a commit), and re-reads the already-created ticket to return
it. Rolling back rather than committing-through matters for the counter specifically: the
ticket-ID counter increment happened earlier in this same doomed transaction, so rolling back
is what keeps a burst of retried duplicate calls from consuming a run of IDs it never uses
(commit-through was measured to leave the counter at 20 after one logical create from 20
racing retries of the same key; rollback leaves it at 1). (Measured overall: a `SELECT`-then-
write idempotency check in `api`, outside any transaction, produced 20 tickets from one
retried create at the same 20-process bar; the `store`-side `UNIQUE`-index-plus-rollback
version produced 1 ticket and a counter of 1.)

Workflow-state legality and blocks/blocked-by cycle detection are enforced **in `store`**,
inside the write transaction — not in `api`. SCOPE.md requires transitions enforced at the
store layer specifically because the primary operator is an AI agent with direct filesystem/
Python access that can `import tessera.store` and skip `api` entirely; enforcement anywhere
else is advisory. `api` may keep a friendly pre-check purely to return a fast, readable error
message before round-tripping to `store`, but `store`'s check is what actually blocks an
illegal transition or a link cycle, inside the same transaction as the write that would create
it (a read-then-write cycle check outside the transaction races the same way the idempotency
check did).

Ticket hierarchy (Sub-task requires a parent Story/Task/Bug) is a `store`-layer constraint for
the same reason, checked in the same transaction as `TicketCreated`.

Per-thread connections: `store` uses `threading.local()` — a `sqlite3` connection is
thread-affine and the HTTP server is threaded (`ThreadingHTTPServer`). Every connection, on
creation, sets `journal_mode=WAL`, `busy_timeout`, and `foreign_keys=ON` (the last is
per-connection and OFF by default in SQLite; project-metadata and projection foreign-key
integrity depend on it being set every time, not once at file creation).

Attachments: not covered by transactional atomicity, because the blob is a filesystem write.
Ordering rule, stated explicitly: write the blob to a temp file, `fsync`, rename to its sha256
content-addressed path, **then** insert the referencing row in the same `BEGIN IMMEDIATE`
transaction as any other write. If the transaction then rolls back, the result is an orphaned
but collectable blob file, never a row pointing at a blob that doesn't exist.

Crash-mid-write (`kill -9`): handled by SQLite's own WAL/journal atomicity, no custom recovery
code — verified directly (12 processes, 11 SIGKILL'd mid-transaction, `PRAGMA integrity_check`
clean, counter/events/tickets counts consistent, hash chain intact, DB writable immediately).

Backup: before any risky operation (rollback, schema change), `store` snapshots via
`VACUUM INTO` — a plain file copy of a live-WAL database is not a consistent backup; `VACUUM
INTO` is.

Project metadata: `store` maintains a `project_metadata` table (one row: `codename`, `prefix`,
`created_at`). This is deliberately schema-general — any future Foreman project's store gets
the same table — not a `TESS`-specific hardcode. The `counters` table's `name` column already
generalizes the same way (keyed by prefix, not hardcoded to `TESS`).

Actor identity: every `events` row carries an `actor` field (agent session/process id, or a
human identifier), threaded from the CLI (`--actor` / an environment variable default) and the
HTTP API (a required request field) into the write. No auth exists in this system (out of
scope per SCOPE.md), so `actor` is **client-asserted, recorded honestly as claimed, not
cryptographically verified** — this document says so rather than implying a guarantee the
system doesn't provide.

## Components

**store** (`tessera/store/`) — the SQLite event-sourced storage engine: schema, hash chain
(with the `UNIQUE(prev_hash)` constraint and non-NULL genesis sentinel), counters, transactional
dual-write, workflow state-machine enforcement, hierarchy enforcement, link-cycle detection,
idempotency, per-thread connection management, `rebuild_projection()`, attachment
content-addressing, `reference_docs` handling (writing it is itself an auditable event),
custom-field storage, `project_metadata`, `VACUUM INTO` backups, and Bug-type fields.

Base fields are first-class columns on the ticket projection (and first-class keys in the
`TicketCreated`/`FieldSet` event payload — there is no single generic `TicketUpdated` event;
per-field-type events like `FieldSet`/`StatusChanged` carry updates instead), not generic custom
fields, for the same reason
Bug's type-specific fields are: `status`, `reporter`, `assignee`, `priority`, `type` (Epic/
Story/Task/Sub-task/Bug), `parent_id` (hierarchy), and `links` (blocks/blocked-by/relates-to,
with the cycle check). Bug tickets additionally carry `severity`, `repro_steps`, and
`environment` as first-class fields on that type's event schema. A schema-less custom field
wouldn't guarantee any of these exist, which is exactly the argument this document already
makes for Bug's fields — it applies to the base set too, and both now get the same treatment.

Archival (SCOPE.md's named alternative to hard delete) is **an event
(`TicketArchived`/`TicketUnarchived`), not a projection-only flag flip.** Stated explicitly
because `rebuild_projection()` replays state purely from `events` — if archival only mutated
the mutable projection table directly, a rebuild would silently un-archive every archived
ticket (or, worse, the rebuild-equality test would pass for the wrong reason, hiding exactly
the kind of drift that check exists to catch). Every state change a reader can observe has to
be an event; the projection has no state that didn't arrive through one.

Ticket-to-commit links store a commit SHA and are the only kind of link the staleness/ancestry
check (owned by `gitops`, below) applies to — `git merge-base --is-ancestor` has no defined
meaning against a branch ref that moves. A ticket may additionally record a branch name as an
informational field, but linking to a branch does not participate in stale-flagging; only a
commit-SHA link does. Said here because SCOPE.md asks for "linkable to commits/**branches**"
without distinguishing the two, and they behave differently under rollback.

**api** (`tessera/api/`) — the CLI (`tessera/api/cli.py`) and local HTTP API
(`tessera/api/http_api.py`, entrypoint `tessera/api/main.py`), both thin wrappers over `store`.
Owns request parsing/validation, translating CLI/HTTP calls into `store` calls, the SSE
endpoint, and serving `reviewui`'s static assets (documented explicitly here — the
`api_to_reviewui` edge covers both the pushed SSE events and this static-file read). Also owns
**discrepancy computation** for the diff-vs-claim view: given a ticket's claim
(`files_touched`) and the real diff for the commit it names (fetched via `gitops`, see below),
`api` computes the set difference (files the diff touched but the claim didn't mention, and
vice versa) and returns it as a first-class `discrepancy` field in the API response — this is
what makes the mismatch something the UI surfaces, not something a human has to notice
unaided. Live-update mechanism: `api` polls `events` for `WHERE id > :last_seen_rowid` on a
short interval (500ms) in a background thread and fans new rows out to connected SSE
subscribers — this is what makes writes from *other processes* (the CLI, a second server
instance) visible to the UI, since SQLite has no cross-process change-notification hook. This poll's correctness does
**not** depend on `BEGIN IMMEDIATE` specifically — an earlier draft of this note claimed that
and review measured it false: under `BEGIN DEFERRED`, delivery still missed nothing and
delivered nothing out of order (it failed in a different, unrelated way — a much higher
transaction-abort rate). What actually guarantees in-order, no-gap delivery is WAL's
single-active-writer invariant (only one transaction's changes are ever visible as committed at
a time, regardless of which `BEGIN` mode took the write lock) combined with `events` being
strictly append-only with no rowid ever reused. Naming the real dependency rather than a
plausible-sounding wrong one, after two other sections of this document made the same kind of
mistake (see the revision history) — the actual coupling to watch is: don't ever reuse
`events` rowids (e.g. via a future `VACUUM`-driven rowid reassignment or a switch away from
`INTEGER PRIMARY KEY`), not the transaction's `BEGIN` mode.

`api` also owns `docs/` file read/write — the standard documentation folder is plain files on
disk, not ticket data in `store`, so it needs its own explicit ownership statement rather than
being silently absorbed into either `store`'s attachment handling or left for `reviewui` to
write directly (which would recreate exactly the unguarded second-write-path problem the
single-write-path design exists to avoid for ticket data). `api` is the **only** writer of
`docs/` files, for both the CLI and the UI's explicit-save button: write-to-temp + `fsync` +
atomic rename (the same durable-write pattern used for attachments), serialized by
`fcntl.flock(LOCK_EX)` on a per-path lockfile, held across the full read-modify-write, **not**
a `threading.Lock` alone. Stated explicitly because the CLI is a separate process from the
running server (see Process model, below) — a doc-file writer is a cross-process writer by the
document's own design, and a `threading.Lock` only serializes threads inside one process.
Review measured a `threading.Lock`-only version losing 75% of concurrent updates (362 of 480)
under 8 concurrent processes with zero torn files (the atomic rename prevents corruption, not
lost updates); the same test with `flock` held across the read-modify-write lost zero. Keep the
`threading.Lock` too, for the same file touched by two threads inside the server process —
`flock` is scoped to an open file description and does not serialize threads sharing one.
Writes are restricted to paths under the project's `docs/` root; a ticket's `reference_docs` may
point outside it, but those external paths are read-only through TESSERA (linked, not edited) —
editing someone else's SME doc from inside the ticket tracker was never a requirement. A
`docs/` file write does **not** emit a `store` event — SCOPE.md's audit-trail requirement is
scoped to ticket mutations; a plain doc edit gets no SSE notification and no audit record. This
is a stated decision, not an oversight.

**gitops** (`tessera/gitops/`) — environment-stage (dev/integration/FT/prod) promotion and
rollback, modeled on the operator's existing separate-repo-per-stage convention (confirmed by
inspecting a real staged-tree project's `-integration/-field-trial/-prod` set: each stage is its own
independent git repository, promoted via ordinary commits + tags, not `git worktree`). Rollback
reverts a stage repo's code to a prior tagged checkpoint; it never touches `store`'s ticket
data. The stage repo itself is the source of truth for its *current* HEAD — `store` records,
in a `stage_heads` projection table (one row per stage name), only the **last HEAD `gitops`
promoted to**, not a live mirror of the repo. `stage_heads` is written **exclusively** as the
projection of an event — a promotion or rollback is itself an event
(`StagePromoted`/`StageRolledBack`), and the `stage_heads` row updates in that event's same
transaction, consistent with "the projection has no state that didn't arrive through an event."
This is now included in `rebuild_projection()`'s scope (see above) precisely so a bug that
wrote `stage_heads` without a backing event would be caught by the rebuild-equality test, not
silently missed by it. In particular: the on-read ancestry reconciliation described next
**computes staleness by comparing `stage_heads` to the repo's live HEAD — it never writes an
observed live HEAD back into `stage_heads`.** Only `StagePromoted`/`StageRolledBack` ever
update that table; an on-read write with no backing event would be exactly the silently-missed
drift the rebuild-equality test exists to catch. `gitops` reads `stage_heads` through `store`'s
API and writes it only by way of those two events, never directly. Reconciliation happens
**on every read**, using `git merge-base --is-ancestor` — not only when `gitops` itself
performs a promotion/rollback,
because a human or agent can move a stage repo's HEAD directly (`git reset`, a manual push)
without going through `gitops` at all, and the staleness signal has to catch that case too.
Staleness is **recomputed from ancestry on read, not latched**: a ticket flagged stale becomes
un-flagged the moment its linked commit is an ancestor of the stage's current HEAD again (e.g.
after a fix is re-promoted) — an accumulate-only flag becomes exactly the cosmetic label
nobody trusts, which SCOPE.md's own premortem names directly. `tessera stage sync` is an
explicit CLI/API command that forces this reconciliation on demand, in addition to it running
automatically on every read of a stage-linked ticket. `gitops` also exposes a
`diff(stage, commit_sha, project=None)` call, used by `api` to fetch the real diff for the discrepancy computation above
(`reviewui` → `api` → `gitops`, using the existing `api_to_gitops` edge — `reviewui` never
calls `git` itself, it has no way to).

One-line check, done here per SCOPE.md's premortem item 11: the project root
(`/path/to/ticket-system`) and every stage repo `gitops` creates must not live under an
iCloud Drive, Dropbox, or other sync-client-managed folder — confirmed for the project root
(a plain path under `~/dev`, not a synced location), and `gitops`'s stage-repo creation must
verify the same for each stage repo's path before first use rather than assuming it.

**reviewui** (`tessera/reviewui/`) — the local web frontend: open bugs/todos list, the
diff-vs-claim view (renders the claim, the real diff, and `api`'s computed `discrepancy` list
prominently — not two panels left for a human to eyeball unaided), explicit-save file editing
(no autosave), and the browser-side SSE client with a visible connected/stale indicator (if the
SSE connection drops, the UI says so rather than silently showing an increasingly-out-of-date
view). Talks only to `api`. **All agent-generated content (titles, comments, claims) is
HTML-escaped on render**, and the server sends a restrictive `Content-Security-Policy` header —
agent-authored ticket content is untrusted input into this renderer and is treated as such, not
as "safe because it's local."

**common** (`tessera/common/`) — small shared utilities genuinely used by more than one
component: canonical JSON serialization and sha256 hashing (used by `store` for `event_hash`
and by `api` for discrepancy/claim-hash computation), and shared timestamp helpers. Also holds
`tessera/__init__.py`. Kept deliberately thin — a dumping ground here would just be
`store`-internals with extra steps.

**tessguard** (`tessera/tessguard/`) — TESSERA-logging enforcement, per an earlier backtest-grounded
design (a 6-project retrospective plus a real toy-model backtest across 8 independent sessions
falsified a self-scored PreToolUse-deny design and converged on this two-layer mechanism instead;
see that project's own internal design history for the evidence). Two layers, differing in both blast radius **and in what
each can detect** — stated together here because a reader forms their model of this component
from the first sentence, and the two-axis difference is the more consequential of the two:

1. **Async binary coverage audit** (non-blocking) — for a **completed** session, checks whether
   the repo had any real Edit/Write (never `Bash` — see below) activity, and if so, whether the
   repo's registered TESSERA project(s) had **zero** real TESSERA-write events anywhere in that
   session. Two prior predicate designs (a per-window ratio, then a session-level fractional
   aggregate) were tried, measured, and both failed under a real architecture review across three
   passes — full history and numbers in `ARCHITECTURE-REVIEW.md` findings F1, N1–N8. The fractional
   approach's fatal flaw, found on the third pass: its calibration depended on unstated design
   choices (which data source, whether writes outside the repo's own root counted, whether `Bash`
   commands counted) and reasonable choices for those **flipped results across the threshold** —
   MOON moved from "clean" (0.389) to "drift" (0.778) depending on scoping alone, and a `Bash`-write
   classifier turned out to be dominated by test-execution noise (`cd`, `python3 -m unittest`,
   `git`, `grep`), not real code changes.

   The design below removes every free parameter that caused that instability, rather than tuning
   around it: **data source** is `store`'s real events via `get_events_since()` — `TicketCreated`/
   `CommentAdded`/`StatusChanged`/`TicketCriteriaFrozen`/`ClaimRecorded` (declared explicitly as
   this set, not the full set of event types `store` defines (24 today, counted by AST scan of
   every emission site, not by grep; 15 of the 24 appear in real `events` rows, so this design
   excludes 19 of the 24 defined, 10 of the 15 actually seen — `LinkAdded`, `FieldSet`,
   `SummarySet`, `DescriptionSet`, `ReferenceDocsSet` among them);
   measured to be currently inert against the backtest corpus — swapping in the full union moves
   no session's flag — but stated on purpose: a session that only set custom fields, for instance,
   should not silently read as "covered" if that changes) —
   attributed to a project by the event's own `ticket_id` prefix (guarded against the ~50 real
   NULL-`ticket_id` rows in `events`, none of which fall in the five counted types today, but the
   implementation checks before splitting rather than relying on that holding forever), filtered
   to the prefix(es) `list_projects()` registers for this repo's `source_root` — **matched by
   `source_root == repo root` OR `source_root` nested under it, on real path components (split on
   `os.sep`, never a bare `str.startswith`)** — needed for GIF specifically: its session `cwd` is
   `/path/to/gif-smith`, a plain directory holding two sibling repos and not itself a git
   repository, while its real registered `source_root` is the nested
   `/path/to/gifsmith`; under exact-match-only this resolves to zero registered
   projects for the outer path. The component-boundary distinction matters for real, live
   registrations: a naive `startswith` collides `/path/to/example-project` with `example-project-ft`/
   `example-project-prod`, and `/path/to/example-audio-app` with its own `-ft`/`-prod`
   siblings, and `/path/to/example-tool` with the unrelated, separately-registered
   `example-tool-playground` (`AVLP`) — three real collisions across the currently
   registered projects, none of which move a backtested flag today (extra matched projects can
   only add coverage under the any-of rule below, never remove it), but the direction is silently
   fail-open and the fix is one clause. **If a repo resolves to zero registered TESSERA projects,
   this layer does not flag** — no project to check coverage against is a different condition from
   a registered project with no coverage, and the two must not collapse to the same output. No
   transcript-side proxy, no regex, no direction-of-bias argument needed for the coverage side,
   because this **is** the production path (`tessguard_to_store`), not an estimate of it.
   **Write side** is `Edit`/`Write`/`MultiEdit`/`NotebookEdit` tool calls only, scoped to file
   paths under this repo's own root (normalized through `realpath` before comparison) —
   `Bash` is excluded entirely: unscopable to a specific file in general, and measured to be
   40–60% write-verb-classifier noise dominated by test/inspection commands, not real code
   changes. **Named blind spot, not silently absorbed**: a session whose real code changes are
   made entirely through `Bash` (a heredoc write, `sed -i`, sourcing a generated file) reads as
   zero in-repo edits and never flags — structurally the same shape as VALE's limitation below,
   stated here for the same reason. **Predicate** is binary — real in-repo edit activity exists,
   and zero real TESSERA-write events exist for this repo's registered project(s), aggregated as
   **any-of across multiple registered projects sharing one repo** (matches the measured 69/69 —
   see layer 2, which uses the same rule, stated once so it isn't restated differently there) —
   not a ratio, so there is no threshold to mis-tune and nothing for a scoping choice to move
   across.

   Re-measured against the real DB and real transcripts, this exact design, all 8 sessions,
   independently re-derived and reproduced exactly on a fourth review pass: GIF (148 in-repo
   edits, 0 real events) and LAAV (4, 0) flag correctly. TESS (481, 336), FORE (35, 76 — two
   projects, AREM+FORE, share that repo's `source_root`, any-of), AUDI (266, 116), WAT (13, 10),
   and — the case that was unstable under the old design — **MOON (18, 21) does not flag,
   unambiguously, no ratio argument required.** VALE does not flag, but for a structurally
   different, honestly-named reason: all 14 of its real Edit/Write calls targeted files in *other*
   repos entirely (a real production repo, `~/Downloads`) — zero in-repo writes means this
   predicate has nothing to evaluate, not that coverage was confirmed. **Named residual limitation,
   not silently absorbed**: cross-repo drift (real unlogged work happening in a *different* repo
   than the one this instance of `tessguard` audits) is out of this predicate's scope by design,
   the same way multi-project rollout is (see Deferred) — a per-repo check cannot see work that
   never touched its repo. A six-variant stability sweep (event-type set, `source_root` matching
   rule, session-span padding) on independent re-derivation moved zero flags — a materially
   different result from the fractional design's own sensitivity, where scoping alone moved MOON
   across the threshold.

   **Named residual limitation, self-selection**: v1's audit is invoked by the same party it
   audits — the operator names the transcript, at the end of a session they choose to end. The
   failure this layer detects (forgot to log) and the failure this invocation model depends on
   (forgot to run the audit) are correlated; a session that drifted is disproportionately the
   session nobody remembers to check. Not a reason to change the design — see below for why the
   alternative was measured worse — but named honestly, in the same register as the cross-repo and
   Bash-only blind spots above, because it is the largest of the residual gaps, not the smallest.

   **"Completed session" has no automatic, threshold-based trigger in v1 — deliberately, not by
   oversight.** An earlier draft of this bullet implied a wall-clock idle-time completion check,
   invoked periodically, and a direct measurement killed it before it shipped: TESSERA's own
   dogfood session contains a 24.4-minute idle gap between its first in-repo edit and its first
   real TESSERA event — a re-measurement on the full session found a 294.3-minute internal
   activity gap and a 154.7-minute (2h34m43s) first-edit-to-first-event span, so any idle or
   elapsed-time threshold short of several hours would declare that session "complete" mid-stream
   and flag correctly-eventually-logged work as a false positive — the same cold-start failure
   mode measured directly against the earlier fractional design's running variant, arriving
   through the one parameter this redesign had left undefined. **v1's audit is invoked explicitly**
   — on-demand, against a specific transcript file the operator names as done — not auto-discovered
   against whatever is currently the most recent transcript, and not triggered by `launchd`/cron
   or any other wall-clock schedule (see Install mechanism and Process model below, which name
   the same restriction). This is a real narrowing of scope, not a relocation of the same judgment
   onto a human: the operator ending a work session has direct knowledge of that fact, which an
   idle timer only has a lossy proxy for — different in kind, not degree. It costs the ability to
   run unattended, and it does not by itself validate that the named transcript is the right one.
   **Accept the transcript if EITHER its recorded `cwd` (first value, since it can vary within one
   transcript if the agent changes directory) is under the audited root, OR it contains at least
   one real in-repo edit — reject only when neither holds.** A stricter, cwd-only precondition was
   the first candidate and was toy-modeled before being written down here: it rejects GIF's own
   transcript outright whenever the audited root is GIF's real git repository (`cwd` recorded as
   the outer, non-git `/path/to/gif-smith`, not the nested git root) — GIF is the single
   clean example this document holds up for layer 1 catching what layer 2 misses, so a rule that
   silently drops it failed its own toy model and was rejected in favor of the OR form above.
   **Named residual, not hidden**: the OR form accepts a transcript whose `cwd` never matched the
   audited root but happens to contain incidental in-repo edits, and that transcript's own span
   becomes the event window for those edits — measured against real data both ways, both outcomes
   are defensible, and this is a real, accepted looseness rather than a solved case. A transcript
   with no in-repo edits AND no matching `cwd` is rejected as invalid input, not silently treated
   as a clean, zero-edit session. An automatic, non-time-based completion signal (e.g. "this
   transcript is no longer the live session," which needs a liveness signal this design does not
   yet have) is named future work, not an assumed extension of what's built here.

   **"Repo root" is not one definition across both layers, stated once here rather than left to
   drift per mention.** Layer 1's root is **operator-supplied** at invocation time and need not be
   a git repository — GIF's own audited root, `/path/to/gif-smith`, is a plain directory
   holding two sibling git repos, exactly the case the nesting-match rule above exists for. Layer
   2's root is **`git rev-parse --show-toplevel`**, necessarily a real git repository, since a
   git hook cannot fire anywhere else. Checked against all 26 currently-resolvable registrations:
   under a git-root definition, zero have a `source_root` that is a strict subdirectory of its own
   git root — the nesting clause's stated necessity is exercised by layer 1's operator-supplied
   roots today, not by layer 2's git-derived ones. The clause is kept for layer 2 too, defensively,
   against a future registration that does nest under a real git root, even though no current one
   does.

   **Every input to this layer fails toward an explicit "cannot audit" outcome, never toward a
   silent clean flag.** `Store()` against an unreachable or wrong DB path does not raise — it
   creates a new, empty, valid database and reports zero registered projects, which under the
   zero-registered-projects rule above would otherwise read as "nothing to flag," indistinguishable
   from a real, checked, clean result. v1 therefore requires the DB path to be resolved absolutely
   and to already exist before the audit runs (verified by a direct file check, not inferred from
   `Store()` succeeding, since `Store.__init__` will happily create one); an absent or unreachable
   DB, an absent or unreadable transcript, and a transcript whose recorded `cwd` doesn't match the
   audited repo are three distinct failure conditions, each producing a distinct non-clean audit-log
   entry, never conflated with a real zero-coverage result. **This is not symmetric with layer 2**:
   under the same unreachable-DB fault, layer 2 fails closed (zero events found in the window,
   commit blocked) while an unvalidated layer 1 would fail open (zero projects, no flag) — the
   "both directions fail in the same, correct sense" claim below is scoped to the case of a single
   unattributable event; it does not extend to a missing data source, which is why the input
   validation above exists as its own explicit requirement rather than being assumed to follow
   from it.

   This layer is a **plain script, never registered in `.claude/settings.json`, and never touches
   any Claude Code permission-decision path** — non-blocking by construction, not by convention, so
   it cannot silently become a gate through a future edit to a hook matcher. It appends every run's
   result to `.foreman/tessguard-audit-log.jsonl`, **tracked by git like the rest of `.foreman/`**
   (not gitignored — every run dirties the working tree until committed, a real, accepted cost of
   keeping the record reviewable rather than local-only). "Append-only" here is **writer
   convention, not a structural guarantee** — unlike `events`, which is append-only because SQL
   `BEFORE UPDATE`/`BEFORE DELETE` triggers raise, this is a plain file in a directory
   `component_coupling.py`'s dotdir exclusion keeps ungated (the same exclusion `.githooks/` hits);
   nothing stops a later edit from truncating it, only the fact that doing so would itself be a
   reviewable git diff. **v1 has no active alerting**; this is a file a human can inspect or `tail`,
   and wiring it to a notification channel is named, explicit future work, not a silently missing
   feature.

   It also self-checks the hard gate's install health on every run, appending to the same log:
   asserts `core.hooksPath == .githooks`, and compares each shim's live sha256 against an
   **`EXPECTED_SHIM_SHAS` mapping (both shims, not one constant), living inside `tessera/tessguard/`
   alongside the rest of this component's code.** Stated honestly, not oversold: `goals_freeze_gate.py`
   denies writes inside a component's path only *until* that component's `GOALS.json` criteria are
   frozen — once frozen, the gate opens permanently and every subsequent edit, including to
   `EXPECTED_SHIM_SHAS` itself, passes unrestricted, so "gated" would overstate this file's
   protection past that point. And the gate's own matcher only inspects `Edit`/`Write`/`Bash` — `MultiEdit`
   and `NotebookEdit`, two of the four tool types this component's own write side counts, are not
   gated at all. So this check is **not enforcement**: a shim neutered and its expected-hash
   constant updated in the same change both pass unimpeded, and the self-check would then compare
   a neutered shim against an already-updated constant and log "healthy." What it genuinely buys:
   neutering the hard gate now requires a **second, visible edit in a different file**, reviewable
   independently of the `.githooks/` change itself, rather than one quiet edit to an untracked
   hook. Real, but a detection-friction improvement, not a guarantee — and how `EXPECTED_SHIM_SHAS`
   itself gets regenerated when a shim legitimately changes is implementation detail for the next
   stage, not resolved here.
2. **Git pre-commit/pre-push hard gate** (blocking) — at commit time, resolves which TESSERA
   project(s) the current repo is registered under and requires at least one real (of the same
   five event types layer 1 counts) TESSERA event for **any one of** those project(s) — same
   any-of aggregation rule and the same `os.sep`-aware `source_root` matching as layer 1, stated
   identically in both places — within the trailing 24 hours, or blocks the commit. **Unlike layer
   1, a repo resolving to zero registered TESSERA projects blocks here** — an empty project set
   under any-of has no real event to find, and blocking-by-default is the correct direction for a
   hard gate (a repo nobody registered with TESSERA yet is not evidence that logging happened),
   the deliberate inverse of layer 1's fail-open on the same input, consistent with the two
   layers' differing fail directions stated at the end of this bullet. Measured: 100% pass (69/69)
   on the five backtested sessions with
   real ticket-backed activity in-window; the other three (VALE, LAAV — projects with zero
   TESSERA activity ever, correctly blocked with nothing to check against) plus **WAT, which is a
   real false positive, not a null case**: WAT has 10 real TESSERA-write events, but its two
   post-registration commits (2026-08-16T16:55Z, 2026-08-17T07:46Z) both **predate** WAT's first
   TESSERA event (2026-08-17T17:27:48Z) — commit-before-ticket, the same ordering WAT's own
   ticket-vs-commit data showed throughout the original retrospective. Blocked for a real reason
   (nothing had been logged *yet*, at commit time), not a vacuous one — stated honestly rather
   than folded into "correct-by-construction," which was true for VALE/LAAV but not for WAT.
   **This layer is a freshness floor, not a drift detector, and is measurably weaker than layer 1
   at the one thing that matters most**: GIF (148 in-repo edits, 0 real events all session) passes
   this gate 14/14 within its 24h window, because its tickets were filed retroactively in a
   *different* session — the 24h window swallows exactly that pattern by construction. **Layer 1
   catches GIF; layer 2 does not.** MOON was examined as a second candidate drift case across
   multiple tessguard review passes and, under the final, robustly-measured predicate, turned out
   **not** to be one within its own repo (18 in-repo edits, 21 real TESSERA-write events, cleanly
   logged) — an earlier fractional predicate design had flagged MOON under some scoping choices
   and not others, which was itself evidence that design was unreliable, not evidence about MOON.
   MOON's real property, established separately in that project's own retrospective, is that the same
   *session* spent hours editing a different repo (`gif-smith`) — a cross-repo attention question,
   not unlogged work inside either repo, and out of scope for a per-repo check by the same
   reasoning as VALE's residual limitation above. GIF is therefore the one clean, robust example
   of layer 1 catching what layer 2 misses; the two layers differ in *what they can detect*, not
   only in blast radius, and a reader should not assume the blocking layer is the stronger one —
   measured, it is the reverse for retroactive-batch drift, and WAT shows the blocking layer has
   its own real false-positive mode (commit-before-ticket) that layer 1 doesn't share. Both
   directions fail in the same, correct sense: an event this component cannot attribute to a
   project isn't counted as coverage by either layer — it makes layer 1's zero-events condition
   more likely to hold (fails loud) and fails layer 2's activity check the same way (fails
   closed) — neither layer's failure mode silently passes.

`tessguard` imports `tessera.store.store.Store` directly, in-process — no server, no subprocess —
matching this document's own stated assumption (above, under `api`'s Process model discussion)
that an agent/tooling caller can `import tessera.store` and skip `api` entirely. It calls only
`store`'s existing public methods (`list_projects()`, `get_events_since()` -- `get_project()`
was named in early design drafts but the shipped implementation never calls it, since
`list_projects()` alone is sufficient for `project_resolve.py`'s matching; caught by
`foreman:integration-test`'s Check 6, corrected here rather than left as a doc/code drift);
**this feature requires zero changes to `store`, `api`, `gitops`, `reviewui`, or `common`** — all
five are already frozen, all-MET components. `events.project_id` exists and is populated for
every ticket-backed event, but `get_events_since()` does not `SELECT` it — `tessguard` derives
project membership from each event's own `ticket_id` prefix instead (`"PREFIX-N".rsplit("-",
1)[0]`, unambiguous for all 30 currently-registered prefixes, all hyphen-free — a fact about
today's data, not a schema-enforced invariant, so this is a real assumption to revisit if a
hyphenated prefix is ever registered) — a process constraint (avoid touching a frozen component
for a one-column read) rather than an architectural one, worded as such rather than implying the
attribution data doesn't exist. `get_events_since(last_seen_rowid)` is rowid-scoped, not
time-scoped — there is no time-windowed event query anywhere in `store`'s public surface — so
both layers call it from `0` and filter by `created_at` in Python: a full scan of `events` per
invocation (under 1,000 rows as of 2026-08-19, free; unbounded growth over time, worth
noting rather than silently inheriting on a hook that runs on every commit — the exact count
drifts every time this document is edited, which is itself why an exact count doesn't belong here).

Two deliberate non-edges, named so their absence reads as a decision, not an oversight: no
`tessguard_to_gitops` edge — `gitops` is scoped to stage-repo promotion/rollback, not the
*source* repo's own git state, which is what the hard gate needs, so `tessguard` locally
reimplements `gitops`'s defensive git-subprocess pattern rather than depend on a component whose
`GOALS.json.done_state` never anticipated this caller. And no interface edge for Claude Code's own
session transcripts (`~/.claude/projects/<hyphenated-path>/*.jsonl`, **and, since a real,
currently-active mechanism was found to write real code changes there, also
`<hyphenated-path>/<session-id>/subagents/agent-*.jsonl`** — measured on this machine: 851
subagent transcripts, 182 containing real Edit/Write/MultiEdit/NotebookEdit calls, 1,341 such
calls total; zero fire in-repo across the 8 backtested sessions, so no backtested number moves,
but the mechanism is real and in daily use, not hypothetical) — that data is harness-owned
filesystem state, not a `tessera` component's output, the same category as `gitops` reading a
live stage repo's git state; `tessguard` reads it read-only. **Toy-modeled before widening the
scan**: including subagent transcripts produces zero duplicate `tool_use` ids against the parent
transcript (no double-counting) and zero change to any backtested session's span or flag —
measured safe, not assumed safe. **Implementation caveat**: a subagent's own recorded `cwd` can
differ from its parent session's (observed: one project's subagent ran at `/path/to/home/.claude`,
unrelated to that session's audited root) — the cwd-validation rule above applies to the *parent*
transcript only; a subagent transcript is scanned for edits under the audited root regardless of
its own recorded `cwd`, the same way a Bash-issued write would be if it weren't already excluded
below. Transcripts can reach into the tens of MB (largest on disk: ~51MB, several others above
32MB) — read streaming, never whole. The directory name is derived forward from a known cwd
(**every non-alphanumeric character**, not only `/` and spaces, collapses to `-` — confirmed
lossy and non-invertible — never parsed backwards), which is all this component needs; a future
multi-project rollout must not rely on reversing it.

**Named coverage gap** (real, not yet exercised by measurement): `store` attributes each event to
an `actor` string (`claude`/`agent`/`jon`/etc.), not a session id — `tessguard` has no way to tell
two concurrent Claude Code sessions in the same repo apart. A session that never touches TESSERA
at all, running alongside a second session that does, would read as "covered" by this design.
An earlier backtest measured one transcript per project and never exercised this case. Stated here
rather than left implicit, per the architecture review.

Install mechanism for the two layers, deliberately different: the audit script has no install
step beyond being invoked — **explicitly, by hand, against a named transcript file, never on an
automatic wall-clock/cron trigger** (see the layer-1 bullet above for the measured reason) — kept
outside the Claude Code hook lifecycle entirely regardless, since that earlier
backtest found that anchoring detection to a tool-lifecycle event, e.g. `PreToolUse`/`Stop`/
`PreCompact`, was exactly the design class that measured worse). The hard gate installs via a
**tracked** `.githooks/` directory plus `git config core.hooksPath .githooks` — not a raw
untracked `.git/hooks/pre-commit` (confirmed only `.sample` files exist there today,
`core.hooksPath` confirmed unset) — because this project already treats enforcement config
(`.claude/settings.json`, every `GOALS.json`) as tracked and reviewable, and `core.hooksPath` is
what preserves that property for a git-native hook. **`component_coupling.py`'s shared path
predicate excludes every dotdir** (`_is_control()`, upstream of glob resolution — confirmed by
the architecture review), so `.githooks/**` can never be brought under any component's glob or
`GOALS.json` freeze, no matter what path is declared. `.githooks/pre-commit` and
`.githooks/pre-push` are therefore **two-line exec shims only** (`exec python3 -m
tessera.tessguard.gitgate`) — all real logic lives in `tessera/tessguard/gitgate.py`, which *is*
gated, and the shim's exact tracked contents are pinned by a `tessguard` `GOALS.json` criterion
whose `verification` is a test that reads the on-disk shim and asserts its sha256 — this is
**test-time verification, not continuous enforcement**: `goals_freeze_gate.py` only gates writes
inside `tessguard`'s own component path, it never reads `.githooks/`, so a shim drift is only
caught when that test is actually run (plus the layer-1 self-check above, on whatever cadence
layer 1 itself runs at, which is the closer-to-continuous backstop for this specific gap). Named,
accepted limitation: `git commit --no-verify` bypasses the hard gate, and this local-only setup
has no server-side git remote to add a second enforcement point (SCOPE.md's constraints rule out
running a remote service here).

Scope, stated once so it isn't re-litigated per file: this build targets `/path/to/ticket-system`
only (dogfood). Most of the other 20+ registered TESSERA projects have a `source_root` in a
*different* repo than where `tessera.db` lives — a real multi-project rollout needs cross-repo
DB-path resolution this component does not attempt; that is a named, separate follow-up, not
solved here (see Deferred, below). Also stated once: none of that backtest's four falsified
candidates — a `PreToolUse` deny-next-action hook, a `Stop`-hook session-end boolean gate, a
`PreCompact`/`PostCompact`-anchored gate, or a synthesis of audit-detection-feeding-`Stop`-hook-
blocking — are being reintroduced by this design; each was measured, not merely disliked, and the
measurements are recorded in that project's own internal history.

**Frozen-edge-graph interaction.** `.foreman/frozen.json` already exists for this project (7
edges, `frozen_at: 2026-08-15T20:00:00Z`) — the two new `tessguard_to_*` edges this document adds
are not yet reflected in it. `freeze_reentry_gate` (designed, not yet built or wired into
`.claude/settings.json` — confirmed) would hard-deny on exactly this once it exists: an edge
present now that was absent at freeze time. `foreman:validate` must regenerate `frozen.json`
(new edges, recomputed `graph_hash`) as part of closing out this build, so a future wiring of
that gate does not deadlock on a stale graph from before `tessguard` existed.

```yaml components
store:      ["tessera/store/**"]
api:        ["tessera/api/**"]
gitops:     ["tessera/gitops/**"]
reviewui:   ["tessera/reviewui/**"]
common:     ["tessera/common/**", "tessera/__init__.py"]
tessguard:  ["tessera/tessguard/**"]
```

```yaml interfaces
api_to_store:         {"producer": "api",       "consumer": "store"}
api_to_gitops:        {"producer": "api",       "consumer": "gitops"}
gitops_to_store:      {"producer": "gitops",    "consumer": "store"}
reviewui_to_api:      {"producer": "reviewui",  "consumer": "api"}
api_to_reviewui:      {"producer": "api",       "consumer": "reviewui"}
store_to_common:      {"producer": "store",     "consumer": "common"}
api_to_common:        {"producer": "api",       "consumer": "common"}
tessguard_to_store:   {"producer": "tessguard", "consumer": "store"}
tessguard_to_common:  {"producer": "tessguard", "consumer": "common"}
```

(Interface values are quoted-key JSON objects — required for `json.loads`, which the
bare-key form `foreman:architecture`'s own SKILL.md example uses does not satisfy. No hook
currently parses this block at all — `component_coupling.py` only parses the `components`
block — so quoting the keys here is forward correctness against the documented format, not a
fix for a live parser breaking today; said plainly rather than implying enforcement that
doesn't exist yet.)

`api_to_store`: every CRUD/query/claim/workflow-transition call, in-process, inside `store`'s
`write_txn_internal()` transactions.
`api_to_gitops`: `promote`/`rollback`/`stage sync` commands, and `diff(stage, commit_sha)` for the
discrepancy computation.
`gitops_to_store`: reading/writing per-stage last-known-HEAD state (used for the on-read
ancestry reconciliation above).
`reviewui_to_api`: browser reads, explicit-save writes (both ticket data and `docs/` files),
opening the SSE stream, and fetching `reviewui`'s own static assets (the browser is the caller
for all of these — producer is the caller/data source per the interface convention, so the
static-asset fetch belongs on this edge, not `api_to_reviewui`; corrected in review).
`api_to_reviewui`: pushed SSE change events only, sourced from `api`'s poll-and-fan-out loop,
not directly from `store`.
`store_to_common` / `api_to_common`: canonical JSON serialization + sha256 hashing, called by
`store` for `event_hash`/`idempotency_key` handling and by `api` for claim-hash and discrepancy
computation — declared as real interfaces (not left implicit) because a silent drift in this
shared code would invalidate every hash in the chain and every claim comparison in the
diff-vs-claim view at once; `foreman:integration-test` enumerates rows from this block, so an
undeclared interface here would never get exercised.
`tessguard_to_store`: `list_projects()` for resolving which TESSERA project(s) a
given repo path is registered under, and `get_events_since()` for both the async audit's
zero-real-events check and the hard gate's 24h activity check — read-only calls into `store`'s
existing public surface, no new method added.
`tessguard_to_common`: `utc_now_iso()`, so `tessguard`'s own "now" (the hard gate's 24h cutoff)
stays in the exact ISO timestamp format `store` writes into `events.created_at` — a
locally-reimplemented "now" formatter that drifted by even a trailing `Z` or a precision digit
would silently corrupt that comparison. **Timestamps must be parsed to `datetime` before
comparing, never string-compared across sources**: Claude Code's own transcript timestamps are
millisecond-precision (`...147Z`) while `events.created_at` is microsecond-precision
(`...753809Z`) — lexicographic comparison sorts backwards inside a shared millisecond (`'8' <
'Z'`). This specifically matters for layer 1, which orders transcript-sourced edit timestamps
against DB-sourced event timestamps for event attribution (not for completion-detection — v1 has
no automatic completion check, see the layer-1 bullet); layer 2 never reads the transcript, only
`store` events against its own wall-clock `now`, so it isn't exposed to the cross-source
comparison.

## Test ownership (gate-coverage, addressed explicitly)

Component-level unit tests live inside their owning component's directory (e.g.
`tessera/store/tests/**`), so they fall under that component's glob and are covered by its own
`GOALS.json` freeze gate like any other implementation file.

Cross-component integration and load evidence — the 20×50 concurrency proof, the kill-mid-write
recovery test, the promote→break→rollback→assert test, the deliberately-mismatched claim demo —
live in a **top-level `tests/` directory that is intentionally outside every component's glob**,
by Foreman's own design: cross-component criteria are owned by `.foreman/GOALS.integration.json`
under `foreman:integration-test`, not duplicated into a component's `GOALS.json`. This is a
disclosed design choice, not the accidental gap the first draft left it as — the first draft
never declared it either way, which is what made it indistinguishable from an oversight.
`tessera/__init__.py`, `tessera/common/**`, and every process entrypoint this document names
now resolve to a declared component (`common`, `api`) — verified against the real component
resolver. This covers every path *this design names*, not every conceivable future path: a new
top-level file added directly under `tessera/` later (e.g. a hypothetical `tessera/main.py` or
`tessera/version.py`) would be unowned until it's added to a component's glob, the same way any
new component would need its own glob entry. Stated precisely rather than as a blanket
"nothing is unowned" claim, which review found false for exactly this class of file.

## Deferred (named, not built yet)

- Exact custom-field validation depth beyond "typed and stored" — deferred to `store`'s own
  `foreman:design-and-scope` pass; SCOPE.md requires validation exist, not a specific schema
  language.
- Attachment size limits / retention policy — deferred to the same pass.
- `tessguard`'s multi-project rollout (config-driven `tessera.db` path resolution for repos whose
  `source_root` lives outside `ticket-system`, per-project `.githooks` templating, canary
  sequencing across the other 20+ registered projects) — this build dogfoods `tessguard` on
  `ticket-system` alone; rollout is a named, separate follow-up ticket, not solved here.

## Process model

Single Python process (stdlib only) runs `api`'s `ThreadingHTTPServer`, which imports `store`
directly and serves `reviewui`'s static assets + SSE stream. **The server binds `127.0.0.1`
only, explicitly** — stated here because it is a SCOPE.md constraint (localhost-only) and the
stdlib default (an empty host string, or `ThreadingHTTPServer(("", port))`) binds every
interface; the process model is the place this gets decided, so it's decided here rather than
left to whichever component happens to construct the server. The CLI is a separate short-lived
invocation of the same `api`/`store` code against the same SQLite file — concurrency between
CLI invocations and the running server is real inter-process concurrency, which is exactly what
SCOPE.md's concurrency bar requires evidence against. `gitops` runs as library calls from
either process, shelling out to `git`.

`tessguard` runs as two more short-lived process kinds, both importing `store` directly the same
way the CLI does — neither is the long-running server, and neither requires it to be up. The audit
script (`python3 -m tessera.tessguard.audit`) runs **explicitly on-demand only** — no wall-clock
or cron trigger in v1 (see the layer-1 bullet above for why) — outside the Claude Code hook
lifecycle entirely. The hard gate (`python3 -m tessera.tessguard.gitgate`)
runs as `git`'s own `pre-commit`/`pre-push` subprocess, invoked by `git` itself via
`core.hooksPath`, not by `api` or the CLI — its exit code is read by `git`, not by any `tessera`
component.

## Language / runtime choice

Python 3 stdlib only (`sqlite3`, `http.server`/`socketserver`, `argparse`, `subprocess`,
`hashlib`, `threading`). Requires SQLite ≥3.35 for `UPDATE ... RETURNING`; the host's bundled
SQLite is 3.51.0 (verified), and `store` checks the version at startup and fails loudly, not
silently, if it's ever run somewhere older.
