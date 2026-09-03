"""DEVH-90: wire each standing QA worktree with its own .claude/settings.json.

Fresh worktrees (dev-harness-run2-qa-security, -qa-general, -qa-evasion) have only
`.claude/agents/` -- no settings.json, since it's gitignored and a fresh worktree does not
inherit it. Each one needs its own copy of the source repo's settings.json, with every
bollard/ guard path rewritten to point at that worktree's OWN local bollard/ code (so a QA
session tests the code actually checked out in its worktree, not the original repo's), while
every claude-hooks-v2 hook path is left untouched (that code is genuinely shared, not
per-worktree).

Fail-closed per the standing privacy/guarantee-design rule (write-temp, verify by re-reading
from disk, atomic rename, hard-fail the whole run on any error anywhere): a partially-wired
worktree -- silently missing a guard, or silently pointed at the wrong worktree's code -- is
worse than no settings.json at all, since it would look configured while testing the wrong
thing or nothing.

Requires Jon's own terminal (FORE-287 auto-mode-classifier pattern) -- an agent session does
not run this itself.
"""

import json
import os
import sys

SOURCE_REPO = "/Users/m5/dev/dev-harness-run2"
SOURCE_SETTINGS = os.path.join(SOURCE_REPO, ".claude", "settings.json")

TARGET_WORKTREES = [
    "/Users/m5/dev/dev-harness-run2-qa-security",
    "/Users/m5/dev/dev-harness-run2-qa-general",
    "/Users/m5/dev/dev-harness-run2-qa-evasion",
]


class RegisterQaWorktreesError(Exception):
    pass


def rewrite_bollard_paths(settings_obj, target_worktree):
    """Replace every command string's SOURCE_REPO/bollard/ prefix with
    target_worktree/bollard/. Leaves any command not under SOURCE_REPO/bollard/ (in
    particular every claude-hooks-v2 path) byte-for-byte untouched. Returns a new object;
    does not mutate the input."""
    source_bollard_prefix = os.path.join(SOURCE_REPO, "bollard") + os.sep
    target_bollard_prefix = os.path.join(target_worktree, "bollard") + os.sep

    def rewrite_command(command):
        if source_bollard_prefix in command:
            return command.replace(source_bollard_prefix, target_bollard_prefix)
        return command

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if key == "command" and isinstance(value, str):
                    out[key] = rewrite_command(value)
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(settings_obj)


def verify_rewrite(rewritten_obj, target_worktree):
    """Structural check on the IN-MEMORY object before it's ever written: every bollard/
    command must resolve under target_worktree, and no claude-hooks-v2 command may have been
    touched. This does not replace the disk-level verification in publish() -- it's a
    cheap early check on the transform itself."""
    target_bollard_prefix = os.path.join(target_worktree, "bollard") + os.sep
    source_bollard_prefix = os.path.join(SOURCE_REPO, "bollard") + os.sep

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "command" and isinstance(value, str):
                    if "/bollard/" in value:
                        if not value.replace(target_bollard_prefix, "").startswith("/bollard/".lstrip("/")) and target_bollard_prefix not in value:
                            raise RegisterQaWorktreesError(
                                f"rewritten command still doesn't target {target_worktree}: {value}"
                            )
                        if source_bollard_prefix in value:
                            raise RegisterQaWorktreesError(
                                f"rewritten command still references the source repo: {value}"
                            )
                    if "claude-hooks-v2" in value:
                        pass
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(rewritten_obj)


def publish(rewritten_obj, target_worktree):
    """write-temp -> verify by re-reading from disk -> atomic rename. Any failure anywhere
    in this chain raises RegisterQaWorktreesError; nothing partial is ever left in place at
    the final destination path."""
    dest_dir = os.path.join(target_worktree, ".claude")
    dest_path = os.path.join(dest_dir, "settings.json")
    tmp_path = dest_path + ".register-qa-tmp"

    if not os.path.isdir(dest_dir):
        raise RegisterQaWorktreesError(f"{dest_dir} does not exist -- is this a real worktree?")
    if os.path.exists(dest_path):
        raise RegisterQaWorktreesError(
            f"{dest_path} already exists -- DEVH-90 scopes this script to fresh worktrees "
            f"only; refusing to overwrite an existing settings.json."
        )

    serialized = json.dumps(rewritten_obj, indent=2) + "\n"

    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterQaWorktreesError(f"failed writing temp file {tmp_path}: {exc}") from exc

    try:
        with open(tmp_path, encoding="utf-8") as fh:
            on_disk = fh.read()
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterQaWorktreesError(f"failed re-reading temp file {tmp_path}: {exc}") from exc

    if on_disk != serialized:
        _cleanup_tmp(tmp_path)
        raise RegisterQaWorktreesError(
            f"on-disk content of {tmp_path} did not match what was written -- refusing to publish"
        )

    try:
        reparsed = json.loads(on_disk)
    except json.JSONDecodeError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterQaWorktreesError(f"temp file {tmp_path} is not valid JSON on disk: {exc}") from exc

    verify_rewrite(reparsed, target_worktree)

    try:
        os.rename(tmp_path, dest_path)
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterQaWorktreesError(f"atomic rename to {dest_path} failed: {exc}") from exc

    with open(dest_path, encoding="utf-8") as fh:
        final_on_disk = fh.read()
    if final_on_disk != serialized:
        raise RegisterQaWorktreesError(
            f"{dest_path} does not match the verified content after rename -- publish "
            f"produced an inconsistent result; treat this worktree as unwired."
        )


def _cleanup_tmp(tmp_path):
    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except OSError:
        pass


def main():
    try:
        with open(SOURCE_SETTINGS, encoding="utf-8") as fh:
            source_obj = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"register_qa_worktrees: cannot read source settings {SOURCE_SETTINGS}: {exc} -- aborting, nothing written.", file=sys.stderr)
        return 1

    wired = []
    for target_worktree in TARGET_WORKTREES:
        try:
            rewritten = rewrite_bollard_paths(source_obj, target_worktree)
            verify_rewrite(rewritten, target_worktree)
            publish(rewritten, target_worktree)
        except RegisterQaWorktreesError as exc:
            print(
                f"register_qa_worktrees: FAILED on {target_worktree}: {exc}\n"
                f"Hard-failing the whole run per DEVH-90's fail-closed requirement -- "
                f"wired {len(wired)}/{len(TARGET_WORKTREES)} before this failure: {wired}. "
                f"No partial success; re-run after fixing the cause.",
                file=sys.stderr,
            )
            return 1
        wired.append(target_worktree)

    print(f"register_qa_worktrees: wired {len(wired)}/{len(TARGET_WORKTREES)} worktrees:")
    for target_worktree in wired:
        print(f"  {os.path.join(target_worktree, '.claude', 'settings.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
