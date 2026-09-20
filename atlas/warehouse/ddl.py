"""Extracts the warehouse DDL and seed SQL from ARCHITECTURE.md section 16 at import/test
time, rather than keeping a second, hand-copied version of the same SQL in the implementation.
The two can drift silently otherwise -- exactly the failure this project's F2/F3 failure
signatures name (a claim with no reproduction path; a reference assumed rather than checked).

ARCHITECTURE.md section 16 has exactly two fenced ```sql blocks: the schema (CREATE TABLE /
CREATE VIEW / INSERT INTO snapshot_source) and, after "### Seed data, applied at schema
creation", the dq_check / dq_known_metric_bug seed INSERTs. Located by heading text and fence
order, not by hardcoded line numbers, so an unrelated edit elsewhere in the document does not
silently point this at the wrong block.

ATLASSN-188, 2026-09-20 -- migrations 23+ source their DDL from LOCAL FILES, not ARCHITECTURE.md.
This is a real, evidence-based architecture-reversal decision (PDP.md section 11.6), not a
routine implementation change -- corrected mid-ticket after Bob caught the orchestrating
session's first framing ("never architecture, zero hits") as a bad grep, not a real absence.
ARCHITECTURE.md line 1922, verbatim: "In a system whose entire migration discipline is that the
document *is* the schema -- `ddl.py` extracts it, `schema_migration` hashes it, `migrate.apply()`
refuses on drift -- silently making a recorded migration's text wrong is a bad trade." Section
23.2 restates the same discipline for why migration 7 is a new section rather than an edit to
22.4. Both independently re-verified directly (not paraphrased) before this file was touched.

SCOPE, DECIDED NARROW ON PURPOSE: migrations 1-22 (`extract_schema_and_seed_sql` through
`extract_migration22_sql`) are UNCHANGED below, byte-for-byte, still reading live from
ARCHITECTURE.md exactly as before. Moving their DDL retroactively into local files would need
20 migrations' worth of SQL text hand-transcribed with zero execution available to verify
byte-parity against every already-migrated warehouse's recorded hash -- exactly the
retroactive-rehash risk that was flagged before building anything, and the orchestrating
session's own corrected decision (ATLASSN-188, logged with evidence and what-would-overturn-it)
confirms: only NEW migrations (23, 24, and future) source DDL from `atlas/warehouse/migrations/`
instead. The architectural discipline line 1922 names -- "the document is the schema" -- is
satisfied by migrations 1-22's continuity, which this change does not touch; it is not violated
by 23+ living somewhere else, since ARCHITECTURE.md never claimed that EVERY future migration
must also live in its own bytes, only that a recorded migration's own text must stay true once
written (which local-file migrations satisfy exactly the same way, just in a different file).

ATLASSN-189, 2026-09-20 -- migration 25 (Iris Chen's real finding): two triggers on dq_check
preventing a severity='contract' row from ever targeting an advisory-only source_table. Local
file, same mechanism ATLASSN-188 established -- no ARCHITECTURE.md edit needed, since this is a
guard on an EXISTING table's write path, not a new table or view with its own document section.
"""

import hashlib
import re
from pathlib import Path

ARCHITECTURE_PATH = Path(__file__).resolve().parent.parent.parent / "ARCHITECTURE.md"

_SECTION_HEADING = "## 16. Warehouse DDL"
# ATLASSN-27. Every migration's DDL lives in its OWN section with its own heading, and this is
# not a stylistic choice: anything added INSIDE section 16 changes ddl_sha256() and makes
# migrate.apply() raise MigrationError against every database that already recorded migration 1
# -- including the live warehouse, once every 60 seconds, from the job this section exists for.
# Verified rather than assumed: migration 1's hash is unchanged at 25ad4065... after each of
# sections 18 through 23 was added.
#
# Amended by ATLASSN-47. This comment used to justify the separate-section rule by saying
# extract_schema_and_seed_sql() "takes the first two sql fences after section 16's heading" and
# that later sections therefore do not collide with it. That was an accurate description of the
# code and a fragile reason to rely on: it held only because section 16's two fences happened to
# come first in document order. The extractor is now BOUNDED to section 16's own extent, so the
# separation is enforced rather than merely observed. The rule above stands unchanged -- it is
# still true that editing section 16 breaks migration 1 -- but it no longer depends on fence
# ordering to be safe.
_MIGRATION2_HEADING = "### 18.6 Migration 2 DDL"
_MIGRATION3_HEADING = "### 19.4 Migration 3 DDL"
_MIGRATION4_HEADING = "### 20.4 Migration 4 DDL"
_MIGRATION5_HEADING = "### 21.3 Migration 5 DDL"
_MIGRATION6_HEADING = "### 22.4 Migration 6 DDL"
_MIGRATION7_HEADING = "### 23.4 Migration 7 DDL"
_MIGRATION8_HEADING = "### 24.4 Migration 8 DDL"
_MIGRATION9_HEADING = "### 25.4 Migration 9 DDL"
_MIGRATION10_HEADING = "### 26.6 Migration 10 DDL"
_MIGRATION11_HEADING = "### 27.6 Migration 11 DDL"
_MIGRATION12_HEADING = "### 28.4 Migration 12 DDL"
_MIGRATION13_HEADING = "### 29.4 Migration 13 DDL"
_MIGRATION14_HEADING = "### 30.4 Migration 14 DDL"
_MIGRATION15_HEADING = "### 31.4 Migration 15 DDL"
_MIGRATION16_HEADING = "### 32.5 Migration 16 DDL"
_MIGRATION17_HEADING = "### 33.5 Migration 17 DDL"
_MIGRATION18_HEADING = "### 35.1 Migration 18 DDL"
_MIGRATION19_HEADING = "### 36.1 Migration 19 DDL"
_MIGRATION20_HEADING = "### 37.3 Migration 20 DDL"
_MIGRATION21_HEADING = "### 38.2 Migration 21 DDL"
# ATLASSN-181. PLACEHOLDER heading, not yet real, and known stale ("39" is taken by ATLASSN-98's
# v_git_commit_detail). Untouched by ATLASSN-188 -- migration 22 is a different ticket's own
# placeholder, still on the ARCHITECTURE.md-extraction path pending whoever owns ATLASSN-181;
# not this ticket's call to move it, per the same "not this ticket's own placeholder to correct"
# convention already applied elsewhere in this proposal batch.
# stub-ok: placeholder value below, pending Clint's real ARCHITECTURE.md section number
_MIGRATION22_HEADING = "### 39.2 Migration 22 DDL"
_FENCE_RE = re.compile(r"^```sql\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
# Matches only a level-2 heading ("## ..."), never section 16's own internal level-3
# subheadings ("### Seed data, applied at schema creation") -- used to find where section 16
# ENDS, so extraction cannot run past its own extent into a later section (ATLASSN-47, ported
# from dev-harness DEVH-40 / its docs/GOALS.json C3).
NEXT_LEVEL2_HEADING_RE = re.compile(r"^## ", re.MULTILINE)

# ATLASSN-188. Where a NEW migration's DDL lives instead of ARCHITECTURE.md, starting at
# migration 23. One file per migration, named by version and a short slug -- no numbering
# collision risk with ARCHITECTURE.md section numbers (which this mechanism no longer needs at
# all), and no MAX_EDIT_BYTES concern either: a new migration's DDL is a new small file, not an
# edit to a 400KB+ document. Sibling to this module, not nested under atlas/warehouse/tests/, so
# it ships with the package.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _read_local_migration_sql(filename, label):
    """The exact text of a local migration file under MIGRATIONS_DIR. Same fail-loud contract
    as _extract_single_fence below (F1's fail-open signature): a missing or empty file raises
    DdlExtractionError rather than returning a partial or empty string silently. No fence
    parsing needed -- the whole file IS the SQL, since there is no surrounding prose to bound
    the extraction against (that bounding problem is specific to sharing one document with
    everything else ARCHITECTURE.md describes, which is exactly the coupling this mechanism
    exists to not have)."""
    path = MIGRATIONS_DIR / filename
    if not path.is_file():
        raise DdlExtractionError(f"migration file {path} does not exist")
    sql = path.read_text(encoding="utf-8")
    if not sql.strip():
        raise DdlExtractionError(f"{label} sql file {path} is empty")
    return sql


class DdlExtractionError(RuntimeError):
    pass


def _read_architecture_text():
    if not ARCHITECTURE_PATH.is_file():
        raise DdlExtractionError(f"{ARCHITECTURE_PATH} does not exist")
    return ARCHITECTURE_PATH.read_text(encoding="utf-8")


def extract_schema_and_seed_sql():
    """Returns (schema_sql, seed_sql), the exact text inside the two ```sql fences following
    section 16's heading, in document order. Raises DdlExtractionError if the heading is
    missing, or fewer than two sql-fenced blocks follow it -- fails loud, never returns a
    partial or empty string silently (F1's fail-open signature).

    Bounded to section 16's own extent: search stops at the next level-2 heading ("## ...")
    after section 16's, or end of file if section 16 is the last section. Without this bound,
    a section-16 edit that drops one of its own two fences while ANY later section has a
    fenced sql block passes the fence-count check (still >= 2 matches) and silently returns
    that later block as this section's schema or seed SQL. That is not hypothetical here:
    this document carries six later migration-DDL fences (sections 18.6, 19.4, 20.4, 21.3,
    22.4, 23.4), every one of them a viable decoy. Ported from dev-harness DEVH-40, where the
    same fix is a no-op because that vendored copy has zero later fences -- the side holding
    the fix had nothing to fail open onto, and the side with the decoys had no fix
    (ATLASSN-47). Never hardcodes a line number for this bound: an unrelated edit elsewhere
    would then silently misdirect extraction, the same failure class in a different disguise
    -- the next level-2 heading is located by scanning, so it moves with the document."""
    text = _read_architecture_text()
    heading_idx = text.find(_SECTION_HEADING)
    if heading_idx == -1:
        raise DdlExtractionError(
            f"heading {_SECTION_HEADING!r} not found in {ARCHITECTURE_PATH}"
        )
    next_heading_match = NEXT_LEVEL2_HEADING_RE.search(text, heading_idx + len(_SECTION_HEADING))
    section_end = next_heading_match.start() if next_heading_match else len(text)
    tail = text[heading_idx:section_end]
    matches = list(_FENCE_RE.finditer(tail))
    if len(matches) < 2:
        raise DdlExtractionError(
            f"expected at least 2 fenced sql blocks after {_SECTION_HEADING!r}, found "
            f"{len(matches)}"
        )
    schema_sql = matches[0].group(1)
    seed_sql = matches[1].group(1)
    if not schema_sql.strip():
        raise DdlExtractionError("extracted schema sql block is empty")
    if not seed_sql.strip():
        raise DdlExtractionError("extracted seed sql block is empty")
    return schema_sql, seed_sql


def ddl_sha256():
    """sha256 of the exact schema SQL text extracted above -- stored in schema_migration so a
    schema that drifted from its recorded migration is mechanically detectable (ARCHITECTURE.md
    section 16's own stated purpose for this column)."""
    schema_sql, _ = extract_schema_and_seed_sql()
    return hashlib.sha256(schema_sql.encode("utf-8")).hexdigest()


def _extract_single_fence(heading, label):
    """The first fenced sql block after `heading`. Shared by every additive migration THROUGH
    22 so a new one is a heading constant rather than another near-copy of this function. Raises
    rather than returning an empty or partial string (F1's fail-open signature).

    ATLASSN-188: migrations 23+ use _read_local_migration_sql() above instead -- this function's
    own contract (fail loud, never partial) is unchanged and still governs migrations 2-22."""
    text = _read_architecture_text()
    heading_idx = text.find(heading)
    if heading_idx == -1:
        raise DdlExtractionError(f"heading {heading!r} not found in {ARCHITECTURE_PATH}")
    match = _FENCE_RE.search(text[heading_idx:])
    if match is None:
        raise DdlExtractionError(f"no fenced sql block found after {heading!r}")
    sql = match.group(1)
    if not sql.strip():
        raise DdlExtractionError(f"extracted {label} sql block is empty")
    return sql


def extract_migration2_sql():
    """Section 18.6 -- the live plane's additive schema (ATLASSN-27)."""
    return _extract_single_fence(_MIGRATION2_HEADING, "migration-2")


def extract_migration3_sql():
    """Section 19.4 -- the deny-join's additive schema (ATLASSN-32)."""
    return _extract_single_fence(_MIGRATION3_HEADING, "migration-3")


def extract_migration4_sql():
    """Section 20.4 -- main-session transcript ingest and the join-coverage buckets
    (ATLASSN-33)."""
    return _extract_single_fence(_MIGRATION4_HEADING, "migration-4")


def migration4_sha256():
    return hashlib.sha256(extract_migration4_sql().encode("utf-8")).hexdigest()


def extract_migration5_sql():
    """Section 21.3 -- demotes fail_open_not_double_counted to advisory and promotes
    fail_open_pairing_delta to contract, on the resolution_rate_delta precedent (ATLASSN-35)."""
    return _extract_single_fence(_MIGRATION5_HEADING, "migration-5")


def migration5_sha256():
    return hashlib.sha256(extract_migration5_sql().encode("utf-8")).hexdigest()


def extract_migration6_sql():
    """Section 22.4 -- v_pip_coverage and v_handler_freshness, the hook-vs-skill coverage fix
    (ATLASSN-36/38)."""
    return _extract_single_fence(_MIGRATION6_HEADING, "migration-6")


def migration6_sha256():
    return hashlib.sha256(extract_migration6_sql().encode("utf-8")).hexdigest()


def extract_migration7_sql():
    """Section 23.4 -- puts v_handler_freshness behind the trust sentinel migration 6 omitted
    (ATLASSN-51). DROP VIEW + CREATE VIEW, wrapped in its own BEGIN IMMEDIATE/COMMIT."""
    return _extract_single_fence(_MIGRATION7_HEADING, "migration-7")


def migration7_sha256():
    return hashlib.sha256(extract_migration7_sql().encode("utf-8")).hexdigest()


def extract_migration8_sql():
    """Section 24.4 -- bash_command_shape table plus its two base-rate views (ATLASSN-61)."""
    return _extract_single_fence(_MIGRATION8_HEADING, "migration-8")


def migration8_sha256():
    return hashlib.sha256(extract_migration8_sql().encode("utf-8")).hexdigest()


def extract_migration9_sql():
    """Section 25.4 -- hook_verdict.ledger_origin and .origin_signal (ATLASSN-62)."""
    return _extract_single_fence(_MIGRATION9_HEADING, "migration-9")


def migration9_sha256():
    return hashlib.sha256(extract_migration9_sql().encode("utf-8")).hexdigest()


def extract_migration10_sql():
    """Section 26.6 -- registers ledger_join_coverage_delta in dq_check (ATLASSN-63).

    26.6, not 26.4. Section 26 has five subsections before its DDL rather than three, so the DDL
    does not sit at the .4 every section from 18 to 25 puts it at. Harmless because this module
    locates fences by exact heading text and never by number, and disclosed in section 26 itself
    so a reader does not pattern-match it as the real numbering defect section 25 carries."""
    return _extract_single_fence(_MIGRATION10_HEADING, "migration-10")


def migration10_sha256():
    return hashlib.sha256(extract_migration10_sql().encode("utf-8")).hexdigest()


def extract_migration11_sql():
    """Section 27.6 -- ingest_run.plane, so the fast plane's ticks stop being indistinguishable
    from batch runs to the five checks that select comparison points from that table
    (ATLASSN-72)."""
    return _extract_single_fence(_MIGRATION11_HEADING, "migration-11")


def migration11_sha256():
    return hashlib.sha256(extract_migration11_sql().encode("utf-8")).hexdigest()


def extract_migration12_sql():
    """Section 28.4 -- session_prior, a per-session deny history for Bob (FORE-281 item 1,
    ATLASSN-80). CREATE VIEW only, no ALTER on any table a prior migration defined."""
    return _extract_single_fence(_MIGRATION12_HEADING, "migration-12")


def migration12_sha256():
    return hashlib.sha256(extract_migration12_sql().encode("utf-8")).hexdigest()


def extract_migration13_sql():
    """Section 29.4 -- registers hook_coverage_per_session (ATLASSN-84) and build_process_ratio
    (ATLASSN-85) in dq_check. Data-only, two INSERTs, exactly like migrations 5 and 10."""
    return _extract_single_fence(_MIGRATION13_HEADING, "migration-13")


def migration13_sha256():
    return hashlib.sha256(extract_migration13_sql().encode("utf-8")).hexdigest()


def extract_migration14_sql():
    """Section 30.4 -- registers turn_final_bytes_per_session_day_delta and
    sendmessage_bytes_per_session_day_delta (ATLASSN-88) in dq_check. Data-only, two INSERTs,
    exactly like migrations 5, 10, and 13."""
    return _extract_single_fence(_MIGRATION14_HEADING, "migration-14")


def migration14_sha256():
    return hashlib.sha256(extract_migration14_sql().encode("utf-8")).hexdigest()


def extract_migration15_sql():
    """Section 31.4 -- bash_command_shape_v2, a parallel table (not an ALTER of migration 8's
    bash_command_shape) carrying Feature A + Feature B's two new columns (ATLASSN-95). See
    section 31.1 for why this is additive-parallel rather than additive-ALTER, on migration 4's
    own precedent for the same constraint-can't-relax-in-place reason."""
    return _extract_single_fence(_MIGRATION15_HEADING, "migration-15")


def migration15_sha256():
    return hashlib.sha256(extract_migration15_sql().encode("utf-8")).hexdigest()


def extract_migration16_sql():
    """Section 32.5 -- v_gaming_evasion_by_session, a single-trust_state live view joining
    hook_verdict, bash_command_shape_v2, and session_prior (ATLASSN-96). See section 32.2 for why
    this view needs only one trust_state, not session_prior's split."""
    return _extract_single_fence(_MIGRATION16_HEADING, "migration-16")


def migration16_sha256():
    return hashlib.sha256(extract_migration16_sql().encode("utf-8")).hexdigest()


def extract_migration17_sql():
    """Section 33.5 -- re-points v_source_freshness so the two transcript corpora are visible
    sources, and registers the two contract checks that let v_queryable_source name them
    (ATLASSN-102)."""
    return _extract_single_fence(_MIGRATION17_HEADING, "migration-17")


def migration17_sha256():
    return hashlib.sha256(extract_migration17_sql().encode("utf-8")).hexdigest()


def extract_migration18_sql():
    """Section 35.1 -- two partial unique indexes deduplicating session_tool_call and
    session_assistant_text on (transcript_id, record_uuid, block_index) WHERE record_uuid IS NOT
    NULL (ATLASSN-144). Must be applied only after the ON CONFLICT-based insert path is already
    live in session_pull.py -- see section 35's sequencing note; this function only extracts and
    hashes the DDL, it does not enforce that ordering."""
    return _extract_single_fence(_MIGRATION18_HEADING, "migration-18")


def migration18_sha256():
    return hashlib.sha256(extract_migration18_sql().encode("utf-8")).hexdigest()


def extract_migration19_sql():
    """Section 36.1 -- registers the session_tool_call_evidence_attrs_present dq_check seed row
    at contract severity (ATLASSN-143). The checker itself already landed in dq_runner.py's
    CHECKERS at commit dbed288 -- code-before-DDL, per section 26.2/29.4's convention."""
    return _extract_single_fence(_MIGRATION19_HEADING, "migration-19")


def migration19_sha256():
    return hashlib.sha256(extract_migration19_sql().encode("utf-8")).hexdigest()


def extract_migration20_sql():
    """Section 37.3 -- integration_interface (a new source table for claude-hooks-v2's
    GOALS.integration.json declarations) and v_integration_progress, the view over it
    (ATLASSN-153). No dependency on any prior migration's tables."""
    return _extract_single_fence(_MIGRATION20_HEADING, "migration-20")


def migration20_sha256():
    return hashlib.sha256(extract_migration20_sql().encode("utf-8")).hexdigest()


def extract_migration21_sql():
    """Section 38.2 -- registry_assertion (a new source table mirroring the enforcement-plane
    verification registry's own `assertion` table at ~/.claude/foreman/registry/registry.db)
    and v_registry_assertion, the view over it (ATLASSN-154). No dependency on any prior
    migration's tables. Reviewed and ledger-bound 2026-09-14 (clint-eastwood)."""
    return _extract_single_fence(_MIGRATION21_HEADING, "migration-21")


def migration21_sha256():
    return hashlib.sha256(extract_migration21_sql().encode("utf-8")).hexdigest()


def extract_migration22_sql():
    """Section 39.2 (placeholder pending Clint's real ARCHITECTURE.md entry -- see
    _MIGRATION22_HEADING's own comment, now flagged stale by ATLASSN-98). v_registered_never_
    proven_recent (ATLASSN-181): joins v_gate_proven_live (f10_proven_live=0) against
    v_handler_freshness.last_seen, filtered to a real recency window and excluding non-hook
    contamination (bash-fragment handler_ids that don't end .py/.sh, literal 'PREFIX'
    template-placeholder leaks, '*probe.py' diagnostic scripts) -- so a genuinely stale/deleted
    hook (persona_decision_rights_gate.py, last fired 11 days before this migration was drafted)
    does not sit alongside something still firing and never resolving. No dependency on any
    prior migration's tables -- both source views already exist. Drafted and validated against
    the real, live warehouse 2026-09-18; not yet applied (the ARCHITECTURE.md entry this
    extraction depends on does not exist yet).

    ATLASSN-188: left on the ARCHITECTURE.md-extraction path deliberately -- this is ATLASSN-181's
    own ticket and placeholder, not moved here unilaterally. The new local-file mechanism below
    is available to it whenever whoever owns that ticket chooses to adopt it."""
    return _extract_single_fence(_MIGRATION22_HEADING, "migration-22")


def migration22_sha256():
    return hashlib.sha256(extract_migration22_sql().encode("utf-8")).hexdigest()


def extract_migration23_sql():
    """ATLASSN-104/REQ-64, session_block_sequence. Corrected by ATLASSN-188 (2026-09-20, a real
    architecture-reversal decision under PDP.md section 11.6, not a routine change -- see this
    module's own top docstring): sourced from `atlas/warehouse/migrations/
    0023_session_block_sequence.sql` rather than an ARCHITECTURE.md heading. No ARCHITECTURE.md
    edit is needed to land this migration at all, which is the entire point -- the prior version
    of this function depended on a document section (`### 40.1 Migration 23 DDL`) that could not
    exist in the frozen document without triggering a full architecture-gate re-review for a
    structural table addition unrelated to anything ARCHITECTURE.md's frozen content actually
    governs."""
    return _read_local_migration_sql("0023_session_block_sequence.sql", "migration-23")


def migration23_sha256():
    return hashlib.sha256(extract_migration23_sql().encode("utf-8")).hexdigest()


def extract_migration24_sql():
    """ATLASSN-98/REQ-77, v_git_commit_detail + v_source_freshness re-point. Corrected by
    ATLASSN-188 (2026-09-20, same architecture-reversal decision as migration 23 above): sourced
    from `atlas/warehouse/migrations/0024_git_commit_detail.sql` rather than an ARCHITECTURE.md
    heading (`### 39.1 Migration 24 DDL`, which never actually existed in the document as of
    this proposal -- see the now-superseded ATLASSN-98-ARCHITECTURE-DDL-HANDOFF.md for that
    history)."""
    return _read_local_migration_sql("0024_git_commit_detail.sql", "migration-24")


def migration24_sha256():
    return hashlib.sha256(extract_migration24_sql().encode("utf-8")).hexdigest()


def extract_migration25_sql():
    """ATLASSN-189 (Iris Chen's real, executed probe). Two triggers on dq_check, preventing a
    severity='contract' row from ever targeting an advisory-only source_table
    (session_assistant_text/session_tool_call) -- ARCHITECTURE.md section 8 addendum A4's own
    stated invariant, enforced until now only by a Python-side static-map test
    (test_view_dependency_drift.py). Local file, same ATLASSN-188 mechanism: no ARCHITECTURE.md
    edit needed, since this guards an EXISTING table's write path rather than adding a new table
    or view that would need its own document section."""
    return _read_local_migration_sql("0025_dq_check_advisory_guard.sql", "migration-25")


def migration25_sha256():
    return hashlib.sha256(extract_migration25_sql().encode("utf-8")).hexdigest()


def extract_migration26_sql():
    """ATLASSN-197 (Nadia Osei's real STRIDE/validate finding, Build 8). session_credential_ack --
    the session-side analog of subagent_credential_ack, needed because session_pull.py had no
    credential scan of any kind (hardcoded credential_hits = 0) and the extended scan this ticket
    adds (deny_text on both subagent_tool_result and session_tool_result, plus a real session-side
    scan of tool_input_json) needs a real triage path for both new sources, per section 8's own
    stated lesson about a self-check with no acknowledgement mechanism. Same local-file mechanism
    ATLASSN-188 established for migrations 23+: no ARCHITECTURE.md edit needed, since this adds one
    new table with no document section of its own to keep in sync."""
    return _read_local_migration_sql("0026_session_credential_ack.sql", "migration-26")


def migration26_sha256():
    return hashlib.sha256(extract_migration26_sql().encode("utf-8")).hexdigest()


def migration3_sha256():
    """Recorded as schema_migration version 3. Independent of versions 1 and 2 for the same
    reason 2 is independent of 1: a change to one section's DDL must never invalidate another
    migration's recorded hash and force a re-ingest of data that did not change."""
    return hashlib.sha256(extract_migration3_sql().encode("utf-8")).hexdigest()


def migration2_sha256():
    """Same purpose as ddl_sha256(), for migration 2. Recorded separately in schema_migration so
    the two migrations drift-detect independently -- a change to section 18.6 must not be able to
    invalidate migration 1's recorded hash, which is the whole reason this schema is a second
    migration rather than an addition to section 16."""
    return hashlib.sha256(extract_migration2_sql().encode("utf-8")).hexdigest()
