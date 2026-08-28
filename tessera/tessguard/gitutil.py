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
