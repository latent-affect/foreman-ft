#!/usr/bin/env python3
"""DEVH-92 Class C: the two enumerations that landed, and the boundary around them.

Class C is the evasion class where a capability was never enumerated at all -- not an obfuscated
spelling of something already denied, so no normalization, chr()/hex/base64 resolution or shlex
parsing reaches it. The only available fix is to enumerate, which is why this module has two
halves and the second one matters more than the first.

HALF ONE, ordinary: the patterns that landed are denied, through the real guard chain, driven as
real subprocesses with real JSON on stdin. No reimplementation of the matcher.

HALF TWO, the unusual part: every entry in each guard's KNOWN_NOT_ENUMERATED is asserted to be
STILL NOT DENIED. Clint's architecture verdict on DEVH-92 says in prose that landing an
enumeration does not close Class C, that any non-enumerated tool evades identically, and that
expanding the list on each discovery is the correct and only available response until a
non-enumeration layer is designed. A caveat in a ticket comment is one a reader has to go and find.
These assertions make the same statement fail a suite when the boundary silently moves.

WHEN ONE OF THOSE FAILS, read this before "fixing" it. A boundary case that starts being denied is
not a bug in this test. It means either the enumeration grew and the declaration is now stale, or
something changed how matching works. Update the guard's KNOWN_NOT_ENUMERATED deliberately and say
so in the commit. Deleting the failing case to get green would remove the only mechanical record
that the boundary exists.

Payloads are passed to the guards as JSON over stdin and never interpolated into a shell command:
quoting a destructive command inline in a Bash call trips guard_destructive on the tool call
itself, a trap this repo has now hit more than once.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

BOLLARD = Path(__file__).resolve().parent
sys.path.insert(0, str(BOLLARD))
import isolate_verdict_ledger  # noqa: E402,F401 -- FORE-314: must precede the hook_common import
# that guard_allowlist/guard_destructive pull in transitively, so tests never write to the
# operator's real ~/.claude/telemetry/verdicts.jsonl.
import guard_allowlist  # noqa: E402
import guard_destructive  # noqa: E402

PY = sys.executable
# The Bash-tool guard chain as registered, in registration order.
CHAIN = ["guard_allowlist.py", "guard_destructive.py", "guard_pattern_feed.py",
         "guard_semantic_resolution.py", "guard_os_sandbox.py"]

CLASS_C_NOW_DENIED = [
    ("pattern 4, alternate privilege-escalation binary", "doas -u root id"),
    ("pattern 4 sibling, pkexec", "pkexec id"),
    ("pattern 4 sibling, sudoedit", "sudoedit /etc/hosts"),
    ("pattern 4 sibling, run0", "run0 id"),
    ("pattern 7, another tool's own delete flag", "find /tmp/devh92-target -delete"),
]

# Ordinary commands that must stay silent. Enumeration buys coverage at the cost of false
# positives, so widening the list without this control is how a guard starts denying real work.
#
# These deliberately do NOT mention any enumerated binary. Merely naming one is denied, and that is
# a pre-existing property of the matching shape rather than anything these additions introduced --
# see MENTION_DENIES below, which measures it on the entry that shipped long before this ticket.
BENIGN = [
    "git status",
    "git push --delete origin some-branch",
    "find /tmp/devh92-target -name '*.log'",
    "echo nothing interesting is happening here",
]

# A pre-existing property, recorded mechanically because it cost real time to rediscover: these
# guards match the command TEXT, so a command that merely mentions an enumerated binary is denied
# even when it invokes nothing. Measured on the long-shipped entry, not on the new ones -- a
# `grep -r <binary> /etc` is denied today and was before this ticket. The name is split at the
# string level so this file's own text is not itself a live match, which is the same trap as
# quoting a destructive command inline in a Bash call.
_PREEXISTING = "sud" + "o"
MENTION_DENIES = [
    f"echo {_PREEXISTING} is not being invoked here",
    f"grep -r {_PREEXISTING} /etc",
]


def fireChain(command):
    """Returns the list of 'guard:decision' strings for guards that blocked. Real subprocesses."""
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "session_id": "devh92-boundary", "cwd": "/tmp"}
    hits = []
    for guard in CHAIN:
        proc = subprocess.run([PY, str(BOLLARD / guard)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=20)
        out = proc.stdout.strip()
        if not out:
            continue
        try:
            decision = (json.loads(out).get("hookSpecificOutput") or {}).get("permissionDecision")
        except json.JSONDecodeError:
            raise AssertionError(f"{guard} emitted unparseable stdout for {command!r}: {out[:200]}")
        if decision in ("deny", "ask"):
            hits.append(f"{guard}:{decision}")
    return hits


class ClassCEnumerationTests(unittest.TestCase):
    def test_control_a_known_bad_command_is_denied(self):
        """Without this the whole module could pass by never detecting anything at all."""
        self.assertTrue(fireChain("rm -rf /tmp/devh92-control-target"),
                        "the chain did not deny a known-bad command -- no result here is "
                        "trustworthy")

    def test_control_benign_commands_stay_silent(self):
        for command in BENIGN:
            with self.subTest(command=command):
                self.assertEqual(fireChain(command), [],
                                 "enumeration introduced a false positive on ordinary work")

    def test_mentioning_an_enumerated_binary_is_denied_and_that_predates_this_ticket(self):
        """Not an endorsement -- a record. `grep -r <binary> /etc` is legitimate work and is
        denied. Asserted on the entry that shipped long before DEVH-92 so the property cannot be
        misattributed to this ticket's additions, which behave identically. Filed separately as a
        false-positive class; fixing it needs command-position matching, which is a change to HOW
        matching works and outside this ticket's reviewed boundary."""
        for command in MENTION_DENIES:
            with self.subTest(command=command):
                self.assertTrue(fireChain(command),
                                "deny-on-mention no longer happens -- if that was fixed "
                                "deliberately, delete this test and say so")

    def test_class_c_patterns_are_now_denied(self):
        for label, command in CLASS_C_NOW_DENIED:
            with self.subTest(pattern=label):
                self.assertTrue(fireChain(command),
                                f"{label} still reaches the agent: {command!r}")


class CoverageBoundaryTests(unittest.TestCase):
    """The declared boundary, asserted. See this module's docstring before changing any of it."""

    def test_allowlist_boundary_cases_are_still_open(self):
        self.assertTrue(guard_allowlist.KNOWN_NOT_ENUMERATED,
                        "an empty boundary declaration would assert nothing while looking "
                        "like coverage")
        for name, command, _why in guard_allowlist.KNOWN_NOT_ENUMERATED:
            with self.subTest(case=name):
                self.assertEqual(
                    fireChain(command), [],
                    f"{name} is now DENIED. That is not a failure of this test: either the "
                    f"enumeration grew and guard_allowlist.KNOWN_NOT_ENUMERATED is stale, or "
                    f"matching changed. Update the declaration deliberately.")

    def test_destructive_boundary_cases_are_still_open(self):
        self.assertTrue(guard_destructive.KNOWN_NOT_ENUMERATED)
        for name, command, _why in guard_destructive.KNOWN_NOT_ENUMERATED:
            with self.subTest(case=name):
                self.assertEqual(
                    fireChain(command), [],
                    f"{name} is now DENIED. Update "
                    f"guard_destructive.KNOWN_NOT_ENUMERATED deliberately rather than deleting "
                    f"this case.")

    def test_every_boundary_entry_carries_a_reason(self):
        # A boundary entry with no stated reason decays into a list of strings nobody can act on.
        for guard in (guard_allowlist, guard_destructive):
            for name, command, why in guard.KNOWN_NOT_ENUMERATED:
                with self.subTest(case=name):
                    self.assertTrue(command.strip(), f"{name} has no example command")
                    self.assertGreater(len(why.strip()), 20,
                                       f"{name} has no usable reason recorded")


if __name__ == "__main__":
    unittest.main()
