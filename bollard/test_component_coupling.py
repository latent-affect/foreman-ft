#!/usr/bin/env python3
"""Regression tests for extract_bash_write_targets: one level of recursion
into a directly-invoked local .sh script, ported from dependency_provenance_gate.py's own
proven pattern. Confirms the fixture that motivated the ticket is now caught, confirms the
containment/depth bounds hold, and confirms every prior consumer's existing behavior on
commands with no invoked script is byte-identical to before.

    python3 -m unittest test_component_coupling -v

(run from /path/to/home/.claude/hooks so the bare `import component_coupling` resolves)
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import component_coupling as cc


class ExtractBashWriteTargetsRecursionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore15-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_fore15_fixture_wrapper_script_write_now_caught(self):
        # The exact known-bad case fixed earlier: a command with no write pattern in
        # its own text, whose invoked script does the real write.
        (self.tmp / "wrapper.sh").write_text(
            "#!/bin/sh\nprintf 'x' > internal/store/backdoor.go\n"
        )
        targets = cc.extract_bash_write_targets("bash wrapper.sh", str(self.tmp))
        self.assertIn(
            (self.tmp / "internal/store/backdoor.go").resolve(),
            targets,
            "the invoked script's own write target must now be visible",
        )

    def test_no_invoked_script_unchanged(self):
        # No .sh token anywhere -- recursion must not alter a single existing consumer's
        # behavior for the overwhelmingly common case.
        targets = cc.extract_bash_write_targets(
            "printf 'hi' > docs/notes.txt", str(self.tmp)
        )
        self.assertEqual(targets, [(self.tmp / "docs/notes.txt").resolve()])

    def test_script_outside_project_root_not_read(self):
        # Containment: an absolute path escaping project_root must not be read, even though
        # it matches the invocation pattern.
        outside = Path(tempfile.mkdtemp(prefix="fore15-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        evil = outside / "evil.sh"
        evil.write_text("printf 'x' > /tmp/should-not-appear.txt\n")
        targets = cc.extract_bash_write_targets(f"bash {evil}", str(self.tmp))
        self.assertEqual(targets, [], "a script outside project_root must not be recursed into")

    def test_one_level_only_second_invocation_not_chased(self):
        # The original bound, ported as-is: an invoked script invoking a THIRD script is not
        # chased. inner.sh's own write must not surface.
        (self.tmp / "inner.sh").write_text("printf 'x' > internal/store/inner-write.go\n")
        (self.tmp / "outer.sh").write_text("#!/bin/sh\nbash inner.sh\n")
        targets = cc.extract_bash_write_targets("bash outer.sh", str(self.tmp))
        self.assertEqual(
            targets, [], "recursion must stop at one level -- inner.sh's write is out of scope"
        )

    def test_no_project_root_skips_recursion_safely(self):
        # cwd resolves to nowhere with a .foreman/ marker -- find_project_root returns None,
        # and recursion must degrade to "don't recurse", not crash.
        no_marker = Path(tempfile.mkdtemp(prefix="fore15-no-marker-"))
        self.addCleanup(shutil.rmtree, no_marker, ignore_errors=True)
        (no_marker / "wrapper.sh").write_text("printf 'x' > somewhere.txt\n")
        targets = cc.extract_bash_write_targets("bash wrapper.sh", str(no_marker))
        self.assertEqual(targets, [])

    def test_oversized_script_not_read(self):
        big = self.tmp / "huge.sh"
        big.write_text("x" * (cc.MAX_SCRIPT_READ_BYTES + 1))
        # Give it a real write line too, so a false PASS (empty result) can't be confused
        # with "the script had nothing to find" -- if the size cap failed to apply, this
        # target would appear.
        with big.open("a") as f:
            f.write("\nprintf 'x' > internal/store/should-not-appear.go\n")
        targets = cc.extract_bash_write_targets("bash huge.sh", str(self.tmp))
        self.assertEqual(targets, [])

    def test_multiline_command_with_invoked_script_both_still_scanned(self):
        # Interaction with the multi-line split fix already in this function: a
        # multi-statement command where ONE statement invokes a script and ANOTHER writes
        # directly must catch both, in the same call.
        (self.tmp / "wrapper.sh").write_text("printf 'x' > internal/store/via-wrapper.go\n")
        command = "bash wrapper.sh\nprintf 'y' > docs/direct.txt"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "internal/store/via-wrapper.go").resolve(), targets)
        self.assertIn((self.tmp / "docs/direct.txt").resolve(), targets)


class ExtractBashWriteTargetsCdAndQuoteTests(unittest.TestCase):
    """Two related findings, reproduced verbatim from the original review comment thread.
    All four of these cases used to resolve to the IDENTICAL target -- the gate could not
    tell a genuine in-repo write from a scratchpad write from prose describing one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore23-"))
        (self.tmp / ".foreman").mkdir()
        (self.tmp / "scratch").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_cd_then_relative_redirect_no_longer_resolves_in_repo(self):
        # Was: resolved to self.tmp/web/index.html -- WRONG, the cd was ignored.
        command = "cd scratch && printf 'hi' > web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertNotIn((self.tmp / "web/index.html").resolve(), targets)

    def test_prose_quoting_a_redirect_is_not_a_write(self):
        # Was: resolved to self.tmp/web/index.html -- WRONG, matched inside the quoted --body.
        command = 'comment --body "denies printf ... > web/index.html on this repo"'
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertNotIn((self.tmp / "web/index.html").resolve(), targets)

    def test_prose_single_quoted_also_not_a_write(self):
        command = "comment --body 'denied on > web/index.html'"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertNotIn((self.tmp / "web/index.html").resolve(), targets)

    def test_control_genuine_in_repo_write_still_caught(self):
        # The real write this whole function exists to catch must still be caught -- neither
        # fix may over-correct into silence.
        command = "printf 'hi' > web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "web/index.html").resolve(), targets)

    def test_control_absolute_target_after_cd_still_caught(self):
        # An ABSOLUTE target is unaffected by an unresolved cd and must still be gated --
        # the cd-safety check only drops RELATIVE targets, never absolute ones.
        command = f"cd scratch && printf 'hi' > {self.tmp}/web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "web/index.html").resolve(), targets)

    def test_quote_awareness_does_not_break_a_real_quoted_target(self):
        # printf 'hi' > "my file.txt" -- the `>` is OUTSIDE any quotes here (between the two
        # quoted arguments), so this must still resolve. Only a `>` that is ITSELF inside a
        # quoted span is prose, not a redirect.
        command = "printf 'hi' > \"my file.txt\""
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "my file.txt").resolve(), targets)

    def test_cd_before_this_segment_does_not_affect_earlier_segments(self):
        # A write BEFORE the cd, in the same multi-segment command, is unaffected by a cd
        # that only takes effect afterward.
        command = "printf 'hi' > docs/before.txt; cd scratch"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "docs/before.txt").resolve(), targets)

    def test_absolute_cd_then_relative_redirect_resolves_against_real_base(self):
        # Found by an adversarial review, 2026-08-22: before this fix, an ABSOLUTE cd was
        # treated identically to a relative one -- any relative target afterward was
        # unconditionally dropped, even though the real destination directory is fully known
        # here. `cd /proj && echo x > ARCHITECTURE.md` walked straight through concept_gate.py.
        command = f"cd {self.tmp} && printf 'hi' > web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp / "scratch"))
        self.assertIn((self.tmp / "web/index.html").resolve(), targets)

    def test_absolute_cd_to_other_dir_does_not_leak_into_original_cwd(self):
        # The resolved target must be against the cd's OWN destination, not silently fall back
        # to the session's original cwd -- that would be a false negative in the other direction.
        other = Path(tempfile.mkdtemp(prefix="fore71-other-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        command = f"cd {other} && printf 'hi' > web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((other / "web/index.html").resolve(), targets)
        self.assertNotIn((self.tmp / "web/index.html").resolve(), targets)

    def test_relative_cd_still_drops_even_with_absolute_cd_earlier(self):
        # A RELATIVE cd must still fall back to today's drop behavior even if an earlier
        # segment set an absolute base -- the relative cd's own destination is unknown, and
        # resolving against the STALE absolute base would be wrong, not an improvement.
        command = f"cd {self.tmp} && cd scratch && printf 'hi' > web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertNotIn((self.tmp / "web/index.html").resolve(), targets)
        self.assertNotIn((self.tmp / "scratch" / "web/index.html").resolve(), targets)


class ExtractBashWriteTargetsAngleBracketTests(unittest.TestCase):
    """An angle bracket with no shell meaning at all -- embedded in ordinary command
    TEXT, not inside quotes (the quote check above doesn't apply here) -- must not be read as
    a redirect operator just because it isn't preceded by a digit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore21-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_html_closing_tag_fragment_not_a_redirect(self):
        # Was: `>` preceded by `v` (not a digit) still matched, capturing "junk" as a target.
        command = "grep -c </div>junk docs/page.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertEqual(targets, [])

    def test_arrow_comment_close_not_a_redirect(self):
        command = "grep -n -->rest docs/notes.txt"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertEqual(targets, [])

    def test_control_space_before_redirect_still_caught(self):
        # The real convention (space before `>`) must be entirely unaffected.
        command = "printf 'hi' > web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "web/index.html").resolve(), targets)

    def test_control_redirect_at_start_of_segment_still_caught(self):
        # `^` alternative: a redirect as the very first thing in a (post-cd) segment.
        command = "true; > web/index.html"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "web/index.html").resolve(), targets)


if __name__ == "__main__":
    unittest.main()
