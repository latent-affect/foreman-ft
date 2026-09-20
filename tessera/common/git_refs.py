import re

COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def validate_commit_sha(commit_sha):
    """Reject anything that isn't a plain lowercase-hex git object id, 7-40 characters.

    TESS-159: a commit_sha this rejects can never begin with '-' (or contain '=', '/',
    whitespace, etc.), so it can never be mistaken for a flag by a `git` invocation that
    takes it as a caller-influenced revision argument. This is the primary gate; callers
    that shell out to git should also treat the revision as a positional argument (e.g.
    `--end-of-options`) as a second, independent layer -- a bypass of one must not defeat
    the other. Raises ValueError, not a bespoke exception, so it composes with every
    existing caller's bad-input handling (http_api.py's dispatch and cli.py both already
    catch ValueError into a clean 400 / CLI error).
    """
    if not isinstance(commit_sha, str) or not COMMIT_SHA_RE.match(commit_sha):
        raise ValueError(
            f"invalid commit_sha {commit_sha!r}: must be 7-40 lowercase hex characters"
        )
