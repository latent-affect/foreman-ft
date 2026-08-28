---
name: ship-readiness
description: Writes .foreman/SHIP-CHARTER.json — the five-check evidence record ship_readiness_gate.py checks before a git push. Use before pushing to a public remote, or any time the ship-readiness gate denies a push and names this skill as the remedy. Distinct from foreman:architecture/design-and-scope, which gate BUILDING, not SHIPPING — a project can have every criteria frozen and met and still not be in a state worth publishing.
---

# foreman:ship-readiness

Produces the one artifact `ship_readiness_gate.py` checks for: `.foreman/SHIP-CHARTER.json`.
The operator's own framing, the reason this exists: professional practice ships against a
severity/occurrence bar, not zero bugs — known S0/S1 fixed, everything else triaged and
disclosed. `architecture_gate.py`/`goals_freeze_gate.py` already gate the separate question of
whether a component's *design* is frozen and met; neither one asks whether the resulting code
is in a state that should actually be pushed. This skill is that second, later check.

## Trigger when

- About to push to a public remote (or any remote, if the project has opted into stricter
  gating) and the project has `.foreman/ship-readiness-gate-enabled` present.
- `ship_readiness_gate.py` denied a push and named this skill as the remedy — read its denial
  reason first: a missing charter, a failing check, and a stale charter (commit hash mismatch)
  are three different problems with three different fixes, not one generic "run this again."

## Output: `.foreman/SHIP-CHARTER.json`

```json
{
  "generated_at": "2026-08-20T18:04:00Z",
  "commit_hash": "<git rev-parse HEAD, taken AFTER every check below has run>",
  "checks": {
    "no_open_s0_s1":        {"pass": true, "evidence": "..."},
    "test_suite_green":     {"pass": true, "evidence": "..."},
    "stretch_test":         {"pass": true, "evidence": "..."},
    "limitations_disclosed":{"pass": true, "evidence": "..."},
    "dogfood_pass":         {"pass": true, "evidence": "..."}
  }
}
```

`commit_hash` is what makes the charter perishable rather than a permanent rubber stamp. The
gate accepts two states: the hash equals `git rev-parse HEAD` (working-tree stamp),
or HEAD is the child of that hash and the only path in that commit is `.foreman/SHIP-CHARTER.json`.
Stamp, then commit only the charter. Do not restamp after that commit. Any other commit since
the recorded hash invalidates the charter.

`evidence` is a real, checkable string per check, not a restatement of `pass: true` — an operator
or a later session reading this file cold must be able to tell WHAT was verified, not just THAT
something was. `pass: false` is a legitimate charter state — a charter that honestly fails a
check is more useful than one that was never generated, and the gate will deny either way with a
different, more specific reason.

### The five checks

1. **`no_open_s0_s1`** — query TESSERA for the current project's registered prefix (via
   `tessera_resolver.resolve`, same mechanism `ship_readiness_gate.py` itself and
   `preflight_blocking_gate.py` already use — don't hand-roll project resolution a third way),
   then `tessera list --status open --project <prefix>` filtered to `severity` 0 or 1. Zero
   matching tickets passes. Evidence: the query run and its result count, e.g. `"0 open S0/S1
   tickets in FORE as of 2026-08-20T18:00Z (tessera list --status open --project FORE, filtered
   severity<=1)"`.

2. **`test_suite_green`** — actually run the project's standard automated test suite (whatever
   command that project already uses — check its own docs/CI config rather than assuming one)
   and record the REAL run: command, exit code, pass/fail counts, timestamp. `evidence` must
   describe an execution that just happened, not a claim that it usually passes. A stale or
   remembered "tests pass" is not this check — this project's own standing rule elsewhere is
   evidence over assertion, and a ship charter is exactly the kind of artifact that rule exists
   to protect.

3. **`stretch_test`** — a pass beyond the standard suite: adversarial input, an edge case the
   standard suite doesn't cover, or real-usage-shaped exercise of the feature. Must be TIED to
   and VERSIONED alongside the test suite — a script or test file committed in the repo, not a
   one-off manual check that leaves no trace, so it doesn't silently rot the next time this
   skill runs. Evidence: the file/command and what it actually found.

4. **`limitations_disclosed`** — known rough edges and limitations are written somewhere a user
   would actually see them (README or equivalent), not silently shipped. Evidence: the file and
   section. If there are genuinely no known limitations, say that explicitly and why that's
   credible (e.g. "no known limitations; see STRETCH_TEST.md for what was actually tried") —
   an empty limitations section with no explanation reads as unchecked, not as clean.

5. **`dogfood_pass`** — at least one real usage pass by a human, documented: what was tried,
   what happened, distinct from code review. This is deliberately not satisfiable by more review
   — the whole reason it's a separate check is that review and actual use structurally catch
   different bug classes (the trim-slider UX bug that was code-review-clean and only surfaced
   when the operator actually used the feature is the standing example for why). Evidence: who,
   when, what was exercised, what if anything was found.

## Gate criteria to next stage

`.foreman/SHIP-CHARTER.json` exists, all five `checks.*.pass` are `true`, and `commit_hash`
matches HEAD or is the parent of a charter-only HEAD commit.
`ship_readiness_gate.py` enforces all three mechanically — existence, the five booleans, and the
hash match. It does NOT re-verify that any `evidence` string is actually true, same disclosed
limitation as `architecture_gate.py` checking `ARCHITECTURE-REVIEW.md`'s mere existence, not its
content: the mechanical check is "did this stage run and record its claims," not "were the
claims correct." Don't write a charter with fabricated evidence to unblock a push faster — the
gate exists specifically to make that a deliberate act, not an accident of missing evidence.

## Guardrails

- **Opt-in, not automatic.** `ship_readiness_gate.py` only fires if
  `.foreman/ship-readiness-gate-enabled` exists at the project root — same precedent as
  `ticket_status_gate`: this is a materially bigger workflow commitment than the
  mid-build gates, so it must be chosen per project, not inherited the moment this skill exists.
- **`commit_hash` is taken AFTER the checks run, not before.** Stamp HEAD, then commit only
  the charter file. The gate accepts that charter-only child. Do not restamp after the commit.
- **A `pass: false` entry is honest, not a bug in this skill.** Fix the underlying problem and
  re-run the check that failed — don't edit the JSON to say `true`.
- **Re-running after a push was denied for staleness** only needs the checks that could have
  changed since the last charter — but when in doubt, re-run all five; a stale-and-also-wrong
  charter is worse than a slightly redundant re-check.

## Pairing

```
(implementation frozen and merged) → foreman:ship-readiness → git push
```
