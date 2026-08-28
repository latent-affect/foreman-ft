"""Pulls commit history from a local git repo (`git log --numstat`) into git_commit /
git_commit_file / git_commit_ticket / git_ticket_candidate_dropped.

Not a streaming source, and unlike tessera_pull there is no single monotonic id to resume
from -- git history can be force-pushed/rewritten, so a "since last-seen sha" cursor could miss
commits a rewrite reordered. Instead this always re-walks the full `git log` and relies on
git_commit's real primary key, (project_prefix, sha) (ARCHITECTURE.md section 16), for
idempotency: INSERT OR IGNORE means re-seeing an already-ingested commit is a no-op, not a
duplicate. Full history for one repo is at most a few thousand commits on this machine
(1,097 across the whole measured corpus, ARCHITECTURE.md section 8) -- re-walking every pull is
cheap enough that the correctness win (self-healing after a rewrite) is worth more than an
incremental fetch's marginal speedup.

Ticket-candidate extraction: ARCHITECTURE.md section 8 describes the real corpus result (167
ticket-shaped strings from 1,097 commits, 15 dropped for an unregistered prefix, including
P0-2/P0-3/P0-4/P0-6, F5-001..004, MR-0, and ISO-8601) but does not fix the exact regex text.
TICKET_CANDIDATE_RE below is chosen to reproduce every one of those cited examples as a
candidate (so the *validation* step, not the regex, is what filters them) -- see
tests/test_gitrepo_pull.py's test_regex_matches_every_cited_real_corpus_example for the
reproduction.
"""

import datetime
import hashlib
import re
import subprocess

TICKET_CANDIDATE_RE_SOURCE = r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b"
TICKET_CANDIDATE_RE = re.compile(TICKET_CANDIDATE_RE_SOURCE)

# A fixed, non-secret salt -- not derived from any real identity, exists only so the same raw
# author string always hashes to the same digest across runs (stable grouping), per D8's "salted
# digest" requirement and this project's standing rule that a real name/email must never reach a
# public surface in plaintext.
AUTHOR_HASH_SALT = "atlas-git-author-hash-v1:"

RECORD_SEPARATOR = "\x01"
FIELD_SEPARATOR = "\x1f"
LOG_FORMAT = RECORD_SEPARATOR + FIELD_SEPARATOR.join(["%H", "%at", "%ae", "%s"])


class GitLogError(RuntimeError):
    pass


def hash_author(raw_author):
    return hashlib.sha256((AUTHOR_HASH_SALT + raw_author).encode("utf-8")).hexdigest()


def run_git_log(repo_path):
    """Real subprocess call, per this project's 'executed means a real call ran' convention.
    Raises GitLogError with the real stderr on failure -- not swallowed (F3's failure
    signature), since a caller iterating multiple repos needs to tell 'this repo failed' from
    'this repo has no commits'."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--numstat", f"--format={LOG_FORMAT}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # A repo whose current branch has zero commits yet ("unborn HEAD") is a real, valid
        # empty state -- git's own message names it distinctly from every other failure this
        # subprocess can hit (missing path, not a repo, corrupted .git). Anything else is a real
        # failure and must raise (F3's failure signature: no false "no commits" for an actual
        # per-repo problem).
        if "does not have any commits yet" in result.stderr:
            return ""
        raise GitLogError(
            f"git log --numstat failed for {repo_path!r} (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _parse_numstat_lines(body):
    files = []
    for line in body.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins, dele, path = parts
        files.append((path, None if ins == "-" else int(ins), None if dele == "-" else int(dele)))
    return files


def parse_git_log(output):
    """Returns a list of dicts: sha, committed_ts (ISO-8601 UTC), author_raw, subject, files
    (list of (path, insertions, deletions)). Empty output (no commits) returns []."""
    commits = []
    for chunk in output.split(RECORD_SEPARATOR):
        if not chunk:
            continue
        header, _, body = chunk.partition("\n\n")
        fields = header.split(FIELD_SEPARATOR)
        if len(fields) != 4:
            continue
        sha, epoch_s, author_raw, subject = fields
        committed_ts = datetime.datetime.fromtimestamp(
            int(epoch_s), tz=datetime.timezone.utc
        ).isoformat()
        commits.append({
            "sha": sha,
            "committed_ts": committed_ts,
            "author_raw": author_raw,
            "subject": subject,
            "files": _parse_numstat_lines(body),
        })
    return commits


def extract_ticket_candidates(subject):
    return TICKET_CANDIDATE_RE.findall(subject)


def insert_commit(warehouse_conn, project_prefix, commit, ingest_run_id, registered_prefixes):
    """Inserts one commit and its files, idempotently, and classifies every ticket-shaped
    candidate in its subject as either a real git_commit_ticket link (prefix is registered) or a
    git_ticket_candidate_dropped row (prefix is not) -- never silently discarded either way, per
    ingest's own F4 failure signature."""
    warehouse_conn.execute(
        "INSERT OR IGNORE INTO git_commit "
        "(project_prefix, sha, ingest_run_id, committed_ts, author_hash, subject, "
        "files_changed, insertions, deletions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_prefix, commit["sha"], ingest_run_id, commit["committed_ts"],
            hash_author(commit["author_raw"]), commit["subject"],
            len(commit["files"]),
            sum(f[1] for f in commit["files"] if f[1] is not None),
            sum(f[2] for f in commit["files"] if f[2] is not None),
        ),
    )
    for path, insertions, deletions in commit["files"]:
        warehouse_conn.execute(
            "INSERT OR IGNORE INTO git_commit_file "
            "(project_prefix, sha, file_path, insertions, deletions) VALUES (?, ?, ?, ?, ?)",
            (project_prefix, commit["sha"], path, insertions, deletions),
        )
    for candidate in extract_ticket_candidates(commit["subject"]):
        candidate_prefix = candidate.rsplit("-", 1)[0]
        if candidate_prefix in registered_prefixes:
            warehouse_conn.execute(
                "INSERT OR IGNORE INTO git_commit_ticket "
                "(project_prefix, sha, ticket_id, origin) VALUES (?, ?, ?, 'commit-message-regex')",
                (project_prefix, commit["sha"], candidate),
            )
        else:
            warehouse_conn.execute(
                "INSERT OR IGNORE INTO git_ticket_candidate_dropped "
                "(project_prefix, sha, candidate, reason, ingest_run_id) "
                "VALUES (?, ?, ?, 'unregistered-prefix', ?)",
                (project_prefix, commit["sha"], candidate, ingest_run_id),
            )


def pull_git_commits(warehouse_conn, repo_path, project_prefix, registered_prefixes, ingest_run_id):
    """Orchestrates one pull pass for one repo. Returns the number of commits seen (including
    ones already present -- INSERT OR IGNORE makes re-seeing them cheap and safe, and the
    caller's own row-count delta, not this return value, is what distinguishes 'newly ingested'
    from 'already had it' if that distinction is ever needed)."""
    output = run_git_log(repo_path)
    commits = parse_git_log(output)
    for commit in commits:
        insert_commit(warehouse_conn, project_prefix, commit, ingest_run_id, registered_prefixes)
    warehouse_conn.commit()
    return len(commits)
