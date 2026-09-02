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
