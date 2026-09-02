# Integration test — dev-harness Run 2, 2026-09-02

**Stage:** `foreman:integration-test` (execution)
**Reviewer:** Dana Okafor persona, session `dev-harness-9b`. Authored none of the seven build
components, the architecture, or the PRD.
**Subject:** `ARCHITECTURE.md`'s `yaml interfaces` block, declared `{}`, against the shipped work
for R1 through R24.
**REQ-19 re-consultation:** `PRD.md` re-read for this stage (bounded: interface, monitoring and
facade references, lines 184, 282, 306) and `ARCHITECTURE.md` sections `## Interfaces` and the
`yaml components` block at line 130.

---

## Status, updated 2026-09-02 after the disposition was worked through

The original finding, that the `{}` interfaces block was inaccurate, stands and is what produced
the row below. All four disposition items have since been actioned. Every claim in this update was
re-verified in this session against the live tree rather than taken from the reporting session.

| Interface | Declared | Check 6 (static) | Executed | Evidence |
|---|---|---|---|---|
| `monitoring_to_atlas` (producer `atlas`, consumer `monitoring`, surface `atlas.query.facade.QueryFacade`) | YES, now declared | **PASS** (a real FAIL was found and fixed first) | **PARTIAL** — accepted and tracked as `DEVH-44` | Import executes and resolves in-tree post-fix; producer contract executes; five `fetch()` calls now handled, 6/6 tests green; full data flow still not executable in this tree |

Zero other cross-component edges exist. The rest of the original empty block was correct.

### What changed, and what I re-verified myself

**Interface declared.** `ARCHITECTURE.md`'s block now reads
`monitoring_to_atlas: {"producer": "atlas", "consumer": "monitoring", "surface":
"atlas.query.facade.QueryFacade"}`. Read directly from the file.

**Check 6 ran independently and found a real FAIL.** All five `facade.fetch()` calls had no
exception handling, despite `fetch()` re-checking status on every call and raising `QueryRefused`
defensively. Fixed in `9effe00` (`monitoring/foreman-status/foreman_status_dashboard_data.py`,
+87/-36, plus a new 180-line test file and `monitoring/GOALS.json`). I did not take the commit
message for it: the five calls at lines 88, 104, 111, 114 and 117 are now inside one `try` with
`except (QueryRefused, sqlite3.Error) as exc` recording `warehouse["fetch_error"]`, and
`facade.close()` has moved to a `finally` at line 124 so it runs on every exit path. Executed the
new suite directly, since `monitoring/` is not an importable package:

```
$ cd monitoring/foreman-status && python3 -m unittest test_foreman_status_dashboard_data -v
Ran 6 tests in 0.055s
OK                                                    # real exit 0
```

**Post-fix re-verification of my own earlier evidence.** The consumer file changed by 87 lines
after my original evidence was taken, so that evidence was stale and I re-derived rather than
carried it. The import still resolves in-tree
(`/Users/m5/dev/dev-harness-run2/atlas/query/facade.py`), and the consumer still exits 1 at
`TESSERA_DB`. The Check 6 fix does not change the end-to-end result, because the failure happens in
`load_tessera_projects()` at line 47, before the facade block is reached.

**Both tickets exist and are open**, confirmed by querying TESSERA directly rather than trusting
the ticket numbers: `DEVH-44` (severity 3, "Close the monitoring->atlas integration PARTIAL when a
warehouse database exists") and `DEVH-47` (severity 2, "gates_by_prefix crashes with TypeError on
an empty-but-clean warehouse").

**What I did not verify.** That `dev-harness-33` ran Check 6 in a genuinely independent context and
re-confirmed the fix in its own negative-control worktree. I observed the resulting code, the
resulting tests and the resulting commit, all of which are consistent with that account, but I did
not observe that session's process and cannot attest to its independence. Recorded as reported,
not as verified.

---

## How this was checked

Not by reading the prose. I built the real cross-component import graph over all 194 Python files
in the worktree, resolving component ownership with the project's own
`bollard/component_coupling.py::component_of()` and `parse_component_map()` rather than a
reimplementation, so files are attributed exactly as the gates attribute them. Imports were
extracted with `ast`, relative imports excluded (they cannot cross a component boundary), and each
resolved import mapped back to an owning component.

Four candidate cross-component edges came out. Three were eliminated by resolving where the import
actually lands, which is the step that separates a real interface from a deployment coupling.

**Architecture's own falsifiable sub-claim holds.** It states "no `bollard/*.py` file imports
`atlas` or `tessera` as a package." Cross-imports found: **0**. That claim is true.

---

## The one real interface

`monitoring/foreman-status/foreman_status_dashboard_data.py:34`

```
ATLAS_REPO = Path(os.environ.get("ATLAS_REPO", str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(ATLAS_REPO))
from atlas.query.facade import QueryFacade, QueryFacadeUnavailable
```

The default matters. The file sits at `monitoring/foreman-status/`, so `parents[2]` is the
repository root, and the import therefore resolves to **this repository's own `atlas` component**.
Executed, not inferred:

```
$ python3 -c "...sys.path.insert(0, repo_root); from atlas.query.facade import QueryFacade..."
ATLAS_REPO default resolves to: /Users/m5/dev/dev-harness-run2
is repo root: True
IMPORT EXECUTED OK -> <class 'atlas.query.facade.QueryFacade'>
resolved from file: /Users/m5/dev/dev-harness-run2/atlas/query/facade.py
```

This is a clean directional producer/consumer pair: `atlas` produces a public API surface
(`QueryFacade`, `QueryFacadeUnavailable`), `monitoring` consumes it by Python import. Both are
declared components. It is exactly the shape the `interfaces` block exists to record.

**Architecture's stated reason for excluding it does not match the code.** The document says
coupling #3's "real counterpart on the `tessera`/`atlas` side is data read via files/CLI, not a
declared interface those components themselves expose." For the `tessera` half that is correct, and
the `tessera` side is verified below. For the `atlas` half it is not: this is a Python import of an
exported class, not a file or CLI read.

### Executed evidence, and what could not be executed

**Producer contract, executed.** `QueryFacade` raises its own declared failure type on a missing
warehouse rather than leaking a raw sqlite error:

```
$ python3 -c "from atlas.query.facade import QueryFacade; QueryFacade('.../atlas/warehouse/atlas.db')"
atlas.query.facade.QueryFacadeUnavailable: cannot open
  /Users/m5/dev/dev-harness-run2/atlas/warehouse/atlas.db read-only:
  unable to open database file -- does it exist and is it migrated?
```

**Consumer end to end, run, and it does not reach the interface.**

```
$ cd monitoring/foreman-status && python3 foreman_status_dashboard_data.py     # exit 1
sqlite3.OperationalError: unable to open database file
  at load_tessera_projects() line 47, connecting to TESSERA_DB
```

It fails at `TESSERA_DB` before ever calling the facade. `TESSERA_DB` defaults to
`/path/to/ticket-system/data/tessera.db`, a genericization placeholder, and no warehouse database
exists at `atlas/warehouse/` in this worktree at all.

So the Executed column is **PARTIAL, and deliberately not PASS**. The import across the boundary is
real and executes. The producer's contract executes. A real call carrying real data from consumer
through producer could not be run in this tree, and per this skill's own guardrail a mock standing
in for either side would not earn a PASS, so none was used. Naming that is the point rather than
rounding it up.

---

## The three edges that are NOT interfaces, and why

Each was eliminated by resolving the import target, not by assumption.

| Candidate | Resolves to | Verdict |
|---|---|---|
| `skills/code-safety/.../posttooluse_hook.py` imports `verdict_ledger` | `_hooks_dir()` walks parents for `.claude`; no such parent exists inside the repo path, so it falls back to `~/.claude/hooks` → `/Users/m5/.claude/hooks` | Outside the repo. Deployment coupling |
| `skills/foreman/scripts/foreman_init.py` imports `tessera_resolver` | `HOOKS_DIR = Path("/path/to/home/.claude/hooks")`, an install-time substitution token | Outside the repo. Deployment coupling |
| `skills/foreman/scripts/foreman_init.py` imports `component_coupling` | Same `HOOKS_DIR` | Outside the repo. Deployment coupling |

Verified: no vendored copies of `verdict_ledger.py`, `component_coupling.py` or `tessera_resolver`
exist anywhere under `skills/`, so these are genuinely reaching for the installed artifacts.

Leaving these undeclared is defensible under this architecture's own framing, since the component
globs describe files in this repository and these imports land outside it. Worth recording anyway:
the files being imported at the installed location are *built from* `bollard/`, so a real
producer/consumer relationship does exist, mediated by installation. That is a different shape from
an in-tree interface and arguably belongs in a deployment section rather than the `interfaces`
block. Flagged, not asserted as a defect.

---

## Disclosure: Check 6 did not run *at the time of the original pass*

**Superseded.** Check 6 has since run independently and PASSed, after finding and closing a real
FAIL. See the status section at the top. The section below is the original disclosure, kept
unedited because it records why the first pass could not run it, which is the reasoning that
produced the disposition.

Stating this plainly, because the skill requires the distinction between "we called Check 6" and
"an independent subagent ran it."

**Independent passes that actually ran: zero.** Two separate reasons, both real:

1. The skill scopes Check 6 to "the files on both sides of each **declared** interface." With the
   block declared `{}`, nothing was in scope by the method's own definition.
2. The skill requires Check 6 to run in an independent subagent, and in-process `Agent` dispatch is
   blocked on this machine by `agent_dispatch_gate.py` (FORE-206), whose named remedy is a peer
   session.

Reason 1 is now partly undermined by this report's own finding: an undeclared interface exists, so
there *are* files on both sides of a real interface that Check 6 would have something to say about.
It remains out of scope under the letter of the method, which keys on declared interfaces, and I am
not going to quietly widen a method's scope to cover a gap I just found in its input. The correct
sequence is: declare the interface, then run Check 6 against it.

---

## Gate criteria to next stage

The skill's criterion is zero FAIL rows outstanding across every interface `ARCHITECTURE.md`
declares. **Satisfied: zero FAIL rows.** One interface is declared, its Check 6 column is PASS, and
its Executed column is PARTIAL rather than FAIL.

This is a real pass now, not the hollow one this report originally refused. The distinction is
worth keeping on the record, because the two look identical from the gate's own vantage point. The
original `{}` block also produced zero FAIL rows, and it did so by omitting the only interface that
could have failed. What changed is not the count; it is that the count is now over a declaration
that matches the code.

**On PARTIAL not being FAIL.** The interface's code is not defective. Its one real defect, the
unhandled `fetch()` calls, was found by Check 6 and fixed. What remains unexecuted is a full data
flow, blocked by two environment conditions rather than by code: no warehouse database exists at
`atlas/warehouse/` in this worktree, and `TESSERA_DB` still defaults to the genericization
placeholder `/path/to/ticket-system/data/tessera.db`. Priya and Clint accepted and disclosed this
rather than building a seeded fixture at this stage.

**The accepted gap is tracked, with a trigger, and that is what keeps it from becoming permanent by
silent omission.** `DEVH-44` (open, severity 3) names the remedy condition and states the gap
reopens as a defect if a real warehouse plus a non-placeholder `TESSERA_DB` does not reach PASS.
Verified open in TESSERA. `DEVH-47` (open, severity 2) is relevant to that trigger: an
empty-but-clean warehouse currently trips a `TypeError` in `gates_by_prefix`, so the fixture DEVH-44
depends on would hit a second bug before it could demonstrate PASS. Those two are coupled, and
whoever picks up DEVH-44 should expect DEVH-47 first.

**Remaining disposition item, unclosed:** whether the three installed-artifact couplings
(`skills` → `bollard`, resolving to `~/.claude/hooks`) belong in a deployment section rather than
the `interfaces` block. `ARCHITECTURE.md` now carries a deployment-couplings paragraph for them;
whether that is the final shape is the architecture stage's call, not this report's.

## What I verified, and what I did not

**Verified by execution:** the component map and ownership of all 194 Python files via the
project's own resolver; zero `bollard` → `atlas`/`tessera` imports; the `monitoring` → `atlas`
import resolving to the in-tree facade; the producer's `QueryFacadeUnavailable` contract; all three
`skills` imports resolving outside the repository; the absence of vendored copies.

**Not verified:** any interface carrying real data end to end, for the environment reasons above.
Check 6, for the two reasons above. Whether the installed hooks at `~/.claude/hooks` are current
with `bollard/` — not in this stage's scope, and it would change the deployment-coupling question
if they have diverged.
