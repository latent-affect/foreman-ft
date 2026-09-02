#!/usr/bin/env python3
"""
report_provenance.py

Shared provenance stamping for dev-harness's own measurement scripts
(foreman_quality_baseline.py, token_bloat_diagnostic.py,
security_privacy_convergence.py). PRD.md R23a / GOALS.json C2.

Every report those scripts write must carry tool_git_sha, tree_git_sha and
formula_version, computed at run time from the git repository that contains
the running script -- never read from a config file, an environment
variable, or a hardcoded literal a dependency bump would leave stale.

tool_git_sha and tree_git_sha are resolved from the script's own on-disk
location (walking up to the nearest .git), not from the caller's current
working directory or from whatever tree the script happens to be analyzing
-- so this still works when invoked with cwd pointed at an unrelated, even
non-git, directory.

On any failure to resolve real provenance, this raises ProvenanceError
rather than returning a placeholder. A caller must not catch this and
substitute "unknown" -- a report with unverifiable provenance is worse than
no report (GOALS.json F2).
"""

import subprocess
from pathlib import Path


class ProvenanceError(RuntimeError):
    pass


def _run_git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ProvenanceError(
            f"git {' '.join(args)} failed in {repo_root}: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ProvenanceError(f"no .git found above {start}")


def compute_provenance(script_file: str, formula_version: str) -> dict:
    """
    script_file: pass __file__ from the calling script.
    formula_version: a version tag naming the specific formulas and/or
        library versions this script's own numbers depend on, computed by
        the caller from live installed state -- not a hardcoded literal.

    Returns {"tool_git_sha": ..., "tree_git_sha": ..., "formula_version": ...}.
    Raises ProvenanceError if the script isn't inside a git repo, or if it
    has never been committed (tool_git_sha has no history to report).
    """
    script_path = Path(script_file).resolve()
    repo_root = find_repo_root(script_path.parent)
    rel_script = script_path.relative_to(repo_root).as_posix()

    tool_git_sha = _run_git(repo_root, "log", "-1", "--format=%H", "--", rel_script)
    if not tool_git_sha:
        raise ProvenanceError(
            f"{rel_script} has no commit history in {repo_root} -- "
            "it must be committed before a report can carry real provenance"
        )
    tree_git_sha = _run_git(repo_root, "rev-parse", "HEAD")

    return {
        "tool_git_sha": tool_git_sha,
        "tree_git_sha": tree_git_sha,
        "formula_version": formula_version,
    }
