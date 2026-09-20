-- ATLASSN-98/REQ-77. Additive: one new view, one view re-pointed. No ALTER on any table any
-- prior migration defined, so versions 1-23's recorded ddl_sha256 values are unchanged.
-- Re-runnable: CREATE VIEW IF NOT EXISTS for v_git_commit_detail; DROP VIEW IF EXISTS before the
-- CREATE for v_source_freshness (migration 7/17's own precedent -- a plain CREATE VIEW cannot
-- alter an existing view's SELECT text).
--
-- Grain: one row per file changed per commit (ARCHITECTURE.md section 39). LEFT JOIN, not INNER
-- -- a toy-modeled correction: 6 real commits across all projects have zero git_commit_file
-- rows, which an INNER JOIN would silently drop with no error (query/GOALS.json C8/F5). NULL
-- insertions/deletions on git_commit_file are preserved, never COALESCEd to 0 -- "not counted"
-- and "no change" are different real states (5,070 of 58,880 rows carry NULL today).
-- author_hash only, never a raw author field (D8) and no session_id column or join, ever (C9).
CREATE VIEW IF NOT EXISTS v_git_commit_detail AS
SELECT
    gc.sha            AS commit_sha,
    gc.project_prefix AS repo,
    gc.committed_ts   AS timestamp,
    gc.author_hash    AS author_hash,
    gcf.file_path     AS file_path,
    gcf.insertions    AS insertions,
    gcf.deletions     AS deletions
FROM git_commit gc
LEFT JOIN git_commit_file gcf
    ON gcf.project_prefix = gc.project_prefix AND gcf.sha = gc.sha;

-- C11: git_commit gains a v_source_freshness row, same UNION-ALL pattern migration 17 already
-- used for session_transcript/subagent_transcript. Freshness is measured against
-- ingest_run.started_at via git_commit.ingest_run_id (when this repo's commits were last
-- PULLED), not committed_ts (when the commit itself happened) -- a repo untouched for a year
-- that was ingested five minutes ago is fresh, not stale, and conflating the two would report
-- the wrong thing. byte_offset has no real meaning for a git-log walk (unlike a byte-oriented
-- transcript tail) and is left NULL rather than a misleading 0, matching this component's own
-- NULL-means-not-tracked discipline elsewhere (e.g. dim_session.claude_version).
DROP VIEW IF EXISTS v_source_freshness;
CREATE VIEW v_source_freshness AS
SELECT s.source_name, s.source_path, s.stream_id, s.byte_offset, s.rows_ingested, s.updated_at,
       ROUND((julianday('now') - julianday(s.updated_at)) * 24 * 60, 1) AS staleness_minutes
FROM ingest_source s
UNION ALL
SELECT 'session_transcript',
       '<projects_root>/*/*.jsonl',
       'multi-file (corpus, cumulative)',
       COALESCE(SUM(t.byte_offset), 0),
       COALESCE(SUM(t.calls_ingested), 0),
       MAX(t.updated_at),
       ROUND((julianday('now') - julianday(MAX(t.updated_at))) * 24 * 60, 1)
FROM session_transcript t
UNION ALL
SELECT 'subagent_transcript',
       '<projects_root>/*/*/subagents/**/agent-*.jsonl',
       'multi-file (corpus, cumulative)',
       COALESCE(SUM(a.byte_offset), 0),
       COALESCE(SUM(a.calls_ingested), 0),
       MAX(a.updated_at),
       ROUND((julianday('now') - julianday(MAX(a.updated_at))) * 24 * 60, 1)
FROM subagent_transcript a
UNION ALL
SELECT 'git_commit',
       '<source_root>/.git (multi-repo)',
       'multi-repo (corpus, cumulative)',
       NULL,
       COUNT(gc.sha),
       MAX(ir.started_at),
       ROUND((julianday('now') - julianday(MAX(ir.started_at))) * 24 * 60, 1)
FROM git_commit gc
JOIN ingest_run ir ON ir.run_id = gc.ingest_run_id;
