# PRD delta: R14 / R15, the layered command guard chain

Supersedes `PRD.md`'s R14/R15 entry, which reads **BLOCKED** and must now be read as historical.
Nothing else in `PRD.md` is changed by this document.

**Authored by:** Session A (priya-desai lens), design-and-scope stage, 2026-09-02.
**Reads against:** `PRD.md` R13/R14/R15 (lines 345 to 394), `ARCHITECTURE.md` lines 274 to 335,
the real `bollard/` code, and TESSERA DEVH-61 through DEVH-64.
**Downstream artifact:** `bollard/GOALS.json`, criteria C9 through C16.

---

## 1. Why the block lifts

`PRD.md` R14 blocked on one question: was `layered_command_guard.py` ever written? It named two
outcomes needing different requirements, and `ARCHITECTURE.md` set the default if the question
went unanswered by architecture freeze: treat it as never written, size R14/R15 as
build-from-nothing at design-and-scope, and re-derive every claim resting only on the brief's
narration.

That default has now fired, and the re-derivation actually happened. It was not a paper exercise.
DEVH-61 through DEVH-64 ran as four sequenced passes in separate contexts, closing with an
independent falsification pass that reproduced both prior toy models from scratch rather than
re-reading them. What follows treats those four tickets as the input record and does not re-argue
them.

Settled by that chain, cited rather than re-derived here:

| Claim | Source | Confidence |
|---|---|---|
| "Capability removal first" is a **compositional** constraint on which layer the guarantee rests on, not a runtime ordering claim | DEVH-61, read from `PRD.md`'s own R13 verification clause plus the working `deny_keychain.sb` / `neutralize_credentials.sh` | High |
| Two-layer split: `{OSSandboxGuard, AllowlistGuard}` carry the guarantee; `{SemanticResolutionGuard, PatternFeedGuard}` are additive only | DEVH-62, reconfirmed DEVH-64 | High |
| `hook_common.run()` fails open on a guard's own crash, and a guard-local `try/except -> deny` closes that without touching the shared harness | DEVH-62 and DEVH-64, two independent scripts against the real module | High |
| A Seatbelt deny profile fails the wrapped process at execution time with a nonzero exit and an errno-shaped stderr, isolated from path confounds by a no-deny negative control | DEVH-63, reproduced fresh in DEVH-64 | High |
| `deny_capability.sb` and `capability_scope.sh` do not exist; `OSSandboxGuard` is architected by extension from a proven mechanism, not from a proven policy | DEVH-61, `ARCHITECTURE.md:280` | Medium on the transfer |

---

## 2. What this pass measured, and what it changes

DEVH-64 closed by handing design-and-scope one empirical target rather than an answer: does a
PreToolUse hook's `updatedInput` field actually rewrite the executed command on this installed
harness, and does a `deny` from one hook still win when another hook in the same round returns
`allow` plus a rewrite? It was doc-verified at Medium-High and explicitly not executed, because
testing it would have meant mutating the shared, gitignored `.claude/settings.json` that a
concurrent peer session might be using.

That constraint is real but it is not binding. A hook configuration does not have to be the shared
one. Eight cases were run against the live installed harness (Claude Code 2.1.258) in five
throwaway project directories, each with its own `.claude/settings.json`, touching no shared
config. The observable is a marker file on disk, not any model's narration of what happened:
`ORIGINAL` means the proposed command executed unchanged, `REWRITTEN` means a hook replaced it
before execution, absent means the call never ran.

| Case | Hooks on the Bash matcher, in order | Marker | Reads as |
|---|---|---|---|
| caseControl | none | absent | **Invalid control, discarded.** Blocked by the permission layer (untrusted workspace ignored the `permissions.allow` entry), not by any hook. Proves nothing either way. |
| caseAllowOnly | allow | `ORIGINAL` | A hook's bare `allow` executes the proposed command unchanged. This is the isolating control the invalid one failed to be. |
| caseRewriteOnly | allow + `updatedInput` | `REWRITTEN` | **`updatedInput` is honored live.** The only source of the string `REWRITTEN` is the hook. |
| caseDenyOnly | deny | absent | Deny blocks. |
| caseRewriteThenDeny | allow+rewrite, deny | absent | **Deny beats allow-plus-rewrite.** The rewrite did not land. |
| caseDenyThenRewrite | deny, allow+rewrite | absent | Same, registration order reversed. |
| caseAllowThenDeny | allow, deny | absent | **Deny beats an explicit `allow`.** This is the deny-vs-explicit-allow round DEVH-64 named as untested. |
| caseDenyThenAllow | deny, allow | absent | Same, order reversed. |

Harness: `/private/tmp/claude-501/-Users-m5-dev-dev-harness-run2/235bc8ca-d283-4f8b-bb9d-a7af7b2a9ad9/scratchpad/updinput-probe/`
(`runProbe.py`, `runProbe2.py`, `hooks/`, results in `probeResults.json` and `probeResults2.json`).
Ephemeral. Reproduce by re-running, do not cite the directory as durable evidence.

**Confidence: High.** Live execution against the installed harness, deterministic on-disk
observable, positive and isolating controls both present. n=1 per cell across eight cells, one
harness version. Basis is GT-validated, not consistency-only. The one thing that would raise it
further is a second harness version, which is not available today.

Three consequences, each of which changes a decision rather than confirming one:

**2.1 The merge lattice holds for the case that actually matters.** DEVH-64 confirmed
deny-beats-silent live and flagged deny-versus-explicit-allow as the untested combination that
the cross-cutting independence constraint depends on. It is now tested, in both registration
orders, including the allow-plus-rewrite variant. Deny wins. The correctness half of the runtime
ordering question DEVH-61 left open is therefore **closed**: registration order among Layer A
guards cannot change the outcome. What remains open is latency ordering against N2's budget, which
is a performance question and is deferred below with a trigger.

**2.2 `OSSandboxGuard` does not need a new invocation mechanism.** DEVH-63 framed the seam as
"genuinely open, mechanism unbuilt," resting on an unstated assumption that a PreToolUse hook can
only allow, deny, or ask. DEVH-64 corrected that from documentation. It is now confirmed by
execution: a PreToolUse hook can rewrite `tool_input.command` to
`sandbox-exec -f deny_capability.sb <original>` and the rewritten command is what runs. Same
registration point as Layer A, same input contract, no new mechanism.

**2.3 A new cost, not previously identified anywhere in the chain.** `caseAllowOnly` ran a command
that `caseControl` shows the workspace's own permission rules would otherwise have blocked. A
hook's `allow` bypasses the permission layer. Since carrying `updatedInput` requires returning
`allow` or `ask`, an `OSSandboxGuard` that wraps every Bash call would auto-approve every Bash
call, and the operator would stop seeing permission prompts entirely. That is a control removed in
exchange for a control added, and it would look like pure hardening from the outside. It is the
single most important thing this pass found, and section 3 resolves it.

---

## 3. Resolved design fork: the `OSSandboxGuard` invocation seam

Three candidates, scored against the measurement above rather than picked on shape.

| Option | Permission layer | Friction | Verdict |
|---|---|---|---|
| A. Rewrite hook returning `allow` on every Bash call | Destroyed. Every prompt suppressed. | None | **Rejected.** Trades a working control for a new one and hides the trade. |
| B. Rewrite hook returning `ask` on every Bash call | Intact | A prompt on every single Bash call | **Rejected.** Unusable, and an unusable guard is a disabled guard, which is this project's own documented failure mode. |
| C. Rewrite hook scoped to a named capability class: silent on everything outside it, `allow` plus `updatedInput` inside it | Intact outside the class. Deliberately bypassed inside it, where the sandbox is the substituted control. | Only on the scoped class | **Selected.** |

C is chosen because the bypass is bounded to exactly the traffic that gets a stronger control in
return: inside the class the command runs, but the kernel denies the named capability regardless of
how the command string was obfuscated, which is the R13 guarantee. Outside the class nothing
changes. The scope of the class is therefore load-bearing and must be declared, not inferred, which
C15 turns into a checkable criterion.

This does not collapse the two-layer split. `OSSandboxGuard` still constrains execution after Layer
A has decided, and its failure shape is still a nonzero exit with errno-shaped stderr rather than a
hook decision, exactly as DEVH-63 established. What changes is only where it is registered.

---

## 4. New finding: the freeze this stage produces would not have gated the files it covers

**Status: found here, fixed by DEVH-72, re-measured green.** The finding is kept in full below
because C9 exists to keep it closed, and a reader needs to know what C9 is guarding against. The
resolution is recorded at the end of this section.

Checked with the real predicate, not by reading the map. `component_coupling.component_of` and
`is_implementation_path` were run against `ARCHITECTURE.md`'s live component block, with positive
controls included so a probe that returned `None` for everything could not read as a finding.

```
POSITIVE CONTROL   bollard/guard_destructive.py          component_of='bollard'   is_implementation_path=True
POSITIVE CONTROL   bollard/hook_common.py                component_of='bollard'   is_implementation_path=True
POSITIVE CONTROL   bollard/lib/deny_keychain.sb          component_of='bollard'   is_implementation_path=True
POSITIVE CONTROL   bollard/test_guard_destructive.py     component_of=None        is_implementation_path=False
R14/R15 new        bollard/guard_allowlist.py            component_of=None        is_implementation_path=False
R14/R15 new        bollard/guard_semantic_resolution.py  component_of=None        is_implementation_path=False
R14/R15 new        bollard/guard_pattern_feed.py         component_of=None        is_implementation_path=False
R14/R15 new        bollard/guard_os_sandbox.py           component_of=None        is_implementation_path=False
R14/R15 new        bollard/lib/deny_capability.sb        component_of='bollard'   is_implementation_path=True
R14/R15 new        bollard/lib/capability_scope.sh       component_of='bollard'   is_implementation_path=True
```

Two things fall out of that, one expected and one not.

Expected: the `bollard` component declares its guards as bare per-file globs
(`bollard/guard_destructive.py` and two siblings). Four new `guard_*.py` files match none of them.
`component_of` returns `None`, `is_implementation_path` returns `False`, and `goals_freeze_gate`
returns silently on the `not-implementation-path` branch. **Freezing criteria in
`bollard/GOALS.json` would not gate a single one of the four guard files this work creates.** The
ceremony would complete and enforce nothing, which is the exact failure shape this project was
built to detect.

Not expected: the fourth positive control failed. `bollard/test_guard_destructive.py` is an
existing, shipped file and it also resolves to `None`. The `bollard/test_*.py` glob has a
mid-string wildcard, and `_prefix_of` only strips trailing stars, so the prefix it produces is the
literal string `bollard/test_*.py`, which nothing starts with. That entry has been inert since it
was written. It is the same class of defect `ARCHITECTURE.md` lines 194 to 205 already found and
fixed for the missing `bollard/` directory prefix, surviving in a second form the fix did not
cover. It is disclosed here rather than fixed here, because the component map is an architecture
artifact and this is the design-and-scope stage.

**Confidence: High.** Run against the shipped parser with discriminating controls. Three of four
positive controls resolved correctly, so the probe is not simply returning `None` for everything.

**Resolution.** DEVH-72 replaced both broken forms with prefix globs, `bollard/guard_` and
`bollard/test_`, and found that all nine existing `bollard/test_*.py` files were inert, not the
one this probe happened to sample. Re-measured here with the same probe after that change: all
four positive controls and every path in the table above resolve to `bollard` with
`is_implementation_path` True. Verified in this session against the amended
`ARCHITECTURE.md:161`, not taken on report. Section 7.1's precondition is therefore met, and C9
turns from an open blocker into a regression guard: a future glob edit that re-breaks resolution
fails C9 loudly rather than silently un-gating the guards.

---

## 5. Requirements

**R14a. `OSSandboxGuard` denies the `su""do` class at the OS level, with both detection guards
disabled.** Build requirement, sized from nothing. Mechanism extends `deny_keychain.sb` and
`neutralize_credentials.sh` to `deny_capability.sb` and `capability_scope.sh`, registered per
option C above. Verification method is the one `deny_keychain.sb`'s own header already documents:
a trap case and a no-deny negative control, so a failure is attributable to the deny rule rather
than to a path or permissions confound.

**R15a. `SemanticResolutionGuard` folds `chr(a)+chr(b)` construction, verified by the chr-built
destructive case being caught by that guard alone with the other three disabled.** Build
requirement, sized from nothing. Its resolution path must classify without evaluating: a guard that
executes attacker-controlled input to discover what it produces is a worse vulnerability than the
one it closes.

**R14b. `AllowlistGuard` fails closed on its own internal error.** It carries half the guarantee,
so `hook_common.run()`'s repo-wide fail-open default is wrong for it specifically. The deviation is
guard-local and deliberate, and the shared harness is not modified. DEVH-62 and DEVH-64 both
toy-modeled the mitigation working.

**R15b. `PatternFeedGuard`'s relationship to `guard_destructive.py` is decided explicitly.** Either
it subsumes that pattern list and the old one is retired, or the two sets are disjoint by
construction. Two competing matchers for the same shape, neither owning it, is how a pattern
retired from one list keeps firing from the other.

**R14c. The component map resolves every new file before any of them is written.** Precondition,
not a deliverable of this stage. Met by DEVH-72 and re-measured in this session, per section 4.
C9 holds it met.

Acceptance for all of the above is `bollard/GOALS.json` criteria C9 through C16 and their failure
criteria F8 through F14. The criteria are the contract; this section is the reason they exist.

---

## 6. Out of scope, with the reason

- Anomaly detection and the LLM judge, R13's two other named detection candidates. Additive to an
  additive layer.
- Changing `hook_common.py`'s shared fail-open default. A real question, raised by DEVH-62, owned
  by whoever owns that module next. R14b works around it locally and does not pre-empt it.
- The literal runtime ordering of guards for latency. Correctness no longer depends on it (2.1).
- Any TESSERA or ATLAS change. Different requirement family.
- Fixing the component map. Architecture stage, section 7.

## 7. Open items, each with a trigger

**7.1 The component-map amendment. CLOSED by DEVH-72**, verified in this session rather than
accepted on report. `bollard/guard_` and `bollard/test_` replaced the two forms that resolved
nothing. Standing trigger, since the defect class has now recurred twice in the same block: any
future edit to the `bollard` glob list re-runs C9 before it lands. **If C9 is ever skipped on such
an edit:** the guards can be silently un-gated again with no visible symptom, which is how this
survived the first fix.

**7.2 `ARCHITECTURE.md`'s R14/R15 section.** DEVH-72 recorded its component-map reasoning inline in
`ARCHITECTURE.md`, so the file has moved; whether the R14/R15 prose entry itself still reads BLOCKED
with no interface space reserved has not been re-read by this session and is not claimed either way
here. The decomposition and interface contract live in DEVH-61 through DEVH-64 and in this document.
**Trigger:** the first implementation ticket for any of the four guards. **If it is not done:** the
next reader re-derives a design that already exists, which is the cost this whole recycle was run to
avoid.

**7.3 Harness version dependence.** Everything in section 2 was measured on Claude Code 2.1.258.
`updatedInput` is a documented contract, so it should be stable, but option C's viability rests on
it. **Trigger:** any harness upgrade before R14a lands. **If the behavior regresses:** option C is
void and the seam reverts to DEVH-63's original framing of an external wrapper, which is a real
re-scope rather than a patch.

**7.4 N2 latency budget for the four guards.** Never measured. `OSSandboxGuard`'s
execution-wrapping shape does not obviously inherit the sub-millisecond class that applies to a
metadata-only PreToolUse hook. **Trigger:** implementation of the second guard, where the cost
becomes additive and measurable rather than hypothetical.

**7.5 The capability-scoping transfer risk.** R13 rates Medium on how far a proven credential-denial
mechanism carries to general command-capability scoping. The mechanism is the same, the policy is
not. C14 is the check that closes it; until C14 records MET, R14a's confidence stays Medium.

---

## 8. What this document does not claim

It does not claim the guard chain works. Nothing is built. Every High confidence above attaches to
a property of the existing harness or the existing `bollard` code, measured this pass or in DEVH-61
through DEVH-64, not to the unbuilt guards.

It does not re-verify R13's ordering premise beyond what DEVH-61 established. DEVH-61 separated the
compositional claim, which is grounded, from the temporal claim, which was open. Section 2.1 closes
the temporal claim for correctness by measurement. Neither pass re-derived the original six-way-tie
scoring, which `PRD.md` section 0.1 already discards as unlocatable.

The criteria in `bollard/GOALS.json` were authored by this session. Per `foreman:design-and-scope`,
they need an adversarial review by a context that did not write them before the freeze is treated
as reviewed. Whether that happened is recorded in the amendment entry alongside the freeze, not
assumed from the freeze existing.
