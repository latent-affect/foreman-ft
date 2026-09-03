"""DEVH-84 regression guard. Per this project's own standing rule (test your own test code):
every check here is proven to fail on a deliberately broken input before its passing result on
good input is trusted -- a validator that has never failed is not yet a validator."""

import copy
import json
import subprocess
import unittest
from pathlib import Path

from goals_results_integrity import check_append_only, check_evidence_quality, current_by_criterion

BOLLARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BOLLARD_DIR.parent
GOOD_ROW = {
    "criterion_id": "C1",
    "status": "MET",
    "evidence": "$ python3 -m unittest test_x -v\nRan 3 tests in 0.01s\nOK\nReal command output, "
                 "long enough to not read as a placeholder.",
    "recorded": "2026-09-03T00:00:00Z",
    "verified_by": "someone",
    "note": "optional field, not required",
}


def _committed_results(rev, path="bollard/GOALS.json"):
    """Reads results[] out of GOALS.json as committed at a given git revision -- the real
    append-only history, not a synthetic fixture, for the two revisions that matter."""
    blob = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(blob)["results"]


class TestAppendOnlyRealHistory(unittest.TestCase):
    """The real commit pair that motivated this check: 5d4dd38 (before) -> 2f61a16 (after),
    where dev-harness-run2-bf appended several rows by hand under the unenforced convention."""

    @classmethod
    def setUpClass(cls):
        cls.old_results = _committed_results("5d4dd38")
        cls.new_results = _committed_results("2f61a16")

    def test_real_append_passes(self):
        violations = check_append_only(self.old_results, self.new_results)
        self.assertEqual(violations, [], violations)

    def test_real_append_actually_added_rows(self):
        # Guards against a checker that passes trivially because nothing changed.
        self.assertGreater(len(self.new_results), len(self.old_results))

    def test_appended_rows_pass_evidence_quality(self):
        appended = self.new_results[len(self.old_results):]
        self.assertGreater(len(appended), 0)
        violations = check_evidence_quality(appended)
        self.assertEqual(violations, [], violations)

    def test_current_working_tree_results_are_append_only_extension_of_head(self):
        """The real gate this ticket is about: today's on-disk GOALS.json vs the last commit
        that touched it. Fails if anyone mutates a historical row without committing first."""
        head_results = _committed_results("HEAD")
        with open(BOLLARD_DIR.parent / "bollard" / "GOALS.json") as f:
            working_tree_results = json.load(f)["results"]
        violations = check_append_only(head_results, working_tree_results)
        self.assertEqual(violations, [], violations)


class TestAppendOnlyAdversarial(unittest.TestCase):
    """Deliberately broken inputs. Each must fail -- this is the proof the check discriminates,
    not just a demonstration that it passes clean input."""

    def setUp(self):
        self.old = [dict(GOOD_ROW, criterion_id="C1"), dict(GOOD_ROW, criterion_id="C2")]

    def test_valid_append_passes(self):
        new = self.old + [dict(GOOD_ROW, criterion_id="C3")]
        self.assertEqual(check_append_only(self.old, new), [])

    def test_valid_supersede_append_passes(self):
        # A new row for an ALREADY-SEEN criterion_id is fine -- that's how supersession works
        # in this file (C19: MET -> NOT_MET -> MET, each a fresh row).
        new = self.old + [dict(GOOD_ROW, criterion_id="C1", status="NOT_MET")]
        self.assertEqual(check_append_only(self.old, new), [])

    def test_mutating_a_historical_row_evidence_fails(self):
        new = copy.deepcopy(self.old)
        new[0]["evidence"] = "quietly rewritten evidence text"
        violations = check_append_only(self.old, new)
        self.assertNotEqual(violations, [])
        self.assertIn("C1", violations[0])

    def test_mutating_a_historical_row_status_fails(self):
        # The sharpest version of the real threat: flipping NOT_MET to MET in place instead
        # of appending a new superseding row.
        old = [dict(GOOD_ROW, criterion_id="C1"), dict(GOOD_ROW, criterion_id="C2", status="NOT_MET")]
        new = copy.deepcopy(old)
        new[1]["status"] = "MET"
        violations = check_append_only(old, new)
        self.assertNotEqual(violations, [])
        self.assertIn("C2", violations[0])

    def test_deleting_a_historical_row_fails(self):
        new = [self.old[0]]
        violations = check_append_only(self.old, new)
        self.assertNotEqual(violations, [])

    def test_reordering_historical_rows_fails(self):
        new = [self.old[1], self.old[0]]
        violations = check_append_only(self.old, new)
        self.assertNotEqual(violations, [])

    def test_no_change_passes(self):
        self.assertEqual(check_append_only(self.old, self.old), [])


class TestEvidenceQualityAdversarial(unittest.TestCase):

    def test_well_formed_row_passes(self):
        self.assertEqual(check_evidence_quality([GOOD_ROW]), [])

    def test_met_with_empty_evidence_fails(self):
        row = dict(GOOD_ROW, evidence="")
        violations = check_evidence_quality([row])
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], 0)

    def test_met_with_placeholder_evidence_fails(self):
        # This is the exact shape the orchestrator asked this check to have an opinion about:
        # a MET row that exists, but with nothing real behind it.
        row = dict(GOOD_ROW, evidence="verified, looks good")
        violations = check_evidence_quality([row])
        self.assertEqual(len(violations), 1)
        self.assertIn("placeholder", violations[0][1])

    def test_missing_verified_by_does_not_fail(self):
        # verified_by is deliberately not required -- real committed history (rows 0-3 of
        # bollard/GOALS.json) predates that convention. Requiring it here would fail
        # legitimate historical data, which is exactly the bug this test caught during
        # development against the real file (see goals_results_integrity.py's own comment).
        row = dict(GOOD_ROW)
        del row["verified_by"]
        violations = check_evidence_quality([row])
        self.assertEqual(violations, [])

    def test_missing_recorded_fails(self):
        row = dict(GOOD_ROW)
        row["recorded"] = ""
        violations = check_evidence_quality([row])
        self.assertTrue(any("recorded" in v[1] for v in violations))

    def test_invalid_status_fails(self):
        row = dict(GOOD_ROW, status="DONE")
        violations = check_evidence_quality([row])
        self.assertTrue(any("status" in v[1] for v in violations))

    def test_non_dict_row_fails(self):
        violations = check_evidence_quality(["not a dict"])
        self.assertEqual(len(violations), 1)

    def test_current_committed_results_pass_evidence_quality(self):
        # Real data, not a fixture: every row ever committed to this project's GOALS.json
        # must clear the same bar new ones do.
        results = _committed_results("HEAD")
        violations = check_evidence_quality(results)
        self.assertEqual(violations, [], violations)


class TestCurrentByCriterionAdversarial(unittest.TestCase):
    """DEVH-78. Deliberately broken inputs first, per this file's own header rule -- a naive
    'first row wins' implementation (e.g. dict comprehension misuse, or setdefault instead of
    plain assignment) would pass every single-row-per-id case and only fail here."""

    def test_single_row_per_id_maps_to_itself(self):
        results = [dict(GOOD_ROW, criterion_id="C1"), dict(GOOD_ROW, criterion_id="C2")]
        self.assertEqual(current_by_criterion(results), {"C1": 0, "C2": 1})

    def test_duplicated_id_picks_the_last_row_not_the_first(self):
        # The real shape: C1 recorded MET, then later found to need re-verification and
        # superseded by a NOT_MET row for the same criterion_id. A first-wins bug would
        # report the stale MET as current.
        results = [
            dict(GOOD_ROW, criterion_id="C1", status="MET"),
            dict(GOOD_ROW, criterion_id="C2"),
            dict(GOOD_ROW, criterion_id="C1", status="NOT_MET"),
        ]
        current = current_by_criterion(results)
        self.assertEqual(current["C1"], 2)
        self.assertEqual(results[current["C1"]]["status"], "NOT_MET")

    def test_four_rows_same_id_picks_the_final_one(self):
        # Real shape from bollard/GOALS.json's own C19: MET -> NOT_MET -> MET -> ... across
        # multiple criterion amendments. Only the last index is current regardless of how the
        # statuses zigzag in between.
        results = [dict(GOOD_ROW, criterion_id="C19", status=s)
                   for s in ("NOT_MET", "MET", "NOT_MET", "MET")]
        self.assertEqual(current_by_criterion(results), {"C19": 3})

    def test_non_dict_row_is_skipped_not_raised(self):
        results = [dict(GOOD_ROW, criterion_id="C1"), "not a dict"]
        self.assertEqual(current_by_criterion(results), {"C1": 0})

    def test_row_missing_criterion_id_is_skipped(self):
        row = dict(GOOD_ROW)
        del row["criterion_id"]
        self.assertEqual(current_by_criterion([row]), {})

    def test_empty_results_returns_empty_map(self):
        self.assertEqual(current_by_criterion([]), {})

    def test_against_real_committed_results_matches_manual_derivation(self):
        # Real data, not a fixture. Manually derived by inspecting bollard/GOALS.json's actual
        # results[] (recorded timestamps confirm array order == chronological order for every
        # duplicated id): C9 -> idx 24 (MET), C16 -> idx 19 (MET), C18 -> idx 18 (MET),
        # C19 -> idx 26 (MET). Fails if a future append changes what "current" means for these
        # ids without this test being updated alongside it -- which is the point.
        results = _committed_results("HEAD")
        current = current_by_criterion(results)
        expected = {"C9": 24, "C16": 19, "C18": 18, "C19": 26}
        for cid, idx in expected.items():
            self.assertEqual(current.get(cid), idx, f"{cid}: expected current index {idx}")
            self.assertEqual(results[idx]["status"], "MET")


if __name__ == "__main__":
    unittest.main()
