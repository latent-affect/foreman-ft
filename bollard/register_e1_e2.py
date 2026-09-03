"""DEVH-95: wire dev-harness-run2-e1 and -e2 with their own .claude/settings.json.

Same governance gap DEVH-90 found in the QA forks (register_qa_worktrees.py), in this
build's own worktree family instead: both -e1 and -e2 are registered TESSERA projects
(DEVHR2E1, DEVHR2E2) with .foreman/ present, but zero .claude/settings.json -- no
PreToolUse hooks, no audit trail, for anything that ran in either worktree.

Identical rewrite logic to register_qa_worktrees.py: copy dev-harness-run2's own live
settings.json, rewrite only the bollard/ command paths to point at each target's own local
bollard/ code, leave every claude-hooks-v2 path untouched, fail-closed publish (write-temp,
disk re-read, structural verify, atomic rename), refuse to overwrite an existing
settings.json.

One real difference from the QA forks, found by verification before this script assumed
otherwise: -e1 and -e2 are dormant since 2026-09-02 at commit 766946b, which predates
guard_allowlist.py, guard_semantic_resolution.py, and guard_pattern_feed.py landing in
bollard/ -- unlike the QA forks (forked from 79e8089, which already has all of them). A
naive path rewrite would register PreToolUse hooks pointing at bollard/ files that do not
exist in either worktree's checkout. Checked directly (ls), not assumed: neither worktree
has those three files. So this script verifies every rewritten command's target file
actually exists on disk for that specific worktree BEFORE publishing, and refuses to wire a
worktree with any dangling command target -- registering a hook chain with holes in it would
be worse than the current zero-governance state, since it would look wired while silently
running fewer checks than the source repo does. See DEVH-95 comment log for what unblocks
this (updating -e1/-e2's checkout to include the missing guards, a git decision outside this
script's scope, not something this script does on its own).

-e2 holds one untracked file, DEVH-76-FALSIFICATION.md -- real work product per DEVH-95's own
text. This script never touches, reads, or globs anything outside .claude/settings.json, so
it cannot clobber it; noted here so nobody assumes otherwise.

Requires Jon's own terminal (FORE-287 auto-mode-classifier pattern) -- an agent session does
not run this itself.
"""

import json
import os
import sys

SOURCE_REPO = "/Users/m5/dev/dev-harness-run2"
SOURCE_SETTINGS = os.path.join(SOURCE_REPO, ".claude", "settings.json")

TARGET_WORKTREES = [
    "/Users/m5/dev/dev-harness-run2-e1",
    "/Users/m5/dev/dev-harness-run2-e2",
]


class RegisterE1E2Error(Exception):
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


def all_commands(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "command" and isinstance(value, str):
                out.append(value)
            else:
                all_commands(value, out)
    elif isinstance(node, list):
        for item in node:
            all_commands(item, out)
    return out


def verify_rewrite(rewritten_obj, target_worktree):
    """Structural check on the IN-MEMORY object before it's ever written:
    - every bollard/ command must resolve under target_worktree, none may still reference
      the source repo.
    - every bollard/ command's target file must actually EXIST on disk for this worktree --
      the real gap register_qa_worktrees.py never had to check, since the QA forks were cut
      from a commit that already had every guard file this repo's settings.json names.
    This does not replace the disk-level verification in publish() -- it's a check on the
    transform itself, run again on the reparsed temp-file content inside publish()."""
    target_bollard_prefix = os.path.join(target_worktree, "bollard") + os.sep
    source_bollard_prefix = os.path.join(SOURCE_REPO, "bollard") + os.sep

    missing_files = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "command" and isinstance(value, str):
                    if "/bollard/" in value:
                        if target_bollard_prefix not in value:
                            raise RegisterE1E2Error(
                                f"rewritten command still doesn't target {target_worktree}: {value}"
                            )
                        if source_bollard_prefix in value:
                            raise RegisterE1E2Error(
                                f"rewritten command still references the source repo: {value}"
                            )
                        script_path = value.split()[-1]
                        if not os.path.isfile(script_path):
                            missing_files.append(script_path)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(rewritten_obj)

    if missing_files:
        raise RegisterE1E2Error(
            f"{target_worktree} is missing {len(missing_files)} guard file(s) this repo's "
            f"settings.json expects: {missing_files}. This worktree's checkout predates "
            f"those guards landing in bollard/ (see this file's module docstring) -- update "
            f"the checkout before wiring, don't register a hook chain with holes in it."
        )


def publish(rewritten_obj, target_worktree):
    """write-temp -> verify by re-reading from disk -> atomic rename. Any failure anywhere
    in this chain raises RegisterE1E2Error; nothing partial is ever left in place at the
    final destination path."""
    dest_dir = os.path.join(target_worktree, ".claude")
    dest_path = os.path.join(dest_dir, "settings.json")
    tmp_path = dest_path + ".register-e1e2-tmp"

    if not os.path.isdir(dest_dir):
        raise RegisterE1E2Error(f"{dest_dir} does not exist -- is this a real worktree?")
    if os.path.exists(dest_path):
        raise RegisterE1E2Error(
            f"{dest_path} already exists -- refusing to overwrite an existing settings.json."
        )

    serialized = json.dumps(rewritten_obj, indent=2) + "\n"

    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterE1E2Error(f"failed writing temp file {tmp_path}: {exc}") from exc

    try:
        with open(tmp_path, encoding="utf-8") as fh:
            on_disk = fh.read()
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterE1E2Error(f"failed re-reading temp file {tmp_path}: {exc}") from exc

    if on_disk != serialized:
        _cleanup_tmp(tmp_path)
        raise RegisterE1E2Error(
            f"on-disk content of {tmp_path} did not match what was written -- refusing to publish"
        )

    try:
        reparsed = json.loads(on_disk)
    except json.JSONDecodeError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterE1E2Error(f"temp file {tmp_path} is not valid JSON on disk: {exc}") from exc

    verify_rewrite(reparsed, target_worktree)

    try:
        os.rename(tmp_path, dest_path)
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        raise RegisterE1E2Error(f"atomic rename to {dest_path} failed: {exc}") from exc

    with open(dest_path, encoding="utf-8") as fh:
        final_on_disk = fh.read()
    if final_on_disk != serialized:
        raise RegisterE1E2Error(
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
        print(f"register_e1_e2: cannot read source settings {SOURCE_SETTINGS}: {exc} -- aborting, nothing written.", file=sys.stderr)
        return 1

    wired = []
    for target_worktree in TARGET_WORKTREES:
        try:
            rewritten = rewrite_bollard_paths(source_obj, target_worktree)
            verify_rewrite(rewritten, target_worktree)
            publish(rewritten, target_worktree)
        except RegisterE1E2Error as exc:
            print(
                f"register_e1_e2: FAILED on {target_worktree}: {exc}\n"
                f"Hard-failing the whole run -- wired {len(wired)}/{len(TARGET_WORKTREES)} "
                f"before this failure: {wired}. No partial success; re-run after fixing the cause.",
                file=sys.stderr,
            )
            return 1
        wired.append(target_worktree)

    print(f"register_e1_e2: wired {len(wired)}/{len(TARGET_WORKTREES)} worktrees:")
    for target_worktree in wired:
        print(f"  {os.path.join(target_worktree, '.claude', 'settings.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
