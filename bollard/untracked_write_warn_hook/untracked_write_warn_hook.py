#!/usr/bin/env python3
"""CHV2-91: advisory PostToolUse warning for a write into a path that is UNTRACKED and NOT
gitignored -- the complementary gap gitignore_warn_hook.py (CHV2-89) structurally cannot see,
since that hook only fires when .gitignore itself matches the path. A file nobody ever
`git add`ed is invisible to that check even though `git status` shows it every time, and a
targeted `git add <files>` workflow (this project's own convention, per CLAUDE.md) can skip a
real file forever without anyone noticing -- confirmed live tonight: 8 real, finished,
previously-untracked files (CHV2-91's own part 1) sat outside history for days.

git ls-files --others --exclude-standard is the authoritative primitive (the ticket's own
naming): it lists exactly the untracked-and-not-ignored set, the same set `git status`'s "??"
rows come from.

DESIGN TENSION, flagged not resolved (Alice, CHV2-91): unlike a .gitignore hit (a static,
usually-anomalous repo property), every brand-new file is untracked-by-construction for the
first several seconds of its life. This hook will warn on the very first write to almost every
genuinely new file, not just the ones someone forgot about. Session-scoped dedup (one warning
per new file per session, not per edit) may be acceptable noise ("you made a new file,
remember to add it") or may be too chatty in practice -- not measured, and likely can't be
without actually running this for a while. No grace period or minimum-age check is added here;
naming the open question is the honest move, not picking an unjustified mitigation.

Registration: advisory PostToolUse, same posture as CHV2-89's own landed gate -- register and
watch before deciding whether the noise level above is a real problem.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import hook_common as hc  # noqa: E402
sys.path.insert(0, str(HERE.parent / "gitignore_warn_hook"))
from gitignore_warn_hook import git_repo_root, git_common_dir, resolve_write_targets  # noqa: E402

RULE_ID = "FOREMAN-UNTRACKED-WRITE-WARN"
DEDUP_RELPATH = Path("info") / "untracked-write-warn-seen.json"
DEDUP_MAX_ENTRIES = 2000
DEDUP_MAX_AGE_SECONDS = 60 * 60 * 24 * 90


def check_untracked(repo_root, relpath):
    """('untracked', None) | ('tracked-or-ignored', None) | ('check-failed', detail).
    git ls-files --others --exclude-standard, the ticket's own named authoritative primitive."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard",
             "--", str(relpath)], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return "check-failed", "git ls-files timed out after 5s"
    except OSError as exc:
        return "check-failed", f"git ls-files could not run: {exc}"
    if proc.returncode != 0:
        return "check-failed", f"git ls-files exited {proc.returncode} (stderr: {proc.stderr.strip()!r})"
    return ("untracked", None) if proc.stdout.strip() else ("tracked-or-ignored", None)


def load_dedup_store(path):
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def prune_dedup_store(store, now):
    fresh = {k: v for k, v in store.items()
             if isinstance(v, dict) and (now - v.get("last_warned_epoch", 0)) < DEDUP_MAX_AGE_SECONDS}
    if len(fresh) > DEDUP_MAX_ENTRIES:
        ordered = sorted(fresh.items(), key=lambda kv: kv[1].get("last_warned_epoch", 0))
        fresh = dict(ordered[-DEDUP_MAX_ENTRIES:])
    return fresh


def dedup_key(relpath, session_id):
    return f"{relpath}\x00{session_id or ''}"


def already_warned(common_dir, relpath, session_id):
    if common_dir is None:
        return False
    store = prune_dedup_store(load_dedup_store(common_dir / DEDUP_RELPATH), time.time())
    return dedup_key(relpath, session_id) in store


def record_warned(common_dir, relpath, session_id):
    if common_dir is None:
        print("[untracked_write_warn_hook] git_common_dir unavailable; dedup will not persist",
              file=sys.stderr)
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
        print(f"[untracked_write_warn_hook] could not record dedup state at {store_path}: {exc}",
              file=sys.stderr)


def warn_message(relpath):
    return (f"[untracked-write-warn] {relpath} is untracked and not gitignored -- `git add` "
            f"was never run for it. A targeted `git add <files>` workflow can skip it forever "
            f"even though `git status` shows it. If this is real work, run `git add {relpath}`. "
            f"If deliberate scratch, delete it or add it to .gitignore -- this warning will "
            f"not repeat for this path in this session.")


def report_check_failed(stage, detail, session_id, cwd):
    print(f"[untracked_write_warn_hook] {stage} failed: {detail}", file=sys.stderr)
    hc.audit("UNTRACKED_WRITE_WARN_CHECK_FAILED", {"stage": stage, "detail": detail},
             session_id=session_id, cwd=cwd, severity="high")


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "NotebookEdit", "MultiEdit", "Bash"):
        return
    targets = resolve_write_targets(data)
    if not targets:
        hc.set_rule(f"{RULE_ID}:no-write-target")
        return
    session_id, cwd = data.get("session_id"), data.get("cwd")
    repo_root, repo_detail = git_repo_root(cwd)
    if repo_root is None:
        if repo_detail is not None:
            report_check_failed("git-repo-root", repo_detail, session_id, cwd)
            hc.set_rule(f"{RULE_ID}:git-unavailable")
            hc.warn_with_system_message(
                "[untracked-write-warn] detector degraded -- see additionalContext",
                f"[untracked-write-warn] could not check this write: {repo_detail}.")
        else:
            hc.set_rule(f"{RULE_ID}:not-a-git-repo")
        return
    common_dir = git_common_dir(repo_root)
    warnings, check_failures = [], []
    for target in targets:
        try:
            relpath = Path(target).resolve().relative_to(repo_root).as_posix()
        except (OSError, ValueError):
            continue
        if already_warned(common_dir, relpath, session_id):
            continue
        status, detail = check_untracked(repo_root, relpath)
        if status == "check-failed":
            check_failures.append(relpath)
            report_check_failed(f"ls-files({relpath})", detail, session_id, cwd)
            continue
        if status != "untracked":
            continue
        warnings.append(relpath)
        record_warned(common_dir, relpath, session_id)
    if not warnings and not check_failures:
        hc.set_rule(f"{RULE_ID}:no-new-warning")
        return
    lines = [warn_message(r) for r in warnings]
    if check_failures:
        lines.append("[untracked-write-warn] could not verify " + ", ".join(check_failures) +
                     " -- git ls-files itself failed (see stderr/audit).")
    if warnings and check_failures:
        hc.set_rule(f"{RULE_ID}:untracked-hit-and-check-degraded")
    elif warnings:
        hc.set_rule(f"{RULE_ID}:untracked-hit")
    else:
        hc.set_rule(f"{RULE_ID}:check-degraded")
    text = "\n".join(lines)
    if check_failures:
        hc.warn_with_system_message("[untracked-write-warn] detector degraded -- see additionalContext", text)
    else:
        hc.warn(text)


if __name__ == "__main__":
    hc.run(main)
