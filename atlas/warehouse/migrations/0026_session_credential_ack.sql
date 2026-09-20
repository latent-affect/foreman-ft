-- ATLASSN-197 (Nadia Osei's real STRIDE/validate finding, Build 8). session_pull.py has NO
-- credential scan of any kind today -- it hardcodes credential_hits = 0 on every run, confirmed
-- by direct read. This table is the session-side analog of subagent_credential_ack (ARCHITECTURE.md
-- section 16), needed for the SAME reason that table exists: a credential-pattern scan with no
-- triage path is a gate that alarms on the first placeholder ("export API_KEY=your-key-here")
-- written into a real session transcript and then gets disabled, per section 8's own stated
-- lesson -- "a self-check that alarms on every run is worse than no self-check."
--
-- Serves BOTH of session_pull's real credential sources -- session_tool_call.tool_input_json
-- (the tool's own arguments) and session_tool_result.deny_text (a denied call's remedy text,
-- which ATLASSN-197 also traced a real leak path into: agent_dispatch_gate.py embeds up to 60
-- characters of the caller-supplied dispatch prompt into its own deny message). Keyed on
-- session_tool_call(call_id) for BOTH sources, resolving a deny_text hit to its call_id via the
-- (transcript_id, tool_use_id) join every other view in this schema already uses for that same
-- purpose (see v_transcript_deny_join, ARCHITECTURE.md section 20.5) -- not a new table per
-- source. This carries one disclosed, accepted trade-off: on the rare row where BOTH the call's
-- own tool_input_json AND its result's deny_text independently match a credential pattern with
-- DIFFERENT payload text, acknowledging one source overwrites the stored hash for the other,
-- which un-acknowledges it again on the next scan. That fails toward MORE scrutiny, never less --
-- it can only make a previously-silenced hit visible again, never silence a hit that was never
-- acknowledged -- so it is accepted rather than solved with a second table, matching this
-- migration's own scope (ATLASSN-197 asks for real scanning, not a redesign of the ack schema).
--
-- Identical shape to subagent_credential_ack, deliberately, down to the CHECK constraints -- this
-- is the same mechanism, applied to the other half of the live plane, not a new design.
CREATE TABLE session_credential_ack (
    call_id         INTEGER PRIMARY KEY REFERENCES session_tool_call(call_id),
    payload_sha256  TEXT NOT NULL,
    pattern_name    TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('false-positive','real-and-rotated')),
    rationale       TEXT NOT NULL,
    acknowledged_by TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    CHECK (length(trim(rationale)) > 0)
);
