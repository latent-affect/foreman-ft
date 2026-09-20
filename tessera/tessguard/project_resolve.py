"""Resolves which TESSERA project(s) a filesystem root belongs to.

Does not live in tessera/common/ -- common/GOALS.json explicitly requires it stay importable
standalone without importing any other tessera component, and this module's whole job is
calling into store.

Path matching is real-path-component-aware (pathlib parents), never a bare str.startswith.
Verified against this repo's actual registrations (docs/tessguard-backtest-evidence/ has the
review passes that found this): naive startswith collides three real families --
glasshouse/-ft/-prod, valence-audio-forensics/-ft/-prod, audio-verification-layer/-playground.
Using Path(...).parents avoids all three, because "glasshouse-ft" is never a path-component
descendant of "glasshouse" even though it IS a string-prefix match.

Returns a LIST, never a singular project or None on multi-match: one filesystem root can
legitimately host more than one registered TESSERA project (AREM and FORE both register
source_root=/Users/m5/agent-remediation in this database today).
"""

import os
from pathlib import Path

from tessera.store import schema

from . import gitutil


def _true_case(path):
    """Best-effort real on-disk casing for each path component that already exists; any
    component that doesn't exist yet is left exactly as typed, since there's no prior on-disk
    casing to disagree with.

    FORE-414: on a case-insensitive, case-preserving filesystem (macOS APFS default),
    Path.resolve() does not case-normalize -- two path strings differing only in case can
    resolve to the identical inode yet stay different strings. root_matches_internal()'s
    containment check is plain Path `==` / `in .parents`, which is exact string comparison
    under the hood, so a genuinely-contained path whose typed case doesn't match the case the
    root was originally registered under would silently fail to match -- a false NEGATIVE
    (fails CLOSED: an internal, legitimate path gets treated as external). Ported verbatim in
    approach from component_coupling.py's own _true_case() (FORE-128, the same root cause
    found and fixed for isImplementationPath()'s prefix matching) rather than casefolding the
    comparison outright: casefolding would ALSO conflate two genuinely different directories
    that differ only in case on a real case-SENSITIVE filesystem (Linux), which is the wrong
    direction to fail for a project-boundary check -- it must never manufacture a match that
    isn't really the same physical directory. Resolving to true on-disk casing instead leaves
    a case-sensitive filesystem's behavior completely unchanged (typed casing already matches
    on-disk casing there, or the two are genuinely different paths and stay unmatched), while
    correcting exactly the case-insensitive-preserving false negative this ticket exists to fix.

    A directory this can't list (permission error, mid-delete) falls back to the typed casing
    rather than raising -- fail toward the pre-fix behavior for that one path component, not
    toward a crash.
    """
    parts = path.parts
    if not parts:
        return path
    resolved = Path(parts[0])
    for part in parts[1:]:
        candidate = resolved / part
        try:
            if candidate.exists():
                match = None
                for entry in os.listdir(resolved):
                    if entry == part:
                        match = entry
                        break
                    if match is None and entry.lower() == part.lower():
                        match = entry
                resolved = resolved / (match if match is not None else part)
            else:
                resolved = candidate
        except OSError:
            resolved = candidate
    return resolved


def root_matches_internal(repo_root_path, source_root_path):
    """True if source_root_path IS repo_root_path, or is nested under it (repo_root_path is
    a real path-component ancestor of source_root_path) -- the direction GIF needs: its
    session cwd is the OUTER, non-git /Users/m5/dev/gif-smith; its real registered
    source_root is the NESTED git repo /Users/m5/dev/gif-smith/gifsmith.

    FORE-414: both sides are resolved to their real on-disk casing (_true_case()) before
    comparison, so a repo_root_path/source_root_path pair that differs only in casing on a
    case-insensitive-preserving filesystem still matches -- see _true_case()'s own docstring
    for why this resolves the bug without casefolding (which would fail the OTHER direction on
    a case-sensitive filesystem)."""
    repo_true = _true_case(repo_root_path)
    source_true = _true_case(source_root_path)
    if source_true == repo_true:
        return True
    return repo_true in source_true.parents


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


def resolve_projects_for_repo(repo_root, store, include_archived=False):
    """All store.list_projects() rows whose source_root == repo_root or is nested under it.
    repo_root need not itself be a git repository -- see the module docstring on GIF.

    Archived registrations are skipped by default (TESS-174). "Archived" means the
    registration is stale, and the whole point of marking one is that it must stop claiming
    a filesystem root -- a dead project whose scratchpad directory was deleted and whose
    path a live project later reuses would otherwise still match here. include_archived=True
    is for a caller auditing the registry itself rather than resolving live work.

    This is fail-closed by design: archiving a project that is actually still live makes its
    repo resolve to zero projects, and layer 2's gitgate BLOCKS on a zero resolution rather
    than waving it through. That is the correct direction to fail for this check, and it is
    recoverable in one command (unarchive-project).

    FORE-282 (worktree-aware fallback, decided in DEVH-54 and DEVH-61-64's architecture
    recycle, propagated here from dev-harness-run2's copy -- the canonical checkout this
    module actually lives in had drifted 0 of 6 copies with the fix applied): if repo_root
    itself matches nothing, try every sibling git-worktree path (git worktree list) before
    returning empty. A git worktree has its own independent top-level working directory,
    distinct from the repo it was created from, so a project registered against one worktree
    (typically the original/main checkout) would otherwise be invisible from every other
    worktree of the very same repository -- gitgate.py's hard gates would fail closed for
    every commit made from a worktree other than the registered one, and layer 1's audit
    would silently read as zero registered projects. root_matches_internal itself is
    unchanged; this only widens the set of candidate paths it is applied to. The
    include_archived filter applies identically to the fallback pass -- an archived project
    must not become reachable again just because a caller is standing in a sibling worktree.
    Safe for a non-git repo_root (GIF's case): `git worktree list` simply fails there and this
    degrades to the prior, exact-match-only behaviour."""
    repo_root_path = Path(repo_root).resolve()
    projects = [p for p in store.list_projects()
                if include_archived or p.get("status") != schema.PROJECT_STATUS_ARCHIVED]
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


def resolve_projects_for_cwd(cwd, store, include_archived=False):
    """Convenience wrapper for a caller that has a cwd known to be inside a real git
    repository (layer 2's usage) -- resolves the repo root via `git rev-parse
    --show-toplevel` first, then delegates to resolve_projects_for_repo. Returns an empty
    list, not an error, if cwd isn't inside a git working tree; a repo resolving to zero
    registered projects is itself a real, meaningful outcome each caller interprets
    differently (layer 1 does not flag on it, layer 2 blocks on it -- see gitgate.py)."""
    repo_root = gitutil.git_toplevel(cwd)
    if repo_root is None:
        return []
    return resolve_projects_for_repo(repo_root, store, include_archived=include_archived)
