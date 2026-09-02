# ARCHITECTURE — dev-harness

Backfilled 2026-08-27, during a pre-handoff spot check for unattended overnight agent work. This
repo's own `README.agents.md` already said plainly, before this document existed: "A Foreman
project needs `ARCHITECTURE.md` at its own root. This clone does not ship one for itself." That is
not a discovery this document is claiming credit for — it is a known, disclosed gap this document
closes, so `architecture_gate.py` stops hard-denying every nested write here. Describes the
codebase as it actually exists, not an idealized target design.

## What this repo is

Per its own `README.md`: "Scaffolding for an autonomous coding agent you can leave running
overnight." This is Foreman's public distribution tree — the packaged, installable form of the
same system `agent-remediation` and `foreman-v2` build and dogfood internally. Three real
subsystems (Bollard, Tessera, ATLAS) plus the install tooling, persona files, packaged skill
subset, and a built public site, all in one clone meant to be installed elsewhere via
`scripts/install-dev-harness.sh`.

## Components

### 1. bollard

The hooks — a renamed public distribution of `~/.claude/hooks/`'s own gate scripts, with the same
real logic: `architecture_gate.py`, `concept_gate.py`, `goals_freeze_gate.py`,
`preflight_blocking_gate.py`, `ship_readiness_gate.py`, `component_coupling.py` (component/interface
parsing + coverage-ratio machinery), `hook_common.py`, `foreman_evidence.py`, `verdict_ledger.py`,
`stamp_ship_charter.py`. Subdirectories: `dependency_provenance_gate/`, `local_review/` (the
Ollama advisory tier), `cross_project_routing/` (an explicit bug-routing table, checked at
import time against real paths, not inferred), `tessera_resolver/` (resolves a project root to its
TESSERA `db_path`/prefix; a library module, not a hook itself — imported by gates that need ticket
lookups; single-store assumption disclosed in its own docstring). Real test files exist alongside
several gates (`test_component_coupling.py`, `test_concept_gate.py`,
`test_preflight_blocking_gate.py`, `test_ship_readiness_gate.py`). `GOALS.json` present at this
component's root.

### 2. tessera

The ticket system's own source: `api/`, `common/`, `gitops/`, `reviewui/`, `store/`, `tessguard/`.
**Carries its own nested `ARCHITECTURE.md`** (66KB, at `tessera/ARCHITECTURE.md`) — this component
is architected as a semi-independent sub-project rather than described fully by this root
document; this document does not duplicate that file's content, it points to it.

### 3. atlas

The warehouse and query facade: `hookclient/`, `ingest/`, `mcp/`, `query/`, `resolve/`,
`snapshot/`, `warehouse/`. No nested `ARCHITECTURE.md` of its own — described here at the
directory level only.

### 4. agents

Seven named persona files: `clint-eastwood.md`, `dana-okafor.md`, `marcus-webb.md`, `muse.md`,
`priya-desai.md`, and, landed 2026-08-27, `nadia-osei.md` and `owen-reyes.md`. Per
`README.agents.md`'s own install instructions, these are copied to `~/.claude/agents/` (and kept in
this clone as the source-of-record copy) — recent history (`git log`) shows this install path was
added same week (`af2bb11`, `9930a62`).

### 5. skills

A packaged subset of the full skill family: `foreman/`, `code-safety/`, `research-lifecycle/` —
not the complete set this operator's own `~/.claude/skills/` carries (no `dashboard/`,
`audio-mir/`, `paper-pipeline/`, etc.), consistent with this being a public distribution scoped to
what Foreman itself needs, not this operator's full personal skill catalog.

### 6. site

The built public site: `index.html` plus `assets/`, `atlas/`, `bollard/`, `install/`,
`quality-bar/`, `support/`, `tessera/` subpages (8 files total). Static HTML, hand-authored or
built from the docs above — no build script found in this pass connecting it mechanically to the
other components; `index.html` includes illustrative copy showing `architecture_gate`'s DENY
behavior as a feature example, which is demo copy, not a live status indicator of this repo's own
state.

### 7. monitoring

`atlas-ingest-cron/`, `compliance-cron/` (`skill_broadscan.py`, reads tessera/atlas), and
`foreman-status/` (`foreman_status_dashboard_server.py` + `_data.py`) — the generic status
dashboard template, per this operator's own instruction that shipped monitoring should be generic
rather than per-pilot.

### 8. scripts

`install-dev-harness.sh` (the install playbook `README.agents.md` documents; fail-closes on a
missing required hook or `GOALS.template.json`, never partial-installs, never copies
`~/.claude/CLAUDE.md`) and `test_install_fail_closed.py`.

### 9. root docs and generated-context scratch

`README.md`, `README.agents.md`, `ACKNOWLEDGEMENTS.md`, `QUALITY-BAR.md`, `ROADMAP.md`,
`prepare_notebook.py` are real, current public-facing documentation and tooling.
`repo_context_part_1.txt` through `_part_5.txt` (five files, ~1.5MB each) are a plain-text
concatenation of this repo's own files (confirmed: `FILE: ./QUALITY-BAR.md`,
`FILE: ./README.agents.md`, etc. as section headers) — almost certainly the whole-repo dump used to
feed this codebase into an external model for the v2.0 PRD redesign process (`docs/PRD.md`'s own
history names "loading the full repo into Gemini"). Generated scratch, not source, and not
referenced by any component above.

## Interfaces

Three real couplings, none of them a Python-level cross-directory import (confirmed: no
`bollard/*.py` file imports `atlas` or `tessera` as a package):

1. **bollard → TESSERA, via `tessera_resolver/` and the CLI/DB path, not an import.**
   `tessera_resolver` resolves a project root to a TESSERA `(db_path, prefix)` pair and looks up
   blocking tickets; it is a library module imported by gates that need ticket lookups (documented
   consumers: `preflight_blocking_gate.py` today, `tier_triage_gate`/`ticket_status_gate` named as
   planned). Single-store assumption disclosed in the module's own docstring, token-substituted at
   install time.
2. **bollard's `cross_project_routing/` — an explicit, checked-at-import-time table**, not a
   runtime inference, routing a bug by where it was found (globally-shared hook → AREM, Foreman's
   own pipeline logic → FORE, a project-specific hook → that project's own prefix, TESSERA's own
   code → TESS, no match → an explicit surfaced "uncategorized," not a silent guess).
3. **monitoring → tessera + atlas, read-only.** `compliance-cron/skill_broadscan.py` and the
   `foreman-status` dashboard scripts read both, the same read-only pattern `agent-remediation`'s
   own `audio-verification-monitor` component already uses for a different pair of projects.

No other cross-component coupling was found in this pass. `agents/`, `skills/`, `site/`, `scripts/`,
and the root docs are each consumed by people (an installer, a reader, a browser), not by another
component's code.

Corrected 2026-09-02, Run 2 architecture pass: every `bollard` glob below now carries the
`bollard/` prefix. It didn't before — `component_of()` (`component_coupling.py:121`) matches by
`str.startswith()` against the exact glob text, so `"bollard/architecture_gate.py"` never matched
a glob of bare `"architecture_gate.py"`. `goals_freeze_gate.py`'s post-architecture protection has
not recognized a single real file in `bollard/` as belonging to the `bollard` component since this
document was backfilled. Found in this pass, not inherited from anywhere. `docs` is added as a
declared component for the same reason — it existed on disk and had never been declared. See
"Remediation architecture — Run 2" below for the new `bollard/guard_*.py`, `review_notify.py`, and
`root-docs-and-scratch` entries this same correction carries.

```yaml components
bollard: ["bollard/architecture_gate.py", "bollard/concept_gate.py", "bollard/goals_freeze_gate.py", "bollard/preflight_blocking_gate.py", "bollard/ship_readiness_gate.py", "bollard/component_coupling.py", "bollard/hook_common.py", "bollard/foreman_evidence.py", "bollard/verdict_ledger.py", "bollard/stamp_ship_charter.py", "bollard/pre_implementation_brief_gate.py", "bollard/audit_lib.py", "bollard/guard_destructive.py", "bollard/guard_prodconfig.py", "bollard/guard_untrusted_web.py", "bollard/review_notify.py", "bollard/GOALS.json", "bollard/dependency_provenance_gate/**", "bollard/local_review/**", "bollard/cross_project_routing/**", "bollard/tessera_resolver/**", "bollard/lib/**", "bollard/test_*.py"]
tessera: ["tessera/**"]
atlas: ["atlas/**"]
agents: ["agents/**"]
skills: ["skills/**"]
site: ["site/**"]
monitoring: ["monitoring/**"]
scripts: ["scripts/**"]
docs: ["docs/**"]
root-docs-and-scratch: ["README.md", "README.agents.md", "ACKNOWLEDGEMENTS.md", "QUALITY-BAR.md", "ROADMAP.md", "prepare_notebook.py", "foreman_quality_baseline.py", "token_bloat_diagnostic.py", "security_privacy_convergence.py", "repo_context_part_1.txt", "repo_context_part_2.txt", "repo_context_part_3.txt", "repo_context_part_4.txt", "repo_context_part_5.txt", ".DS_Store", ".gitignore", ".githooks/**"]
```

```yaml interfaces
{}
```

Declared empty for the same reason `agent-remediation`'s own `ARCHITECTURE.md` declares it empty:
none of the three real couplings described in prose above is a clean, directional
producer/consumer pair between two components both declared above with a matching schema — #1 and
#2 are both intra-`bollard` (a library module and a routing table consumed by sibling files in the
same component), and #3's real counterpart on the `tessera`/`atlas` side is data read via files/CLI,
not a declared interface those components themselves expose. An honest empty block is correct
here; a fabricated direction is not.

## Remediation architecture — Run 2 (PRD.md R1-R24)

Originated 2026-09-02 against `PRD.md` (Priya Desai, `foreman:product-requirements`, commit
`7aa6ebe`), gated by a real TPM concept-stage pass (`.foreman/tpm-gate-concept.json`, decision
`go`, run by a session that authored neither the brief nor the PRD). Extends the components
above; does not replace them. Every claim below was checked against the live tree in this pass,
not taken from PRD.md's own text on trust — citations name the file and line actually read. Two
independent fresh reads (muse, running as peer session `dev-harness-run2-1e`, plus a genuinely
cold sub-read it dispatched) ran alongside this pass, not after it; their load-bearing findings
are folded in below and marked `[muse]`.

### A verified bug this pass found, unprompted by any requirement

`component_of()` (`bollard/component_coupling.py:121`) matches a project-relative path against a
component's glob **prefix** via plain `str.startswith()`. The `bollard` component's declared
globs (`architecture_gate.py`, `hook_common.py`, `lib/**`, etc.) carry no `bollard/` directory
prefix, unlike every sibling component (`tessera: ["tessera/**"]`, `atlas: ["atlas/**"]`, ...).
`"bollard/architecture_gate.py".startswith("architecture_gate.py")` is `False`. Confirmed by
reading the function, not assumed. **Net effect: `goals_freeze_gate.py`'s post-architecture
protection does not recognize a single real file in `bollard/` as belonging to the `bollard`
component today.** The gate system's own gate code has been ungated by its own declaration since
the backfill. This is exactly the "registered, healthy, exiting zero, never once did its job"
pattern `clint-eastwood.md` warns about, found in the one place that would be most embarrassing
to leave. Fixed in the corrected component block below (every `bollard` entry now carries the
`bollard/` prefix). `docs/` was also never declared as a component at all — added, because R16b
and R22 both write into it.

### R24 — guard dogfooding

Three new files, same shape as the six that already exist:
`bollard/guard_destructive.py`, `bollard/guard_prodconfig.py`, `bollard/guard_untrusted_web.py`,
each with a `test_guard_*.py` sibling, DENY-or-nothing per `hook_common.py`'s existing
convention (`architecture_gate.py`'s own docstring: "this hook either denies or says nothing").
`guard_destructive` matches `Bash`; `guard_prodconfig` matches `Edit|Write` on prod-config-shaped
paths; `guard_untrusted_web` matches whatever tool surface performs outbound fetch in this
harness. Wired into `skills/foreman/config/settings.json.template`'s `PreToolUse` block
(currently six hooks, zero guards — confirmed by reading the template directly) and into
`scripts/install-dev-harness.sh`'s `REQUIRED_HOOKS` preflight (currently eight names at line 18,
none of the three — confirmed).

`[muse]` **M1 as written only tests presence, not function** — a guard file existing and being
listed in the template is a different claim from the guard actually denying a real destructive
command. Architecture adds a fourth verification leg beyond M1's file-existence and template-
registration checks: a synthetic destructive-command probe (the `su""do`-class construct R13
already needs a reproduction for) run against a fresh install, asserted denied. Skipping this
leg means M1 can pass on three inert files.

### R8 — write-time credential scrub, fail-closed

Toy-modeled before being written down (`research-lifecycle:toy-models` discipline, per
`foreman:architecture`'s own dispatch requirement): `/private/tmp/claude-501/-Users-m5-dev-dev-harness-run2/73be5b99-042f-4341-bfcf-529e29aa04d7/scratchpad/r8_writetime_scrub_toy.py`,
`[RAN]`. Three controls against a write-temp → verify-from-disk (fresh connection, not the
in-memory return value) → atomic `os.replace` publish harness, using the actual ten
`CREDENTIAL_PATTERNS` read from `atlas/warehouse/dq_runner.py:246-257`:

- **Positive** (real scrub): row lands, disk read-back clean. Passed.
- **Trap** (the exact bug PRD.md section 7 names for R8 — a function whose *return value* is
  clean while the *persisted* value is the original): the harness's disk-read-back step caught
  it, hard-failed, zero rows. This is the finding that matters — it demonstrates the
  verify-from-disk requirement is load-bearing, not decorative; a verification that trusted the
  return value would have shipped this exact bug clean.
- **Negative** (scrub raises): hard-failed, zero rows, no partial state.

All three ran; none were asserted without running. Architecture: `atlas/ingest/audit_scrub.py`,
new file, sits between `audit.py`'s existing `make_row_mapper` (unchanged — still produces the
row dict) and `insert_audit_event_row` (unchanged signature, now called only against a temp
table by the new harness, never directly against the live warehouse). No new cross-component
interface — both live under `atlas/**`, same reasoning the existing empty `interfaces` block
already gives for intra-component couplings.

`[muse]` **Batch-vs-record atomicity, decided here rather than left implicit.** The unit of
fail-closed atomicity is one audit line, not one ingest run. A scrub bug on line N hard-fails
line N's own write-temp/verify/publish cycle and is logged as a fail-closed trigger (a counted,
alertable event — silent-but-safe is still silent, and muse is right that N1 doesn't by itself
require pipeline-wide DoS as the cost of the guarantee); it does not abort lines N-1 and N+1's
already-independent cycles. This is a real design decision, not inherited slogan: per-run
atomicity would make one malformed line an outage for every session's audit trail, which is a
cost N1's own fail-closed rule does not ask for — the rule's scope is the redaction guarantee on
one record, not availability of the whole ingest pipeline. `check_audit_payload_credential_scan`
(`dq_runner.py:260`, currently advisory) stays live as the independent read-time control this
pattern's own fail-closed-privacy-design doctrine asks for — a second, differently-shaped check
that doesn't share the write-path's blind spots — rather than being retired once the write-time
gate lands.

### R9 — ledger routing, write-time

Same shape, smaller: resolve `cwd` against `dim_project.source_root` (the same table
`check_audit_ledger_partition_by_cwd`, `dq_runner.py:276`, already reads) inside the row-mapper
path, before the row is written, not after. Promote the check from advisory to `contract` in
`test_schema.py:39` once `observed_value=0` — blocked on an actual current count, which this pass
did not query (PRD.md is explicit the 477 figure is unverified; this document does not invent a
replacement number).

### R13 — capability-scoping is the primary control; sandboxing cost re-ranked, ordering not re-verified

`bollard/lib/deny_keychain.sb` and `bollard/lib/neutralize_credentials.sh` both exist (confirmed
by listing `bollard/lib/`), a working Seatbelt deny-profile plus fail-closed wrapper. Architecture
extends the *same mechanism* — a `.sb` deny-profile family plus a wrapper invoked the way
`neutralize_credentials.sh` already is — from credential denial to command-capability scoping:
`bollard/lib/deny_capability.sb` (new profile class) and `bollard/lib/capability_scope.sh` (new
wrapper), denying a named capability (destructive filesystem ops, a network-egress class) at the
OS level regardless of how the command string is obfuscated — the `su""do` class this
requirement names. This closes the *cost* correction PRD.md asks for: the mechanism is proven,
the marginal cost of a second profile is real but bounded, not "build Seatbelt tooling from
nothing."

`[muse]` **What this document does NOT re-verify: the capability-removal-first ordering itself.**
PRD.md section 0.1 discards R13's own six-way-tie scoring as unverified, then keeps "capability-
removal first, detection additive-only" as the surviving ordering on the grounds that it's
"defensible on its own argument" — the same evidentiary shape (specific, narrated, not
independently re-derived) that gets R14/R15 marked BLOCKED two sections later. That asymmetry is
real and this pass does not resolve it; the mechanism above is architected because it's
independently verified (the files exist, the pattern is proven), the *priority ordering* is
carried forward as an inherited, unverified premise, flagged as such, not as settled fact. A
falsification pass or design-and-scope should re-derive the ordering from N2's latency budget and
the actual threat model rather than accept it on the brief's authority.

`[muse]` **Also flagged, not resolved here:** cheap detection for high-frequency/low-severity
cases with capability-removal reserved for high-severity is a real alternative to "capability-
removal first, detection additive only," and it was never weighed against N2's sub-millisecond
budget for the cheapest hooks. Worth a real comparison at design-and-scope; this document doesn't
have the data to make that call today.

### R14/R15 — BLOCKED, three sequencing decisions made explicit rather than left implicit

`layered_command_guard.py` and its four named classes (`OSSandboxGuard`, `SemanticResolutionGuard`,
`AllowlistGuard`, `PatternFeedGuard`) do not exist as executable code anywhere under `/Users/m5`
outside `Library`. Corrected by the falsification pass (`ARCHITECTURE-REVIEW.md` Finding 1): a
re-run of the same search over the same scope returns six hits, not zero —
`/Users/m5/Downloads/dev-harness-BUILD-BRIEF.md` and its two revisions, `PRD.md`,
`ARCHITECTURE.md` itself, and an overnight handoff file. All six are narrative documents
describing or discussing the alleged guard, none are code. The substantive conclusion is
unchanged — no working implementation exists anywhere searched — but "zero hits" as originally
stated overclaimed; the accurate claim is **zero hits in executable code, hits only in prose that
narrates or discusses the design**, and that distinction is now stated explicitly rather than
implied by an absolute "zero." `[muse]` the search space itself was bounded — `Library` excluded,
no casing/naming variants tried, and the negative was never verified beyond this one machine — so
"High confidence" here is confidence in a specific negative search over executable code, not a
settled fact about the artifact's existence anywhere. Three explicit calls, so this doesn't
default into an indefinite
hold by omission (matches concept-gate condition C1 exactly):

1. **Owner and timeout on the operator-confirmation blocker.** Jon, asked directly, not implied
   by silence. Default if unanswered by architecture freeze: treat as never written, size R14/R15
   as build-from-nothing at design-and-scope, and re-derive every R12-R15 claim resting only on
   the brief's narration.
2. **No placeholder interface space reserved.** Designing scaffolding for a guard whose shape is
   unconfirmed risks building against a name, not a spec — the same trap R14/R15's own BLOCKED
   status exists to avoid. If it existed and was lost, R14/R15 become real, separately-scoped
   build requirements (per PRD.md's own two-outcome fork) sized from nothing, same as "never
   written" — the architecture cost is closer between the two outcomes than PRD.md's framing
   suggests.
3. **Re-architecture trigger.** If `layered_command_guard.py` surfaces later with an ordering
   that contradicts R13's capability-removal-first design, that is a signal to re-open R13's
   architecture, not to bolt the found file on top of a design built without it.

### R16 — `dim_session.model`, data source still unresolved

`docs/atlas-architecture.md:63` confirmed directly: `~/.claude/telemetry/sessions.jsonl` carries
`claude_version` and `git_branch` for 1,030 sessions (98.2% ledger coverage) and explicitly no
machine or model field. The DDL comment at line 600 states the same. This pass did not find an
alternative *historical* data source, and does not invent one — R16a stays blocked on the
operator confirming whether one exists, exactly as PRD.md frames it. What this pass adds: if no
historical source exists, R16 splits into two different requirements PRD.md doesn't currently
distinguish — backfilling past sessions (may be impossible) versus capturing model identity
*going forward* (a hook writing it at session start, straightforward) — and design-and-scope
should not treat "no source" as blocking the forward-capture half.

### R17 — concurrent-session view

Buildable now, no new ingestion. `dim_session` (`docs/atlas-architecture.md:602`) already carries
every column needed: `session_id`, `started_ts`, `ended_ts`, `git_branch`, `start_cwd`. New view
in `atlas/warehouse/`, added to `EXPECTED_VIEWS` (`test_schema.py:20`). Per PRD.md's own
falsification note, ships with a correctness case built from known overlapping synthetic
sessions, not just "returns rows."

### R18 — hotspot signal, relabeled not re-sourced

Confirmed by reading the actual line: `foreman_quality_baseline.py:405`,
`entry["hotspot_signal"] = round(max_cc * entry["git_commits_touching_file"], 1)`. Churn is
correct (the concept-stage gate's independent re-derivation put the tree at 9 commits, PRD.md's
own text says 7 — stale by two, changes no conclusion; `get_git_churn` at line 218 counts real
history with no window bug either way) — the defect is that a single-input signal is labeled as
two-input. Fix: at report-generation time, compute churn variance across the analyzed file set;
when it's degenerate (repo history too short to discriminate — this repo's actual case), the
JSON field is emitted as `hotspot_signal_complexity_only` with a header note, not silently as
`hotspot_signal`. A test asserts the relabeling triggers when every file shows churn 1, matching
PRD.md's verification clause exactly.

### R19 — cross-session review notification

Already proven live in this session, not toy-modeled — the mechanism this requirement needs is
the one that carried this entire origination pass, including the concept-stage gate hand-off
itself. Real `SendMessage`/`ListAgents` round-trips ran repeatedly during this pass across three
peer sessions, including ones that changed this document's own content (the muse findings folded
in above) and unblocked it (the concept-gate result). `[RAN]`, the strongest form of evidence this
project's own doctrine asks for. Architecture: a new lightweight hook, `bollard/review_notify.py`,
matching `Edit|Write` on any declared component path, calling `ListAgents` then `SendMessage` to
any peer session registered against the same project root, naming the artifact path and stating
the write is subject to review — fired from inside the writing session's own `PostToolUse` hook
context, per PRD.md's explicit constraint, never as a standalone process. `[muse]` **Accepted
limitation, stated rather than assumed away:** because of that same constraint, R8's ingest
failures and R24's install-time guard gaps can never get durable cross-session alerting unless a
second session happens to be open and registered against the same project when the failure
happens. This is a real gap this architecture does not close — flagged for design-and-scope
rather than silently inherited.

### R21 — quality KPI, storage only

Depends on R18 (fixed above) and R23 (below) landing first — and per concept-gate condition C3,
specifically on DEVH-4 and DEVH-5 (open bugs in `foreman_quality_baseline.py`) closing before any
KPI series is formalized on its output, not just on the script becoming tracked. New retention
path under `monitoring/quality-history/`, one dated JSON per run (matching the existing
`compliance-cron` pattern already in this component), plus a small diff CLI. The one-score-vs-
per-category shape stays explicitly open, per PRD.md's own instruction not to pre-empt
design-and-scope.

### R22 — verbatim-persistence inventory, scoped not written

`docs/verbatim-persistence-inventory.md` (new file, new `docs` component — see the bug note
above). Must include, beyond R8/R9's two known violations: TESSERA ticket free-text fields (per
PRD.md's own instruction) and, found in this pass, `repo_context_part_1.txt` through `_part_5.txt`
(~1.5MB each, this document's own component 9) — a verbatim whole-repo dump sitting at rest at
the project root, unclassified by any inventory today. Writing the inventory itself is
implementation work, out of this stage's scope; this section only establishes that it must exist
and names one entry PRD.md's own R22 text doesn't yet.

### R23 — tooling versioning: a worktree-locality problem PRD.md's own text names but doesn't flag as one

`git rev-parse --git-common-dir` confirms this worktree (`dev-harness-run2`, branch
`build-brief-run2`) and `/Users/m5/dev/dev-harness` share the same underlying repository
(`/Users/m5/dev/dev-harness/.git`). The three scripts PRD.md's R23 targets
(`foreman_quality_baseline.py`, `token_bloat_diagnostic.py`, `security_privacy_convergence.py`)
exist on disk **only** in `/Users/m5/dev/dev-harness` (confirmed: 21.5KB, 24.9KB, 25.7KB
respectively) and are untracked there — meaning they do not exist at all in this worktree,
verified (a filesystem search here returns nothing). Untracked files are worktree-local; a shared
`.git` does not carry them across. **R23 cannot be satisfied by `git add` alone in this branch —
the files have to be copied into this tree first**, a step PRD.md's own text doesn't name because
it was written against the other checkout. Architecture: copy the three scripts into this repo
root (next to `prepare_notebook.py`, same `root-docs-and-scratch` component, matching where the
brief's own generation tooling already sits), then commit. `[muse]` **Formula versioning, not
just SHA pinning:** each script's report header gets `tool_git_sha` and `tree_git_sha` (per
PRD.md's M3) plus one more field this pass adds — a formula/library version tag (radon's version
for the MI calculation, an explicit version string for the churn×complexity-or-complexity-only
formula itself) — because a dependency bump can silently change every number in a report with an
identical tool SHA and no tree diff to explain it. SHA pinning alone doesn't catch that class of
drift.

### Epistemics this pass is explicit about, per `[muse]`

Several PRD.md claims marked **High confidence** rest on reading the *report output* of the same
three untracked, unreproduced scripts R23 itself flags as unreliable (R1's MI numbers, R13's
sandbox trial results). "Read directly from the report" and "independently re-derived" are not
the same claim, and this document does not upgrade PRD.md's confidence notation on those numbers
— R3/N6's separate-context re-verification standard applies going forward, not retroactively to
numbers already spent. Separately: this PRD is authored by an LLM persona (Priya), architected
here by another (Clint), reviewed alongside by a third (muse) — for a system whose stated premise
is "an account of the system that wasn't independently verified isn't trustworthy." That chain is
named here, not resolved; the falsification pass below is the closest thing this pipeline has to
an answer, and Priya owning design-and-scope next (where several of this document's own "operator
must confirm" items route back through her, and where concept-gate condition C2's own PRD
correction is now also her open item) is a real structural repeat of the pattern this project
exists to catch, one level up. Flagged for the orchestrator, not fixed by this document.

**Component and interface blocks are not repeated here.** `parse_component_map()`
(`bollard/component_coupling.py:75`) scans top-to-bottom and `break`s out of its *entire*
loop at the first closing fence of a `\`\`\`yaml components` block — a second such block later in
the same file is silently never read, the same "looks declared, isn't" shape as the `bollard/`
prefix bug above. Caught in this pass before it shipped as a second bug of the same kind inside
the document meant to fix the first one. The single, corrected, extended block lives at its
original location earlier in this document (now carrying the `bollard/` prefix fix, the three new
guard files, `review_notify.py`, the `docs` component, and the three R23 scripts). The interfaces
block there is still correct, re-verified rather than carried forward unchanged: `audit_scrub.py` sits
between `audit.py` and the warehouse writer, but both live inside `atlas/**` — intra-component,
same reasoning the original empty block already gives for couplings #1 and #2. `review_notify.py`
calls a session-messaging primitive external to every declared component (not code this repo
owns), so it has no producer/consumer pair to declare here either. No requirement in this pass
introduced a real cross-component producer/consumer pair the existing empty block didn't already
account for.

## What is unfinished, stated plainly

- **This repo has never gated itself.** This document exists to close that specific gap, not
  because the gap was hidden — `README.agents.md` disclosed it in its own install instructions
  before this document was written.
- **`tessera/`'s own nested `ARCHITECTURE.md` was not re-verified in this pass.** This document
  trusts its existence and points to it; it does not re-check its 66KB of claims.
- **`site/`'s relationship to the docs it presents is inferred, not confirmed by a build script.**
  No mechanism was found in this pass that regenerates `site/` from `QUALITY-BAR.md`,
  `README.md`, or the component docs when they change — it may be hand-maintained, drifting
  silently if so.
- **No content diff was run between this repo's `agents/*.md` and `~/.claude/agents/*.md`** beyond
  the two files landed together this session (`nadia-osei.md`, `owen-reyes.md`); the other five
  may have already diverged, per this project's own previously-named registry-sync gap.
- **`repo_context_part_*.txt`'s exact provenance is inferred from its own section headers, not
  confirmed against a specific commit or session that generated it.**
