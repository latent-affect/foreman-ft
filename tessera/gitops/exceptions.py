class GitOpsError(Exception):
    """Base class for gitops-layer errors."""


class SyncedFolderError(GitOpsError):
    """A stage repo path resolves under an iCloud Drive / Dropbox / other
    sync-client-managed folder -- refused per SCOPE.md premortem item 11."""


class NonFastForwardError(GitOpsError):
    """promote_stage()'s target commit is not a fast-forward from the stage's current
    HEAD. gitops never force-resets past this -- that's the operator's call via git."""


class GitCommandError(GitOpsError):
    """A git subprocess exited non-zero. Carries the real stderr, never swallowed."""


class InvalidRevisionError(GitOpsError):
    """A caller-influenced revision (commit_sha) does not match the plain lowercase-hex
    object-id shape -- rejected before it ever reaches a git subprocess argv (TESS-159).
    Independent of the store-boundary check in tessera.common.git_refs.validate_commit_sha:
    a bypass of one layer must not defeat the other."""
