"""GOALS.json C5-C7 (DEVH-19 / PRD.md R18): the hotspot_signal field must not describe a
single-input number as "complexity x git churn" when churn is degenerate (every analyzed file
shares one commit count). Real fixtures -- constructed isolated git repos with real commit
history, not mocked churn maps -- because get_git_churn() shells out to `git log`.

    /Users/m5/.venv/bin/python3 -m unittest tests.test_hotspot_signal_relabel -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import foreman_quality_baseline as fqb  # noqa: E402
import security_privacy_convergence as spc  # noqa: E402

PY = sys.executable


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                    env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.t",
                         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.t",
                         "PATH": "/usr/bin:/bin"})


def _write_py(path, extra_lines=0):
    body = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    body += "\n".join(f"# padding {i}" for i in range(extra_lines)) + "\n"
    path.write_text(body)


class DegenerateChurnFixture(unittest.TestCase):
    """C5: a constructed repo where every .py file has the same real commit count (1)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git(self.tmp, "init", "-q")
        _write_py(self.tmp / "a.py", extra_lines=1)
        _write_py(self.tmp / "b.py", extra_lines=5)
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "initial")

    def test_c5_degenerate_churn_emits_complexity_only_field(self):
        report = fqb.build_report(self.tmp)
        self.assertTrue(report["meta"]["hotspot_signal_degenerate_churn"])
        self.assertIn("hotspot_signal_note", report["meta"])
        self.assertIn("constant at 1", report["meta"]["hotspot_signal_note"])

        for entry in report["per_file_detail"]:
            self.assertIn("hotspot_signal_complexity_only", entry)
            self.assertNotIn("hotspot_signal", entry)

        self.assertTrue(report["hotspots_top15"])
        for h in report["hotspots_top15"]:
            self.assertIn("hotspot_signal_complexity_only", h)
            self.assertNotIn("hotspot_signal", h)


class NonDegenerateChurnFixture(unittest.TestCase):
    """C6: a constructed repo where per-file commit counts genuinely differ."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git(self.tmp, "init", "-q")
        _write_py(self.tmp / "once.py", extra_lines=1)
        _write_py(self.tmp / "thrice.py", extra_lines=1)
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "initial")
        for i in range(2):
            _write_py(self.tmp / "thrice.py", extra_lines=2 + i)
            _git(self.tmp, "add", "-A")
            _git(self.tmp, "commit", "-q", "-m", f"edit {i}")

    def test_c6_non_degenerate_churn_is_unchanged_pre_fix_behavior(self):
        report = fqb.build_report(self.tmp)
        self.assertFalse(report["meta"]["hotspot_signal_degenerate_churn"])
        self.assertNotIn("hotspot_signal_note", report["meta"])

        by_path = {e["file_path"]: e for e in report["per_file_detail"]}
        self.assertEqual(by_path["once.py"]["git_commits_touching_file"], 1)
        self.assertEqual(by_path["thrice.py"]["git_commits_touching_file"], 3)

        for entry in report["per_file_detail"]:
            self.assertIn("hotspot_signal", entry)
            self.assertNotIn("hotspot_signal_complexity_only", entry)
            expected = round(entry["cyclomatic_complexity"]["max"] * entry["git_commits_touching_file"], 1)
            self.assertEqual(entry["hotspot_signal"], expected)

        for h in report["hotspots_top15"]:
            self.assertIn("hotspot_signal", h)
            self.assertNotIn("hotspot_signal_complexity_only", h)


class DownstreamConsumerTests(unittest.TestCase):
    """C7: security_privacy_convergence.py's cross_reference_code_findings() must read
    whichever signal key the quality report actually emitted."""

    def _quality_report(self, degenerate):
        entry = {
            "file_path": "risky.py",
            "max_cc": 12,
            "git_commits": 1,
            "mi_score": 30.0,
            "mi_rank": "A",
        }
        if degenerate:
            entry["hotspot_signal_complexity_only"] = 12.0
        else:
            entry["hotspot_signal"] = 48.0
        return {"hotspots_top15": [entry]}

    def test_c7_degenerate_report_does_not_raise_and_populates_compound(self):
        bandit = [{"file_path": "risky.py", "issue": "B101"}]
        convergent, single, compound = spc.cross_reference_code_findings(
            bandit, [], self._quality_report(degenerate=True)
        )
        self.assertEqual(len(compound), 1)
        self.assertEqual(compound[0]["quality_hotspot_signal"], 12.0)

    def test_c7_non_degenerate_report_still_works(self):
        bandit = [{"file_path": "risky.py", "issue": "B101"}]
        convergent, single, compound = spc.cross_reference_code_findings(
            bandit, [], self._quality_report(degenerate=False)
        )
        self.assertEqual(len(compound), 1)
        self.assertEqual(compound[0]["quality_hotspot_signal"], 48.0)


if __name__ == "__main__":
    unittest.main()
