#!/usr/bin/env python3
"""Regression tests for FORE-17's ship_readiness_gate.py, extended for REQ-20's sixth check.

    python3 -m unittest test_ship_readiness_gate -v
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ship_readiness_gate as srg


class IsRealGitPushTests(unittest.TestCase):
    def test_plain_git_push_detected(self):
        self.assertTrue(srg.is_real_git_push("git push"))

    def test_git_push_with_args_detected(self):
        self.assertTrue(srg.is_real_git_push("git push origin main"))

    def test_git_push_after_chain_detected(self):
        self.assertTrue(srg.is_real_git_push("cd repo && git push"))

    def test_quoted_prose_not_detected(self):
        self.assertFalse(srg.is_real_git_push('git commit -m "reverts the git push regression"'))

    def test_quoted_single_prose_not_detected(self):
        self.assertFalse(srg.is_real_git_push("echo 'remember to git push later'"))

    def test_unrelated_command_not_detected(self):
        self.assertFalse(srg.is_real_git_push("git status"))


class ShipReadinessGateMainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore17-"))
        (self.tmp / ".foreman").mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.tmp, check=True)
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp,
                                   capture_output=True, text=True, check=True).stdout.strip()

    def _payload(self, command="git push"):
        return {"tool_name": "Bash", "cwd": str(self.tmp), "tool_input": {"command": command}}

    def _full_charter(self, commit_hash=None, prd_satisfaction=None, cold_pass=None):
        return {
            "generated_at": "2026-08-20T00:00:00Z",
            "commit_hash": commit_hash if commit_hash is not None else self.head,
            "checks": {name: {"pass": True, "evidence": "test"} for name in srg.REQUIRED_CHECKS},
            "prd_satisfaction": prd_satisfaction if prd_satisfaction is not None
            else self._good_prd_satisfaction_section(),
            "cold_pass": cold_pass if cold_pass is not None
            else self._good_cold_pass_section(),
        }

    def _good_prd_satisfaction_section(self):
        return {
            "authored_by": "Priya Desai",
            "entries": {
                "REQ-1": {
                    "status": "satisfied",
                    "verification_text_cited": "a real, specific citation of the requirement's "
                                                "own Verification text, well past the minimum "
                                                "length this check enforces.",
                    "evidence_checked": "Ran the requirement's real test suite live, all "
                                         "assertions passed, evidence attached to a real ticket.",
                }
            },
        }

    def _good_cold_pass_section(self):
        return {
            "reviewer_session": "tessera-v2-12",
            "no_prior_exposure_statement": "This session had no prior exposure to the codebase "
                                            "or to any earlier review report before forming its "
                                            "own view.",
            "findings": [],
        }

    def _enable(self):
        (self.tmp / ".foreman" / srg.ENABLED_MARKER).write_text("")

    def _write_charter(self, charter):
        (self.tmp / srg.CHARTER_PATH_REL).write_text(json.dumps(charter))

    def _run(self, command="git push"):
        with mock.patch.object(srg.hc, "deny") as deny, mock.patch.object(srg.hc, "set_rule"):
            srg.main(self._payload(command))
            return deny

    def test_non_bash_tool_ignored(self):
        with mock.patch.object(srg.hc, "deny") as deny:
            srg.main({"tool_name": "Write", "cwd": str(self.tmp),
                      "tool_input": {"file_path": "x"}})
        deny.assert_not_called()

    def test_non_push_command_ignored(self):
        deny = self._run("git status")
        deny.assert_not_called()

    def test_not_opted_in_stays_silent(self):
        # No marker file written -- deliberate opt-in-by-default-off, matches ticket_status_gate.
        deny = self._run()
        deny.assert_not_called()

    def test_opted_in_no_charter_denies(self):
        self._enable()
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("no", deny.call_args[0][0])

    def test_unreadable_charter_denies(self):
        self._enable()
        (self.tmp / srg.CHARTER_PATH_REL).write_text("not json")
        deny = self._run()
        deny.assert_called_once()

    def test_failing_check_denies_and_names_it(self):
        self._enable()
        charter = self._full_charter()
        charter["checks"]["test_suite_green"] = {"pass": False, "evidence": "2 tests failed"}
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("test_suite_green", deny.call_args[0][0])

    def test_string_valued_pass_is_not_treated_as_passing(self):
        self._enable()
        charter = self._full_charter()
        charter["checks"]["stretch_test"] = {"pass": "false -- no stretch test", "evidence": "x"}
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("stretch_test", deny.call_args[0][0])

    def test_missing_check_key_denies(self):
        self._enable()
        charter = self._full_charter()
        del charter["checks"]["dogfood_pass"]
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("dogfood_pass", deny.call_args[0][0])

    def test_stale_charter_denies(self):
        self._enable()
        self._write_charter(self._full_charter(commit_hash="0" * 40))
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("0" * 40, deny.call_args[0][0])

    def test_current_charter_all_passing_allows(self):
        self._enable()
        self._write_charter(self._full_charter())
        deny = self._run()
        deny.assert_not_called()

    def test_prd_satisfaction_pass_true_but_section_missing_denies(self):
        """REQ-20's real point: a bare `checks.prd_satisfaction.pass = true` boolean, with no
        actual section behind it, must not be enough -- the exact content-blindness gap its
        revised Verification text names."""
        self._enable()
        charter = self._full_charter()
        del charter["prd_satisfaction"]
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("prd_satisfaction", deny.call_args[0][0])

    def test_prd_satisfaction_bare_label_entry_denies(self):
        self._enable()
        charter = self._full_charter(prd_satisfaction={
            "authored_by": "Priya Desai",
            "entries": {"REQ-1": {"status": "satisfied",
                                   "verification_text_cited": "ok",
                                   "evidence_checked": "satisfied"}},
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("prd_satisfaction", deny.call_args[0][0])

    def test_prd_satisfaction_missing_authored_by_denies(self):
        self._enable()
        charter = self._full_charter(prd_satisfaction={
            "entries": {"REQ-1": {
                "status": "satisfied",
                "verification_text_cited": "a real, specific citation, well past minimum length.",
                "evidence_checked": "Ran the real suite live, all assertions passed, see ticket.",
            }},
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()

    def test_prd_satisfaction_not_satisfied_without_reason_denies(self):
        self._enable()
        charter = self._full_charter(prd_satisfaction={
            "authored_by": "Priya Desai",
            "entries": {"REQ-24": {
                "status": "not-satisfied",
                "verification_text_cited": "a real, specific citation, well past minimum length.",
                "evidence_checked": "Checked the agent registries directly, no persona found.",
            }},
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()

    def test_ablation_real_unmet_requirement_documented_not_satisfied_allows(self):
        """REQ-20's own required ablation case at the FULL GATE level: a charter that honestly
        documents a real, deliberately-unmet requirement as not-satisfied, with a real reason,
        is a VALID charter (this check's job is to make an honest not-satisfied verdict
        acceptable, not to force a fabricated satisfied). The other five checks are separately
        what would still block an actual push in this scenario in real life; this test isolates
        the prd_satisfaction check's own behavior."""
        self._enable()
        charter = self._full_charter(prd_satisfaction={
            "authored_by": "Priya Desai",
            "entries": {
                "REQ-1": self._good_prd_satisfaction_section()["entries"]["REQ-1"],
                "REQ-24": {
                    "status": "not-satisfied",
                    "verification_text_cited": "a named security-review persona file and skill "
                                                "must exist, be dispatched, and produce a real "
                                                "finding or clean pass.",
                    "evidence_checked": "Checked ~/.claude/agents/ and dev-harness/agents/ "
                                         "directly: five persona files exist, none is a "
                                         "security or privacy reviewer.",
                    "reason": "PRD.md's own REQ-24 text states this requirement cannot ship as "
                              "designed here -- it needs a new persona and skill authored first, "
                              "neither of which exists yet. Not a silent gap: documented.",
                },
            },
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_not_called()

    def test_cold_pass_pass_true_but_section_missing_denies(self):
        """FORE-232's real point, same shape as REQ-20's prd_satisfaction check: a bare
        checks.cold_pass.pass = true boolean, with no actual section behind it, must not be
        enough to satisfy the requirement for a genuinely cold review pass."""
        self._enable()
        charter = self._full_charter()
        del charter["cold_pass"]
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("cold_pass", deny.call_args[0][0])

    def test_cold_pass_missing_reviewer_session_denies(self):
        self._enable()
        charter = self._full_charter(cold_pass={
            "no_prior_exposure_statement": "Had no prior exposure to this codebase whatsoever.",
            "findings": [],
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("reviewer_session", deny.call_args[0][0])

    def test_cold_pass_missing_no_prior_exposure_statement_denies(self):
        self._enable()
        charter = self._full_charter(cold_pass={
            "reviewer_session": "tessera-v2-12",
            "findings": [],
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("no_prior_exposure_statement", deny.call_args[0][0])

    def test_cold_pass_placeholder_statement_denies(self):
        """A trivially short placeholder ("true", "yes", "n/a") must not satisfy the
        statement requirement -- it has to be a real sentence."""
        self._enable()
        charter = self._full_charter(cold_pass={
            "reviewer_session": "tessera-v2-12",
            "no_prior_exposure_statement": "yes",
            "findings": [],
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("no_prior_exposure_statement", deny.call_args[0][0])

    def test_cold_pass_missing_findings_denies(self):
        self._enable()
        charter = self._full_charter(cold_pass={
            "reviewer_session": "tessera-v2-12",
            "no_prior_exposure_statement": "Had no prior exposure to this codebase whatsoever.",
        })
        self._write_charter(charter)
        deny = self._run()
        deny.assert_called_once()
        self.assertIn("findings", deny.call_args[0][0])

    def test_cold_pass_empty_findings_list_allows(self):
        """A cold pass that found nothing is a valid, real outcome -- must not be forced to
        manufacture a finding just to pass shape validation."""
        self._enable()
        self._write_charter(self._full_charter(cold_pass={
            "reviewer_session": "tessera-v2-12",
            "no_prior_exposure_statement": "Had no prior exposure to this codebase whatsoever.",
            "findings": [],
        }))
        deny = self._run()
        deny.assert_not_called()

    def test_cold_pass_with_real_findings_allows(self):
        self._enable()
        self._write_charter(self._full_charter(cold_pass={
            "reviewer_session": "tessera-v2-12",
            "no_prior_exposure_statement": "Had no prior exposure to this codebase whatsoever.",
            "findings": ["N1: dispatch index dir not parameterized", "N2: expiry ignored"],
        }))
        deny = self._run()
        deny.assert_not_called()

    def _write_prior_charter(self, name, generated_at, verdict):
        (self.tmp / ".foreman" / name).write_text(json.dumps({
            "generated_at": generated_at, "verdict": verdict,
        }))

    def _run_audit(self, command="git push"):
        """Like _run(), but also captures hc.audit so tests can assert on the FORE-465 signal
        specifically without disturbing the existing _run() helper every other test uses."""
        with mock.patch.object(srg.hc, "deny") as deny, \
             mock.patch.object(srg.hc, "set_rule"), \
             mock.patch.object(srg.hc, "audit") as audit:
            srg.main(self._payload(command))
            return deny, audit

    def test_hold_to_go_escalation_with_weak_rationale_flags(self):
        """FORE-465 (c): a rationale that's just restated confidence, with a real prior HOLD
        charter on disk, must flag -- audit-only, never a deny."""
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "HOLD")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "GO"
        charter["verdict_change_rationale"] = "Re-read it and feel good about it now."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_called_once()
        self.assertEqual(audit.call_args[0][0], "SHIP_READINESS_UNSUBSTANTIATED_ESCALATION")

    def test_hold_to_go_escalation_with_cold_pass_phrase_does_not_flag(self):
        """FORE-465 (a): a rationale citing a cold pass must not flag."""
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "HOLD")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "GO"
        charter["verdict_change_rationale"] = ("A genuinely cold pass, no authorship exposure "
                                                "to the earlier review, reached the same GO.")
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_not_called()

    def test_hold_to_go_escalation_with_ticket_citation_does_not_flag(self):
        """FORE-465 (b): a rationale citing a named ticket must not flag."""
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "HOLD")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "GO"
        charter["verdict_change_rationale"] = "All four defects from FORE-443 are now closed."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_not_called()

    def test_hold_to_go_escalation_with_command_citation_does_not_flag(self):
        charter_setup_gen_at = "2026-09-02T00:00:00Z"
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "HOLD")
        charter = self._full_charter()
        charter["generated_at"] = charter_setup_gen_at
        charter["verdict"] = "GO"
        charter["verdict_change_rationale"] = "Re-ran `python3 -m unittest -v`, 80/80 green."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_not_called()

    def test_downgrade_with_weak_rationale_does_not_flag(self):
        """FORE-465 (d): the rule only restricts UPWARD movement -- a GO-to-HOLD downgrade
        with the same weak rationale must not flag."""
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "GO")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "HOLD"
        charter["verdict_change_rationale"] = "Just didn't feel right on a second look."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_not_called()

    def test_hold_to_hold_with_weak_rationale_does_not_flag(self):
        """FORE-465 (d), the lateral case: HOLD to HOLD is not an ascending pair either."""
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "HOLD")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "HOLD"
        charter["verdict_change_rationale"] = "Still not there."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_not_called()

    def test_no_supersedes_or_prior_charter_does_not_flag(self):
        """FORE-465 (e): first charter for a project, nothing to compare against."""
        self._enable()
        self._write_charter(self._full_charter())
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_not_called()

    def test_kill_to_anything_else_is_treated_as_ascending(self):
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "KILL")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "HOLD"
        charter["verdict_change_rationale"] = "Reconsidered."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_called_once()

    def test_ship_with_fixes_to_ship_is_treated_as_ascending(self):
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-PRIOR-20260901.json",
                                   "2026-09-01T00:00:00Z", "SHIP WITH FIXES")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "SHIP"
        charter["verdict_change_rationale"] = "Fixes landed."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_called_once()

    def test_only_a_later_prior_charter_is_ignored(self):
        """A sibling charter dated AFTER this one is not "prior" and must not be used for the
        comparison -- only strictly earlier generated_at counts."""
        self._enable()
        self._write_prior_charter("SHIP-CHARTER-LATER-20261001.json",
                                   "2026-10-01T00:00:00Z", "HOLD")
        charter = self._full_charter()
        charter["generated_at"] = "2026-09-02T00:00:00Z"
        charter["verdict"] = "GO"
        charter["verdict_change_rationale"] = "No real justification here."
        self._write_charter(charter)
        deny, audit = self._run_audit()
        deny.assert_not_called()
        audit.assert_not_called()

    def test_not_a_git_repo_stays_silent(self):
        """Renamed (was test_not_a_foreman_project_stays_silent) -- FORE-580 gave 'not a git
        repo at all' its own distinct code path (definitely_not_a_repo), separate from 'is a
        real repo but has no .foreman marker'. This fixture (plain tempdir, no `git init`) was
        always testing the FORMER; the old name implied the latter."""
        outside = Path(tempfile.mkdtemp(prefix="fore17-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with mock.patch.object(srg.hc, "deny") as deny:
            srg.main({"tool_name": "Bash", "cwd": str(outside),
                      "tool_input": {"command": "git push"}})
        deny.assert_not_called()

    def test_real_repo_no_foreman_marker_stays_silent(self):
        """Distinct from test_not_a_git_repo_stays_silent (no repo at all) and
        test_not_opted_in_stays_silent (.foreman exists, enabled marker doesn't) -- this is a
        REAL git repo with no .foreman directory at all."""
        real_repo = Path(tempfile.mkdtemp(prefix="fore580-norepo-marker-"))
        self.addCleanup(shutil.rmtree, real_repo, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=real_repo, check=True)
        with mock.patch.object(srg.hc, "deny") as deny, \
             mock.patch.object(srg.hc, "set_rule") as set_rule:
            srg.main({"tool_name": "Bash", "cwd": str(real_repo),
                      "tool_input": {"command": "git push"}})
        deny.assert_not_called()
        self.assertIn("FOREMAN-SHIP-READINESS-GATE:not-a-foreman-project",
                       [c.args[0] for c in set_rule.call_args_list])


class ReadPrdTextTests(unittest.TestCase):
    """FORE-238: PRD.md's real location isn't fixed to docs/ -- component_coupling.py's
    CONTROL_FILENAMES now exempts it at any depth (FORE-205's companion fix), and at least one
    real project (tessera-v2) was already forced to relocate its live PRD.md to project root as
    a workaround before that exemption existed. _read_prd_text must find it either place, prefer
    root when both exist, and never let a stale-but-readable tombstone shadow real content at
    the other location."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fore238-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_reads_from_project_root(self):
        (self.tmp / "PRD.md").write_text("# REQ-1 root content\n")
        self.assertEqual(srg._read_prd_text(self.tmp), "# REQ-1 root content\n")

    def test_falls_back_to_docs_when_root_absent(self):
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "PRD.md").write_text("# REQ-1 docs content\n")
        self.assertEqual(srg._read_prd_text(self.tmp), "# REQ-1 docs content\n")

    def test_prefers_root_when_both_exist(self):
        (self.tmp / "PRD.md").write_text("# real root content\n")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "PRD.md").write_text("# stale docs content\n")
        self.assertEqual(srg._read_prd_text(self.tmp), "# real root content\n")

    def test_empty_root_tombstone_does_not_shadow_real_docs_content(self):
        # The exact FORE-238 fixture, inverted: a near-empty tombstone at ONE location must not
        # win over real content at the other, regardless of which one is checked first.
        (self.tmp / "PRD.md").write_text("   \n")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "PRD.md").write_text("# real REQ-1..15 content\n")
        self.assertEqual(srg._read_prd_text(self.tmp), "# real REQ-1..15 content\n")

    def test_neither_location_returns_none_not_raise(self):
        self.assertIsNone(srg._read_prd_text(self.tmp))

    def test_both_locations_empty_returns_none(self):
        (self.tmp / "PRD.md").write_text("\n")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "PRD.md").write_text("")
        self.assertIsNone(srg._read_prd_text(self.tmp))


class TicketCitationPrefixTests(unittest.TestCase):
    """CHV2-137. The evidence-citation regex could not recognise 9 of the 42 project prefixes
    live in TESSERA, from two independent causes -- a charset that excludes digits, and a
    six-character length cap.

    These assert against the REAL prefixes rather than a handful of invented ones. A fixture of
    two or three hand-picked ids is what let this survive: every one of them happened to be
    short and alphabetic, which is the same single-fixture blind spot CHV2-124's own ablation
    had (its Arm G positive control used FORE-999 and passed throughout)."""

    DIGIT_PREFIXES = ["CHV2-119", "DEVHR2-3", "F9FR-12", "FOREV2-5", "TESSV2-88"]
    LONG_PREFIXES = ["ATLASQA-1", "ATLASSN-84", "BAYAREA-7", "SELFTEST-2"]
    ALREADY_WORKED = ["FORE-351", "TESS-192", "GIF-4", "WAT-9"]

    def test_digit_bearing_prefixes_are_recognised(self):
        for ticket in self.DIGIT_PREFIXES:
            with self.subTest(ticket=ticket):
                self.assertTrue(srg.TICKET_CITATION_RE.search(f"see {ticket} for evidence"),
                                f"{ticket} reads as citing no ticket at all")

    def test_prefixes_longer_than_six_characters_are_recognised(self):
        for ticket in self.LONG_PREFIXES:
            with self.subTest(ticket=ticket):
                self.assertTrue(srg.TICKET_CITATION_RE.search(f"see {ticket} for evidence"),
                                f"{ticket} reads as citing no ticket at all")

    def test_previously_working_prefixes_still_work(self):
        """The control. A regex that matched everything would satisfy both tests above."""
        for ticket in self.ALREADY_WORKED:
            with self.subTest(ticket=ticket):
                self.assertTrue(srg.TICKET_CITATION_RE.search(f"see {ticket} for evidence"))

    def test_the_false_positive_surface_is_unchanged_from_the_previous_regex(self):
        """Widening the accept set must not quietly widen what counts as a citation. The
        previous regex already matched HTTP-404 and ISO-8601 -- those pre-existing false accepts
        are out of scope here, but this fix must not ADD any, so both regexes are compared
        directly rather than the new one being judged on its own."""
        previous = re.compile(r"\b[A-Z]{2,6}-\d+\b")
        for text in ["HTTP-404", "UTF-8", "ISO-8601", "COVID-19", "A-1", "x-1", "AB-1",
                     "ABCDEFGHIJKLMNOPQ-1", "lower-1", "-5", "FORE-"]:
            with self.subTest(text=text):
                self.assertEqual(bool(srg.TICKET_CITATION_RE.search(text)),
                                 bool(previous.search(text)),
                                 f"{text!r} is classified differently than before this fix")

    def test_every_live_tessera_prefix_is_recognised(self):
        """The guard against this recurring: asserted against the real project table, so a
        prefix added later that this regex cannot see fails here rather than silently reading as
        an uncited rationale. Skips rather than passing if the database is unreachable -- a skip
        is visible, a vacuous pass is not."""
        import sqlite3
        db = Path("/Users/m5/dev/ticket-system/data/tessera.db")
        if not db.is_file():
            self.skipTest(f"TESSERA database not present at {db}")
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            prefixes = sorted({row[0] for row in conn.execute(
                "SELECT DISTINCT project_prefix FROM v_tickets "
                "WHERE project_prefix IS NOT NULL") if row[0]})
        except sqlite3.Error as exc:
            self.skipTest(f"TESSERA database unreadable: {exc}")
        self.assertGreater(len(prefixes), 10, "suspiciously few prefixes; fixture is wrong")
        unseen = [p for p in prefixes if not srg.TICKET_CITATION_RE.search(f"{p}-1")]
        self.assertEqual(unseen, [],
                         f"{len(unseen)} live project prefixes are invisible to the "
                         f"evidence-citation check: {unseen}")

if __name__ == "__main__":
    unittest.main()
