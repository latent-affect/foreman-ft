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

```yaml components
bollard: ["architecture_gate.py", "concept_gate.py", "goals_freeze_gate.py", "preflight_blocking_gate.py", "ship_readiness_gate.py", "component_coupling.py", "hook_common.py", "foreman_evidence.py", "verdict_ledger.py", "stamp_ship_charter.py", "GOALS.json", "dependency_provenance_gate/**", "local_review/**", "cross_project_routing/**", "tessera_resolver/**", "lib/**", "test_*.py"]
tessera: ["tessera/**"]
atlas: ["atlas/**"]
agents: ["agents/**"]
skills: ["skills/**"]
site: ["site/**"]
monitoring: ["monitoring/**"]
scripts: ["scripts/**"]
root-docs-and-scratch: ["README.md", "README.agents.md", "ACKNOWLEDGEMENTS.md", "QUALITY-BAR.md", "ROADMAP.md", "prepare_notebook.py", "repo_context_part_1.txt", "repo_context_part_2.txt", "repo_context_part_3.txt", "repo_context_part_4.txt", "repo_context_part_5.txt", ".DS_Store", ".gitignore", ".githooks/**"]
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
