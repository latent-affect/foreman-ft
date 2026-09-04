#!/usr/bin/env python3
"""bollard/GOALS.json C17 (PRD-DELTA-R14-R15.md R15b, DEVH-16). Driven as a real subprocess over
stdin, per F5's own established discipline -- a guard whose logic is correct but never wired to
__main__ denies nothing in production.

    cd bollard && /Users/m5/.venv/bin/python3 -m unittest test_guard_pattern_feed -v

PatternDisjointnessTests is the mechanical proof for R15b/F13: guard_pattern_feed.py is DISJOINT
BY CONSTRUCTION from guard_destructive.py's DESTRUCTIVE_PATTERNS, not a subsuming replacement.
Each guard is driven against the OTHER guard's own trap commands (the same commands its own
tests use), asserting the wrong guard stays silent both directions -- this is the "two pattern
sets are disjoint" test F13 names as the mechanical form the decision must take.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolate_verdict_ledger  # noqa: E402,F401 -- FORE-314: redirects HOME before any guard
# subprocess spawns below, so tests never write to the operator's real
# ~/.claude/telemetry/verdicts.jsonl.

PATTERN_FEED_HOOK = Path(__file__).resolve().parent / "guard_pattern_feed.py"
DESTRUCTIVE_HOOK = Path(__file__).resolve().parent / "guard_destructive.py"


def run_guard(hook_path, tool_name, tool_input):
    payload = {"tool_name": tool_name, "tool_input": tool_input, "session_id": "test", "cwd": "/tmp"}
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
    )


def pattern_feed_bash(command):
    return run_guard(PATTERN_FEED_HOOK, "Bash", {"command": command})


def destructive_bash(command):
    return run_guard(DESTRUCTIVE_HOOK, "Bash", {"command": command})


class GuardPatternFeedTests(unittest.TestCase):
    """One test per declared shape, plus the same DENY-or-nothing / obfuscation-resistance
    discipline C5/C8 already established for guard_destructive.py."""

    def test_secure_delete_shred_is_denied(self):
        result = pattern_feed_bash("shred -uz /tmp/some/real/secret")
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(decision["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_disk_erase_diskutil_is_denied(self):
        result = pattern_feed_bash("diskutil eraseDisk APFS wipe /dev/disk3")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_git_force_push_is_denied(self):
        result = pattern_feed_bash("git push --force origin main")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_git_force_branch_delete_is_denied(self):
        result = pattern_feed_bash("git branch -D some-branch")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_sql_drop_table_is_denied(self):
        result = pattern_feed_bash('psql -c "DROP TABLE users;"')
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_quote_obfuscated_force_push_is_still_denied(self):
        # Same evasion class C5 requires for guard_destructive.py -- empty quote pairs split
        # the flag so a naive literal-string match would miss it.
        result = pattern_feed_bash('git push --for""ce origin main')
        decision = json.loads(result.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_ordinary_git_push_produces_empty_stdout(self):
        result = pattern_feed_bash("git push origin main")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_ordinary_git_branch_listing_produces_empty_stdout(self):
        result = pattern_feed_bash("git branch -a")
        self.assertEqual(result.stdout.strip(), "")

    def test_ordinary_select_query_produces_empty_stdout(self):
        result = pattern_feed_bash('psql -c "SELECT * FROM users;"')
        self.assertEqual(result.stdout.strip(), "")

    def test_diskutil_list_produces_empty_stdout(self):
        result = pattern_feed_bash("diskutil list")
        self.assertEqual(result.stdout.strip(), "")

    def test_unrelated_tool_name_produces_empty_stdout(self):
        result = run_guard(PATTERN_FEED_HOOK, "Write",
                            {"file_path": "/tmp/foo.py", "content": "git push --force"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_empty_payload_exits_zero_with_no_output(self):
        result = subprocess.run(
            [sys.executable, str(PATTERN_FEED_HOOK)], input="{}",
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class PatternDisjointnessTests(unittest.TestCase):
    """R15b / F13, the mechanical form: guard_pattern_feed.py's patterns and
    guard_destructive.py's DESTRUCTIVE_PATTERNS own disjoint shapes. Each guard's real hook
    entry point is driven against the OTHER guard's own trap commands; both directions must
    produce empty stdout. A shared shape would show up here as the wrong guard firing."""

    # One exemplar per guard_destructive.py DESTRUCTIVE_PATTERNS entry (mirrors that guard's
    # own test file's trap commands exactly, including the quote-obfuscated variant).
    DESTRUCTIVE_TRAP_COMMANDS = [
        "rm -rf /tmp/some/real/path",
        "rm -fr /tmp/some/real/path",
        "git clean -fdx",
        "git clean -dfx",
        "dd if=/dev/zero of=/dev/disk2 bs=1m",
        "mkfs.ext4 /dev/disk3",
        'r""m -r""f /tmp/some/real/path',
    ]

    # One exemplar per guard_pattern_feed.py PATTERN_FEED_PATTERNS entry.
    PATTERN_FEED_TRAP_COMMANDS = [
        "shred -uz /tmp/some/real/secret",
        "diskutil eraseDisk APFS wipe /dev/disk3",
        "git push --force origin main",
        "git push -f origin main",
        "git branch -D some-branch",
        'psql -c "DROP TABLE users;"',
        'mysql -e "DROP DATABASE prod;"',
        'git push --for""ce origin main',
    ]

    def test_destructive_trap_commands_do_not_fire_pattern_feed_guard(self):
        for command in self.DESTRUCTIVE_TRAP_COMMANDS:
            with self.subTest(command=command):
                result = pattern_feed_bash(command)
                self.assertEqual(
                    result.stdout.strip(), "",
                    f"guard_pattern_feed.py fired on a guard_destructive.py trap command "
                    f"({command!r}) -- the two pattern sets are not disjoint.",
                )

    def test_pattern_feed_trap_commands_do_not_fire_destructive_guard(self):
        for command in self.PATTERN_FEED_TRAP_COMMANDS:
            with self.subTest(command=command):
                result = destructive_bash(command)
                self.assertEqual(
                    result.stdout.strip(), "",
                    f"guard_destructive.py fired on a guard_pattern_feed.py trap command "
                    f"({command!r}) -- the two pattern sets are not disjoint.",
                )

    def test_pattern_sets_share_no_pattern_name(self):
        # Cheap structural sanity check alongside the behavioral proof above: the two guards
        # must not even coincidentally reuse a rule name, which would make ledger/rule-id
        # attribution ambiguous regardless of the regex behavior.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import guard_destructive
        import guard_pattern_feed
        destructive_names = {name for name, _ in guard_destructive.DESTRUCTIVE_PATTERNS}
        pattern_feed_names = {name for name, _ in guard_pattern_feed.PATTERN_FEED_PATTERNS}
        self.assertEqual(destructive_names & pattern_feed_names, set())


if __name__ == "__main__":
    unittest.main()
