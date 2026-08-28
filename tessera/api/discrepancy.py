import subprocess
import sys

from ..common.git_sha import git_show_rev_args


def compute_discrepancy(claim_files_touched, real_files_touched):
    """Set-difference in both directions between a claim's stated files_touched and the
    real diff's files_touched. Empty in both directions means the claim matches reality."""
    claimed = set(claim_files_touched)
    real = set(real_files_touched)
    return {
        "matches": claimed == real,
        "touched_but_not_claimed": sorted(real - claimed),
        "claimed_but_not_touched": sorted(claimed - real),
    }


def discrepancy_for_ticket(store, gitops, ticket_id, stage):
    claim = store.get_latest_claim(ticket_id)
    if not claim:
        return None
    # Resolve the TICKET's own project, not gitops's default -- same bug class as
    # found earlier (found by extension while fixing those: a ticket outside gitops's
    # default project would otherwise silently read the wrong project's stage repo here).
    ticket = store.get_ticket(ticket_id, with_context=False)
    real_files = gitops.files_touched(stage, claim["commit_sha"], project=ticket["project_prefix"])
    result = compute_discrepancy(claim["files_touched"], real_files)
    result["claim"] = claim
    result["real_files_touched"] = real_files
    return result


def check_and_record_closure(store, ticket_id, actor):
    """The actual automatic wiring: call this after a ticket transitions to 'closed', from
    every real call site (tessera.api.cli's `transition` command, tessera.api.http_api's
    transition endpoint) -- not a hook, a plain function call, so both entry points get the
    identical behavior from one place rather than two copies drifting apart.

    Advisory only, by design, matching this project's own judgment-call-not-structural-claim
    posture: never raises, never blocks the close that already happened. A ticket legitimately
    closed as wontfix/duplicate/docs-only has no code diff at all, and that's not a defect --
    this function records what it finds, it doesn't grade it. Any internal failure (git not
    resolvable, store method missing, whatever) is caught, reported to stderr so it's not a
    silent no-op, and swallowed rather than propagated -- the transition it's riding on has
    already committed by the time this runs.

    Returns a small status dict for callers that want to report what happened (real_files check
    performed and matched, mismatched, no claim recorded, or skipped and why) -- not required
    reading, the durable record is the TESSERA event this writes, not the return value.
    """
    try:
        claim = store.get_latest_claim(ticket_id)
        if not claim:
            store.record_closed_with_no_claim(ticket_id, actor)
            return {"checked": "no_claim"}
        if not claim.get("commit_sha"):
            return {"checked": "skipped", "reason": "claim has no commit_sha"}
        result = discrepancy_for_ticket_singlerepo(store, ticket_id)
        if result is None:
            return {"checked": "skipped", "reason": "diff could not be computed (see files_touched_singlerepo)"}
        store.record_diff_check(ticket_id, actor, result)
        return {"checked": "diff", "matches": result["matches"]}
    except Exception as exc:  # noqa: BLE001 -- advisory check, must never break a real close
        print(f"tessera: closure discrepancy check failed for {ticket_id}: {exc} -- "
              f"not blocking the close, but this check did not run.", file=sys.stderr)
        return {"checked": "error", "reason": str(exc)}


def files_touched_singlerepo(repo_root, commit_sha):
    """Same real diff computation as gitops.files_touched (identical git invocation, same
    -m --first-parent merge-commit fix, same -z NUL-separation fix, both applied earlier),
    against a PLAIN repo root instead of a staged dev/integration/FT/prod path. Returns None,
    not an empty list, on any git failure (unknown sha, not a repo, etc.) -- an empty list
    would read as "a real diff with zero files," which is a different claim than "couldn't
    compute this," and the caller (discrepancy_for_ticket_singlerepo) must not conflate them."""
    rev_args = git_show_rev_args(commit_sha)
    if rev_args is None:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "show", "--name-only", "--pretty=format:",
             "-z", "-m", "--first-parent", *rev_args],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    return [f for f in out.stdout.split("\0") if f]


def discrepancy_for_ticket_singlerepo(store, ticket_id):
    """The harness-postmortem fix: discrepancy_for_ticket() above only ever worked
    for TESSERA's own staged dev/integration/FT/prod deployment model (gitops.files_touched
    resolves a STAGE path, not a project's real repo root) -- every Foreman pilot project
    (this project's own single-repo activity, plus several sibling projects)
    is a plain single repo with no staging concept at all, so the mechanism this project's own
    marketing calls its "signature feature" had structurally never been callable for any of
    them. compute_discrepancy() itself was already fully generic; only the real-diff resolution
    was staging-coupled. This is the single-repo path, real diff sourced directly from the
    ticket's own registered project source_root via plain git, no gitops dependency at all.

    Returns None if there's no claim to check (same contract as discrepancy_for_ticket), or if
    the ticket's project has no resolvable source_root, or if the real diff couldn't be computed
    (unknown commit, source_root not a real repo, etc.) -- callers must treat None as "couldn't
    check," not as "checked and clean," matching this project's own fail-toward-visible-gap
    discipline elsewhere (verdict_ledger.py's silent/fire/error split, project_resolve's
    unregistered/ambiguous/unreachable split).
    """
    claim = store.get_latest_claim(ticket_id)
    if not claim or not claim.get("commit_sha"):
        return None
    ticket = store.get_ticket(ticket_id, with_context=False)
    project = store.get_project(ticket["project_prefix"])
    if not project or not project.get("source_root"):
        return None
    real_files = files_touched_singlerepo(project["source_root"], claim["commit_sha"])
    if real_files is None:
        return None
    result = compute_discrepancy(claim["files_touched"], real_files)
    result["claim"] = claim
    result["real_files_touched"] = real_files
    return result
