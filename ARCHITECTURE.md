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
"Remediation architecture — Run 2" below for the new `bollard/guard_`-prefixed and
`root-docs-and-scratch` entries this same correction carries.

Corrected again 2026-09-02, R14/R15 recycle (DEVH-72, Clint Eastwood persona), same defect class
surfacing in a second form. Found by Priya at design-and-scope (`dev-harness-run2-6a`) and
independently re-verified live against the real parser, not narrated — `component_of()`/
`is_implementation_path()` run directly, with positive and negative controls, before and after
this edit; script and raw output at
`/private/tmp/claude-501/-Users-m5-dev-dev-harness-run2/711af95c-95ec-4af6-b28b-4d2bc76d6f8e/scratchpad/devh72_before_after.py`.
Two gaps, both in this block: (1) the three individual bare `guard_*.py` filenames covered only
guards that already existed when this document was backfilled — the four new R14/R15 guard files
(`guard_allowlist.py`, `guard_semantic_resolution.py`, `guard_pattern_feed.py`,
`guard_os_sandbox.py`) all resolved `component_of=None`, so `goals_freeze_gate.py` would gate none
of them once implemented, silently. (2) `"bollard/test_*.py"` carries a mid-string wildcard;
`_prefix_of()` (`component_coupling.py:135-137`) only strips *trailing* stars, so the literal
string `"bollard/test_*.py"` was the prefix it produced, and nothing starts with a string
containing a bare `*`. That entry has been inert since it was written — confirmed against all nine
real `test_*.py` files under `bollard/`, not just `test_guard_destructive.py`, the one the design-
and-scope pass happened to probe.

Fix, in both cases: replace the individual/starred entries with a bare literal prefix and no
wildcard character at all (`"bollard/guard_"`, `"bollard/test_"`) — `_prefix_of()` is a no-op on a
string with no trailing star, so the entry already IS its own prefix, which sidesteps the
mid-string-star bug entirely rather than triggering a third instance of it. This also
future-proofs new `guard_*.py`/`test_*.py` files without another architecture-stage edit. Verified
by rerunning the same script against the edited file: all four new guard paths and all nine test
paths now resolve `component_of='bollard'`, `is_implementation_path=True`; the three existing
positive controls and the two negative controls (an unrelated root file, an unrelated `tessera`
path) are unchanged. `bollard/GOALS.json`'s C9-C16 freeze (Priya, design-and-scope) was blocked on
this landing; it is not this pass's concern beyond unblocking it. `PRD-DELTA-R14-R15.md` §7.2 (the
still-stale BLOCKED prose two sections below) is a separate, not-yet-triggered item — deliberately
untouched here.

```yaml components
bollard: ["bollard/architecture_gate.py", "bollard/concept_gate.py", "bollard/goals_freeze_gate.py", "bollard/preflight_blocking_gate.py", "bollard/ship_readiness_gate.py", "bollard/component_coupling.py", "bollard/hook_common.py", "bollard/foreman_evidence.py", "bollard/verdict_ledger.py", "bollard/stamp_ship_charter.py", "bollard/pre_implementation_brief_gate.py", "bollard/audit_lib.py", "bollard/guard_", "bollard/review_notify.py", "bollard/GOALS.json", "bollard/dependency_provenance_gate/**", "bollard/local_review/**", "bollard/cross_project_routing/**", "bollard/tessera_resolver/**", "bollard/lib/**", "bollard/test_"]
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
monitoring_to_atlas: {"producer": "atlas", "consumer": "monitoring", "surface": "atlas.query.facade.QueryFacade"}
```

**Corrected 2026-09-02, `foreman:integration-test` execution (Dana Okafor persona, session
`dev-harness-9b`).** Declared `{}` originally; that was wrong, not a defensible omission. The
integration-test pass built the real cross-component import graph over all 194 Python files using
this project's own `component_of()`/`parse_component_map()` resolvers, not a reimplementation, and
found `monitoring/foreman-status/foreman_status_dashboard_data.py:34` imports
`atlas.query.facade.QueryFacade` directly. Verified by execution, not inference: the import resolves
in-tree to `atlas/query/facade.py` (not an installed path), and `QueryFacade`'s own declared
failure contract (`QueryFacadeUnavailable` on a missing warehouse) executes correctly. This is a
real, directional producer/consumer pair between two declared components — exactly the shape this
block exists to record — and the prior text's stated reason for omitting it (coupling #3's
`tessera`/`atlas` side being "data read via files/CLI, not a declared interface") was correct for
the `tessera` half and wrong for the `atlas` half: this is a Python import of an exported class, not
a file or CLI read. Full evidence, including what could and could not be executed end-to-end in
this worktree (`TESSERA_DB` and the warehouse db are environment gaps, not code defects) and the
three deployment-only couplings that were checked and correctly excluded, is in
`INTEGRATION-TEST-REPORT.md`.

The other two prose couplings (#1 `tessera_resolver`, #2 `cross_project_routing`) are still
correctly excluded: both remain intra-`bollard`, no cross-component producer/consumer pair to
declare.

**Deployment couplings, recorded but not in the `yaml interfaces` block.** The same pass found
three imports (`skills/code-safety/.../posttooluse_hook.py` → `verdict_ledger`,
`skills/foreman/scripts/foreman_init.py` → `tessera_resolver` and → `component_coupling`) that
resolve outside this repository at install time (`~/.claude/hooks`, via `HOOKS_DIR`/`_hooks_dir()`
substitution), not to any in-tree component. Correctly excluded from the interfaces block — that
block declares couplings between components *this repository's own globs describe*, and these
targets aren't in this tree. But they're real: `skills/` genuinely depends on artifacts *built
from* `bollard/` functioning correctly post-install, mediated by the install step rather than a
Python import at rest here. Leaving them completely unrecorded anywhere risks the exact shape
Addendum 3 already found once tonight — a real coupling silently absent rather than explicitly
noted as out-of-scope-and-why. Recorded here for that reason, not added to the interfaces block
itself, which stays reserved for in-tree, Python-level producer/consumer pairs.

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

### R14/R15 — architecture stage resolved via recycle (DEVH-61–DEVH-64); design-and-scope in progress

Resolved 2026-09-02 (DEVH-73). The BLOCKED entry below was correct when written but is now stale:
Clint Eastwood's Tier-3 gate decision on `DEVH-16` (RECYCLE — the entry was a deliberate deferral,
not a component design, and Tier 3 needs one) sent R14/R15 back through real architecture
origination. Four tickets, each closed with real, independently-verified content, not narrated and
not assumed: `DEVH-61` (R13's capability-removal-first ordering is a real precondition on the guard
chain's compositional structure, not on runtime invocation order — the two are separable and only
the first is settled), `DEVH-62` (component decomposition — a two-layer split between the primary
capability-removal guards (`OSSandboxGuard`, `AllowlistGuard`) and the additive-only detection
guards (`SemanticResolutionGuard`, `PatternFeedGuard`), plus a real, toy-modeled conflict between
that split and `hook_common.py`'s repo-wide fail-open default, tracked separately as `DEVH-70`
rather than fixed inline), `DEVH-63` (interface contract and composition semantics — the four
guards are not one homogeneous chain; toy-modeled confirmation that `OSSandboxGuard`'s denial is a
live process failure, structurally unlike the other three guards' pre-execution refusal), and
`DEVH-64` (independent falsification, no prior exposure to DEVH-62/63's own work before reading it
fresh — verdict: the design holds on every claim re-derived from scratch, plus one real sharpening
of `OSSandboxGuard`'s invocation seam via the harness's documented `updatedInput` contract, narrowing
"unbuilt mechanism" to "an existing harness contract, not yet chosen or validated live"). Read those
four tickets and `PRD-DELTA-R14-R15.md` (design-and-scope's own further resolution — the
`OSSandboxGuard` registration choice, and requirements R14a/R14b/R14c/R15a/R15b) for the real
content; not reproduced here.

Status as of this edit: `bollard/GOALS.json`'s C9–C16 freeze for those requirements has not yet
landed (`criteria_frozen_at` in that file still reflects the pre-existing C1–C8 freeze) — `DEVH-72`
fixed the component-map bug that would have made such a freeze gate nothing, but the freeze write
itself is still design-and-scope's (Priya's) pending next step, not yet exercised.

Kept below for its real content, not superseded wholesale: the original existence question and its
resolution (the C1 default firing), and the falsification correction to the "zero hits" search
claim, both still stand as accurate history. Two of the original three "explicit calls" are now
historical rather than live — call 1 (the operator-confirmation timeout) fired its own stated
default and is closed; call 2 ("no placeholder interface space reserved") is now **literally
false** as written — `DEVH-62`/`DEVH-63` did the real component/interface design that call was
correctly refusing to do prematurely, before the existence question had an answer. Call 3 (the
re-architecture trigger) still stands, sharpened by `DEVH-61`: it is R13's *compositional*
precondition that would trigger re-architecture if contradicted, not a temporal one.

---

### R14/R15 — original architecture-stage record (superseded above; kept for its real content)

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
   the brief's narration. **Historical — resolved.** The default fired at architecture freeze
   (`DEVH-16` comment history); R14/R15 were sized build-from-nothing exactly as this call
   specified.
2. **No placeholder interface space reserved.** Designing scaffolding for a guard whose shape is
   unconfirmed risks building against a name, not a spec — the same trap R14/R15's own BLOCKED
   status exists to avoid. If it existed and was lost, R14/R15 become real, separately-scoped
   build requirements (per PRD.md's own two-outcome fork) sized from nothing, same as "never
   written" — the architecture cost is closer between the two outcomes than PRD.md's framing
   suggests. **Historical — now literally false as written.** The existence question resolved
   (never written), and interface space was then reserved for real: `DEVH-62`/`DEVH-63` did the
   component decomposition and interface contract this call was correctly refusing to do
   prematurely, before there was an answer to build against.
3. **Re-architecture trigger.** If `layered_command_guard.py` surfaces later with an ordering
   that contradicts R13's capability-removal-first design, that is a signal to re-open R13's
   architecture, not to bolt the found file on top of a design built without it. **Still live**,
   sharpened by `DEVH-61`: the precondition R13 imposes on R14/R15 is compositional (which guards
   must be independent and fail-closed), not temporal (literal runtime invocation order) — a
   surfaced file with a compositional ordering that contradicts that split is what would trigger
   this, not one with a different runtime sequence.

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

## Addendum — found during design-and-scope, 2026-09-02

Not a rewrite of anything above; this document is already locked and DEVH-6 implementation has
started. Purely additive, per the same practice the rest of this pipeline has used tonight
(falsification fixes, muse's findings) — append findings, don't silently rewrite frozen
conclusions.

**Gap:** running `is_implementation_path()` for real during design-and-scope (Priya, not read
from this document's prose) found that `PRD.md` and `DESIGN-AND-SCOPE.md` both resolve to
`component=None` from `component_of()` — neither is covered by any declared component's glob.
Confirmed independently in this pass: `is_implementation_path()` returns `(False, None)` for both,
identically to `ARCHITECTURE.md`/`SCOPE.md`/`GOALS.json` — but for a different reason. The control
files are *deliberately* exempted via `CONTROL_FILENAMES`
(`bollard/component_coupling.py:57`); `PRD.md`/`DESIGN-AND-SCOPE.md` land in the same place *by
accident of no glob matching*, not by declared intent. Today the observable behavior is identical,
which is exactly why this was easy to miss — the gap is in what's declared, not (yet) in what's
enforced.

**The call:** add `PRD.md` and `DESIGN-AND-SCOPE.md` to `CONTROL_FILENAMES`. They are Foreman
stage-gate pipeline documents, structurally the same kind as `ARCHITECTURE.md`/`SCOPE.md` —
produced and gated by their own dedicated stage hooks (`concept_gate.py` for `PRD.md`'s downstream
gate; design-and-scope's own gate for `DESIGN-AND-SCOPE.md`), not owned by any single component
and not subject to `goals_freeze_gate`'s per-component discipline. Declaring the exemption makes
it match its actual reason instead of coinciding with it, which matters the moment any future
component's glob is broad enough to accidentally start matching a root filename.

**Found while deciding the fix, not asked for:** this compounds with R19. `review_notify.py`'s
matcher, as specified above ("`Edit|Write` on any declared component path"), would derive its
trigger from the same `component_of()`/`is_implementation_path()` machinery — meaning it would
never fire on an edit to `PRD.md`, `ARCHITECTURE.md`, or `DESIGN-AND-SCOPE.md` either way, whether
they're accidentally uncovered or deliberately exempted via `CONTROL_FILENAMES`. That is
backwards: governance-document edits (a PRD correction, an architecture addendum like this one)
are exactly the writes R19 exists to get reviewed, not the ones it should skip. Revised R19
requirement: `review_notify.py`'s trigger must be "`Edit|Write` on any declared component path
**or** any of `PRD.md`, `ARCHITECTURE.md`, `SCOPE.md`, `DESIGN-AND-SCOPE.md`" — an explicit
inclusion, not derived solely from `is_implementation_path()`.

**Not implemented here.** `bollard` is a frozen, implementing component (DEVH-6 in progress) —
this is a design call, not a live patch to code under implementation. Two DEVH tickets needed,
distinct per this project's own R20 (one ticket per requirement), not folded into R19 or R24:
one for the `CONTROL_FILENAMES` addition, one for `review_notify.py`'s trigger correction (which
may fold into R19's own ticket if R19 hasn't been written yet — a call for whoever authors it).

## Addendum 2 — atlas's component granularity was wrong from the backfill, not from tonight

Found while resolving a gap Priya surfaced while freezing R8: `component_root('atlas')` resolves
to `atlas/`, so `goals_freeze_gate.py` only ever reads `atlas/GOALS.json` for any write anywhere
under `atlas/**`. Checked what that actually orphans before deciding anything, not assumed:
`atlas/ingest/GOALS.json` (11,928 bytes), `atlas/warehouse/GOALS.json` (7,186 bytes),
`atlas/snapshot/GOALS.json`, `atlas/resolve/GOALS.json`, `atlas/hookclient/GOALS.json`,
`atlas/query/GOALS.json` (8.6–11.6 KB each) all exist, all real. `ingest`'s and `warehouse`'s were
read in full: both are complete, frozen (`criteria_frozen_at` 2026-08-21/22), `MET` across every
criterion with real test evidence, and `ingest`'s own record includes a real production run
against all 35 live TESSERA-registered projects, not a fixture. Both declare `"components": 1`
for themselves — this component was built and shipped at per-subdirectory granularity from the
start. `atlas/mcp/` has no `GOALS.json` at all and never did — consistent with R7's disposition
(narrowed to a risk-register item, no implementation required).

**The call: these are not orphaned files to delete, they're real historical design-and-scope
records that a wrong component declaration made unreachable.** The original backfill's "atlas...
described here at the directory level only" was already inaccurate the day it was written —
5 days after `ingest` and `warehouse` had already been built and frozen at their own component
granularity. Fix: declare `atlas-ingest`, `atlas-warehouse`, `atlas-snapshot`, `atlas-resolve`,
`atlas-hookclient`, `atlas-query` as their own components (`atlas/ingest/**` etc.), matching what
already shipped; keep `atlas` itself as a narrower catch-all for `atlas/__init__.py` and
`atlas/mcp/**`, the one real subdirectory with no existing design-and-scope record — `mcp/` needs
its own GOALS.json before anything under it can be gated at all, not inherited coverage from the
catch-all. `component_of()`'s longest-prefix-wins matching (`component_coupling.py:130`) makes
this resolve correctly without touching the matching logic itself, only the declaration.

**Time-sensitive, not queue-behind-not-urgent like Addendum 1.** Priya's `atlas/GOALS.json`,
frozen minutes ago for R8, sits exactly where this fix moves coverage away from: R8's
architecture places `audit_scrub.py` in `atlas/ingest/`, which once subcomponents are declared
resolves to `atlas-ingest`, not `atlas`. Left as-is, the subcomponent fix would silently orphan a
freeze that was just correctly done. The right sequencing is not "declare subcomponents, then
separately notice R8 broke" — it's one change: declare the subcomponents **and**, in the same
motion, relocate R8's six criteria from `atlas/GOALS.json` onto `atlas/ingest/GOALS.json` as an
amendment (that file's own `amendments[]` convention already exists and was already used once,
for its own C2). `atlas/GOALS.json` itself then either gets deleted (nothing real component-level
lands directly under `atlas/` root — only `__init__.py`) or stays as `mcp/`'s eventual home once
that subdirectory gets its own design-and-scope pass — a naming call for whoever does that work,
not decided here. This is Priya's artifact and her stage; recommending the relocation, not
performing it — same discipline as Addendum 1, but flagged as blocking-adjacent given DEVH-6 is
active and R8 implementation may start writing to `atlas/ingest/` before this resolves.

**Correction, 2026-09-02, same day: the "one motion" sequencing above is wrong, superseded by
Priya's mechanical check.** She ran `parse_component_map` rather than reading this prose, and
found two things that break the plan as written. First, relocating before declaring causes
exactly the orphaning it exists to prevent — the gate keeps reading `atlas/GOALS.json` until the
subcomponent declaration actually lands, so a relocated-but-not-yet-covered freeze is worse than
an unmoved one. Second, and this is the part this addendum didn't check: declaring the
subcomponents splits R8 across three areas, not one — `atlas-ingest` (`audit_scrub.py`),
`atlas-warehouse` (C5's contract promotion in `test_schema.py`), and `docs` (C6's possible DDL
edit in `docs/atlas-architecture.md`) — and `docs/GOALS.json` does not exist, so that third of R8
would be hard-denied outright the moment the split landed. She tried declaring it, confirmed this
by running the resolver, and reverted. Right call: defer the subcomponent split until R8's
criteria reach MET, then declare cleanly and archive R8's six criteria out — not two amended,
hash-attested MET records plus a third new one, for nothing R8 currently needs. Under the current
single `atlas` declaration, all three of R8's target areas correctly resolve to one file, which
matches R8 being one requirement. The underlying finding (atlas's real per-subdirectory
granularity, the six real completed records) stands; only the *timing* recommendation above was
wrong, and is corrected here rather than left on record as if it still held.

## Addendum 3 — requirement coverage sweep, run because nobody had run one until now

A PRD-coverage spot-check (Jon, relayed via the orchestrator) found R2 has zero architectural
treatment anywhere in this document. Verified by grep, then extended to every live R-number
rather than checking R2 in isolation:

```
R1: 2   R2: 0   R3: 1   R4: 0   R5: 0   R6: 0   R7: 1   R8: 16  R9: 2   R10: 0  R11: 0  R12: 1
R13: 5  R14: 5  R15: 6  R16: 2  R17: 1  R18: 2  R19: 5  R20: 1  R21: 1  R22: 3  R23: 6  R24: 4
```

R4, R5, R6, R10, R11 at zero are correct — PRD.md section 8 and section 0 already close all five
before this stage, with no requirement text needing architecture at all. R1, R2, R3, and R20 are
the real gap: each was silently absent, not explicitly dispositioned, and per the same standard
this document already applies elsewhere (R1's target number, R16a's data source, R21's metric
shape are all explicitly stated as open rather than left silent) — silence and "deliberately
deferred" read identically to a reader, and three more instances of exactly that ambiguity were
sitting in this document uncaught. Fixed here, not by rewriting the sections above:

- **R1 — no architecture decision, and that's correct, now stated.** Refactoring an existing
  file (`store.py`) to hit an MI target, and justifying or dropping the two R1b files, introduces
  no new component, file, or interface. The only open question (the target number) is already
  PRD.md's own blocking precondition, owned by design-and-scope's `GOALS.json`, not architecture.
- **R2 — same shape, same conclusion.** Characterization tests proven to fail against a mutant
  before a refactor commit lands is a verification *procedure* applied to whichever component R1
  lands in (`tessera/store`), not a design decision — no new file, module, or cross-component
  coupling. It belongs in that component's own `GOALS.json` criteria as a required pre-commit gate
  on the R1 work, the same way R8's mutant/trap-control discipline (this document's own R8
  section) already lives in `criteria`/`verification` fields rather than as a separate
  architecture section, not because R2 is less important but because it's the same *kind* of
  requirement.
- **R3 — same shape.** A second baseline run from a non-authoring context, diffed against the
  first, is a process requirement with a stated dependency on R23 (already covered above) and no
  component/interface content of its own. Design-and-scope's job when R1's `GOALS.json` is
  written, not architecture's.
- **R20 — PRD.md already says so explicitly** ("Timing: design-and-scope, per R20's own text. Not
  this stage") — architecture should still have echoed that rather than silently complying with
  it, since a reader can't distinguish "read and correctly out of scope" from "not read."

**Why this wasn't caught earlier, plainly, not defensively.** Three passes touched this document
before Jon's spot-check found the gap: this pass's own origination, muse's collaboration, and
`dev-harness-32`'s independent falsification. All three checked *correctness* — is a claim this
document makes actually true — and none checked *completeness* — does this document say something
about every live requirement, even "nothing needed here." `ARCHITECTURE-REVIEW.md`'s own stated
scope confirms this: it lists what it verified and what it declined to re-verify, and a
requirement-coverage sweep against PRD.md's full list is absent from both lists, meaning it was
never framed as a check to run, not that it was run and passed. The mechanical version of that
check is five minutes (the grep table above) and would have caught this immediately. Recommending
to the orchestrator that a coverage sweep — every live PRD requirement has a disposition, even a
one-line "no architecture needed, see design-and-scope" — become a standard, named step of this
pipeline's falsification pass going forward, not a special case run only when someone happens to
ask for one.
