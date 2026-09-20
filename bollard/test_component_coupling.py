#!/usr/bin/env python3
"""Regression tests for FORE-15's fix to extract_bash_write_targets: one level of recursion
into a directly-invoked local .sh script, ported from dependency_provenance_gate.py's own
proven pattern. Confirms the fixture that motivated the ticket is now caught, confirms the
containment/depth bounds hold, and confirms every prior consumer's existing behavior on
commands with no invoked script is byte-identical to before.

    python3 -m unittest test_component_coupling -v

(run from /Users/m5/.claude/hooks so the bare `import component_coupling` resolves)
"""
import shutil
import sys
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
        # The exact known-bad case from FORE-15/FORE-14: a command with no write pattern in
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
        # FORE-14's own bound, ported as-is: an invoked script invoking a THIRD script is not
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
        # Interaction with the FORE-14 multi-line split fix already in this function: a
        # multi-statement command where ONE statement invokes a script and ANOTHER writes
        # directly must catch both, in the same call.
        (self.tmp / "wrapper.sh").write_text("printf 'x' > internal/store/via-wrapper.go\n")
        command = "bash wrapper.sh\nprintf 'y' > docs/direct.txt"
        targets = cc.extract_bash_write_targets(command, str(self.tmp))
        self.assertIn((self.tmp / "internal/store/via-wrapper.go").resolve(), targets)
        self.assertIn((self.tmp / "docs/direct.txt").resolve(), targets)


class ExtractBashWriteTargetsCdAndQuoteTests(unittest.TestCase):
    """FORE-23 findings 1 and 2, reproduced verbatim from the ticket's own comment thread.
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
        # M1 (FORE-71 adversarial review, 2026-08-22): before this fix, an ABSOLUTE cd was
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
    """FORE-21: an angle bracket with no shell meaning at all -- embedded in ordinary command
    TEXT, not inside quotes (FORE-23's quote check doesn't apply here) -- must not be read as
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


class PrdMdControlExemptionTests(unittest.TestCase):
    """FORE-238/FORE-205: PRD.md was missing from CONTROL_FILENAMES, so a nested docs/PRD.md
    was gated exactly like implementation code -- forcing a real project (tessera-v2) to
    relocate its live PRD.md to project root just to keep writing to it pre-architecture, which
    then broke ship_readiness_gate.py's hardcoded docs/PRD.md read. Confirms PRD.md is now
    exempt at any depth, same as the three pre-existing control filenames, and confirms a
    similarly-named-but-different file is NOT exempt (negative control -- the exemption matches
    on exact filename, not a prefix/substring)."""

    def test_root_prd_md_is_control(self):
        self.assertTrue(cc._is_control(Path("PRD.md")))

    def test_nested_docs_prd_md_is_control(self):
        self.assertTrue(cc._is_control(Path("docs/PRD.md")))

    def test_deeply_nested_prd_md_is_control(self):
        self.assertTrue(cc._is_control(Path("some/deep/path/PRD.md")))

    def test_similarly_named_file_is_not_control(self):
        # Negative control: proves the match is on the exact filename "PRD.md", not a substring
        # or prefix match that would silently over-exempt something like MYPRD.md.
        self.assertFalse(cc._is_control(Path("docs/MYPRD.md")))

    def test_existing_control_filenames_still_control(self):
        # Regression guard: adding PRD.md must not have disturbed the other three.
        for name in ("ARCHITECTURE.md", "ARCHITECTURE-REVIEW.md", "SCOPE.md", "GOALS.json"):
            with self.subTest(name=name):
                self.assertTrue(cc._is_control(Path("docs") / name))

    def test_ordinary_implementation_file_still_not_control(self):
        self.assertFalse(cc._is_control(Path("src/main.py")))


class ParseComponentMapSecondBlockTests(unittest.TestCase):
    """FORE-260 instances 2 and 6. parse_component_map() used to `break` the whole scan on the
    first closing ```yaml fence, silently dropping every LATER ```yaml components block in the
    file -- no error, no malformed entry, a component simply never existed as far as any caller
    could tell. This is that fix's regression test (instance 6's own disclosed gap: the original
    fix, wherever it landed, shipped with no test covering this exact shape).

    FAIL-FIRST, disclosed: as of this test's own authorship, the fix could not be landed in this
    file (goals_freeze_gate.py requires a frozen GOALS.json for the declared 'component_coupling'
    component before any implementation write, and freezing one here would also gate six other
    unrelated declared components that share the same physical hooks/GOALS.json path). This test
    is written against the CURRENT, still-broken code and is expected to FAIL until that
    prerequisite is resolved and the fix actually lands -- confirmed by running it before adding
    this docstring: it fails exactly as described, not vacuously."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore260-i2-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_second_components_block_is_not_silently_dropped(self):
        (self.tmp / "ARCHITECTURE.md").write_text(
            "some text\n\n"
            "```yaml components\n"
            'first_comp: ["hooks/first.py"]\n'
            "```\n\n"
            "more text\n\n"
            "```yaml components\n"
            'second_comp: ["hooks/second.py"]\n'
            "```\n"
        )
        components, malformed = cc.parse_component_map(self.tmp)
        self.assertIn("first_comp", components, "first block must still parse (control)")
        self.assertIn("second_comp", components,
                       "FORE-260 instance 2: a second components block must not be silently "
                       "dropped by the first block's closing fence")
        self.assertEqual(malformed, [],
                          "a dropped second block is not even reported as malformed -- it is "
                          "simply invisible, which is the actual defect")

    def test_single_block_is_unaffected_control(self):
        """Positive control: the overwhelmingly common single-block case must be completely
        unaffected by whatever fixes the second-block case."""
        (self.tmp / "ARCHITECTURE.md").write_text(
            "```yaml components\n"
            'only_comp: ["hooks/only.py"]\n'
            "```\n"
        )
        components, malformed = cc.parse_component_map(self.tmp)
        self.assertEqual(components, {"only_comp": ["hooks/only.py"]})
        self.assertEqual(malformed, [])


class OutboundSymlinkEscapeTests(unittest.TestCase):
    """FORE-588. A write target's DIRECTORY must be resolved without following the TARGET, or a
    governed file replaced by a symlink pointing out of the project resolves to the outside path
    and jurisdiction is lost -- the gate goes silent on a write to a path it governs.

    Two sites carried this, and the second is the one a fix aimed only at the ticket's title
    would have missed:
      extract_bash_write_targets  ended each target with a whole-path .resolve()  (the Bash path)
      project_root_for_target     did candidate.resolve().parent                 (EVERY path)

    So the escape worked through Edit/Write too, not only Bash. Both are covered here, and the
    ordinary non-symlink cases are covered alongside them, because a fix that simply stopped
    resolving anything would pass the escape arms while breaking every normal path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore588-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "governed-project"
        (self.project / ".foreman").mkdir(parents=True)
        (self.project / "src").mkdir()
        self.outside = self.tmp / "elsewhere"
        self.outside.mkdir()
        (self.outside / "planted.py").write_text("from outside\n")
        self.escaped = self.project / "src" / "escaped.py"
        self.escaped.symlink_to(self.outside / "planted.py")
        self.ordinary = self.project / "src" / "ordinary.py"
        self.ordinary.write_text("inside\n")

    def test_jurisdiction_follows_the_path_named_not_the_symlink_target(self):
        self.assertEqual(str(cc.project_root_for_target(str(self.escaped), str(self.project))),
                         str(self.project))

    def test_jurisdiction_unchanged_for_an_ordinary_file(self):
        """The control for the arm above: a fix that stopped resolving at all would pass that
        test and break this one."""
        self.assertEqual(str(cc.project_root_for_target(str(self.ordinary), str(self.project))),
                         str(self.project))

    def test_bash_redirect_into_an_escaped_symlink_stays_in_the_project(self):
        targets = cc.extract_bash_write_targets(f"printf x > {self.escaped}", str(self.project))
        self.assertEqual([str(t) for t in targets], [str(self.escaped)])

    def test_bash_redirect_into_an_ordinary_file_is_unchanged(self):
        targets = cc.extract_bash_write_targets(f"printf x > {self.ordinary}", str(self.project))
        self.assertEqual([str(t) for t in targets], [str(self.ordinary)])

    def test_a_symlinked_parent_directory_is_still_resolved(self):
        """The distinction the fix rests on, and the reason it is not simply "stop resolving".
        A symlinked DIRECTORY in the path is an ordinary arrangement and must still resolve to
        whatever project really holds it. Only the FINAL component stays literal, because only
        the final component is what the command names as its destination."""
        link_dir = self.tmp / "link-to-src"
        link_dir.symlink_to(self.project / "src")
        through_link = link_dir / "ordinary.py"
        self.assertEqual(str(cc.project_root_for_target(str(through_link), str(self.tmp))),
                         str(self.project))

    def test_a_target_whose_leaf_does_not_exist_yet_still_resolves(self):
        """The ordinary case for a WRITE: the file is usually being created. The leaf must not
        need to exist for jurisdiction to be determinable."""
        not_yet = self.project / "src" / "brand-new.py"
        self.assertEqual(str(cc.project_root_for_target(str(not_yet), str(self.project))),
                         str(self.project))


class LnWriteTargetTests(unittest.TestCase):
    """FORE-589. This file had ZERO ln tests before these, which is exactly the shape of the gap
    the ticket describes: no jurisdiction check ran against an ln command, and nothing here would
    have noticed.

    Keyed to the ground-truth rig at
    049310eb-d73d-471f-aa8a-b50f25411f86/scratchpad/dup-check/ln-ground-truth.py, which runs each
    real ln command in a clean directory and compares what actually appears on disk against what
    the extractor predicts. EXTRACT FIRST, EXECUTE SECOND: once a symlink exists, the extractor's
    own .resolve() follows it to the source, so a rig that measures after the fact reports
    disagreements that have nothing to do with ln parsing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore589-ln-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "src.txt").write_text("x")
        (self.tmp / "existingdir").mkdir()

    def targets(self, command):
        return [str(p) for p in cc.extract_bash_write_targets(command, str(self.tmp))]

    def test_symlink_with_no_link_name_targets_the_basename(self):
        """The bug this ticket names. The tail token is the SOURCE; the real link is
        ./basename(source) in the current directory."""
        self.assertEqual(self.targets("ln -s sub/src.txt"),
                         [str((self.tmp / "src.txt").resolve())])

    def test_hard_link_with_no_link_name_has_the_identical_shape(self):
        """`-s` was never the trigger -- positional COUNT is. A bare hard link with no flags at
        all has the same wrong-target bug."""
        self.assertEqual(self.targets("ln sub/src.txt"),
                         [str((self.tmp / "src.txt").resolve())])

    def test_explicit_link_name_still_uses_the_tail_token(self):
        """Regression guard on the path that was already correct."""
        self.assertEqual(self.targets("ln -s sub/src.txt mylink.txt"),
                         [str((self.tmp / "mylink.txt").resolve())])

    def test_hard_link_with_explicit_name_still_uses_the_tail_token(self):
        self.assertEqual(self.targets("ln sub/src.txt mylink.txt"),
                         [str((self.tmp / "mylink.txt").resolve())])

    def test_link_into_an_existing_directory_targets_the_link_not_the_directory(self):
        """`ln -s src existingdir` creates existingdir/basename(src). Textually identical in
        shape to the explicit-link-name form above -- only what is on disk tells them apart,
        which is why this needs a live is_dir check rather than parsing alone."""
        self.assertEqual(self.targets("ln -sf sub/src.txt existingdir"),
                         [str((self.tmp / "existingdir" / "src.txt").resolve())])

    def test_a_nonexistent_last_positional_is_a_link_name_not_a_directory(self):
        """The negative half of the same check: when the last positional is NOT a directory on
        disk, it is the link name and the tail token is right. Without this, a test suite could
        pass with is_dir() hardwired to True."""
        self.assertEqual(self.targets("ln -s sub/src.txt notadir"),
                         [str((self.tmp / "notadir").resolve())])

    def test_relative_cd_leaves_the_directory_question_unanswered(self):
        """A relative `cd` earlier in the command makes a relative candidate unresolvable, so the
        is_dir probe is not attempted and the target is dropped by the existing cd rule rather
        than guessed against the wrong base."""
        self.assertEqual(self.targets("cd somewhere && ln -s sub/src.txt existingdir"), [])

    def test_npm_ln_and_npm_link_are_not_write_commands(self):
        """`npm ln` is a real alias for `npm link`. Command-position anchoring (FORE-591/579) is
        what keeps these out; these arms fail loudly if that anchor is ever loosened."""
        self.assertEqual(self.targets("npm ln @scope/pkg"), [])
        self.assertEqual(self.targets("npm link @scope/pkg"), [])

    def test_cp_and_mv_are_unchanged(self):
        """The shared branch now forks on a named group, so both other verbs need a guard."""
        self.assertEqual(self.targets("cp sub/src.txt out.txt"),
                         [str((self.tmp / "out.txt").resolve())])
        self.assertEqual(self.targets("mv sub/src.txt out.txt"),
                         [str((self.tmp / "out.txt").resolve())])


class ExtractBashWriteTargetsUnresolvableTests(unittest.TestCase):
    """FORE-590/595, per orchestrator-7fe5ba's PDP section 11.6 decision (2026-09-13T19:09:45Z):
    adopt the TargetList record-shape design (surface unresolvable rather than silently drop),
    and catch ValueError alongside OSError/RuntimeError in the resolution guard, both flagged
    via the same .unresolvable mechanism -- one shared fix, not two."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore590-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @unittest.skipUnless(
        sys.version_info < (3, 10),
        "FORE-621's interpreter differential, now correctly scoped: circular symlinks only "
        "raise on .resolve() before Python 3.10ish, on EITHER the parent or leaf position "
        "(measured: 3.9.6 raises, 3.14.7 doesn't, for the parent-component case this test "
        "exercises). Skipping here is honest non-coverage, not a pass; run under "
        "/usr/bin/python3 (3.9.6) to actually exercise this.")
    def test_circular_symlink_in_a_parent_component_is_flagged(self):
        """REAL CORRECTION TO THE ORIGINAL PREMISE, found by executing this against the actual
        current resolve_keeping_leaf (FORE-588), not assumed from the earlier measurement.
        resolve_keeping_leaf resolves the PARENT via a real stat/lstat call and deliberately
        never touches the LEAF (that is its whole point) -- so a circular symlink AT THE LEAF
        position (`printf hi > loop-a`, where loop-a is itself the write target) is never
        dereferenced at all and resolves cleanly under every Python version tested (3.9.6 and
        3.14.7 both) -- there is nothing to flag there, and nothing was silently dropped either;
        the original FORE-590 measurement most likely predates FORE-588's parent-only-resolve
        fix. A circular symlink in a PARENT directory component of the target, by contrast, DOES
        go through a real stat/lstat call and IS a live, real, currently-reproducing case --
        under Python versions before ~3.10 (FORE-621's own differential, same as always)."""
        (self.tmp / "loop").symlink_to(self.tmp / "loop")
        target = f"{self.tmp}/loop/a"
        targets = cc.extract_bash_write_targets(f"printf hi > {target}", str(self.tmp))
        self.assertEqual(len(targets), 1, "must appear, not be silently dropped (P1)")
        self.assertEqual(len(targets.unresolvable), 1)
        self.assertEqual(targets.unresolvable[0][0], target)

    def test_circular_symlink_at_the_leaf_itself_is_not_reproducible_here(self):
        """Documents the negative finding above as an executable check, not just a docstring
        claim: a circular symlink AT the leaf position is not flagged, because
        resolve_keeping_leaf never touches the leaf -- on EITHER interpreter, not just 3.14.
        This is not a regression to fix; it's the original FORE-590 premise not applying to the
        current, post-FORE-588 code for this specific arrival position."""
        (self.tmp / "a").symlink_to(self.tmp / "b")
        (self.tmp / "b").symlink_to(self.tmp / "a")
        targets = cc.extract_bash_write_targets(f"printf hi > {self.tmp}/a", str(self.tmp))
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets.unresolvable, (),
                         "if this ever starts failing, resolve_keeping_leaf's leaf-avoidance "
                         "contract changed -- re-derive this test, don't just update the assert")

    def test_nul_byte_in_the_leaf_is_flagged_via_the_direct_string_check(self):
        """The leaf-position case: resolve_keeping_leaf never raises here (same structural
        reason as the symlink test above), so this is caught by the direct string check, not
        the exception handler -- verified as its own case, not assumed to share coverage with
        the parent-component case below."""
        raw = f"{self.tmp}/before\x00after.txt"
        targets = cc.extract_bash_write_targets(f"printf hi > {raw}", str(self.tmp))
        self.assertEqual(len(targets), 1, "must appear, not crash the caller (FORE-595)")
        self.assertEqual(len(targets.unresolvable), 1)

    def test_nul_byte_in_a_parent_component_is_flagged_via_the_exception_path(self):
        raw = f"{self.tmp}/bef\x00ore/after.txt"
        targets = cc.extract_bash_write_targets(f"printf hi > {raw}", str(self.tmp))
        self.assertEqual(len(targets), 1, "must appear, not crash the caller (FORE-595)")
        self.assertEqual(len(targets.unresolvable), 1)

    def test_ordinary_target_carries_empty_unresolvable(self):
        targets = cc.extract_bash_write_targets("printf hi > docs/notes.txt", str(self.tmp))
        self.assertEqual(targets.unresolvable, ())

    def test_coercing_through_plain_list_loses_the_attribute(self):
        """Disclosed limit, made checkable rather than left as a comment nobody re-verifies."""
        targets = cc.extract_bash_write_targets("printf hi > docs/notes.txt", str(self.tmp))
        self.assertFalse(hasattr(list(targets), "unresolvable"))


if __name__ == "__main__":
    unittest.main()
