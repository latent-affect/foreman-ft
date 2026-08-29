---
name: adversarial-code-review
description: Adversarially reviews code for defects that do not raise. Use when the user asks for a review before merging, asks whether code is safe to ship, says a model wrote it, asks what was silently got wrong, or asks for a red-team of a diff.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[adversarial-code-review · loaded · C9263FAD]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary elsewhere in this practice — a clean result is only
> trustworthy if the canary came back.


# Adversarial Code Review

A normal review asks "does this look right." This asks "what would have to be true for this
code to run clean, return a plausible answer, and still be lying to me." Those are different
questions, and the gap between them is where the expensive bugs live: code that compiles, exits
0, and returns a confident, wrong result because it silently read the wrong field, silently
caught an error, or silently fell back to a stand-in value. None of that shows up if the review
just asks whether the code "looks reasonable." It shows up when someone goes looking for it on
purpose.

This skill is a fixed six-check gate, not an open-ended read. Two of the checks are close to
mechanical (a call either resolves to something real or it doesn't; a catch block either signals
failure or it doesn't) and are backed by `scripts/scan.py`. Three require actual adversarial
reasoning (data-proxy disclosure is part mechanical, part judgment; security and optimization are
mostly judgment). Say which kind each finding is -- don't dress a judgment call up as a mechanical
fact, and don't wave off a mechanical fact as if it needed judgment.

**Validation status.** Run for real against a Go + vanilla-JS project and a shell + Python
project. Found genuine FATAL and SERIOUS bugs on each, and got two things wrong that later
passes caught: a severity mislabel on a deprecated-but-functional CLI form, and a fix direction
pointing at a REST API for a capability the CLI actually had. Both errors are now guardrails
below. `corpus/` holds a seeded-bug corpus with a manifest of planted defects; `run_corpus.py`
measures the mechanical tier's false-negative rate directly. Current: **7/7 caught, 0 false
positives** -- but read that number carefully. Its first run scored 43% false-negative, of which
two of three "misses" were an off-by-one artifact in the corpus itself and only one was a real
gap. The corpus needed calibrating before its number meant anything, which is worth remembering
before quoting it. The reasoning tier (checks 3-6 defects that no regex can reach) is
deliberately unscored by the runner and stands as a checklist for the model-driven pass.

This skill was also turned on its own tooling, which had never been reviewed. `scan.py` was
failing checks 1 and 2 in its own codebase: three silent `except Exception: continue` handlers
that skipped unreadable files and still reported "0 candidates" (confidently clean while
silently incomplete -- this skill's exact target, inside its own detector), and a substring
match that read `logout()` as logging, `flagship_reset()` as flagging, and `systemd_restart()`
as signaling, missing 3 of 4 planted silent handlers. Both fixed. The lesson generalizes: the
tool a review leans on is part of the reviewed system, and it is the last thing anyone thinks to
check.

**Watch the tooling asymmetry.** Checks 1 and 2 have script backing and therefore *feel* more
rigorous; checks 3-6 are where the most damaging findings have actually come from. The risk is
spending attention where the tool makes it cheap rather than where the bugs are. If a run's
findings are concentrated in the two scripted checks, that's a signal the other four were
rushed, not a signal the code is clean there.

## Environment note -- read first

**On Claude Code:** use subagents for independence, especially on checks 1 and 4. The main risk
isn't missing a pattern, it's a model reviewing code it (or a very similar model) just wrote and
quietly trusting its own assumptions about what a function does or what data actually flows
through a path. A fresh subagent that never saw the generation doesn't inherit that trust. You can
also actually execute the code, run the real test suite, run a type checker for the half of check 1
the script can't reach, and wire the fast mechanical tier into a standing `PostToolUse` hook --
`scripts/posttooluse_hook.py` is ready to drop into `.claude/hooks/` and fires checks 1-3 on every
Write/Edit/MultiEdit, feeding findings back into the same session's context instead of waiting for
a separate review pass. See "Wiring a live hook" below. It only covers the mechanical tier -- checks
4-5 and the judgment-heavy sub-patterns still need a full run before merge/handoff. Prefer real
local tooling over any script or web-doc-trace here whenever it's actually available -- `errcheck`
or `go vet` over the regex heuristic, an installed CLI's own `--help`/dry-run over fetched docs, a
real type checker over the undefined-calls scanner. `scan.py` and doc-tracing exist for when the
real tool isn't available, not as the first choice when it is.

**Second round -- confirm blockers, then muse the report itself.** One subagent-per-check round
isn't the end of the process for anything that actually drives the verdict. Two things, after the
six checks produce a draft report:

1. **Re-confirm every FATAL / DO-NOT-SHIP finding with a second, independent subagent** before
   trusting it -- fresh, given only the specific claim (not the first subagent's reasoning), told to
   verify it against the real thing (execute it, fetch current docs, run the actual tool) rather than
   evaluate the first subagent's argument. This is not generic caution: it's a direct fix for a
   confirmed miss. A review here once called a CLI's noun-verb-inverted command form FATAL and
   effectively nonexistent; the vendor's own README listed it under "legacy commands, still supported
   but deprecated" one section down from what got fetched. A second independent pass, given only "is
   `<command>` still functional" rather than the first pass's framing, is cheap insurance against
   exactly that -- confidently wrong in a way that would have driven a real rewrite. Checks that came
   back clean, or that only produced SERIOUS/MINOR findings, don't need this -- it's specifically for
   what the verdict is about to block on.
2. **Run `muse` as its own subagent against the finished report**, not the code -- blind to the five
   checks' reasoning, given only the report and asked what a fresh, skeptical reader would want
   verified before acting on it. This is muse's actual designed use (a plan that's about to be acted
   on, checked for blind spots before commitment) applied to the review artifact instead of a
   pre-execution plan. Concretely useful things this catches that the six checks structurally can't
   catch about themselves: whether an external-tool claim was checked against something live/current
   or just asserted; whether independence was actually achieved (see the byline guardrail below) or
   merely claimed; whether a severity label matches what the cited evidence actually supports. Report
   muse's output alongside the review, don't silently fold it in -- if it found nothing, say so
   plainly, same as any other check in this skill.

Both of these are "run subagents more than once" in the literal sense -- not a blanket doubling of
all six checks (expensive, and the checks that came back clean don't need re-litigating), but a
second, differently-scoped round aimed at the two places a single round has structurally weak
independence: its own most consequential claims, and its own blind spots about itself.

**On Claude.ai:** no subagents. Run the six checks as sequential firewalled inline passes --
adopt a clean-slate reviewer persona per check, explicitly forbidden from carrying conclusions
from the previous check forward. For check 4 (security/privacy) specifically, if the stakes are
real, suggest the user paste the diff into a fresh chat for a genuinely cold read. If a
code-execution tool is available, use it to actually run `scripts/scan.py` and, where feasible,
the code itself. If only a pasted snippet is available with no runnable environment, say so
plainly in the "Not verified" section of the output -- an unrun check is a null result, not a
pass, and reporting it as a pass is exactly the kind of silent, confident wrongness this skill
exists to catch.

## Trigger when

- Explicit: "review this code," "audit this PR/diff," "is this safe to ship," "red-team this."
- Proactively, right after a nontrivial chunk of code gets written or edited -- by Claude Code, by
  this session, or handed over from elsewhere -- before calling it done, and especially before
  handing off to run against a real API, a real database, or real production data.
- Before merging, before wiring a new function into an existing pipeline, before a "this is ready"
  claim.
- Route elsewhere for scope outside these six checks: [[critique]] for open-ended
  architecture/design judgment; [[risk-assessment]] for a full NIST/CIS-grounded formal threat
  model once check 4 surfaces something that needs one, not a code-level flag.

## The six checks (all six, every run)

Run all six even if the user only asked about one of them. Checks 1 and 3 catch the failure mode
nobody thinks to ask for -- confidently wrong output from code that never raises an error -- and
skipping them because the request was scoped to "just check the security" defeats the point.

### 1. Function-call integrity -- FATAL if broken

Every function, method, and API the code calls must exist, be in scope, and match the signature
it's invoked with -- right arity, right keyword args, a return type the caller actually uses
correctly. This catches hallucinated calls: a name that looks like it should exist on a library or
in this codebase but doesn't, or existed in a different version than the one actually pinned.

Method: trace every call site to a real definition or import -- don't accept "looks like a real
function name" as evidence. `scripts/scan.py undefined-calls <file.py>` catches calls to bare
names that are never defined, imported, or built in. It can NOT catch a wrong method on a properly
imported object (`df.wrong_method()` on a real DataFrame) -- that needs real type info, so run the
code or a type checker for that half. If the code executed cleanly in this session, treat that as
evidence, not proof -- a broken call on a path the test data never exercises still counts as
broken. A file with a `from X import *` makes this check unreliable -- the script flags that
case explicitly as low-confidence rather than guessing; fall back to checking those calls by hand.

Fix direction: name the actual definition/import needed, or the correct signature.

**Sub-technique -- external CLI/API calls.** For a call into an external tool (a CLI, a REST/GraphQL
API) rather than the local codebase, there's no AST to check against -- hand-trace it against the
tool's *currently fetched* official docs, not memory or a plausible-looking name. When a call looks
wrong, check specifically whether it's actually nonexistent or whether it's a deprecated-but-still-
supported legacy form -- vendors often keep old command shapes working for backward compatibility
long after publishing a new preferred one (confirmed case: a noun-verb-inverted CLI form was called
FATAL/nonexistent when the vendor's own README listed it under "legacy commands, still supported but
deprecated" -- a real severity difference between "won't run" and "should modernize"). If the tool
is actually available (a real binary, a real account), a live `--help` or dry-run beats any amount
of doc-tracing -- doc-tracing is the fallback for when it isn't, not the first choice when it is.

### 2. Silent-failure audit -- SERIOUS to FATAL depending on what's masked

Any place execution can hit a problem and continue anyway: bare `except`, `except Exception`,
a handler whose body is just `pass` or a comment, a default value substituted when a real one
wasn't available, validation that returns early without raising, a retry loop that eventually
gives up quietly. For each one: does it currently emit something -- a log line, a raised warning,
a returned error flag, an incremented failure counter -- specific enough that a reader six months
from now (human or automated) can tell "this succeeded" from "this failed and got papered over,"
without re-deriving it themselves? If not, that's a failure of this check regardless of how
careful the surrounding code looks.

`scripts/scan.py silent-failures <path>` finds handlers with no raise/log/flag in their body
(Python via AST; Go via a regex heuristic for `_ = call()` discards and empty/no-op
`if err != nil` blocks -- prefer `errcheck` for Go if it's installed; other languages via a
weaker empty-catch-block regex, now including inline `<script>` blocks in .html files). Treat
every non-Python, non-`errcheck` result as a starting point, not a real scan.

Two things count as sufficient disclosure even without a runtime log line, confirmed against a
real review: a discard whose real pass/fail outcome is determined and checked through a
*different* channel right nearby (e.g. a deadlock-guard discards a process's `Wait()` error, but
the actual success/failure is read from a channel checked immediately after) -- verify the real
outcome is actually checked before calling it a finding, don't assume every discard is silent
just because that one variable is; and a discard with a clear inline comment explaining why it's
safe (e.g. "err intentionally ignored: Start()'s error is already returned to the caller, and the
child's exit code is documented as unreliable even on success") -- the check's own bar is whether
a reader six months out would know if this succeeded or failed, and a comment that actually
answers that satisfies it as well as a log line would.

Fix direction: name the specific log statement or return flag to add, and what value it should
carry.

### 3. Data-proxy / mock-data disclosure -- FATAL if silent and consequential

Coding agents reach for a stand-in when the real thing is inconvenient: a hardcoded example value,
a stub left wired into a path that looks production, a fallback to a cached or sample source when
the live one errors, a field read that resolves to the wrong-but-present value instead of failing
when the intended field is missing. None of that is wrong on its own -- stubs and fallbacks are
normal mid-build. The failure is doing it *without disclosure*, so nobody downstream knows the
numbers in front of them came from a placeholder. A field-mismatch bug that silently reads the
wrong-but-present key and returns a confident, empty, or wrong result is the sharpest version of
this: nothing crashes, nothing logs, and the output looks exactly like a real answer.

Method: two passes, not one. `scripts/scan.py proxy-markers <path>` catches labeled stand-ins
(mock, stub, placeholder, TODO, hardcoded, fallback, and similar). It will NOT catch a substitution
that uses none of those words -- so separately, trace every external data dependency (API call,
file read, DB query, external ID lookup) by hand and confirm the field or key actually consumed
matches what the docstring, comment, or caller believes is being consumed. That second pass is the
one that catches the bug the marker grep can't see.

PASS bar: every value presented as real either is real, or is visibly flagged as synthetic/fallback
at the point it's *consumed*, not just at its definition several lines or files away.

Fix direction: name the exact substitution point and what disclosure (log line, changed return
type, raised exception) would make it visible where it's used.

Severity: FATAL if silent and would be consumed as real by a person or a downstream pipeline stage;
SERIOUS if disclosed but easy to miss (buried in a docstring, not in the actual data path); MINOR
if clearly disclosed and genuinely low-stakes (a UI placeholder image, not a data value).

**Sub-pattern -- disclosed per-item, silent in aggregate.** A change can correctly thread a new
discriminator field (a `Source` enum, an `is_synthetic` flag) through every per-item render or
consumption path, while a pre-existing aggregate -- a summary count, a headline string, a total --
was written before the field existed and doesn't know to break out by it. This is a real, confirmed
case, not a hypothetical: a job-done banner correctly disclosed a new "scene-cut stand-in" chunk
type at the per-chunk card level and in the import filter, but the one-line aggregate count didn't
distinguish real transcription from the new fallback type. Usually MINOR if the per-item disclosure
that actually matters for trust decisions is intact -- but check both levels, not just the one a
new field was deliberately threaded through. When a new field is added to a type that's already
rendered or counted in more than one place, check every pre-existing aggregate built from the same
collection, not just the newest call site.

### 4. Privacy & security -- judgment-heavy, severity follows exploitability

Cover at minimum: hardcoded or logged secrets/keys; how PII or other sensitive data is collected,
stored, transmitted, retained, and exposed to any third party or external API the code calls;
injection risk (SQL, shell, template, deserialization); overbroad permissions or scopes; unsafe
`eval`/`exec`/pickle-style deserialization; anything sensitive sent unencrypted; supply-chain
concerns for anything network-facing. When a code path crosses a trust boundary -- routing to a
different provider, a different data-handling policy, a different audience -- check specifically
what crosses that boundary and whether it's the minimum necessary.

Don't ask "is this secure." Ask "if I wanted to exfiltrate data or escalate privilege through this
exact code path, how would I do it." Severity tracks actual exploitability and blast radius, not
how alarming the pattern sounds on its own.

**Sub-pattern -- new code doesn't inherit an established sibling protection.** When a codebase
already has a convention for protecting a class of resource-touching endpoint or handler (a rate
limit, a concurrency semaphore, a cooldown), a newly added sibling that lacks it is easy to miss --
it doesn't look wrong sitting on its own, only next to the others. Confirmed case: a new endpoint
reasoned correctly about one attack (guessing someone else's resource ID) while missing that every
other resource-touching endpoint in the same file had a rate limit or semaphore it didn't, leaving
a real, drive-by-triggerable resource-exhaustion path. Check: for any new handler that spawns a
process, writes to disk, or does other non-trivial work, look at what protection its siblings in
the same file or package already have, and treat a gap as a finding even if the new code's own
inline security reasoning sounds correct in isolation -- correct reasoning about the wrong threat
model is still a gap.

### 5. Optimization -- judgment-heavy, but evidence-based

Big-O behavior against the *actual* expected data size, not the asymptotic case in the abstract --
a quadratic step is a non-issue at ten records and a real problem at ten thousand. N+1 I/O or query
patterns, redundant recomputation of the same value, full-file or full-table loads where a stream
or index would do, missed caching for an expensive pure function called repeatedly. Fix direction
names the concrete alternative pattern -- "this is slow" isn't a finding.

### 6. Cross-unit contract integrity -- FATAL if a seam is broken

Checks 1-5 are all *within-unit*: they ask whether a given file, function, or call is
internally sound. This one asks whether the units actually fit together. A unit can be
perfectly valid on its own and still break the thing it participates in: script A's comment
promises to write a state file, scripts B and C read it, and the write never happens. Every
function resolves, nothing is silently caught, no proxy data is involved, and the system is
broken anyway. Checks 1-3 will all return PASS on that codebase because the defect isn't
*in* any unit, it's in the seam between them.

This check exists because that exact bug got missed by two full review passes of this skill
before a third caught it. It isn't an exotic AI failure mode -- it's a classic integration
bug, which is the point: the other five checks were tuned for LLM-specific failure shapes and
left the ordinary ones uncovered.

Method, in rough order of yield:
- **Producer/consumer pairing.** For every artifact one unit creates and another consumes (a
  file on disk, an env var, a DB row, a queue message, a global, a cache key), find the write
  and the read and confirm both exist, agree on name and location, and agree on format.
  A read with no matching write, or two units disagreeing about a path or key, is the seam bug.
- **Promises in comments and docstrings vs. actual behavior.** "Saves the id to `.pod_id`" in
  a comment with no corresponding write is a contract stated and not honored. Comments that
  describe cross-unit behavior are contract text; check them against code rather than treating
  them as decoration.
- **Interface agreement across the call boundary.** Caller's assumed return shape vs. callee's
  actual return shape; assumed units, nullability, ordering, and error convention.
- **Sequencing assumptions.** Does B require A to have run? Is that enforced, or just hoped for?

Fix direction: name the specific missing write, the disagreeing pair, or the unenforced
ordering, and which side should change.

Severity: FATAL when the broken seam is on a primary path (the system does not work, or works
wrongly and silently). SERIOUS when it only breaks a recovery, cleanup, or error path -- those
fail exactly when something else has already gone wrong, which is the worst time to discover it.



## Severity: anchor to reachability, not to how alarming it sounds

FATAL/SERIOUS/MINOR are useless if they float on unanchored judgment. Two findings landed on
SERIOUS in one real run: a public unauthenticated endpoint leaking logs to anyone who finds the
IP, and a CLI command that will need updating in a year. Same label, orders of magnitude apart
in urgency. Anchor on two axes:

**Reachability** -- who can trigger this, and what do they need?
- *Drive-by*: any unauthenticated party, no special position, today. Highest.
- *Authenticated / local*: needs credentials, local access, or an existing foothold.
- *Maintainer-only*: only reachable by someone editing the code.
- *Latent*: not reachable now; becomes reachable on some future change.

**Consequence** -- what happens on trigger? Silent wrong data consumed as real, and money
burning continuously, both rank above a crash. A loud failure is recoverable; a quiet wrong
answer propagates.

Map: drive-by reachable plus real consequence is FATAL. Maintainer-only or latent, however bad
it would be, is at most SERIOUS -- and say *why* it's capped, so nobody reads the low label as
"this doesn't matter." When reachability is genuinely unknown (you asserted a port is exposed
but never executed anything), that uncertainty belongs in "Not verified," not silently averaged
into the severity.

## Wiring a live hook (Claude Code only)

`scripts/posttooluse_hook.py` runs the fast mechanical tier (checks 1-3, single-file scope) after
every `Write`/`Edit`/`MultiEdit`, and pushes findings back into the session's own context via
`additionalContext` -- so a silent failure or an undisclosed stand-in gets caught while the code is
still hot, not at the next separate review pass. It stays silent on a clean file; it does not fire
on every edit regardless of content, only when it finds something.

Copy the script into the target project and register it:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3",
            "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/posttooluse_hook.py"]
          }
        ]
      }
    ]
  }
}
```

Add this to `.claude/settings.json` for a shared, committable project convention, or
`.claude/settings.local.json` to keep it machine-local. What's confirmed: the JSON-parsing,
scanning, and output-formatting logic below all run correctly against synthetic stdin matching the
documented `PostToolUse` schema exactly (clean file stays silent and exits 0; a file with real
findings produces valid, well-formed `additionalContext` under the 10,000-character cap; malformed
input and non-matching tool calls both exit 0 without crashing).

**Live-run confirmed 2026-08-09** (macOS, Claude Code v2.1.x, `agent-remediation` Phase 4). The
hook fires inside a real session: the `Edit|Write|MultiEdit` matcher matches, the scan runs, and
`additionalContext` lands beside the tool result as a `system-reminder` on the same turn, exactly
as the docs describe. A brand-new `.claude/settings.json` was picked up by the file watcher within
one tool call -- no session restart. Timeout behavior is still unexercised (no run has come close
to the 15s limit set in the registration).

Two things the live run settled that the synthetic tests could not. **You do not need to copy this
script into the project.** Register it in exec form with an absolute path straight into the plugin's
own `scripts/` directory -- `${CLAUDE_PLUGIN_ROOT}` is a plugin-context substitution and does not
resolve in a project `.claude/settings.json`, but a plain absolute path does, and it keeps one
source of truth instead of a per-project fork that drifts (a copied `posttooluse_hook.py` without
`scan.py` beside it exits 2 by design):

```json
{
  "type": "command",
  "command": "/usr/bin/python3",
  "args": ["/abs/path/to/adversarial-code-review/scripts/posttooluse_hook.py"],
  "timeout": 15
}
```

**System `python3` is enough.** `posttooluse_hook.py` and `scan.py` import only `json`, `sys`,
`pathlib`, `ast`, `re`, `builtins`. Verified byte-identical output under macOS system Python 3.9.6
and a 3.14.6 venv, so don't pin the hook to a virtualenv that may be rebuilt.

Expect occasional mechanical false positives on the skill's own prose -- scanning this very file's
docstring flags the word "fallback" as a proxy marker. That is the fast tier working as documented,
not a bug: candidates need a judgment call, they are not automatic findings.

This is deliberately the fast tier only. Checks 4 and 5, and the sibling-convention-consistency and
aggregate-disclosure sub-patterns above, need real reasoning across more than one file -- they
belong to a full skill run (subagents per check) before merge or handoff, not to a script that has
to return before the next tool call. Don't try to cram them into this hook; a slow hook that blocks
every edit is worse than no hook.

## Scoping the review

Checks 1 and 3 need real context to be trustworthy: undefined-calls run against a bare diff, with
no visibility into imports declared elsewhere in the file, will produce false positives on every
name that's actually defined outside the hunk. Before running checks 1 and 3, expand a diff or
snippet to at least its full containing file; expand further to the module if imports live in an
`__init__.py` or a shared config the file depends on. If that context genuinely isn't available
(a pasted snippet with no repo access), say so in "Not verified" rather than letting the check run
degraded and silently.

## Output format

```
## Adversarial Code Review -- <scope>

**Verdict:** SHIP / SHIP WITH FIXES / DO NOT SHIP
  (any FATAL finding -> DO NOT SHIP. SERIOUS findings with no FATAL -> SHIP WITH FIXES.
   MINOR only, or clean -> SHIP. A finding still "not verified" pulls the verdict down one
   level from what the verified findings alone would suggest -- an unverified check 1 or 3
   is not the same as a passed one.)

**1. Function-call integrity:** <PASS, or findings: file:line, what's broken, fix direction>
**2. Silent-failure audit:** <every catch/fallback found, and whether it currently logs -- PASS/FAIL each>
**3. Data-proxy disclosure:** <every real-vs-stand-in boundary found -- PASS/FAIL each, marker-grep AND hand-traced>
**4. Privacy & security:** <findings by severity, framed as "how would I exploit this">
**5. Optimization:** <findings by severity, with the concrete alternative pattern named>
**6. Cross-unit contracts:** <every producer/consumer pair traced -- PASS/FAIL each, naming the artifact>

**Not verified:** <anything you couldn't actually check -- no execution environment, no access to
the pinned dependency versions, a snippet with no repo context -- stated plainly, not folded into
a PASS>

**Passed clean:** <which of the six checks had zero findings -- say so plainly, don't manufacture
a minor note to look thorough>
```

## Guardrails

- **Every finding cites file:line or an exact excerpt.** "The error handling seems thin" isn't a
  finding; "example.py:7, bare `except: pass` around a CSV load with no log" is.
- **This skill diagnoses; it doesn't silently rewrite.** Report findings with a fix direction, one
  line, not a full patch, unless asked for one. Flag it and hand back -- don't unilaterally patch
  code and then run it against a real API or real data without the human seeing what changed first.
- **Never mark a check PASS when it wasn't actually verified.** An unrun test suite, an unavailable
  pinned dependency, a pasted snippet with no repo context are all "not verified" -- a different,
  honest status from PASS.
- **All six checks run every time**, regardless of how the request was scoped. A request to "just
  check the security" still gets checks 1 and 3, because those are exactly the ones nobody thinks
  to ask for and exactly the ones that produce confidently wrong output with no error anywhere.
- **Severity tracks actual impact, not pattern-alarm.** A bare `except: pass` around a non-critical
  logging call is not the same severity as one wrapping a data fetch that feeds downstream analysis.
- **A degraded pass marks itself down, it doesn't just disclaim.** This skill exists to catch
  confident output that's silently incomplete, and its own reports are capable of exactly that:
  a low-distance pass (shared context, no execution environment, no repo access) can emit a
  clean-looking PASS/FAIL grid that reads identically to a rigorous one. So when independence or
  verification was reduced, the verdict moves, not just a caveat: an unverified check can't be
  reported as PASS, and a verdict resting on unverified checks drops one level. Writing "reduced
  distance" in a footnote while the grid still reads all-PASS is the failure mode, not the fix.
- **Findings get re-reviewed after they're fixed, by someone other than the fixer.** The author
  who wrote the bug is usually the one asked to fix it, and a fix written from the same
  assumptions can re-introduce the same class of error -- or implement a correction that was
  itself wrong. This is not hypothetical: in one run, a review's fix direction told the author to
  replace a CLI call with a REST API call, when the CLI command it claimed didn't exist actually
  did. Applying that fix would have added permanent unnecessary complexity. Re-run the affected
  checks against the patch before closing anything, and treat "the fix direction was wrong" as a
  live possibility, not just "the fix was applied incorrectly."
- **State how many independent passes actually ran, not just who ran them.** "Clean-slate pass, Fable"
  doesn't say whether that's one persona covering all six checks or six separately firewalled
  passes -- and the difference is exactly the independence the Claude.ai environment note exists to
  protect. Name the count explicitly: "five firewalled passes" or "one merged pass, reduced
  independence" are both fine to report, an unstated pass count isn't. On Claude Code, state this for
  the second round too -- whether any FATAL finding got an independent re-confirmation, and whether
  muse ran against the report, not just whether the six checks ran.

## Pairing

```
adversarial-code-review (six-check gate: calls, silent failure, data-proxy, security,
                         optimization, cross-unit contracts)
  ← whatever wrote or edited the code, run before handoff or merge
  → muse              (Claude Code second round: blind-spot pass on the finished report itself,
                       not the code -- see "Second round" above; not optional when a FATAL finding
                       is about to drive a rewrite)
  ~ critique         (open-ended holistic red-team for architecture/design judgment calls outside
                       these six checks)
  ~ risk-assessment   (deeper, framework-grounded security assessment -- NIST/CIS -- once check 4
                       surfaces something that needs a formal threat model, not just a flag)
  ~ experiment-ledger (if check 3 surfaces a result that was already logged somewhere as real, that
                       result needs re-classification, not just a code fix)
```
