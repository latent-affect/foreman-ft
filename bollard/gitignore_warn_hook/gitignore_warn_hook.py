#!/usr/bin/env python3
"""gitignore_warn_hook.py -- CHV2-89.

PostToolUse advisory (never a deny) on Edit|Write|NotebookEdit|MultiEdit|Bash: when a write
lands on a path git will never see (a real `git check-ignore -v` hit), say so out loud, once
per (repo, path, session) rather than let it recur silently. Design sketch:
tools/qa-iris-chen/chv2-89-gitignore-warn-hook/DESIGN-SKETCH-CHV2-89-GITIGNORE-WARN-HOOK.md
(Iris Chen). Ten confirmed real instances of the underlying incident before this hook existed;
every prior fix was "name the new path in the allowlist," which the sketch's own count shows
does not generalize. This hook is structural: catch the moment, not the symptom.

WHY ADVISORY, NEVER DENY (sketch's own reasoning, restated): the write already happened, and is
very often legitimate scratch content. The defect this hook exists to catch is the SILENCE
around an invisible write being mistaken for a tracked one, not the write itself. A deny here
would train sessions to route around the check rather than read it.

SCOPE: generic, not Foreman-adoption-gated. `git rev-parse --show-toplevel` is the only
qualification, not `component_coupling.find_project_root()`'s `.foreman/` marker -- a repo with
no `.foreman/` directory at all still has files git will never see, and `git check-ignore`
does not care about Foreman adoption.

DEDUP KEYED BY (repo_root, relpath, session_id), NOT (repo_root, relpath) ALONE -- the one place
this file departs from the design sketch's own suggested default. Repo-scoped dedup permanently
burns each path's one warning on whichever session happens to see it first, so a LATER session
that starts using that same "already decided fine to be scratch" directory for something that
actually matters gets silence forever -- the same structural failure this whole hook exists to
close, one level up. Session-scoping keeps the sketch's real, stated goal (no re-warning within
one session already warned and repeatedly writing to the same place) without letting one
session's judgment call silently bind every future one. Cost, named not hidden: more dedup-store
entries over time, and a repeat warning across sessions for a directory everyone already treats
as scratch -- bounded by DEDUP_MAX_AGE_SECONDS and DEDUP_MAX_ENTRIES below, enforced on the READ
path as well as the write path (see already_warned()'s own docstring for why that distinction
mattered).

DEDUP STORAGE SURVIVES A GIT WORKTREE (CHV2-89 review finding 2). A worktree's own `.git` is a
FILE (a `gitdir: <path>` pointer), not a directory -- `Path(repo_root) / ".git" / ...` breaks
the moment that's true, and this repo has a real worktree (`atlas-sonnet-qa`) today, so this was
live, not theoretical. git_common_dir() resolves the real, shared directory via `git rev-parse
--git-common-dir` instead of assuming `.git` is a directory -- the same shared location for a
worktree and its main checkout, which is also the semantically right answer: dedup state is a
property of the repo's ignore rules, not of which worktree happened to write first.

CHECK-FAILURE IS NEVER SILENT (CHV2-89 review finding 3, the sharpest one). Before this fix,
`check_ignore()`'s "the CHECK itself broke" outcome (an unexpected `git` exit code, unparseable
`-v` output, a timeout, `git` missing entirely) was indistinguishable from a genuine "not
ignored" answer -- same return shape, no stderr, no audit trail, no distinct verdict-ledger
rule_id. A `check-ignore` output-format change, or a shadowing wrapper script, would have taken
this whole hook permanently and silently dark, with the first symptom being a human losing work
with a "working" safety net installed -- worse than a hang, because a hang is noticed in
minutes. Fixed on four channels at once, deliberately redundant rather than picking one: stderr
(cheap, immediate), `hc.audit()` (durable, queryable later even if nobody was watching that
session), a distinct verdict-ledger rule_id (`:check-degraded` / `:git-unavailable` /
`:gitignore-hit-and-check-degraded`, so a fleet-wide query can see the detector breaking without
reading any one session's transcript), and an in-session `hc.warn_with_system_message()` so the
person actually at the keyboard sees it too, not just a log nobody's looking at yet. The same
four-channel treatment now also covers `git_repo_root()` failing (git missing from PATH
entirely) -- the identical indistinguishable-silence shape one level higher up, closed here for
consistency rather than left as a matching gap next to the one that was named.

DISCLOSED, NOT FIXED (CHV2-89 review finding 1, argued rather than patched -- see the proposal
message this file shipped with for the full reasoning): inside a nested, non-submodule git
repo, this hook is architecturally blind to that whole repo being invisible to an ENCLOSING
repo one level up. `git rev-parse --show-toplevel` correctly scopes to the INNERMOST repo
boundary, which is the right answer for "which repo does this write belong to" in the ordinary
case, but has no way to also ask "is this repo itself invisible to something further up"
without walking parent directories for further `.git` boundaries and independently re-running
`check-ignore` at each one -- a materially bigger, unbounded-depth mechanism than this ticket
scoped, with no existing convention in this codebase for composing ignore precedence ACROSS
repo boundaries (check-ignore does not do this for you). A genuine submodule (a DECLARED nested
repo) has no gap here -- confirmed, checked clean -- the blind spot is specific to an
undeclared, ad-hoc nested repo. If real incidents later show this matters, the precedented next
increment is a BOUNDED one-level-up check (dependency_provenance_gate.py's own one-level script
recursion is this codebase's existing precedent for that shape), not full recursive generality.

NOT BUILT HERE, disclosed rather than silently out of scope: a periodic full-tree sweep for
writes not mediated by any Claude Code tool call at all (a script-internal writer -- run_pull.py/
migrate.py-shaped ingest code, seed_defeaters.py-shaped maintenance scripts). Real gap, matching
k1_standing_detector's own two-cadence precedent -- recommended as a distinct follow-up ticket
once this cadence has run for a while, not built speculatively here.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402

RULE_ID = "FOREMAN-GITIGNORE-WARN"

DEDUP_RELPATH = Path("info") / "gitignore-warn-seen.json"  # under the git dir, never the tracked tree
DEDUP_MAX_ENTRIES = 2000
DEDUP_MAX_AGE_SECONDS = 60 * 60 * 24 * 90  # 90 days

# Matched against the .gitignore PATTERN text (from `check-ignore -v`'s own output), never
# against the file's own name -- a file named results.log living in an otherwise-legitimate
# reports/ directory must still warn if what caught it is a deny-all `/*`, not a `*.log` rule
# that clearly means it. See is_noise_pattern().
NOISE_PATTERN_DENYLIST = (
    "__pycache__", "*.pyc", ".DS_Store", "node_modules", ".venv", "*.lock", "*.log", ".git/",
)

# `git check-ignore -v`'s own output shape: "<source>:<linenum>:<pattern>\t<pathname>".
CHECK_IGNORE_LINE_RE = re.compile(r"^(?P<gifile>.+?):(?P<line>\d+):(?P<pattern>.*)\t(?P<path>.*)$")


def git_repo_root(cwd):
    """(repo_root_or_None, detail_or_None). `detail` is set ONLY when git itself could not be
    consulted (missing binary, timeout) -- never when git ran fine and correctly reported "not
    a repo" (ordinary, expected, silent). Conflating those two is the same indistinguishable-
    silence shape CHV2-89 review finding 3 named for check_ignore(): if `git` vanished from
    PATH entirely, this hook must not look identical to "you're just not in a git repo".

    Deliberately NOT component_coupling.find_project_root(): that requires a `.foreman/` marker
    this concern has nothing to do with (see module docstring's SCOPE section)."""
    if not cwd:
        return None, None
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None, "git rev-parse --show-toplevel timed out after 5s"
    except OSError as exc:
        return None, f"git could not run: {exc}"
    if proc.returncode != 0:
        return None, None  # ordinary "not a git repo" -- git ran fine and said so
    root = proc.stdout.strip()
    return (Path(root), None) if root else (None, None)


def git_common_dir(repo_root):
    """Absolute path to the repo's real, shared git directory -- correct even when `repo_root`
    is a git WORKTREE, where `.git` is a plain FILE (a `gitdir: <path>` pointer), not a
    directory (CHV2-89 review finding 2: `Path(repo_root) / ".git"` raises NotADirectoryError
    there, caught, and dedup silently never persists -- confirmed live against this very repo's
    own `atlas-sonnet-qa` worktree). `git rev-parse --git-common-dir` is git's own answer to
    exactly this question, and returns the SAME directory for a worktree and its main checkout
    -- the semantically right answer too: dedup state is a property of the repo's ignore rules,
    shared across all of a repo's worktrees, not private to whichever one wrote first.

    None on any failure. Callers degrade to "dedup unavailable for this call", which means
    over-warning (a repeat warning) rather than under-warning (a suppressed one) -- the
    direction this hook already commits to as safe elsewhere."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    common_dir = Path(raw)
    if not common_dir.is_absolute():
        common_dir = (Path(repo_root) / common_dir).resolve()
    return common_dir


def resolve_write_targets(data):
    """Absolute Path(s) this tool call wrote, or []. Edit/Write/NotebookEdit/MultiEdit share
    hc.target_path()'s single-field resolution -- all four write exactly one target file per
    call, including MultiEdit (its edits[] are multiple edits WITHIN one file, not multiple
    files). Bash gets component_coupling.extract_bash_write_targets, which already returns
    resolved absolute paths -- same reasoning stage_order_gate.py/defeater_ledger.py give for
    why the Bash channel needs its own check: a redirect, tee, cp, mv bypasses the Edit/Write
    channel entirely."""
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    if tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        target = hc.target_path(tool_input)
        return [Path(target)] if target else []
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        cwd = data.get("cwd")
        return list(cc.extract_bash_write_targets(command, cwd))
    return []


def check_ignore(repo_root, relpath):
    """('ignored', gitignore_path, line_no, pattern, None)
     | ('not-ignored', None, None, None, None)
     | ('check-failed', None, None, None, detail)

    The third case -- the CHECK itself failing, not a clean answer -- is CHV2-89 review finding
    3's exact target: before this fix it was indistinguishable everywhere from a genuine
    'not-ignored'. `detail` is this function's only way to say WHY; it does not silence
    anything itself. main() below is what actually surfaces `detail` on four channels (stderr,
    hc.audit, a distinct verdict-ledger rule_id, an in-session warn) -- this function only
    reports."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-v", "--", str(relpath)],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return "check-failed", None, None, None, "git check-ignore timed out after 5s"
    except OSError as exc:
        return "check-failed", None, None, None, f"git check-ignore could not run: {exc}"
    if proc.returncode == 1:
        return "not-ignored", None, None, None, None
    if proc.returncode != 0:
        return "check-failed", None, None, None, (
            f"git check-ignore exited {proc.returncode}, expected 0 or 1 "
            f"(stderr: {proc.stderr.strip()!r})"
        )
    stripped = proc.stdout.strip()
    if not stripped:
        return "check-failed", None, None, None, "git check-ignore exited 0 with no output"
    m = CHECK_IGNORE_LINE_RE.match(stripped.splitlines()[0])
    if not m:
        return "check-failed", None, None, None, (
            f"git check-ignore -v output did not match the expected "
            f"'<source>:<line>:<pattern>[TAB]<path>' shape: {stripped.splitlines()[0]!r}"
        )
    return "ignored", m.group("gifile"), m.group("line"), m.group("pattern"), None


def is_noise_pattern(pattern):
    """True if the matched .gitignore PATTERN (not the file's own name) is one of the
    denylist's near-always-intentional entries."""
    pattern = (pattern or "").strip()
    return any(noise in pattern for noise in NOISE_PATTERN_DENYLIST)


def load_dedup_store(path):
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def prune_dedup_store(store, now):
    """Age-out entries older than DEDUP_MAX_AGE_SECONDS, then hard-cap at DEDUP_MAX_ENTRIES by
    dropping the oldest survivors. Age-out first (a store that never grows past its own
    retention window doesn't need the cap in practice), the cap as a hard backstop for a
    retention window that turns out to be wrong."""
    fresh = {k: v for k, v in store.items()
             if isinstance(v, dict) and (now - v.get("last_warned_epoch", 0)) < DEDUP_MAX_AGE_SECONDS}
    if len(fresh) > DEDUP_MAX_ENTRIES:
        ordered = sorted(fresh.items(), key=lambda kv: kv[1].get("last_warned_epoch", 0))
        fresh = dict(ordered[-DEDUP_MAX_ENTRIES:])
    return fresh


def dedup_key(relpath, session_id):
    return f"{relpath}\x00{session_id or ''}"


def already_warned(common_dir, relpath, session_id):
    """True if this exact (relpath, session_id) has already been warned about. `common_dir` is
    git_common_dir()'s output, resolved ONCE per hook invocation by the caller -- never
    re-derived here, both to avoid a second subprocess call per target and so a read and its
    matching write always agree on the same directory within one invocation.

    None `common_dir` means dedup is unavailable for this call (git_common_dir() itself
    failed) -- returns False, i.e. never suppress, which means over-warning: the safe
    direction, matching this module's own committed-to convention elsewhere.

    Prunes on READ, not only on write. Age-out is one of the two bounds the session-scoping
    argument in this module's own docstring rests on; if only the write path pruned, an entry
    past DEDUP_MAX_AGE_SECONDS would keep suppressing its warning indefinitely until some
    UNRELATED write happened to trigger a prune -- the exact shape of structural silence that
    argument exists to close, reintroduced one layer below where it was made."""
    if common_dir is None:
        return False
    store_path = common_dir / DEDUP_RELPATH
    store = prune_dedup_store(load_dedup_store(store_path), time.time())
    return dedup_key(relpath, session_id) in store


def record_warned(common_dir, relpath, session_id):
    """Best-effort. `common_dir` as already_warned()'s. A failure to record must not turn an
    advisory hook into one that crashes the tool call -- caught and disclosed to stderr, never
    raised. `common_dir is None` is itself disclosed here too, not silently skipped -- it means
    every write to this repo this invocation will re-warn, which the caller should be able to
    notice happened."""
    if common_dir is None:
        print("[gitignore_warn_hook] git_common_dir unavailable; dedup will not persist for "
              "this write", file=sys.stderr)
        return
    store_path = common_dir / DEDUP_RELPATH
    try:
        store = load_dedup_store(store_path)
        now = time.time()
        store[dedup_key(relpath, session_id)] = {"last_warned_epoch": now}
        store = prune_dedup_store(store, now)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps(store))
    except OSError as exc:
        print(f"[gitignore_warn_hook] could not record dedup state at {store_path}: {exc}",
              file=sys.stderr)


def warn_message(relpath, gitignore_path, line_no, pattern):
    return (
        f"[gitignore-warn] {relpath} is invisible to git -- matched "
        f"{gitignore_path}:{line_no}:{pattern}. `git status` and `git add -A` will never "
        f"surface this file. If this is real work you want tracked, either add an explicit "
        f"!/{relpath} exemption to .gitignore, or move it under a directory already on the "
        f"allowlist. If this is deliberate scratch content, no action needed -- this warning "
        f"will not repeat for this path in this session."
    )


def report_check_failed(stage, detail, session_id, cwd):
    """CHV2-89 review finding 3's fix, factored to one place so main()'s two call sites (repo-
    root resolution and per-target check-ignore) can't drift apart. Two of the four channels
    live here (stderr, hc.audit); the other two (verdict-ledger rule_id, in-session warn) are
    set by main() itself, since they depend on what ELSE happened in this same invocation."""
    print(f"[gitignore_warn_hook] {stage} failed: {detail}", file=sys.stderr)
    hc.audit(
        "GITIGNORE_WARN_CHECK_FAILED", {"stage": stage, "detail": detail},
        session_id=session_id, cwd=cwd, severity="high",
    )


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "NotebookEdit", "MultiEdit", "Bash"):
        return

    targets = resolve_write_targets(data)
    if not targets:
        hc.set_rule(f"{RULE_ID}:no-write-target")
        return

    session_id = data.get("session_id")
    cwd = data.get("cwd")

    repo_root, repo_detail = git_repo_root(cwd)
    if repo_root is None:
        if repo_detail is not None:
            report_check_failed("git-repo-root", repo_detail, session_id, cwd)
            hc.set_rule(f"{RULE_ID}:git-unavailable")
            hc.warn_with_system_message(
                "[gitignore-warn] detector degraded -- see additionalContext",
                f"[gitignore-warn] could not check this write against .gitignore: "
                f"{repo_detail}. This is the detector breaking, not a clean result.",
            )
        else:
            hc.set_rule(f"{RULE_ID}:not-a-git-repo")
        return

    # Boundary named, not closed here -- see module docstring's DISCLOSED section (CHV2-89
    # review finding 1): this is the INNERMOST repo boundary containing `cwd`. A repo nested
    # inside another, undeclared as a submodule, is invisible to this hook exactly the way it's
    # invisible to the enclosing repo's own `git status` -- the defect class this hook exists to
    # catch, reproduced by its own scoping rather than by a bug in it.
    common_dir = git_common_dir(repo_root)

    warnings = []
    check_failures = []
    for target in targets:
        try:
            relpath = Path(target).resolve().relative_to(repo_root).as_posix()
        except (OSError, ValueError):
            continue  # outside the repo, or unresolvable -- not this hook's concern
        if already_warned(common_dir, relpath, session_id):
            continue
        status, gi_path, line_no, pattern, detail = check_ignore(repo_root, relpath)
        if status == "check-failed":
            check_failures.append(relpath)
            report_check_failed(f"check-ignore({relpath})", detail, session_id, cwd)
            continue
        if status != "ignored":
            continue
        if is_noise_pattern(pattern):
            continue
        warnings.append((relpath, gi_path, line_no, pattern))
        record_warned(common_dir, relpath, session_id)

    if not warnings and not check_failures:
        hc.set_rule(f"{RULE_ID}:no-new-warning")
        return

    lines = [warn_message(*w) for w in warnings]
    if check_failures:
        lines.append(
            "[gitignore-warn] could not verify " + ", ".join(check_failures) +
            " against .gitignore -- git check-ignore itself failed, so these writes were NOT "
            "checked (see stderr/audit for why). This is the detector breaking, not a clean "
            "result -- if this repeats, git or its check-ignore output shape may have changed."
        )

    if warnings and check_failures:
        hc.set_rule(f"{RULE_ID}:gitignore-hit-and-check-degraded")
    elif warnings:
        hc.set_rule(f"{RULE_ID}:gitignore-hit")
    else:
        hc.set_rule(f"{RULE_ID}:check-degraded")

    text = "\n".join(lines)
    if check_failures:
        hc.warn_with_system_message(
            "[gitignore-warn] detector degraded -- see additionalContext", text,
        )
    else:
        hc.warn(text)


if __name__ == "__main__":
    hc.run(main)
