import re
import subprocess
from pathlib import Path

from ..common.git_sha import git_show_rev_args
from .exceptions import GitCommandError, NonFastForwardError, SyncedFolderError

STAGE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")

SYNCED_FOLDER_MARKERS = ("Mobile Documents", "Dropbox", "Google Drive", "OneDrive")


def check_not_synced_internal(path):
    resolved = str(Path(path).resolve())
    for marker in SYNCED_FOLDER_MARKERS:
        if marker in resolved:
            raise SyncedFolderError(
                f"{path} resolves under a sync-client-managed folder ({marker!r} in "
                f"{resolved!r}) -- refused per SCOPE.md premortem item 11. Stage repos "
                f"must live on a plain, non-synced path."
            )


def run_git_internal(repo_path, args, check=True):
    # Every git operation goes through this one choke point, so this is the
    # single place to catch "the stage repo directory doesn't exist at all" -- e.g. no
    # stage has ever been promoted for this project -- and say that plainly instead of
    # letting `git -C <path> ...` fail with a raw, easy-to-misread `fatal: cannot change
    # to '<path>': No such file or directory`. Root-caused directly: GET .../discrepancy
    # for a real ticket with a real claim, against a project that had never promoted any
    # stage, reproduced exactly this raw message.
    if not Path(repo_path).is_dir():
        raise GitCommandError(
            f"no stage repo at {repo_path} -- has this stage ever been promoted for "
            f"this project? (promote_stage/`promote` creates it on first use)"
        )
    proc = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise GitCommandError(
            f"git -C {repo_path} {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    return proc


class GitOps:
    def __init__(self, store, stages_root, project=None):
        """project is a prefix string scoping this GitOps instance's DEFAULT project.
        Every method below also accepts an explicit project= override for a single call --
        that override, not attribute mutation, is how a caller (e.g. the HTTP server's one
        long-lived, cross-thread-shared GitOps instance) should switch projects per
        request. Mutating self.project directly per-request was a real, found bug
        (found by direct repro): concurrent requests for different projects raced on the same shared
        attribute, so one request's project could silently leak into another's git
        operations. None falls back to the store's default project, so existing single-
        project call sites need no change."""
        self.store = store
        self.stages_root = Path(stages_root)
        self.project = project
        check_not_synced_internal(self.stages_root)

    def resolve_project_prefix_internal(self, project):
        candidate = project or self.project
        self.store.resolve_project_id_internal(candidate)  # validates; raises UnknownProjectError
        return candidate or self.store.default_project_prefix

    def stage_path(self, stage, project=None):
        """Project-scoped: stages_root/<project prefix>/<stage>, NOT stages_root/<stage>.
        Two projects can legitimately both have a "dev" stage -- before this fix they
        physically shared ONE git repo at stages_root/dev, so promoting or rolling back
        one project's dev stage silently moved the other project's dev stage's real git
        HEAD too (confirmed by direct repro: a BE-scoped GitOps reconciling
        flagged AL's own ticket stale)."""
        prefix = self.resolve_project_prefix_internal(project)
        if not PREFIX_RE.fullmatch(prefix) or ".." in prefix:
            raise GitCommandError(
                f"refusing stage path: project prefix {prefix!r} is not "
                f"[A-Za-z][A-Za-z0-9_-]{{0,15}}"
            )
        if not STAGE_NAME_RE.fullmatch(str(stage)) or ".." in str(stage):
            raise GitCommandError(
                f"refusing stage path: stage {stage!r} is not "
                f"[A-Za-z][A-Za-z0-9_-]{{0,31}}"
            )
        return self.stages_root / prefix / stage

    def init_stage(self, stage, source_repo=None, project=None):
        """Creates the stage as its own independent, initially-empty git repository --
        NOT a clone checked out to the source's tip, which would make "promote to an
        earlier commit" impossible to express as a fast-forward. If source_repo is given
        it's recorded as the 'origin' remote; commits reach the stage only via an explicit
        promote_stage() call, which fetches on demand."""
        path = self.stage_path(stage, project=project)
        check_not_synced_internal(path)
        if (path / ".git").exists():
            return path
        path.mkdir(parents=True, exist_ok=True)
        run_git_internal(path, ["init"])
        run_git_internal(path, ["config", "user.email", "tessera@localhost"])
        run_git_internal(path, ["config", "user.name", "tessera"])
        if source_repo:
            run_git_internal(path, ["remote", "add", "origin", str(source_repo)])
        return path

    def head(self, stage, project=None):
        return run_git_internal(self.stage_path(stage, project=project), ["rev-parse", "HEAD"]).stdout.strip()

    def promote_stage(self, stage, commit_sha, actor, project=None):
        path = self.stage_path(stage, project=project)
        if "origin" in run_git_internal(path, ["remote"]).stdout.split():
            run_git_internal(path, ["fetch", "origin"])

        is_bootstrap = run_git_internal(path, ["rev-parse", "--verify", "HEAD"], check=False).returncode != 0
        if is_bootstrap:
            # First promotion into an empty stage: nothing to be a non-fast-forward
            # from, so this always succeeds once commit_sha is a real, reachable commit.
            run_git_internal(path, ["checkout", "-B", "main", commit_sha])
        else:
            check = run_git_internal(path, ["merge-base", "--is-ancestor", "HEAD", commit_sha], check=False)
            # `git merge-base --is-ancestor` exit codes: 0 = is an ancestor (fast-forward
            # OK), 1 = a valid commit that genuinely isn't an ancestor (real
            # non-fast-forward), anything else (typically 128) = one of the two objects
            # doesn't exist at all -- a different failure that was previously
            # misdiagnosed as "not a fast-forward" (code-review finding), which sends
            # whoever debugs it looking at the wrong cause (a real non-ff vs. a commit
            # that was simply never fetched).
            if check.returncode == 1:
                raise NonFastForwardError(
                    f"{commit_sha} is not a fast-forward from {stage}'s current HEAD "
                    f"({self.head(stage, project=project)}); gitops will not force this -- "
                    f"resolve via git directly."
                )
            elif check.returncode != 0:
                raise GitCommandError(
                    f"git -C {path} merge-base --is-ancestor HEAD {commit_sha} exited "
                    f"{check.returncode}: {check.stderr.strip()} -- likely means {commit_sha!r} "
                    f"does not exist in this stage repo's object store (was it fetched?), "
                    f"not that it's a non-fast-forward"
                )
            run_git_internal(path, ["merge", "--ff-only", commit_sha])

        new_head = self.head(stage, project=project)
        tag = f"cp-promote-{stage}-{new_head[:12]}"
        run_git_internal(path, ["tag", "-f", tag, new_head])
        self.store.record_stage_promotion(stage, new_head, actor, project=project or self.project)
        self.reconcile_stage(stage, project=project)
        return new_head

    def rollback_stage(self, stage, target, actor, project=None):
        path = self.stage_path(stage, project=project)
        target_sha = run_git_internal(path, ["rev-parse", target]).stdout.strip()
        run_git_internal(path, ["reset", "--hard", target_sha])
        self.store.record_stage_rollback(stage, target_sha, actor, project=project or self.project)
        self.reconcile_stage(stage, project=project)
        return target_sha

    def reconcile_stage(self, stage, project=None):
        """On-read ancestry reconciliation, per ARCHITECTURE.md: fires whether the HEAD
        move went through gitops or was a direct git operation on the stage repo -- reads
        the repo's REAL current HEAD every time, never relies on stage_heads alone.
        project is threaded through to get_commit_links_for_stage (found by
        direct repro: without it, this always queried the store's DEFAULT project's
        commit links regardless of which project's stage repo path/, was actually being
        reconciled -- an obvious inert no-op for the default project and a silent
        cross-project data-integrity bug for any other)."""
        path = self.stage_path(stage, project=project)
        real_head = self.head(stage, project=project)
        links = self.store.get_commit_links_for_stage(stage, project=project or self.project)
        results = []
        unreconciled = []
        for link in links:
            ticket_id, linked_sha, currently_stale = link["ticket_id"], link["commit_sha"], link["stale"]
            check = run_git_internal(path, ["merge-base", "--is-ancestor", linked_sha, real_head], check=False)
            # Same ambiguity promote_stage's own check already fixed (see the comment
            # there): returncode 1 is a genuine, decisive "not an ancestor" -- anything
            # else is a real git failure (missing object, corrupted repo, disk issue),
            # not a staleness verdict. This loop used to collapse both into
            # should_be_stale=True, silently writing a wrong flag on a real git error.
            # A failure here now leaves the ticket's CURRENT stale flag untouched (never
            # guessed) and is collected to raise after the rest of the batch is
            # processed, rather than aborting the whole reconcile on the first failure.
            if check.returncode not in (0, 1):
                unreconciled.append((ticket_id, check.returncode, check.stderr.strip()))
                continue
            is_ancestor = check.returncode == 0
            should_be_stale = not is_ancestor
            if currently_stale != should_be_stale:
                self.store.set_ticket_stale(ticket_id, stage, should_be_stale)
            results.append((ticket_id, should_be_stale))
        if unreconciled:
            detail = "; ".join(
                f"{tid} (git exited {code}: {stderr})" for tid, code, stderr in unreconciled
            )
            raise GitCommandError(
                f"reconcile_stage({stage!r}) could not determine ancestry for "
                f"{len(unreconciled)} linked ticket(s), left unchanged rather than "
                f"guessed: {detail}"
            )
        return results

    def diff(self, stage, commit_sha, project=None):
        rev_args = git_show_rev_args(commit_sha)
        if rev_args is None:
            raise GitCommandError(
                f"refusing git show: commit_sha {commit_sha!r} is not a hex object name"
            )
        return run_git_internal(
            self.stage_path(stage, project=project), ["show", *rev_args]
        ).stdout

    def files_touched(self, stage, commit_sha, project=None):
        """-m --first-parent: `git show` prints NO diff at all for a merge commit by
        default, so a merge commit's real file changes were silently reported as an empty
        list -- an honest claim then looked entirely fabricated against a real merge
        (confirmed by direct repro against a constructed merge commit: empty
        output before this fix). -m re-enables diff generation for merges; --first-parent
        picks one well-defined diff (against the branch being merged into) rather than one
        per parent.
        -z: NUL-separates output and disables git's default quoting/octal-escaping of
        paths containing spaces or non-ASCII bytes (confirmed by direct repro: a
        path with a space and an accented character came back as a quoted, octal-escaped
        string like '"with space and \\303\\251.txt"', which never string-equals the same
        path as written in a claim -- producing a false discrepancy in both directions)."""
        path = self.stage_path(stage, project=project)
        rev_args = git_show_rev_args(commit_sha)
        if rev_args is None:
            raise GitCommandError(
                f"refusing git show: commit_sha {commit_sha!r} is not a hex object name"
            )
        out = run_git_internal(
            path,
            ["show", "--name-only", "--pretty=format:", "-z", "-m", "--first-parent", *rev_args],
        ).stdout
        return [f for f in out.split("\0") if f]
