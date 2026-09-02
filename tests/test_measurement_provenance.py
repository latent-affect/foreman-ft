"""DEVH-30: executable verification of R23's frozen criteria (root GOALS.json).

R23 shipped with real verification performed in the implementer's session and no committed
test, so its own verification was not reproducible by anyone else. For the requirement whose
done_state is about making measurements re-derivable by someone who was not in the room, that
is the failure it exists to prevent, one level up. This module closes it.

One test per criterion, named for the criterion, matching the shape
atlas/ingest/tests/test_audit_scrub.py already establishes.

Run: /Users/m5/.venv/bin/python3 -m unittest tests.test_measurement_provenance -v
"""

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
SCRIPTS = ("foreman_quality_baseline.py", "token_bloat_diagnostic.py",
           "security_privacy_convergence.py")


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def snapshot(directory):
    """Independent per-file sha256 of a directory tree, taken by the test.

    Deliberately not a list the tool under test reports about itself: a subject that defines
    the evidence used to judge it can protect the file it discloses and clobber one it does not.
    """
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(directory).rglob("*")) if p.is_file()}


def run_script(script, workdir, *args):
    return subprocess.run([PY, str(REPO / script), *[str(a) for a in args]],
                          cwd=str(workdir), capture_output=True, text=True)


def load_json_reports(paths):
    """Parse every .json path given. A parse failure raises rather than being skipped.

    Silently dropping an unparseable report would let a scope assertion pass vacuously when
    nothing parsed, which is the weak-oracle failure this module exists to rule out.
    """
    out = []
    for p in paths:
        if str(p).endswith(".json"):
            out.append((p, json.loads(Path(p).read_text())))
    return out


def hint_of(report):
    """project_hint, read from meta -- the same key on every report shape (DEVH-36). Before the
    fix, the zero-match path had no `meta` key at all and put project_hint at the top level
    instead, which is how the C4 generated-hint pair first failed here; this helper used to read
    both shapes to work around that. Now there is only one shape to read."""
    return report["meta"]["project_hint"]


class R23ProvenanceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r23check-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- C1

    def test_c1_all_three_scripts_are_tracked_and_produce_real_analysis(self):
        """Tracked in git AND actually analysing something, not merely exiting 0."""
        tracked = git("ls-files", "--", *SCRIPTS).splitlines()
        self.assertEqual(sorted(tracked), sorted(SCRIPTS),
                         "all three measurement scripts must be tracked, and nothing else matched")

        proc = run_script("foreman_quality_baseline.py", self.tmp, REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads((Path(self.tmp) / "quality_baseline_report.json").read_text())

        meta = report["meta"]
        self.assertGreaterEqual(meta["python_files_analyzed"], 150)
        self.assertGreaterEqual(meta["total_functions_analyzed"], 900)

        # Known-real values a silently-broken analysis pass cannot produce. store.py is the
        # only grade-C maintainability file in the tree; measured 2026-09-02 at max_cc 32,
        # MI 4.78. Bounds are loose enough to survive the refactor R1 will make, and tight
        # enough that an empty or stubbed analysis fails.
        store = [f for f in report["per_file_detail"]
                 if f["file_path"].endswith("tessera/store/store.py")]
        self.assertEqual(len(store), 1, "store.py must appear exactly once in per_file_detail")
        self.assertGreaterEqual(store[0]["cyclomatic_complexity"]["max"], 20)
        self.assertLess(store[0]["maintainability_index"]["score"], 25)

        # Third script. Only the environment-independent half of C1's clause is asserted:
        # dependency pins are PARSED from a manifest in the tree, which any machine can do.
        # C1 also names "at least 30 pins actually queried" against OSV, which needs network
        # and installed scanners -- this venv currently reports bandit, detect-secrets and
        # pip-audit all absent -- so asserting it would make this suite fail for reasons that
        # say nothing about the code. That clause is recorded as not reproduced here rather
        # than quietly weakened; see root GOALS.json results[] for C1.
        conv_dir = Path(self.tmp) / "conv"
        conv_dir.mkdir()
        proc = run_script("security_privacy_convergence.py", conv_dir, REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        conv = json.loads((conv_dir / "security_privacy_convergence.json").read_text())
        self.assertGreaterEqual(conv["meta"]["python_files_scanned"], 150)
        self.assertGreaterEqual(conv["meta"]["dependency_pins_parsed"], 30)
        self.assertEqual(conv["meta"]["dependency_lines_skipped_unparsed"], 0,
                         "a skipped manifest line is an unscanned dependency, not a rounding "
                         "error -- the audit trail exists so zero findings can be checked")

    # ---------------------------------------------------------------- C2

    def test_c2_provenance_matches_live_git_and_is_read_at_run_time(self):
        """SHAs compared against git computed here; radon version proven dynamic by a shim."""
        proc = run_script("token_bloat_diagnostic.py", self.tmp, "--days", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        reports = load_json_reports([str(p) for p in Path(self.tmp).glob("*.json")])
        self.assertTrue(reports, "a JSON report must be written")
        _, report = reports[0]

        for key in ("tool_git_sha", "tree_git_sha", "formula_version"):
            self.assertTrue(report.get(key), f"{key} must be present and non-empty")
        self.assertEqual(report["tool_git_sha"],
                         git("log", "-1", "--format=%H", "--", "token_bloat_diagnostic.py"))
        self.assertEqual(report["tree_git_sha"], git("rev-parse", "HEAD"))

        # Forced dynamism, the half a live-match check cannot cover. A hardcoded literal
        # equals the installed version today and keeps passing after an upgrade, which is
        # precisely the event formula_version exists to surface.
        real = importlib.metadata.version("radon")
        sentinel = "9.9.9-sentinel"
        self.assertNotEqual(real, sentinel)

        unpatched_dir = Path(self.tmp) / "unpatched"
        unpatched_dir.mkdir()
        proc = run_script("foreman_quality_baseline.py", unpatched_dir, REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        unpatched = json.loads((unpatched_dir / "quality_baseline_report.json").read_text())
        self.assertIn(real, unpatched["formula_version"],
                      "an unpatched run must report the installed radon version")

        shim = Path(self.tmp) / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            "import importlib.metadata as _m\n"
            "_real = _m.version\n"
            f"_m.version = lambda d: {sentinel!r} if d == 'radon' else _real(d)\n"
        )
        patched_dir = Path(self.tmp) / "patched"
        patched_dir.mkdir()
        proc = subprocess.run([PY, str(REPO / "foreman_quality_baseline.py"), str(REPO)],
                              cwd=str(patched_dir), capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(shim)))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        patched = json.loads((patched_dir / "quality_baseline_report.json").read_text())
        self.assertIn(sentinel, patched["formula_version"],
                      "formula_version must be read at run time; a hardcoded literal would "
                      "report the real version here and pass every live-match check forever")
        self.assertNotIn(real, patched["formula_version"],
                         "the patched run must not also carry the real version, which would "
                         "mean the field concatenates both rather than reading one")

    # ---------------------------------------------------------------- C3

    def test_c3_a_second_run_from_one_directory_does_not_clobber_the_first(self):
        """True created set established by independent snapshot diff, not by self-report."""
        before = snapshot(self.tmp)
        self.assertEqual(run_script("token_bloat_diagnostic.py", self.tmp,
                                    "--project-hint", "dev-harness", "--days", "30").returncode, 0)
        created = {k: v for k, v in snapshot(self.tmp).items() if k not in before}
        self.assertTrue(created, "the first run must create at least one file")

        self.assertEqual(run_script("token_bloat_diagnostic.py", self.tmp,
                                    "--days", "30").returncode, 0)

        for path, digest in created.items():
            self.assertTrue(Path(path).is_file(), f"{path} was removed by the second run")
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest,
                             f"{path} was modified by the second run")
        parsed = load_json_reports(list(created))
        self.assertTrue(parsed, "the created set must include a parseable JSON report")
        for path, report in parsed:
            self.assertEqual(hint_of(report), "dev-harness", f"{path} lost its original scope")

    # ---------------------------------------------------------------- C4

    def test_c4_non_clobbering_generalizes_beyond_the_dev_harness_hint(self):
        """Three pairs, none protecting a dev-harness run, one hint generated at test time."""
        generated = "scopeprobe-" + os.urandom(4).hex()
        pairs = [
            (["--project-hint", "tessera", "--days", "30"],
             ["--project-hint", "atlas", "--days", "30"], "tessera"),
            (["--days", "30"],
             ["--project-hint", "dev-harness", "--days", "30"], None),
            (["--project-hint", generated, "--days", "30"],
             ["--project-hint", "dev-harness", "--days", "30"], generated),
        ]
        for first, second, expected_hint in pairs:
            with self.subTest(protected=first):
                d = tempfile.mkdtemp(prefix="r23pair-", dir=self.tmp)
                before = snapshot(d)
                self.assertEqual(run_script("token_bloat_diagnostic.py", d, *first).returncode, 0)
                created = {k: v for k, v in snapshot(d).items() if k not in before}
                self.assertTrue(created, "the protected run must create at least one file")
                self.assertEqual(run_script("token_bloat_diagnostic.py", d, *second).returncode, 0)

                for path, digest in created.items():
                    self.assertTrue(Path(path).is_file(), f"{path} removed by the second run")
                    self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest,
                                     f"{path} modified by the second run")
                parsed = load_json_reports(list(created))
                self.assertTrue(parsed, "the created set must include a parseable JSON report")
                for path, report in parsed:
                    self.assertEqual(hint_of(report), expected_hint,
                                     f"{path} lost its original scope")


    # ------------------------------------------------- schema unity (DEVH-36)

    def test_zero_match_report_schema_matches_the_populated_one(self):
        """DEVH-36 fix, pinned the other direction: the populated and zero-match reports must
        carry the SAME top-level and meta shape, differing only in whether "error" is present
        and in what meta's own fields say -- not in which fields exist at all. Before the fix,
        the zero-match path had no meta key and put project_hint/days_scanned/parse_stats at
        the top level instead; a consumer reading meta.project_hint unconditionally (hint_of(),
        above) got None on that path with no way to tell "no scope given" from "scope given,
        nothing found in it"."""
        populated = Path(self.tmp) / "pop"
        populated.mkdir()
        self.assertEqual(run_script("token_bloat_diagnostic.py", populated,
                                    "--project-hint", "dev-harness", "--days", "30").returncode, 0)
        pop = load_json_reports([str(p) for p in populated.glob("*.json")])[0][1]

        empty = Path(self.tmp) / "empty"
        empty.mkdir()
        nomatch = "scopeprobe-" + os.urandom(4).hex()
        self.assertEqual(run_script("token_bloat_diagnostic.py", empty,
                                    "--project-hint", nomatch, "--days", "30").returncode, 0)
        zero = load_json_reports([str(p) for p in empty.glob("*.json")])[0][1]

        self.assertIn("meta", pop)
        self.assertIn("meta", zero, "the zero-match path must carry meta too, same as populated")
        self.assertNotIn("error", pop, "a populated run is not an error")
        self.assertIn("error", zero, "the no-data condition lives inside the schema, as a field")

        expected_meta_keys = {
            "project_hint", "days_scanned", "session_files", "parse_stats",
            "projects_seen", "scope_warning",
        }
        self.assertEqual(set(pop["meta"]), expected_meta_keys)
        self.assertEqual(
            set(zero["meta"]), expected_meta_keys,
            "meta must carry the same fields on both paths, not a reduced set",
        )
        self.assertEqual(hint_of(zero), nomatch, "meta.project_hint must survive on this path")
        self.assertNotIn(
            "project_hint", zero, "project_hint must live in meta only, not also at top level"
        )

        for key in ("tool_git_sha", "tree_git_sha", "formula_version"):
            self.assertTrue(zero.get(key), f"{key} must survive on the zero-match path too")


if __name__ == "__main__":
    unittest.main()
