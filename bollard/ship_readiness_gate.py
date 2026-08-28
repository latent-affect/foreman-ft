#!/usr/bin/env python3
"""Hard gate on a real `git push` -- "ready to publish/push" is a different bar than
"criteria are frozen and met", which architecture_gate.py/goals_freeze_gate.py already enforce
mid-build. The operator's decision (b), 2026-08-20: a foreman:ship-readiness skill stage produces
.foreman/SHIP-CHARTER.json (five checks: no open S0/S1 tickets, test suite green with recorded
evidence, a stretch-test pass tied to and versioned alongside the suite, known limitations
disclosed, at least one documented dogfood pass); this hook is the thin PreToolUse check that
the artifact exists, shows all five checks passing, and is current, before a push proceeds.

Opt-in, matching ticket_status_gate's precedent for the identical
reasoning: this is a materially bigger workflow commitment than architecture_gate/
goals_freeze_gate -- it can block a push over missing evidence, not just an in-progress build --
so it must be chosen per project, not inherited by every Foreman-managed project the moment this
file exists. Marker: .foreman/ship-readiness-gate-enabled.

"Current" means SHIP-CHARTER.json still vouches for the code being pushed. Two accepted
states: commit_hash equals HEAD (working-tree stamp, charter not yet committed),
or HEAD is the child of commit_hash and `git diff --name-only` of that parent..HEAD is
exactly `.foreman/SHIP-CHARTER.json` (stamp, then commit the charter). The charter file
cannot contain its own commit hash, so the second state is how a committed charter stays
current without a restamp loop. Any other HEAD, or a git error, is stale / fail-closed.

Same mechanical-existence posture as architecture_gate.py checking ARCHITECTURE-REVIEW.md: this
hook verifies the artifact exists, is fresh, and says all five checks passed. It does not and
cannot re-verify that any recorded `evidence` string is actually true -- a disclosed, accepted
limitation, not an oversight.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402

RULE_ID = "FOREMAN-SHIP-READINESS-GATE"
ENABLED_MARKER = "ship-readiness-gate-enabled"
CHARTER_PATH_REL = Path(".foreman") / "SHIP-CHARTER.json"
REQUIRED_CHECKS = (
    "no_open_s0_s1",
    "test_suite_green",
    "stretch_test",
    "limitations_disclosed",
    "dogfood_pass",
)
GIT_PUSH_RE = re.compile(r'\bgit\s+push\b')


def quoted_spans(text):
    """Character ranges of `text` sitting inside a single- or double-quoted string. Local,
    self-contained copy of the same check component_coupling.py uses --
    not imported, matching this codebase's own established convention of keeping each hook
    self-contained (see the matching comment in component_coupling.py). The false-positive class
    is the same shape: `git commit -m "reverts the git push regression"` must not read as a
    real push."""
    spans = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            end = text.find(ch, i + 1)
            if end == -1:
                spans.append((i, n))
                break
            spans.append((i, end + 1))
            i = end + 1
        else:
            i += 1
    return spans


def inside_quotes(pos, spans):
    return any(start <= pos < end for start, end in spans)


def is_real_git_push(command):
    """True if `command` contains a real, unquoted `git push` invocation -- not the same text
    appearing inside a quoted commit message, comment, or grep pattern. Segment-split on the
    same shell delimiters component_coupling.py uses, so `foo; git push` and `foo && git push`
    are both caught per-segment without needing a real shell parser."""
    for seg in re.split(r"[;&|\n]", command):
        spans = quoted_spans(seg)
        for m in GIT_PUSH_RE.finditer(seg):
            if not inside_quotes(m.start(), spans):
                return True
    return False


def git_stdout(args, cwd):
    try:
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def current_commit_hash(cwd):
    text = git_stdout(["git", "rev-parse", "HEAD"], cwd)
    if text is None:
        return None
    return text.strip() or None


def parent_commit_hash(cwd, commit):
    text = git_stdout(["git", "rev-parse", "--verify", commit + "^"], cwd)
    if text is None:
        return None
    return text.strip() or None


def diff_name_only(cwd, a, b):
    text = git_stdout(["git", "diff", "--name-only", a, b], cwd)
    if text is None:
        return None
    return [line for line in text.splitlines() if line]


def charter_is_current(cwd, charter_commit, real_commit):
    """True if the charter still vouches for the code at HEAD.

    Exact HEAD match, or HEAD is the charter-only child of commit_hash.
    Git errors return False so the caller fails closed.
    """
    if not charter_commit or not real_commit:
        return False
    if charter_commit == real_commit:
        return True
    parent = parent_commit_hash(cwd, real_commit)
    if parent is None or parent != charter_commit:
        return False
    changed = diff_name_only(cwd, charter_commit, real_commit)
    if changed is None:
        return False
    return set(changed) == {CHARTER_PATH_REL.as_posix()}


def main(data):
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not is_real_git_push(command):
        return

    cwd = data.get("cwd")
    project_root = cc.find_project_root(cwd)
    if project_root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    if not (project_root / ".foreman" / ENABLED_MARKER).is_file():
        hc.set_rule(f"{RULE_ID}:not-opted-in")
        return

    charter_path = project_root / CHARTER_PATH_REL
    if not charter_path.is_file():
        hc.set_rule(f"{RULE_ID}:no-charter")
        hc.deny(
            f"Foreman: this project has ship-readiness gating enabled but no {charter_path}. "
            f"Run foreman:ship-readiness before pushing."
        )
        return

    try:
        charter = json.loads(charter_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # Fails toward DENY, not toward silently letting an unreadable charter through --
        # same fail-safe direction as goals_freeze_gate.py's own GOALS.json read failure.
        hc.set_rule(f"{RULE_ID}:charter-unreadable")
        hc.deny(
            f"Foreman: {charter_path} exists but could not be read as JSON ({exc}). "
            f"Regenerate it with foreman:ship-readiness before pushing."
        )
        return

    checks = charter.get("checks") or {}
    failed = [name for name in REQUIRED_CHECKS if not (checks.get(name) or {}).get("pass")]
    if failed:
        hc.set_rule(f"{RULE_ID}:checks-not-passing")
        hc.deny(
            f"Foreman: {charter_path} does not show all five ship-readiness checks passing "
            f"(failing or missing: {', '.join(failed)}). Run foreman:ship-readiness again "
            f"before pushing."
        )
        return

    charter_commit = charter.get("commit_hash")
    git_cwd = str(project_root)
    real_commit = current_commit_hash(git_cwd)
    if real_commit is None:
        hc.set_rule(f"{RULE_ID}:git-unavailable")
        hc.deny(
            f"Foreman: could not read git rev-parse HEAD in {cwd}; ship-readiness "
            f"fails closed rather than treating an unread HEAD as current. "
            f"Run foreman:ship-readiness after git is available."
        )
        return
    if not charter_is_current(git_cwd, charter_commit, real_commit):
        hc.set_rule(f"{RULE_ID}:stale-charter")
        hc.deny(
            f"Foreman: {charter_path} was generated against commit {charter_commit!r}, but "
            f"HEAD is now {real_commit!r} -- the charter's claims (tests green, no open "
            f"S0/S1, etc.) predate whatever changed since, or HEAD is not a charter-only "
            f"child of that hash. Re-run foreman:ship-readiness before pushing."
        )
        return

    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
