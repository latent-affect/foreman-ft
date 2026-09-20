-- ATLASSN-104/REQ-64. Additive: one new table, one new index. Alters no table any prior
-- migration defined, so versions 1-22's recorded hashes are unchanged. Re-runnable: CREATE
-- TABLE/INDEX IF NOT EXISTS.
--
-- Structural only, deliberately no content column (section 40's own decision, alongside
-- ATLASSN-11's already-open redaction question) -- session_assistant_text/session_tool_call
-- remain the content-bearing tables for their own types. One row per block, every block, any
-- type, at every position, for every session-transcript assistant record -- including a type
-- none of map_transcript_line()'s existing branches name (thinking, fallback, and anything not
-- yet observed).
CREATE TABLE IF NOT EXISTS session_block_sequence (
    block_seq_id  INTEGER PRIMARY KEY,
    transcript_id INTEGER NOT NULL REFERENCES session_transcript(transcript_id),
    byte_offset   INTEGER NOT NULL,
    block_index   INTEGER NOT NULL,
    pull_run_id   INTEGER NOT NULL REFERENCES subagent_pull_run(pull_run_id),
    ts            TEXT,
    record_uuid   TEXT,
    block_type    TEXT,
    UNIQUE (transcript_id, byte_offset, block_index)
);
CREATE INDEX IF NOT EXISTS ix_sbs_transcript ON session_block_sequence(transcript_id, block_index);
