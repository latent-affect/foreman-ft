-- ATLASSN-189 (Iris Chen's real, executed probe). ARCHITECTURE.md section 8 addendum A4 states
-- session_assistant_text and session_tool_call are advisory-only and can never appear in
-- v_queryable_source -- enforced today only by a Python-side static-map test
-- (test_view_dependency_drift.py), never at the schema level. A real INSERT of a
-- severity='contract' dq_check row against one of these two source_table values (confirmed live:
-- a clean warehouse, one INSERT, one matching dq_check_run row, and session_assistant_text
-- appears in v_queryable_source alongside the real sources) was never rejected by anything the
-- database itself enforces. No current ALLOWED_VIEWS entry maps to it, so fetch() is not
-- currently exploitable through this -- but v_evidence_act (ATLASSN-142) is explicitly named in
-- facade.py's own comment as depending on this invariant staying enforced once it lands, and
-- nothing stops a future accidental or malicious seed row from violating it silently.
--
-- Two triggers, not a CHECK constraint: SQLite cannot add a CHECK constraint to an existing
-- table without recreating it, and dq_check already holds real seed rows from migrations 1
-- through 24 that this migration must not disturb (additive only, per this project's own
-- migration discipline throughout ddl.py -- no ALTER, no rebuild). A trigger fires on the actual
-- INSERT/UPDATE path regardless of which migration or future code seeds or edits a dq_check row,
-- closing the gap at the one place every writer, present or future, must pass through.
--
-- FIX 2026-09-20 (this session's own real-execution self-test): the RAISE(ABORT, ...) message
-- was first written as two adjacent SQL string literals across two lines, on the mistaken
-- assumption (Python's own rule, not SQL's) that adjacent string literals auto-concatenate --
-- SQLite has no such rule, and executescript() raised a real syntax error the instant this was
-- run. Fixed to one unbroken string literal per trigger.
CREATE TRIGGER trg_dq_check_no_contract_on_advisory_insert
BEFORE INSERT ON dq_check
WHEN NEW.severity = 'contract'
     AND NEW.source_table IN ('session_assistant_text', 'session_tool_call')
BEGIN
    SELECT RAISE(ABORT, 'dq_check: contract severity is not allowed for an advisory-only source_table (session_assistant_text/session_tool_call) -- ARCHITECTURE.md section 8 addendum A4');
END;

CREATE TRIGGER trg_dq_check_no_contract_on_advisory_update
BEFORE UPDATE ON dq_check
WHEN NEW.severity = 'contract'
     AND NEW.source_table IN ('session_assistant_text', 'session_tool_call')
BEGIN
    SELECT RAISE(ABORT, 'dq_check: contract severity is not allowed for an advisory-only source_table (session_assistant_text/session_tool_call) -- ARCHITECTURE.md section 8 addendum A4');
END;
