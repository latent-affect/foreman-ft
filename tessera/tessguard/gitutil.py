"""Local git-subprocess plumbing for tessguard.

Deliberately NOT an import of tessera.gitops -- gitops is scoped to stage-repo promotion/
rollback (dev/integration/FT/prod repos under stages_root), never the *source* repo a
developer is actually committing to, which is exactly what the hard gate needs to introspect.
Depending on gitops here would couple tessguard to a component whose GOALS.json.done_state
never anticipated this caller. This module reimplements the one git-subprocess operation
tessguard actually needs (git_toplevel below) rather than depend on a component whose
API was never built for this caller.
"""

import subprocess


def git_toplevel(cwd):
    """Repo root for cwd, or None if cwd isn't inside a git working tree. Never raises --
    "not a git repo" is a real, expected outcome for a caller to branch on, not an error."""
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def git_worktree_paths(cwd):
    """DEVH-54. All worktree paths sharing cwd's repository (`git worktree list`), or [] if
    cwd isn't inside a git working tree or the command fails for any reason -- never raises,
    same convention as git_toplevel above. A git worktree has its own distinct top-level
    working directory (git_toplevel(cwd) returns that worktree's own path, not any sibling's),
    but every worktree of one repository shares the same object store and the same TESSERA
    project identity ought to apply to all of them -- this is what lets a caller try sibling
    paths when the current worktree's own path isn't itself a registered source_root."""
    proc = subprocess.run(
        ["git", "-C", str(cwd), "worktree", "list", "--porcelain"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return []
    return [
        line[len("worktree "):].strip()
        for line in proc.stdout.splitlines()
        if line.startswith("worktree ")
    ]
