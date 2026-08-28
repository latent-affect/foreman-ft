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
