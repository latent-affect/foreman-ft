# Security & privacy review — dev-harness Run 2, 2026-09-02

**Stage:** `foreman:security-privacy-review`, after `foreman:validate` (MET), before
`foreman:ship-readiness`.
**Reviewer:** Nadia Osei persona, session `dev-harness-9b`. Authored none of the components under
review.
**Method:** STRIDE-per-interaction against named boundaries, not per-component; plus a
data-minimization trace. DREAD used only to order the three real findings, with its limitation
stated where used.
**Input, bounded per REQ-19/REQ-34:** `PRD.md`'s R7 and R8 text only; `ARCHITECTURE.md`'s
`yaml interfaces` block and the three declared couplings; the real code at each boundary.
`VALIDATION-REPORT.md`'s scope-completeness finding was not re-derived.

**Not a clean pass.** Three real findings, all Information Disclosure or Denial of Service, none
FATAL, none blocking on its own. Two are structural rather than currently-exploitable, and I have
said which is which rather than inflating either.

---

## Trust boundaries named

Four. Two were introduced or materially changed by Run 2; two are pre-existing and reviewed
because `ARCHITECTURE.md` declares them, with that scope difference marked rather than blurred.

| # | Boundary | Crossing | Run 2 status |
|---|---|---|---|
| B1 | MCP client (an LLM agent) ↔ ATLAS warehouse, via `atlas/mcp/server.py` | Untrusted tool caller → gated warehouse views | Untouched code; R7 (DEVH-12) makes this pass a Run 2 requirement |
| B2 | `monitoring` → `atlas`, via `atlas.query.facade.QueryFacade` | Warehouse rows → emitted JSON snapshot | **Newly declared this build**; consumer modified in `9effe00` |
| B3 | Audit-plane ingest, `safety.jsonl` → `audit_event` | Untrusted verbatim shell text → persisted warehouse row | **Shipped this build** (R8, DEVH-13) |
| B4 | `bollard` → TESSERA, via `tessera_resolver` CLI/DB path | Workspace-controlled project identity → gate authority | Pre-existing, untouched by Run 2 (`git log 280e692..HEAD` on those paths returns empty) |

---

## STRIDE findings

### F1 — Information disclosure (I), boundary B1. The MCP view exclusion is a denylist of one name.

`atlas/mcp/server.py:22`:

```python
MCP_VIEWS = frozenset(v for v in ALLOWED_VIEWS if v != "v_fail_open_incident")
```

The secret-bearing view is excluded **by name**, not by property, and `MCP_VIEWS` is derived from
`ALLOWED_VIEWS` by subtraction. Every view added to `ALLOWED_VIEWS` in future is therefore
published over the MCP tool surface automatically, with no review step and no failure mode that
would surface it. The safe direction here is the opposite construction: an explicit allowlist of
what MCP may serve, so a new view is invisible until someone deliberately admits it.

**Currently exploitable: no.** I verified the present state is correct — `v_fail_open_incident` is
the payload-bearing view, it is the one excluded, and R8 has now scrubbed credential patterns at
ingest anyway. The defect is that safety here depends on whoever next edits `ALLOWED_VIEWS`
remembering this line exists in a different file.

This is the same denylist-versus-allowlist shape that fails silently on addition rather than
loudly, which is why it is worth fixing before a view is added rather than after.

### F2 — Denial of service (D), boundary B1. `limit` does not bound the work done.

`atlas_query_view` clamps `limit` to 1..1000 and then calls `get_facade().fetch(view_name)`.
`QueryFacade.fetch` builds `sql = f"SELECT * FROM {view_name}"` and returns `cursor.fetchall()`
(`atlas/query/facade.py`, read directly). The clamp is applied afterwards in Python:
`rows = rows[:limit]`.

So an MCP caller asking for `limit=1` still causes the entire view to be materialized in memory.
The reference warehouse carries 363,289 `hook_verdict` rows, and views over it are correspondingly
large. Cost is bounded by view size, not by anything the caller's `limit` controls, and the call is
repeatable.

There is a real design tension here and it is worth naming rather than glossing: `fetch()`
deliberately refuses caller-supplied `where_sql` ("this facade serves whole gated views only"),
which is a genuine and correct injection control. A `LIMIT` pushdown must not become the crack
that reintroduces caller-controlled SQL. A server-side integer-only `LIMIT ?`, bound as a
parameter and never string-interpolated, keeps the injection control intact while bounding the
work.

### F3 — Information disclosure (I), boundary B2. Facade error strings carry the absolute warehouse path into the emitted snapshot.

Two paths put an exception's text straight into the JSON this component emits:

- `load_atlas_snapshot()`: `return {"available": False, "reason": str(exc)}` — pre-existing.
- the fetch block: `warehouse["fetch_error"] = str(exc)` — **added by `9effe00`**, the fix that
  closed the Check 6 FAIL.

`QueryFacadeUnavailable`'s message embeds the full path. Observed directly, not hypothesised:

```
cannot open /Users/m5/dev/dev-harness-run2/atlas/warehouse/atlas.db read-only:
  unable to open database file -- does it exist and is it migrated?
```

That is an absolute filesystem path including the machine account name, in a document whose whole
purpose is to be read and displayed elsewhere.

**Two things I want to be precise about, because overstating this would be the failure mode.**
First, `m5` is a pseudonymous account name, not the operator's real name, so this does **not** trip
the standing hard rule about real names reaching public surfaces. It is ordinary path disclosure,
not an identity incident. Second, the fix that introduced the second path was correct and
necessary — the finding is that it widened an existing leak from one path to two, not that closing
the Check 6 FAIL was wrong.

The module already holds itself to a stricter standard elsewhere: its own docstring states outputs
"carry only the resolved prefix, not a human-readable name or path." The error paths do not meet
the promise the same file makes.

### Comparative ordering (DREAD, with its limit stated)

DREAD scores are comparative and rater-subjective. They order these three against each other; they
are not an objective severity scale and should not be read as one.

| Finding | Damage | Reproducibility | Exploitability | Affected users | Discoverability | Ordering |
|---|---|---|---|---|---|---|
| F2 (D, B1) | Low | High | High | One operator | High | **1st** — the only one exploitable today by a caller |
| F1 (I, B1) | Med | High *once triggered* | Low today | One operator | Low | **2nd** — latent, triggered by a future edit elsewhere |
| F3 (I, B2) | Low | High | Low | One operator | Med | **3rd** — real, small, and self-inconsistent with its own docstring |

---

## Boundaries that came back clean, said explicitly

**B3, the R8 audit-ingest scrub, is the strongest thing in this build and I could not break it on
review.** Saying so plainly, because a clean result is a result.

The chain matches the standing fail-closed pattern exactly: `scrub_row()` redacts, the row is
staged into a `TEMP` table that shadows `audit_event` for unqualified statements on that
connection, the staged row is then **re-read from disk with a fresh SELECT** rather than trusting
the in-memory dict the scrub just returned, re-checked against every `CREDENTIAL_PATTERNS` entry,
parsed as JSON to prove the staged bytes are well-formed, and only then published with an explicit
`INSERT INTO main.audit_event` that escapes the shadow. Any failure drops the line rather than
persisting it. I re-executed the suite rather than reading it: `python3 -m unittest
atlas.ingest.tests.test_audit_scrub` → **10 tests, OK, real exit 0**, including forced-failure
cases.

I checked the one construct that looked dangerous and it is correctly handled. The cleanup is an
unqualified `DROP TABLE IF EXISTS audit_event`, and the code's own comment records that with no
temp shadow present this drops the *real* table. The shadow is created unconditionally **before**
the `try`, so a failure to create it propagates without ever reaching the `finally`, and the
missing-source case returns early before either. That is the right ordering, and it is deliberate
rather than lucky.

**B2's data minimization is clean.** The five views the consumer reads —
`v_ticket_diff_binding`, `v_decision_outcome_rate`, `v_source_freshness`,
`v_project_resolution_coverage`, `v_gate_proven_live` — were each checked for path-shaped columns
(`cwd`, `source_path`, `start_cwd`, `root`). None carries one. The component's claim to emit "only
the resolved prefix, not a human-readable name or path" holds for its data path. F3 is about its
*error* path, which is a different thing.

**B4 produced no Run 2 finding.** `tessera_resolver`'s subprocess calls use list-form argv with no
shell, so the prefix cannot inject a command. The `tessera-prefix` override file wins outright but
only after validation against the live registered-prefix set, and a stale or mistyped value
degrades to `unreachable` rather than being silently trusted — the distinction between
"misconfigured" and "genuinely fine" is preserved, which is the right failure direction.

One structural observation about B4, offered as an observation and explicitly **not** counted as a
Run 2 finding, since Run 2 did not touch this code and the property predates it: a file inside the
workspace (`.foreman/tessera-prefix`) determines which project's ticket authority a gate consults.
Validation constrains it to a *registered* prefix, so it cannot point somewhere arbitrary, but it
can point at a different real project — whose open-ticket set is not the one the gate should be
reading. Whether that matters depends on whether the workspace is ever less trusted than the gate,
which is a question about deployment rather than about this build. Flagged for whoever owns
`tessera_resolver`, not billed to Run 2.

---

## Data-flow findings

**Deployment profile:** none declared. No `.foreman/deployment-profile` exists in this project.
The component-level `tessera/SECURITY-PRIVACY-REVIEW.md` records `solo` with `actor_ref_split` as
the required layer, and records that `actor_ref` is structurally present but always `NULL` — a
documented, already-decided state, not a gap for this review to reopen.

**Nothing in Run 2 moves personal or identity-shaped data outside the decided boundary.** R8
narrows rather than widens: it removes credential-pattern matches from `audit_event.payload_json`
before persistence, where previously they were stored verbatim. B2 adds a read path over five
views that carry no path or identity columns. No new PII field, no new retention, no new
cross-project data movement.

**One trace worth recording, not a finding under the decided boundary.** `audit_event.payload_json`
still persists verbatim shell text after scrubbing, and that text contains absolute paths carrying
the account name. R8's scope is credential patterns, not identity-shaped strings, and that scoping
is correct for what R8 was asked to do. It stays inside the decided boundary because the warehouse
is local-only. It would leave that boundary the moment the warehouse is exported, synced across
machines, or widened in ingestion scope — which is exactly the question raised in
`/Users/m5/dev/atlas-sonnet/ATLAS-SCOPE-EXPANSION-ASSESSMENT.md`. Recorded here so the dependency
between the two is visible rather than rediscovered.

---

## Referred elsewhere, not counted here

- **`DEVH-47`** (empty-but-clean warehouse trips a `TypeError` in `gates_by_prefix`) is a
  correctness defect with no trust-boundary or data-flow shape. It belongs to
  `adversarial-code-review`'s lane and is already ticketed. Not counted here.
- **`DEVH-44`**'s accepted PARTIAL on the `monitoring_to_atlas` execution evidence is a
  ship-readiness judgment about an accepted, tracked gap. **Marcus Webb's lane**, and this review
  takes no position on whether it is acceptable to ship on.
- **Warehouse growth and backup posture** (578 MB at narrow scope, no recovery plan, a volume at
  97 percent) is operational blast radius. **Marcus Webb's lane.** Named in the ATLAS scoping
  assessment; not re-argued here.
- **The three installed-artifact couplings** (`skills` → `bollard` via `~/.claude/hooks`) are an
  architecture-declaration question already open from the integration-test stage, not a security
  finding. They cross no trust boundary that the in-tree equivalents do not.

---

## Disposition

Three findings, none blocking. R7's requirement is satisfied: this document is the written STRIDE
pass over `atlas/mcp/server.py`'s trust boundary that DEVH-12 asks for, and F1 and F2 are its
risk-register rows with real dispositions rather than "monitor" placeholders.

Recommended, for the gate owner rather than decided here:

1. **F1** — invert `MCP_VIEWS` to an explicit allowlist. Small change, removes a latent exposure
   that grows with the view set.
2. **F2** — push `LIMIT` into `fetch()` as a bound integer parameter, never interpolated, keeping
   the existing `where_sql` refusal intact.
3. **F3** — emit a fixed reason string with the exception *type* rather than `str(exc)`, or
   redact the path, so the module's error path meets the promise its own docstring makes.

None of the three needs to block ship on its own. F2 is the one I would fix first, because it is
the only one a caller can trigger today.

---

## Addendum — R14a/R15a guard chain (bollard capability-scoping layer), 2026-09-04

**Stage:** `foreman:security-privacy-review`, closing a HOLD Priya Desai's Validate-stage render
named: `guard_allowlist.py`, `guard_semantic_resolution.py`, `guard_pattern_feed.py`,
`guard_os_sandbox.py`, `deny_capability.sb`, `capability_scope.sh` were entirely absent from this
document. Confirmed absent before this addendum by direct grep, zero matches on all six names.
**Reviewer:** Nadia Osei persona, rendered in-session (`dev-harness-run2-93`), not as a subagent.
Authored none of the six components under review.
**Method:** same as above — STRIDE-per-interaction against named boundaries, not per-component;
DREAD used only to order the two real findings, with its limitation restated.
**Input, bounded:** the six named files, read in full this pass, not recalled from a prior pass in
this session; `hook_common.py`'s `run()`/`run_body()` (lines 261-304) for the fail-open/fail-closed
contract each guard claims to participate in; the git history behind each file's own docstring
claims, checked rather than trusted.

**Not a clean pass.** Two new findings, both Tampering-class, one with an Elevation-of-Privilege
consequence. Neither is FATAL. One is exploitable only under a malformed-input precondition the
normal harness does not produce; the other is a real, live gap in a documented invariant that
nothing currently enforces.

### Trust boundaries named (continuing B1-B4 above)

| # | Boundary | Crossing | Status |
|---|---|---|---|
| B5 | `guard_os_sandbox.py`'s match+rewrite decision → `capability_scope.sh` + `deny_capability.sb` kernel enforcement | A detected destructive-shape command → a rewritten, auto-"allow"ed sandbox-exec invocation | Reviewed this pass, not previously in this document |
| B6 | `guard_semantic_resolution.py` / `guard_pattern_feed.py` (additive-only detection) → `guard_os_sandbox.py` / `guard_allowlist.py` (capability-removal/deny layer) | A frozen DEVH-62 contract: neither capability-removal guard may treat the detection pair's presence, absence, or crash as evidence of safety | Reviewed this pass |
| B7 | Bash `tool_input` (agent/session-controlled) → `guard_allowlist.py`'s fail-closed guarantee | Untrusted tool-call shape → a guard whose whole reason to exist is not failing open | Reviewed this pass |
| B8 | `capability_scope.sh` as a standalone, directly-invokable artifact → its caller, independent of `guard_os_sandbox.py`'s wrapper | A caller-supplied shell command string → `sandbox-exec` | Reviewed this pass |
| B9 | `guard_os_sandbox.py`'s capability-class match → `deny_capability.sb`'s actually-enforced scope | The declared capability ("destructive filesystem writes") → what the kernel profile actually removes | Reviewed this pass |

### STRIDE findings

### F4 — Tampering (T), boundary B7. `guard_allowlist.py`'s fail-closed backstop covers capability matching, not `tool_input`'s own shape. Tracked as `DEVH-104`.

`guard_allowlist.py:91-100`:

```python
def main(data):
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command:
        return

    try:
        matched = _matched_capability(command)
    except Exception as exc:
```

The module's own docstring states the design intent plainly: "classification here is wrapped in
its own try/except that calls `hc.deny()` directly, rather than letting the exception reach
`hook_common.run()`'s outer handler" — because `hook_common.run()`'s outer handler fails **open**
(`hook_common.py:291-304`: `main_fn(data)` raising anything results in "no decision emitted =>
tool proceeds"), and R13/DEVH-61 name this guard specifically as one of the two layers the deny
must survive on even when everything else has failed.

Line 94 sits **before** the `try` block that starts at line 98. If `data.get("tool_input")`
resolves to a truthy non-dict value (a string, a list — anything without a `.get` method), `.get`
raises `AttributeError` outside the guarded region, reaches `hook_common.run_body`'s outer
handler, and this guard fails open — the exact failure shape its own docstring says must not be
this guard's last word.

**Currently exploitable: not against a normally-shaped Claude Code hook payload.** Every real
`tool_input` this session observed for a `Bash` PreToolUse call is a JSON object. This is not a
caller-triggerable bypass today. It is a real gap in the guard's own stated coverage claim,
independent of how likely the precondition is: the code says the try/except exists so this guard
never reaches the outer fail-open path, and for this one input shape, it still can.

### F5 — Tampering / Elevation of privilege (T/E), boundary B5. `BOLLARD_DENY_CAPABILITY_SB_OVERRIDE` has no runtime guard against being set outside a test process. Tracked as `DEVH-105`.

`guard_os_sandbox.py:55-62`:

```python
# BOLLARD_DENY_CAPABILITY_SB_OVERRIDE exists ONLY so C16's live-harness negative-control arm and
# C18's own negative control can point this guard's real, unmodified code path at a profile with
# the deny rule removed ... It is never set in normal operation; the shipped guard always resolves
# to lib/deny_capability.sb.
DENY_CAPABILITY_SB = Path(
    os.environ.get("BOLLARD_DENY_CAPABILITY_SB_OVERRIDE") or str(LIB_DIR / "deny_capability.sb")
)
```

Grepped the whole `dev-harness-run2` tree for this name. Two hits, both already known:
`guard_os_sandbox.py` itself and `test_guard_os_sandbox.py` (which sets it deliberately for C16's
negative control). **Nothing else references it** — no startup assertion, no check in `main()`, no
CI guard — that confirms it is unset whenever a real (non-test) hook process runs.

`_profile_can_deny` (`guard_os_sandbox.py:71-119`) is the only gate standing between this override
and a silent bypass, and its own docstring is explicit that it is "deliberately shallow": it
"parses for the presence of a `(deny ...)` rule," not that the rule denies the *right* capability.
A profile containing any syntactically real `(deny ...)` clause — `(deny network-outbound)`, or a
`(deny file-write* (subpath "/nonexistent"))` that denies nothing reachable — passes this check.

The consequence compounds with B5's other half: on a match, `main()` (`guard_os_sandbox.py:171-178`)
calls `hc.rewrite(...)`, and per this file's own docstring (lines 33-38), a rewrite carries an
automatic **"allow"** that bypasses the operator's own permission prompt for that call — "the cost
this design exists to bound." So a leaked or forgotten override does not just weaken the sandbox;
it removes the sandbox's real protection *and* keeps the automatic bypass of the human checkpoint
that would otherwise have caught the unprotected command.

**Currently exploitable: not via `tool_input`** — this is process environment, not something a Bash
command's own arguments can set from inside the sandbox. It is a real, live gap in an invariant the
code states as fact ("never set in normal operation") but nothing enforces: a stray `export` left
in a shell profile, an inherited CI environment, or a test command copy-pasted and run outside
`unittest` would all silently reproduce this. Reproducible and high-damage once the precondition
holds; the precondition itself is an operational-hygiene question, not a remote one.

### Comparative ordering (DREAD, with its limit stated)

| Finding | Damage | Reproducibility | Exploitability | Affected users | Discoverability | Ordering |
|---|---|---|---|---|---|---|
| F5 (T/E, B5) | High | High, once set | Med | One operator | Low — no runtime signal it happened | **1st** — a single environmental slip produces a silent, undetectable full bypass of the capability-removal layer while still auto-suppressing the permission prompt |
| F4 (T, B7) | Med | Low | Low | One operator | Low | **2nd** — real gap in the guard's own coverage claim, but the harness does not produce the triggering input shape today |

### Boundaries that came back clean, said explicitly

**B6 holds structurally, not just by convention.** DEVH-62's frozen constraint says neither
capability-removal guard may treat the detection pair's presence, absence, or crash as evidence of
safety. Checked by reading the import graph rather than re-reading the prose:
`guard_os_sandbox.py` imports only `hook_common` and `guard_destructive` (line 49);
`guard_allowlist.py` imports only `hook_common`. Neither references `guard_semantic_resolution` or
`guard_pattern_feed` at all — the constraint holds because no import path exists through which it
could be violated, not merely because nobody has violated it yet.

**B5's pattern-sharing side is clean, and already disclosed rather than found here.**
`guard_os_sandbox.CAPABILITY_CLASS_PATTERNS` is a re-export of
`guard_destructive.DESTRUCTIVE_PATTERNS` (`guard_os_sandbox.py:68`) — the same object, not a
value-equal copy — so widening `guard_destructive`'s pattern list also silently widens which
commands receive the automatic permission-bypass rewrite. That two-sided consequence was already
found and disclosed this session: commit `6a4049f`, "DEVHR2-1: disclose that DESTRUCTIVE_PATTERNS
also drives guard_os_sandbox's permission-bypass scope" (DEVH-16 comment 1368). Verified the
disclosure is real by reading the commit, not restated from memory. Not re-raised as a new finding.

**B8, `capability_scope.sh`'s own input handling, is clean.** `COMMAND="$*"` then
`exec sandbox-exec -f "$PROFILE" /bin/sh -c "$COMMAND"` (`capability_scope.sh:62,64`) passes the
joined string as one double-quoted expansion — no word-splitting or glob expansion before it
reaches `sh -c`, and the script's entire purpose is to execute the caller-supplied command under
the sandbox, so command execution here is the contract, not an injection vector. It refuses to run
at all (exit 4) rather than degrade unprotected if the profile is missing or `sandbox-exec` is
unavailable (`capability_scope.sh:51-56`) — checked that this is the real behavior in the code, not
just asserted by its own header comment.

**B9's scope is honestly declared, not a finding.** `deny_capability.sb` denies only
`file-write*` (`deny_capability.sb:44`); network, process exec, and reads are untouched by design,
and the file's own header says so plainly (lines 6-8, 28-34) rather than implying broader coverage.
The actual blast-radius control is which commands `guard_os_sandbox.py` decides to wrap at all
(`CAPABILITY_CLASS_PATTERNS`), not this profile pretending to cover more than it does.

### Data-flow findings

`bollard/verdict_ledger.py` (distinct from `claude-hooks-v2`'s own copy fixed under CHV2-1 earlier
tonight — different module, different lineage) persists a deliberately narrow, allowlisted field
set per record (`verdict_ledger.py:127-157`): `ts`, `handler_id`, `event`, `verdict`, `kind`,
`session_id`, `cwd`, `probe_id`, `run_id`, `tool_name`, `tool_use_id`, `self_duration_ms`,
`target`, `decision`, `rule_id` — never the raw `tool_input` or command text. Checked all six
guards' `hc.deny()`/`hc.rewrite()` call sites directly: none passes raw command text into the
message argument, only a short symbolic capability-class name (`privilege_escalation_sudo`,
`secure_delete_shred`, etc., drawn from each guard's own name tuples) — the Bash command a user
actually typed never reaches the ledger file. This is the allowlist shape F1 (above) says
`atlas/mcp/server.py` should have used instead of a denylist; `verdict_ledger.py` chose it from the
start. The one path-shaped field is `cwd` (line 135), written to a local, mode-0600 file
(lines 97, 164) — the same "stays inside the decided boundary because local, not exported" status
already established for B3's `audit_event.payload_json` above; not reopened here.

### Referred elsewhere, not counted here

- **`guard_allowlist.py`'s own `KNOWN_NOT_ENUMERATED`** (lines 68-74) already discloses that `su`
  itself is not caught, tracked under DEVH-92 Class C, with its own regression test
  (`test_class_c_coverage_boundary.py`) asserting the gap stays open rather than silently closing.
  Same shape as F4 — an enumeration/shape boundary — but already ticketed and self-testing. Not
  re-raised here.

### Addendum disposition

Two findings, F4 and F5, neither FATAL. F5 is the one I would fix first: it is the only one where
a single environmental slip produces a silent, undetectable full bypass of the capability-removal
layer while the automatic permission-prompt bypass stays in effect. Recommended, for the gate
owner rather than decided here:

1. **F5** — add a runtime assertion, outside test collection, that
   `BOLLARD_DENY_CAPABILITY_SB_OVERRIDE` is unset whenever the process is not running under
   `unittest`/the declared negative-control harness. `_profile_can_deny` is the wrong layer to
   close this at — it is deliberately shallow and correctly scoped to a different question (does
   the resolved profile deny *something*, not whether the override should exist at all).
2. **F4** — move the `data.get("tool_input") or {}` extraction inside the same try/except
   `guard_allowlist.py` already has, so the file's own documented invariant covers its full input
   surface rather than only the capability-matching step.

This closes the specific gap Priya Desai's HOLD named: all six of `guard_allowlist.py`,
`guard_semantic_resolution.py`, `guard_pattern_feed.py`, `guard_os_sandbox.py`,
`deny_capability.sb`, and `capability_scope.sh` now have a real STRIDE-per-interaction pass in this
document, at the same rigor and format as the original three findings above. **Validate stage
verdict (this document's scope only): go(unbound)** — per PDP.md §8, `go(unbound)` because the
required machine-readable ledger-row mechanism for a formal stage-close does not exist for this
kind of narrative security/privacy pass, matching this document's own original three findings,
which never received a formal ledger-backed stamp either, only this same Disposition-prose
convention. Two real findings recorded with dispositions, neither blocking on its own. I have no
visibility into whether Priya's HOLD named anything beyond this six-file absence — if it did,
that part is not addressed by this addendum and should be confirmed against her own render.
