"""Resolves which TESSERA project(s) a filesystem root belongs to.

Does not live in tessera/common/ -- common/GOALS.json explicitly requires it stay importable
standalone without importing any other tessera component, and this module's whole job is
calling into store.

Path matching is real-path-component-aware (pathlib parents), never a bare str.startswith.
Verified against this repo's actual registrations (an internal review pass found this): naive
startswith collides three real families -- example-project/-ft/-prod,
example-audio-app/-ft/-prod, example-tool/-playground.
Using Path(...).parents avoids all three, because "example-project-ft" is never a path-component
descendant of "example-project" even though it IS a string-prefix match.

Returns a LIST, never a singular project or None on multi-match: one filesystem root can
legitimately host more than one registered TESSERA project (AREM and FORE both register
source_root=/path/to/agent-remediation in this database today).
"""

from pathlib import Path

from . import gitutil


def root_matches_internal(repo_root_path, source_root_path):
    """True if source_root_path IS repo_root_path, or is nested under it (repo_root_path is
    a real path-component ancestor of source_root_path) -- the direction GIF needs: its
    session cwd is the OUTER, non-git /path/to/gif-smith; its real registered
    source_root is the NESTED git repo /path/to/gifsmith."""
    if source_root_path == repo_root_path:
        return True
    return repo_root_path in source_root_path.parents


def _match_against_roots(repo_root_path, projects):
    matched = []
    for project in projects:
        source_root = project.get("source_root")
        if not source_root:
            continue
        source_root_path = Path(source_root).resolve()
        if root_matches_internal(repo_root_path, source_root_path):
            matched.append(project)
    return matched


def resolve_projects_for_repo(repo_root, store):
    """All store.list_projects() rows whose source_root == repo_root or is nested under it.
    repo_root need not itself be a git repository -- see the module docstring on GIF.

    DEVH-54: if repo_root itself matches nothing, try every sibling git-worktree path
    (git worktree list) before returning empty. A git worktree has its own distinct
    top-level working directory, so a project registered against one worktree (most
    commonly the original/main checkout) would otherwise be invisible from every other
    worktree of the very same repository -- gitgate.py's hard gates would fail closed for
    every commit made from a worktree other than the registered one, and layer 1's audit
    would silently read as zero registered projects. root_matches_internal itself is
    unchanged; this only widens the set of candidate paths it is applied to. Safe for a
    non-git repo_root (GIF's case): `git worktree list` simply fails there and this
    degrades to the prior, exact-match-only behaviour."""
    repo_root_path = Path(repo_root).resolve()
    projects = store.list_projects()
    matched = _match_against_roots(repo_root_path, projects)
    if matched:
        return matched
    for sibling in gitutil.git_worktree_paths(repo_root):
        sibling_path = Path(sibling).resolve()
        if sibling_path == repo_root_path:
            continue
        sibling_matched = _match_against_roots(sibling_path, projects)
        if sibling_matched:
            return sibling_matched
    return []


def resolve_projects_for_cwd(cwd, store):
    """Convenience wrapper for a caller that has a cwd known to be inside a real git
    repository (layer 2's usage) -- resolves the repo root via `git rev-parse
    --show-toplevel` first, then delegates to resolve_projects_for_repo. Returns an empty
    list, not an error, if cwd isn't inside a git working tree; a repo resolving to zero
    registered projects is itself a real, meaningful outcome each caller interprets
    differently (layer 1 does not flag on it, layer 2 blocks on it -- see gitgate.py)."""
    repo_root = gitutil.git_toplevel(cwd)
    if repo_root is None:
        return []
    return resolve_projects_for_repo(repo_root, store)
