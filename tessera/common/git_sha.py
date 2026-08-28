"""Allowlist a git object name before it is passed to `git show`.

A claim `commit_sha` that starts with `-` is parsed as an option (`--output=`,
`--pretty=format:...`). Callers must reject those before argv construction, and
must pass `--` immediately before the revision.
"""
import re

COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


def validated_commit_sha(commit_sha):
    """Return the sha if it is hex of length 4-40, else None. Never returns a
    value that git will treat as an option."""
    if not isinstance(commit_sha, str):
        return None
    candidate = commit_sha.strip()
    if not COMMIT_SHA_RE.fullmatch(candidate):
        return None
    return candidate


def git_show_rev_args(commit_sha):
    """Validated sha as a git object argument, or None if rejected.

    Do not prefix the sha with `--`. In `git show`, `--` starts pathspecs, so
    `git show -- <sha>` looks for a file named <sha> and returns an empty
    diff. Option injection is stopped by the hex allowlist (a leading `-` never
    reaches argv).
    """
    sha = validated_commit_sha(commit_sha)
    if sha is None:
        return None
    return [sha]
