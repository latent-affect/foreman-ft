# Foreman v1.0 Quality Bar

What a gate criterion has to cost to fake, and the bar a project must clear to call itself v1.0.

---

## 1. The frame: evidence tiers

Every gate criterion is a claim. The question that matters is what it costs to fake.

- **E0 — existence.** A file is there, non-empty. Cost to fake: one byte.
- **E1 — binding.** The artifact carries a digest of the thing it claims about; the checker
  recomputes it. Cost to fake: possessing and reading the real artifact.
- **E2 — re-execution.** The artifact names a command and an expected result; the checker runs it
  and compares. Cost to fake: the claim must be true.
- **E3 — independent re-execution.** E2, performed by a process that did not produce the artifact,
  with its own captured tool-call record.

**This frame has real, acknowledged limits:**

1. **Binding proves the artifact is real, not that it's relevant.** A correctly-hashed artifact
   can still lie about what it measures — a genuinely, fully-hashable build artifact can still
   carry the wrong security posture. E1-or-higher does not close this by itself; §2 handles it
   per-gate by requiring a human/independent *reviewer*, not just a hash, wherever relevance
   judgment is unavoidable (A6), and §3's F-7 floor carries the same requirement at the
   aggregate-score level.
2. **E1 silently depends on E3's independence property against a motivated fabricator.** If the
   hash computation is an agent-asserted claim rather than a harness-captured, independently
   invoked tool call, a fabricator can print a plausible hex string without touching the real
   file — which is E0-level fakeable wearing an E1 label. **Every E1 criterion in this document
   requires the digest computation to be captured by the harness's own tool-call record, not
   asserted in prose by the reporting agent.** Where that is not yet wired for a given gate, treat
   the criterion as E1-*intended*, not yet E1-*achieved*.
3. **Continuous/statistical claims (coverage %, latency, hallucination rate) bind a *report* of a
   measurement, not the measurement's truth under nondeterminism.** Flagged, not solved, per axis
   in §4.
4. **Scope adequacy is an orthogonal axis this hierarchy doesn't touch at all.** A test can be
   faithfully, independently re-executed and still be scoped wrong — the gap is in what got
   tested, not whether the test that ran was faithfully executed. F-10 (proven live in production)
   is the closest floor to this, and it's a proxy, not a direct measure.

---

## 2. Per-gate quality bars

### 2.1 `architecture_gate.py` — QB-ARCH

Bar: `ARCHITECTURE.md` exists, `ARCHITECTURE-REVIEW.md` is `st_size > 0` (a single byte passes,
zero bytes denies).

| ID | Criterion | Tier |
|---|---|---|
| A1 | Both fenced blocks parse (`parse_component_map()` ≥1 component, `parse_interfaces_map()` a dict) | E1 |
| A2 | Every source file is inside a declared component **or** on a checked-in `.foreman/ungated.txt` with a one-line reason. Coverage ratio must be 1.0 | E1 |
| A3 | `ARCHITECTURE-REVIEW.md` contains `reviews-architecture-sha256: <64hex>` matching `sha256(ARCHITECTURE.md)`, computed by the checker (see frame limit 2) | E1 |
| A4 | Review records the reviewing session's transcript path and tool-call count; that transcript contains ≥1 Read of `ARCHITECTURE.md` | E3 |
| A5 | Verdict ∈ {SHIP, SHIP WITH FIXES, NO SHIP}; every FATAL finding closed or carries a ticket id | E1 |
| A6 | Every interface carries `trust: internal \| untrusted-input \| secret-bearing \| network` | E0→E1 |

Security placement is here, not later, because this is the only gate that sees the interface graph
before code exists — the cheapest point in the lifecycle to name the attack surface. By
ship-readiness the boundary is implicit in thousands of lines and nobody re-derives it.

### 2.2 `goals_freeze_gate.py` — QB-FREEZE

| ID | Criterion | Tier |
|---|---|---|
| F1 | `len(criteria) >= 3` and ≥1 states a negative (what must NOT happen) | E1 |
| F2 | Freeze hash covers `criteria[] + done_state + declared globs`, not `criteria[]` alone | E1 |
| F3 | Every criterion declares `verification_mode ∈ {executed, manual, static}` — declared, not inferred | E1 |
| F4 | `verifiable: false` must be usable and used; a component at 100% `true` with ≥1 `static` criterion is flagged | E1 |
| F5 | `architecture_hash_at_freeze` records `sha256(ARCHITECTURE.md)` at freeze time | E1 |
| F6 | Each component declares `capabilities: [network, subprocess, filesystem-write-outside-root, credential-read]` | E1 |

F4 exists because a boolean field that is always the same value carries zero bits of signal. A
field like this can look handled purely by being present, while being invisible in practice
because nothing ever uses the value it was meant to carry — flag non-use of the field itself as a
finding, not just its presence.

### 2.3 `preflight_blocking_gate.py` — QB-PREFLIGHT

Its asymmetry — `unregistered` stays silent, `ambiguous` and `unreachable` both DENY — is
deliberate: "confirmed clean" and "couldn't check" must never look the same.

| ID | Criterion |
|---|---|
| P1 | ASK (not DENY) when the project has non-closed, NULL-severity tickets older than 7 days. DENY would halt real work on day one for any project with an established backlog; ASK is the honest speed bump. |
| P2 | `ticket_ratified()`'s `agent_actor_names` must be a deny-by-default allowlist of human identities, not a blocklist of one string. |

### 2.4 `dependency_provenance_gate.py` — QB-DEPPROV

Manifest-driven; never resolves an artifact path as a filesystem object, which closes a
symlink-class bug structurally.

| ID | Criterion |
|---|---|
| D1 | Opt-in becomes conditional-mandatory: any project declaring `subprocess` capability (F6) or producing a build artifact must have `.foreman/artifact-provenance.json` |
| D2 | Every declared `producer` path exists |
| D3 | Manifest covers 100% of declared build artifacts |
| D4 | Any artifact whose `posture` claims redaction/anonymity must name a verifier that fails closed, proven by a committed negative test that corrupts the verification step and asserts the operation aborts |

D4 exists because a redaction/anonymity claim is a property of a specific artifact, and the
manifest is already path-keyed by artifact — a fingerprinting or metadata-leak defect in a build
output is exactly the kind of thing a redaction claim needs to be checked against, not asserted
about.

### 2.5 `ship_readiness_gate.py` — QB-SHIP

| ID | Criterion |
|---|---|
| S1 | Token-based `git push` detection, with one level of local script recursion, rather than a bare regex |
| S2 | `evidence` must be structured (`{mode: executed, command, exit_code}` re-executed by the gate, or `{mode: manual, observer, observed_at, observation}`), never free text |
| S3 | `no_open_s0_s1` additionally requires zero non-closed NULL-severity tickets — without it the check is a green light computed over an arbitrarily small slice of the real queue |
| S4 | Staleness check must fail **closed** when `git rev-parse` fails, not open |
| S5 | A sixth check, security review hash-bound to the current tree, is a strong recommendation but must not be added unilaterally — it changes what "ready to ship" means and needs explicit operator sign-off, not silent inclusion |

### 2.6 The frozen trio — bar defined, not yet built

- **`tier_triage_gate`**: bar is ≤1 spurious ASK per 20 real commits, measured against the
  project's real git history *before* wiring, not after.
- **`freeze_reentry_gate`**: `frozen.json` must record who froze and against which commit.
- **`ticket_status_gate`**: must not be satisfiable by a ticket the same session created and
  self-transitioned in one step, or it's self-attestation with extra ceremony.

These three are not required for v1.0. The only thing v1.0 needs from this family is
`compute_edge_set`/`compute_graph_hash` support in `component_coupling.py`, which is separable
from whether the trio itself ever ships.

---

## 3. The aggregate v1.0 score

**Two parts. Never summed together.**

### Part A — the floor. Boolean AND, no weights, no partial credit.

Safety and verification risks are multiplicative, not an average. A project at 100% on
documentation and 0% on real-bug-detection is broken, not an 80% release. A project clears every
floor or it does not reach v1.0.

| Floor | Predicate | Tier |
|---|---|---|
| F-1 Coverage | Every source file gated or explicitly excluded with a reason. Ratio = 1.0 | E1 |
| F-2 Binding | Review + every GOALS.json hash-bound to criteria + done_state + architecture | E1 |
| F-3 Non-vacuity | Zero components with empty criteria; ≥3 criteria per component incl. ≥1 negative | E1 |
| F-4 Executed evidence | Every `executed` criterion re-executes now and matches; every `manual` one carries observer/timestamp/observation | E2 |
| F-5 Independence | The process recording verification evidence is not the process that implemented it | E3 |
| F-6 Triage complete | Zero non-closed NULL-severity tickets; zero open S0/S1 | E1 |
| F-7 Security | Hash-bound review covering credential handling + every `untrusted-input` interface + every declared capability; zero unresolved S0/S1 | E1 |
| F-8 Execution safety | Declared capabilities match observed imports/calls; no undeclared subprocess/network/credential-read | E2 |
| F-9 Privacy fail-closed | Every redaction/anonymity claim has a verifier proven (by a committed negative test) to abort on failure | E2 |
| F-10 Proven live | Every registered gate has ≥1 `fire` record in the verdict ledger from a real project directory, this release | E1 |
| F-11 Remedy | Every DENY message names a path and a specific skill, verified by the gate's own test suite | E1 |
| F-12 Ticket-diff binding | A ticket's claimed files-touched is auto-checked against the real git diff, not pull-only (§9, T-1) | E1→E2 |
| F-13 Claim discipline | Zero tickets closed with no claim recorded (§9, T-2) | E1 |
| F-14 Comment evidence | Closing comments carry real, cross-checked evidence, not a bare status note (§9, T-3 — E2 only when paired with F-12) | E1/E2 |
| F-15 Pre-work freeze | Criteria frozen before work starts, per a written definition of "work starts" (§9, T-4 — gated on that definition existing) | E1 |
| F-16 Tessguard live | tessguard's git-level gate is wired, not just installed, on every repo this release touches (§9, T-5) | E1 |
| F-17 Commit-ticket binding | A commit only counts as real compliance evidence for a ticket if it names that ticket directly, or a TESSERA event within a tight window names the specific files the commit touches (§9, T-7) | E1 |

F-10 is the floor worth fighting for specifically: a gate that has never fired in production is an
untested gate wearing a passing test suite.

### Part B — the posture index. Six axes, aggregated by `min()`, never by mean.

Boolean AND solves the floor; a weighted average over the remainder reintroduces the exact
laundering problem one layer down. `min()` cannot launder a weak axis, and it degenerates cleanly
into boolean AND at threshold.

**v1.0 = (all seventeen floors true) AND (min of six axes ≥ 70).**

Treat 70 as a starting configuration, not a fixed constant — recalibrate it once real projects
have actually been scored end to end against this bar, rather than defending the number in the
abstract.

---

## 4. The six axes

| Axis | Owner gate(s) | Floor | Note |
|---|---|---|---|
| Security | Architecture (A6, boundary) → Ship (S5, review) | F-7 | Split across two gates deliberately — the boundary is a design fact, the review is a code fact; each gate can only see one. |
| Privacy | Dependency-provenance (D4) | F-9 | The checkable artifact is the negative test proving the fail-closed path actually aborts, not a comment claiming it does. |
| Code execution safety | Freeze (F6, declared capabilities) | F-8 | A component with no criterion stating required behavior on malformed input has not specified the failure this axis is supposed to catch. |
| Testing rigor | — | F-3, F-4 | Not "tests exist" or "tests pass" — tests assert, and the assertions re-execute now. `criteria: []` and frozen is a gate-open state this axis exists to catch. |
| Independent review of testing | — | F-5 | The reviewer must be a process that didn't write the code; checkable via a transcript reference that isn't the implementing session's own. Structurally weaker than F-2 (see frame limit 2) — a session id in JSON is still a claim, not a hash. |
| Debugging by design | — | F-10, + one per-component criterion: every failure path emits a distinguishable `rule_id` | A `rule_id` taxonomy that distinguishes e.g. `:no-manifest` from `:manifest-malformed` is the difference between "never opted in" and "opted in and broken" — recording that distinction is what lets gate crashes be counted instead of silently merged into "nothing happened." |

---

## 5. Design principles

These are the resolved answers behind the bar above, stated as rules rather than as a log of how
each was decided.

- **Every DENY must name an executable remedy, and the harness must verify that it does** — not
  just assert it in the gate's own code comments. (F-11.)
- **An artifact claim requires E1 binding with process independence.** A session id recorded
  alongside a claim is still attestation, not cryptographic proof — treat it as weaker evidence
  than a harness-captured hash (F-5 vs. F-2).
- **A verification-mode field that is always the same value carries no signal.** Require the field
  to be genuinely exercised in both directions, and flag non-use itself as a finding, not just its
  presence (F4).
- **An escaped-defect rate cannot be computed without a `found_by` field** (`found_by ∈
  {automated-gate, review, human-use, production}`) on the ticket schema. Without it, this axis has
  no data source at all — name that as a real, standing gap rather than silently omitting the axis.
- **A version/release label must be enforced by a hook, not by a string a session can omit.** A
  release gate should read a hash-bound state file and fail **closed** when that file is missing or
  stale, matching the direction every other structural claim in this document takes.
- **Any frozen evidence artifact must invalidate itself against a moving commit hash.** A charter,
  score file, or freeze record that silently keeps vouching for code that has since changed is
  worse than no record at all.
- **Structural claims fail closed; judgment claims fail to ASK, not DENY.** A binding, a hash, or a
  re-execution result that cannot be verified does not exist, full stop. A triage or provenance
  decision that requires human judgment should slow the session down with a question, not build an
  unconditional wall a rushed session will simply route around.
- **Design every criterion for a future agent picking the project up cold**, not only for whoever
  is running the project today. That is the stricter of the two audiences and subsumes the other —
  if a criterion would only make sense to the current operator, it is under-specified.
- **A local git hook is the fast warning; a tracked PreToolUse gate is the real enforcement.**
  `core.hooksPath` does not travel with a clone, so anything wired only through git hooks silently
  stops applying the moment someone clones the repo. Keep a git hook (pre-commit/pre-push) as a
  cheap, early, local check, and put the check that must survive a fresh clone inside a hard gate
  wired through the tracked PreToolUse settings file the install script writes.

---

## 6. Known open scoping tensions

- **The 70 threshold** (§3) is a starting configuration, not a derived constant — recalibrate
  after real projects have been scored against it.
- **F-6/F-7 strictness for a solo-operator project** is a real, unresolved tension: "zero
  unresolved S0/S1 security findings" against a project with a large untriaged backlog can mean a
  long triage pass before anything reaches v1.0. Scoped as: for a solo-operator project, F-6/F-7
  apply to the components declared as part of *this specific release*, not the whole project's
  backlog — an unscoped floor nobody can clear gets waived in practice, and a waived floor is worse
  than a smaller, honestly-scoped one.
- **The escaped-defect axis has no data source at all** until the ticket schema carries `found_by`
  — name this as a real, standing gap, not folded into any score until it exists.
- **Scope adequacy** (frame limit 4, §1) is not measured by anything in this document. F-10 is the
  closest proxy, and it is a proxy, not the thing itself.

---

## 7. What this does not claim

The opening question is what it costs to fake a claim, not whether faking is impossible. Cost goes
up. Victory over deception is not declared.

E3 means a process ran real code against real bytes. A model cannot talk its way past that the way
it can invent a hex string at E1. E3 does not hold if the agent can edit the verifier, the git
history, or the ledger-writing code — frame limit 2 already covers this, and F-5 already treats a
session id in JSON as attestation, not a signature.

Against an agent optimizing for "looks done": F-10 exists to catch a gate that has never caught
anything real. One context implements; a separate context reviews.

Against an agent with write access to the verification code: not closed. Nothing at E3 closes it.
The next tier is institutional — a signing key the verifier cannot read, or a reviewer with
something real to lose — and is not claimed here.

No dedicated red-team pass has been run against these gates.

---

## 8. QB-TESSERA — ticket-trail floors

F-1 through F-11 check the code and the review process. None of them ask whether the ticket that
authorized the work was real before work started, claimed on close, evidenced in its comments, and
checked against the actual diff. A change can clear every one of those eleven floors with a hollow
ticket trail — the floors below close that gap.

**F-12 (T-1) — Ticket-diff binding.** A ticket's claimed files-touched must be automatically
checked against the real git diff on every close, not merely checkable on request. A pull-only
mechanism that a caller has to remember to invoke is a real, correct mechanism that automation
never actually calls, which is functionally the same as not having it.

**F-13 (T-2) — Claim discipline.** No ticket may close with zero claims recorded. A closed ticket
with no claim is a status change with no evidence trail behind it.

**F-14 (T-3) — Comment evidence.** Closing comments must carry real, cross-checked evidence, not a
bare status note. A non-empty evidence field alone (e.g. a code snippet) only proves something was
pasted, not that it is accurate or relevant — real rigor here requires pairing this floor with
F-12, so the evidence is checked against the actual diff it claims to represent, not merely
present.

**F-15 (T-4) — Pre-work freeze.** Criteria must be frozen before work starts, against a single,
written definition of "work starts," decided and recorded in advance. Different reasonable
definitions of "work starts" (first status change, first claim, first event of any kind) can
produce wildly different compliance numbers for the same ticket history — pick one, write it down,
and score against that, rather than picking whichever definition produces the most favorable
number after the fact.

**F-16 (T-5) — Tessguard wired.** tessguard's git-level gate must be wired via `core.hooksPath` on
every repository the release touches, not merely installed as a mechanism somewhere. A hook can be
architecturally correct and still be entirely absent from the one repo that needed it, which is a
different failure than the hook being broken.

**F-17 (T-7) — Commit-ticket binding.** A commit only counts as real compliance evidence for a
ticket if the commit message names that ticket directly, or a TESSERA comment/transition on that
ticket within a tight window (minutes, not hours) names the specific file paths the commit's diff
touches. Mere temporal proximity to *any* project activity does not count — a check that only asks
"did some event happen for this project recently" grants coverage that falls exactly as
cross-project activity scales up, which is the opposite of what a hard gate should do under more
real usage. Either form (commit message or timed event) must be checkable against real artifacts
on disk — the commit message, the event's own recorded content — never against a self-report that
a rule was followed.

**These are adopted as new floors F-12 through F-17**, joining F-1 through F-11 under the same
boolean-AND aggregation used throughout this document: a hollow ticket trail fails the whole bar
exactly like a hollow test suite does, for the same multiplicative-bottleneck reason §3 states for
the rest of the floor.
