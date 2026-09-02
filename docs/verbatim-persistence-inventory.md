# Verbatim-persistence inventory

DEVH-22 / PRD.md R22. R8 and R9 named two known violations of the scrub-at-capture
principle (`audit_event.payload_json`, and cross-project ledger routing). This
requirement is the enumeration R8/R9 were carved out of: every location in this
project where raw or verbatim externally sourced content is persisted, classified
`scrubbed-at-write`, `scrubbed-at-read`, `unscrubbed`, or `not-applicable` (the
location exists but never captures raw content in the first place), each traceable
to a specific file and line.

"Externally sourced" here means content whose bytes originate outside this project's
own generated/structured data: hook payloads, ticket free text a human or an agent
typed, tool output, a file's own contents. It excludes this project's own git history,
commit messages, and code -- self-generated, not "externally sourced" in the sense R8's
credential-scrub concern or R22's PII concern are about.

Per R22's verification clause, the architecture stage is not closed while any entry
below is unclassified. All ten are classified.

## Summary table

| # | Location | Content | Classification | Evidence |
|---|---|---|---|---|
| 1 | `atlas/ingest/audit_scrub.py` -> `audit_event.payload_json` | Raw hook-event payloads (tool inputs/outputs, cwd, session text) | **scrubbed-at-write** | `redact_payload()` at `audit_scrub.py:132`, verified by reading the row back from disk (DEVH-13) |
| 2 | `~/.claude/audit-plane/<project>/audit.jsonl` and `.../global-safety/safety.jsonl` | Raw `**payload` kwargs passed to `audit_append()`, including `repr(exc)` strings that can embed fragments of the tool input that caused the error | **unscrubbed** (deliberate, disclosed) | `bollard/audit_lib.py:54-58` (paths), `:122-141` (`audit_append`, no redaction step); PRD.md:665 explicitly defers this: *"Redaction of `safety.jsonl` itself. R8 covers the derived warehouse. The append-only source is a separate decision with different reversibility."* |
| 3 | `tessera/store/schema.py` -> `tickets.description`, `tickets.repro_steps`, `tickets.environment` | Free-text ticket fields, human- or agent-authored, may contain PII | **unscrubbed** | Columns at `schema.py:74,77,78`. No scrub step anywhere in `store.py`'s write path (`create_ticket`, `set_description`, `set_field`, etc. all pass the caller's string straight to a parameterized `INSERT`/`UPDATE`) |
| 4 | `tessera/store/schema.py` -> `comments.body`, `comments.code_snippet` | Free-text comments and pasted code, may contain PII or secrets | **unscrubbed** | Columns at `schema.py:110-111`. `add_comment()` writes `body`/`code_snippet` verbatim into both the event payload and the projection row (DEVH-19's own comment-snippet work this session touched this same path without adding a scrub step, since none was in scope) |
| 5 | `tessera/store/schema.py` -> `events.payload` | The same free-text fields (#3, #4) plus every other ticket field, captured a second time inside each `TicketCreated`/`CommentAdded` event's JSON payload | **unscrubbed**, and structurally worse than #3/#4 | Column at `schema.py:12`. This table is append-only under a hash chain (`prev_hash`/`event_hash`, enforced by BEFORE triggers -- `tessera/store/GOALS.json` C1-C3): a row here cannot be redacted after the fact without breaking `verify_chain()`'s guarantee. Any future PII remediation for #3/#4 must be a NEW event type appending a correction (the same pattern `set_comment_code_snippet`'s backfill already establishes for a different purpose), never an UPDATE against `events` |
| 6 | `/Users/m5/dev/dev-harness/repo_context_part_1.txt`, `repo_context_part_2.txt` | A verbatim whole-repo text dump (every source and doc file concatenated under `FILE: <path>` headers) | **unscrubbed**, at rest | Measured directly 2026-09-02: `ls -la /Users/m5/dev/dev-harness/repo_context_part_*.txt` shows exactly **two** files (1,570,567 and 424,456 bytes), not the five (`_part_1` through `_part_5`) DEVH-22's own filed text names -- corrected here rather than repeated unverified. Untracked, worktree-local (same class of file R23's own GOALS.json constraints already document for the three measurement scripts), present in `/Users/m5/dev/dev-harness` and absent from this `dev-harness-run2` worktree. No scrub pass of any kind touches this dump; whatever the source tree contained at generation time is what it holds |
| 7 | `security_privacy_convergence.py` -> `run_presidio()` findings | PII detected by Presidio in source files | **not-applicable** (never captures raw content) | `security_privacy_convergence.py:333-338`: the finding dict carries `entity_type`, `score`, `source`, `file_path` only -- `r.text` (Presidio's own matched substring) is never read into the finding. The scanned file path is recorded; the matched PII text itself is not persisted anywhere by this tool |
| 8 | `security_privacy_convergence.py` -> `run_bandit()` / `run_detect_secrets()` findings | Static-analysis and secret-detection hits | **not-applicable** (never captures raw content) | `security_privacy_convergence.py:184-193` (bandit: file/line/test_id/severity/cwe only) and `:218-223` (detect-secrets: file/line/type only). Neither persists the matched code or secret value -- detect-secrets' own upstream design stores a hash of a secret in its baseline format, not the secret, and this wrapper doesn't even go that far; it drops the value entirely |
| 9 | `tessera/tessguard/audit.py` -> `.foreman/tessguard-audit-log.jsonl` | Session-audit results over a Claude Code transcript | **not-applicable** (never captures raw content) | `audit.py:137-146`: the logged `result` dict is transcript **path**, counts (`in_repo_edits`, `real_events`), and booleans -- never transcript message content. `append_log_internal` (`:31-36`) serializes exactly that dict, nothing more |
| 10 | `token_bloat_diagnostic.py` -> its own JSON/txt reports | Session transcript files (`~/.claude/projects/**/*.jsonl`), which can contain arbitrary tool/user content | **not-applicable** (never captures raw content) | `token_bloat_diagnostic.py:130-139`: each `records` entry carries `message_id`, `timestamp`, `model`, and `usage` token counts read from `message.usage` -- message `content`/`text` fields are never read. Confirmed by reading the parse loop (`:95-139`) in full, not inferred from the output schema alone |

## Related, not a separate row: R9 (cross-project ledger routing)

R9 is a **routing** defect, not a redaction defect -- rows in row #2's ledgers land under
the wrong project's `cwd` scope, not with different scrub treatment. It doesn't get its
own inventory row because it doesn't change row #2's classification, but it is still
open: `check_audit_ledger_partition_by_cwd` (`atlas/warehouse/dq_runner.py:276-291`,
registered `advisory` at `dq_runner.py:493`) has not been promoted to `contract`, and no
current misrouted-row count has been measured in this pass (PRD.md:321-324 already flags
the brief's cited figure of 477 as unverified). Left open, not fixed here -- R9 is its
own requirement.

## Scope and what was checked but excluded

Checked and deliberately excluded as out of R22's frame (content this project's own
git history, not "externally sourced"): commit messages, diffs, and this project's own
source/doc files under version control.

Checked and found to have no persistence mechanism at all (nothing to classify):
`bollard/verdict_ledger.py`'s hook-verdict ledger (`verdict_ledger.py:96-127`) --
metadata only (event name, decision, session/tool ids, timings), no `tool_input`
content of any kind ever enters `record_obj`.

Not checked in this pass, named rather than silently omitted: Claude Code's own
session transcript files under `~/.claude/projects/**/*.jsonl` are themselves a
verbatim-persistence point for every tool call this and every other project's
sessions make -- but they are written by the Claude Code harness itself, outside
this repository's code, so no file/line in *this* codebase can be cited for them.
Out of R22's "traceable to a specific line" verification clause by construction, not
by oversight.

## Confidence

High on rows 1-5 and 7-10 -- each is a direct line-level read of the write path, not
an inference from behavior. Medium-High on row 6 (the file count is measured directly,
but nothing scans the dump's actual contents for what specific sensitive material, if
any, made it into row 6's snapshot at generation time -- that would be a separate,
deeper pass). High on R9 being open, since its own registration in `dq_runner.py` is
read directly rather than assumed from the ticket text.
