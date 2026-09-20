"""Rebuilds cwd_project (and cwd_project_candidate) for a set of cwd values against the
current dim_project/project_root registry, applying the .foreman/tessera-prefix override where
one exists and is valid. Idempotent: an unchanged registry re-run leaves cwd_project identical;
a changed registry re-run updates the affected rows in place (ARCHITECTURE.md section 4)."""

import datetime

from . import matcher, override


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_projects(conn):
    rows = conn.execute(
        "SELECT project_prefix, source_root FROM dim_project WHERE source_root IS NOT NULL"
    ).fetchall()
    return [(prefix, root) for prefix, root in rows]


def resolve_one(cwd, projects):
    """Pure function: (cwd, projects) -> (resolution, matched_root, project_prefix, candidates).

    ATLASSN-9, resolved: ARCHITECTURE.md section 3's own words are "ATLAS reads that override
    where present and PREFERS IT OVER THE REGISTRY" -- no ambiguous-only qualifier. The original
    implementation was narrower than the document's own text, only consulting the override on
    an already-ambiguous match. Widened to match both the document's plain language and the
    real, pre-existing tessera_resolver.py precedent (imported by preflight_blocking_gate.py):
    a valid override at the matched root wins outright, over a unique match too, not only over
    an ambiguous one. Validated against ANY real registered prefix (matching
    tessera_resolver.py's own `if override in known_prefixes`), not narrowed to the tied
    candidates at that specific root -- an override's whole point is to name the CORRECT answer,
    which for a unique-but-wrong match is by definition not among the "candidates" the naive
    matcher found.

    Structural gap not closed here, disclosed rather than silently assumed: tessera_resolver.py
    additionally searches from cwd's own Foreman project root (found by walking the directory
    tree), independent of whether that root appears in the TESSERA registry at all -- so a
    genuinely UNREGISTERED cwd can still be resolved via override there. This function only
    checks an override at a root the registry itself already matched (unique or ambiguous);
    an unregistered cwd's own directory tree is not walked. See README.md / ATLASSN-9's ticket
    for this remaining generalization, not implemented in this pass."""
    resolution, matched_root, candidates = matcher.resolve_cwd(cwd, projects)
    if matched_root is not None and resolution in ("unique", "ambiguous"):
        all_known_prefixes = {prefix for prefix, _ in projects}
        result = override.read_override(matched_root, all_known_prefixes)
        if result.valid:
            return "unique", matched_root, result.prefix, [result.prefix]
    if resolution == "unique":
        return resolution, matched_root, candidates[0], candidates
    return resolution, matched_root, None, candidates


def resolve_all(conn, cwds):
    """cwds: iterable of distinct cwd strings. Writes/updates cwd_project and
    cwd_project_candidate for each. Returns {cwd: resolution} for test/caller inspection."""
    projects = _load_projects(conn)
    now = _now()
    results = {}

    for cwd in cwds:
        resolution, matched_root, project_prefix, candidates = resolve_one(cwd, projects)
        candidate_count = len(candidates) if resolution != "unregistered" else 0

        conn.execute(
            "INSERT INTO cwd_project (cwd, matched_root, project_prefix, candidate_count, "
            "resolution, resolved_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cwd) DO UPDATE SET matched_root=excluded.matched_root, "
            "project_prefix=excluded.project_prefix, candidate_count=excluded.candidate_count, "
            "resolution=excluded.resolution, resolved_at=excluded.resolved_at",
            (cwd, matched_root, project_prefix, candidate_count, resolution, now),
        )
        conn.execute("DELETE FROM cwd_project_candidate WHERE cwd = ?", (cwd,))
        if resolution == "ambiguous":
            for prefix in candidates:
                conn.execute(
                    "INSERT INTO cwd_project_candidate (cwd, project_prefix) VALUES (?, ?)",
                    (cwd, prefix),
                )
        results[cwd] = resolution

    conn.commit()
    return results
