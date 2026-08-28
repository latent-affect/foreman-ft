# TESSERA — security & privacy posture

Covers the trust boundaries a deployment of TESSERA actually has, what has been checked against
STRIDE (spoofing/tampering/repudiation/information-disclosure/denial-of-service/elevation-of-
privilege), known limitations, and what to check before publishing a repository that uses
TESSERA/tessguard.

## Deployment profile and required privacy layers

TESSERA resolves a deployment profile live via `deployment_profile.resolve_deployment_profile()`.
No `.foreman/deployment-profile` marker means the profile defaults to `solo`.

Required privacy layers at `solo`: **`actor_ref_split`** (`SOLO_TEAM_LAYERS`). The three
`enterprise`-only layers — `consent_registry`, `dsar_tooling`, `oauth_scoped_elevation` — are not
required and are out of scope unless a project explicitly declares the `enterprise` profile. Do
not assume the fuller apparatus (consent tracking, DSAR tooling, OAuth-scoped elevation) exists
until that profile is declared.

## Trust boundaries

Twelve boundaries exist in the current design. Each has been run through STRIDE.

| # | Boundary | Result |
|---|---|---|
| B1 | HTTP surface → trusted context (`127.0.0.1` bind, no auth by design) | clean |
| B2 | CLI argv → store | clean |
| B3 | api / gitops → `git` subprocess (caller-influenced arguments) | clean |
| B4 | store / sql_query → attached SQLite databases | see SP-2 |
| B5 | export → portable file that leaves the machine | see below |
| B6 | reviewui browser JS → HTTP API, and served static assets | clean |
| B7 | tessguard → Claude Code transcript JSONL (external format, harness-owned) | see SP-3, P-1 |
| B8 | tessguard audit output → committed file in a *second repository* | see SP-1 |
| B9 | docs_store → filesystem paths | clean |
| B10 | attachments → content-addressed blob storage | clean |
| B11 | SSE stream → connected clients | clean |
| B12 | api → ATLAS QueryFacade | declared, no code yet — nothing to check |

## B5 — the export surface

Export produces a portable file (CSV or full-fidelity JSON) a human or agent can copy off the
machine. This is the one boundary where data genuinely leaves the process, so it gets full
treatment rather than a table row.

Path: `records.py` → `jiracsv.py` / the JSON writer → `writer.publish_verified`.

**Formula-injection neutralization.** `neutralize()` prefixes any cell beginning `=`, `+`, `-` or
`@` with an apostrophe, applied to **every cell** rather than to a per-field allowlist — a stale
allowlist is exactly the failure mode a per-field approach invites as fields get added, and the
cost of over-applying is one leading apostrophe.

**Atomic, verified publish.** `writer.publish_verified` renders to a sibling temp file, `fsync`s
**before** reading back (so verification reads durable bytes, not the page cache), passes the
disk-read bytes to `verify()` — never the in-memory buffer, which would silently turn the check
into "does my own buffer agree with itself" — then publishes with `os.replace`, atomic within one
directory. Any failure anywhere removes the temp file and raises; nothing partially-written is
ever left at the final path. If `verify()` fails, cleanup of the temp file is best-effort; a
double failure (verify raises *and* the cleanup unlink fails) can leave a `.partial` file,
`mkstemp`-created at mode `0600`, containing an *unverified* artifact — not a permissions
exposure, but worth knowing the residue can exist.

**What actually crosses the boundary.** The CSV carries: summary, ticket id, type, status,
priority, severity, reporter and assignee as recorded, description, parent, timestamps, project,
tier, archived flag, repro steps, environment, plus repeated columns for reference docs,
blocked-by, blocks, watchers, attachment filenames, and full comment bodies. The full-fidelity
JSON carries the complete record plus claims, criteria and attachment metadata — including the
entire comment corpus, by design, since issue history is what an export is for. Actor identifiers
export as recorded, unredacted, since they are client-asserted in the first place, not because
redacting them was overlooked.

**A real minimization.** Attachments export as filename, sha256 and size only — never blob
contents. Metadata crosses the boundary; payloads do not.

## Known limitations

**Audit log records absolute filesystem paths, including your account name.**
`audit.append_log_internal()` appends every tessguard audit result as a JSON line to a configured
`AUDIT_LOG_PATH`. Each record carries `transcript` and `repo_root` as resolved absolute paths — the
transcript path takes the shape `~/.claude/projects/<hyphenated-cwd>/<session>.jsonl`, which
encodes both your account name and local directory layout. If this log file is tracked by git and
you later publish that repository, this history goes with it. **Before publishing a repo that uses
tessguard: gitignore the audit log, or change the write path to record repo-relative paths
instead** — the latter removes the account name at the source and survives someone re-adding the
file later. Check with `git check-ignore -v <path-to-log>` and `git ls-files --error-unmatch
<path-to-log>` before a first push, not after.

**The SQL attach-alias guard depends on a caller invariant, not just its own validation.**
`sql_query.run_readonly_query` interpolates its ATTACH alias directly into SQL text
(`f"ATTACH DATABASE ? AS {alias}"` — the path is parameterized, the alias is not). Today the only
production caller passes an alias already validated at `Store` construction, and
`ATTACH_ALIAS_RE` (`^[A-Za-z_][A-Za-z0-9_]*$`) plus sqlite3's own refusal of multi-statement
`execute()` calls both independently reject injection-shaped input — not exploitable as shipped.
But the function's own safety currently rests on an invariant held by a *different* component,
documented only in a docstring. A future caller that skips that validation reopens the question
with nothing failing loudly. If you call `run_readonly_query` directly, validate your own alias
first; do not assume the callee re-checks it.

**Tessguard's audit completeness has one deliberate blind spot.** `transcript.py` counts writes
using `WRITE_TOOL_NAMES`, a hardcoded allowlist of tool names. A write from any tool outside that
set is invisible to the audit. An *incomplete parse* is correctly surfaced as `coverage_incomplete`
and folds into a `flagged` result rather than presenting as clean — but an *unrecognized write
tool* does not currently produce the same signal. If you extend the harness with new write-capable
tools, tessguard's audit will not see them until `WRITE_TOOL_NAMES` is updated.

**Actor identity is self-asserted, not authenticated.** Every `actor` field is a client-asserted
string; there is no authentication layer anywhere, by design. The hash chain proves records were
not altered after the fact — it does not establish who actually wrote them. Do not read chain
integrity as attribution integrity; they are different guarantees.

**`actor_ref_split` exists in schema but nothing currently populates it.** The `solo`-profile
privacy layer is present structurally (`events.actor_ref`, an `identity_registry` table outside
event-sourcing) but nothing writes to either column today — every event carries only the raw
client-asserted `actor`, and `actor_ref` is always `NULL`. This is the documented, decided state
at `solo` (no OAuth/DSAR/directory integration until a real second tenant exists), not an
in-progress gap — but a column being present is not the same claim as the layer being active, and
it is easy to check the schema and stop there.

## Verified clean

- **B1.** The server binds `127.0.0.1` explicitly, never a wildcard. Origin is checked on
  state-changing methods and absent on GET; no GET mutates state and no
  `Access-Control-Allow-Origin` is ever emitted, so a hostile page can issue a cross-origin GET but
  cannot read the response.
- **B3.** Every `commit_sha` reaching a git subprocess is validated first, by a single shared
  `common.validate_commit_sha`, at every call site across store, api and gitops.
- **B4, read-only half.** `mode=ro` URI, `PRAGMA query_only=ON`, and an authorizer that denies
  runtime `ATTACH`/`DETACH` — three independent mechanisms, none depending on parsing the query
  text.
- **B9.** Docs-root path resolution resolves both sides and checks `is_relative_to`, which also
  defeats a symlink planted inside the docs root.
- **B10.** Blob paths are `blobs_dir / sha256_hex(data)`; the user-supplied filename is only ever a
  database column and never touches a filesystem path.
- **B12.** No ATLAS-facing code exists yet in this boundary, so there is nothing to check.

## Data handled

- **Transcript ingestion is minimized.** Claude Code session transcripts are the highest-risk data
  source in the system — prompts, file contents, tool payloads. tessguard's transcript scan
  streams them line by line and retains exactly `first_cwd`, `in_repo_edits`, `session_start`,
  `session_end` and `coverage_incomplete` — counts, timestamps, and one path. No prompt text, no
  file contents, no tool payloads are retained, and nothing derived from a transcript is written
  into the event-sourced data model. The one retained path (`first_cwd`) is what surfaces in the
  audit-log limitation above.
- **Export purpose limitation.** Export introduces no new personal-data field, no new retention (it
  is a projection of data already stored), and moves nothing across a project boundary.

## Publishing checklist

Before a first `git push` of a repository that uses TESSERA/tessguard:

1. Confirm the audit log path is gitignored, or is writing repo-relative rather than absolute
   paths (see the audit-log limitation above).
2. If any copy of the audit log has already been committed, remove it — a plain deletion for an
   unpushed repo; a history rewrite if it has ever been pushed.
3. Confirm `git remote -v` reflects what you intend to publish, and that no working tree elsewhere
   depends on refs a history rewrite would invalidate.
4. Treat this document's "Known limitations" section as a disclosure checklist, not a to-do list —
   several of these are documented, decided trade-offs (self-asserted identity, `actor_ref_split`
   inert at `solo`), not defects to fix before shipping.
