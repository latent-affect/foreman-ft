#!/usr/bin/env python3
"""
foreman_quality_baseline.py

Full quality / complexity / maintainability baseline for a Python repo,
built to answer one question honestly: where is this codebase actually
weak, and by how much, against real published thresholds -- not against
numbers pulled from an unsourced chat.

WHY THIS EXISTS (vs. the prior complex.py + Gemini pipeline)
--------------------------------------------------------------------------
1. complex.py had a real bug: it parsed lizard's plain-text "location"
   field (format: `function_name@start-end@file_path`) and took the
   FIRST segment after splitting on '@', which is the function name,
   not the file path. Every entry in the old file_complexity_matrix.json
   is keyed by function name. This script uses lizard's Python API
   directly instead of text-parsing subprocess output, which structurally
   avoids that entire bug class.
2. The Gemini conversation stated Radon's Maintainability Index bands as
   "100-60 A / 59-40 B / 39-20 C" (and elsewhere "100-40 A / 39-20 B /
   <20 C"). Radon's actual, documented bands are A >= 20, 10 <= B < 20,
   C < 10 (see https://radon.readthedocs.io -- radon/cli/tools.py
   mi_rank()). This script uses the real bands, cited inline below, so a
   file scoring e.g. 45 is correctly read as an A, not misread as a B.
3. "9/10, 10/10, 4.68/10" earlier came from a tool called "repowise",
   not CodeScene, even though the interpretation guidance given for
   those numbers was CodeScene's specific published bands. This script
   does NOT assume repowise and CodeScene compute health the same way.
   It computes an independent, clearly-labeled approximation instead,
   and never presents it as a CodeScene score.
4. Human-vs-AI baselines: real, cited sources are noted in the report
   header. Nothing here should be read as "the" industry baseline --
   see the citations and their caveats.

REQUIREMENTS
--------------------------------------------------------------------------
pip install radon lizard
(pathspec is optional but recommended for full .gitignore fidelity:
 pip install pathspec)

USAGE
--------------------------------------------------------------------------
python3 foreman_quality_baseline.py [path-to-repo-root]

Defaults to the current directory. Writes two files next to wherever
you run it:
  quality_baseline_report.json   (full machine-readable data)
  quality_baseline_report.txt    (human-readable summary)
"""

import fnmatch
import importlib.metadata
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from report_provenance import ProvenanceError, compute_provenance

try:
    import radon.complexity as radon_cc
    from radon.metrics import mi_visit, h_visit
    from radon.raw import analyze as radon_raw_analyze
except ImportError:
    print("Missing dependency: radon. Install with: pip install radon")
    sys.exit(1)

try:
    import lizard
except ImportError:
    print("Missing dependency: lizard. Install with: pip install lizard")
    sys.exit(1)

try:
    import pathspec
    HAVE_PATHSPEC = True
except ImportError:
    HAVE_PATHSPEC = False


# ---------------------------------------------------------------------------
# REAL, SOURCED THRESHOLDS -- everything below is cited, nothing is invented.
# ---------------------------------------------------------------------------

# McCabe cyclomatic complexity (per function). McCabe's original 1976 paper
# and NIST 500-235 recommend a limit of 10 (up to 15 tolerable with strong
# process). Widely-cited risk bands used across tooling (SonarQube, CodeClimate,
# NDepend, JetBrains):
#   1-10   Low risk / simple
#   11-20  Moderate risk
#   21-50  High risk
#   50+    Very high risk / effectively untestable
CC_BANDS = [
    (10, "Low risk"),
    (20, "Moderate risk"),
    (50, "High risk"),
    (float("inf"), "Very high risk / untestable"),
]

# Radon's OWN documented Maintainability Index rank bands (0-100 scale).
# Source: https://radon.readthedocs.io/en/latest/commandline.html
# and radon's mi_rank() implementation. This is NOT the "100-60/59-40/39-20"
# scale that was used earlier in the project's chat history -- that scale
# does not match radon's real behavior and would misclassify files.
def mi_rank(score: float) -> str:
    if score >= 20:
        return "A"  # radon: "very high" maintainability
    elif score >= 10:
        return "B"  # radon: "medium"
    else:
        return "C"  # radon: "extremely low"


# Radon's per-function/per-block CC letter grades (separate scale from MI):
#   A 1-5   low, simple block
#   B 6-10  low, well-structured
#   C 11-20 moderate, slightly complex
#   D 21-30 more than moderate
#   E 31-40 high, alarming
#   F 41+   very high, error-prone
def cc_rank(score: int) -> str:
    return radon_cc.cc_rank(score)


def cc_band(score: int) -> str:
    for limit, label in CC_BANDS:
        if score <= limit:
            return label
    return CC_BANDS[-1][1]


# ---------------------------------------------------------------------------
# .gitignore handling
# ---------------------------------------------------------------------------

ALWAYS_EXCLUDE_DIRS = {".git"}


def load_gitignore_spec(root: Path):
    gi_path = root / ".gitignore"
    if not gi_path.exists():
        return None, []

    lines = [l.rstrip("\n") for l in gi_path.read_text(errors="ignore").splitlines()]
    patterns = [l for l in lines if l.strip() and not l.strip().startswith("#")]

    if HAVE_PATHSPEC:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        return spec, patterns
    else:
        print(
            "NOTE: 'pathspec' not installed -- using a simplified gitignore "
            "matcher (fnmatch-based). Negation patterns (!) and some gitignore "
            "edge cases will not be honored exactly. pip install pathspec for "
            "full fidelity.",
            file=sys.stderr,
        )
        return None, patterns


def simple_gitignore_match(rel_path: str, patterns) -> bool:
    """Best-effort fallback matcher when pathspec isn't available."""
    parts = rel_path.split(os.sep)
    for pat in patterns:
        pat = pat.strip()
        if pat.startswith("!"):
            continue  # negation not supported in fallback; conservative skip
        pat_clean = pat.rstrip("/")
        if fnmatch.fnmatch(rel_path, pat_clean) or fnmatch.fnmatch(rel_path, f"*/{pat_clean}"):
            return True
        if any(fnmatch.fnmatch(part, pat_clean) for part in parts):
            return True
    return False


def is_ignored(rel_path: str, spec, patterns) -> bool:
    if spec is not None:
        return spec.match_file(rel_path)
    if patterns:
        return simple_gitignore_match(rel_path, patterns)
    return False


def discover_python_files(root: Path):
    spec, patterns = load_gitignore_spec(root)
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDE_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        # prune ignored directories early so we don't descend into them
        pruned = []
        for d in list(dirnames):
            rel_d = os.path.normpath(os.path.join(rel_dir, d)) + "/"
            rel_d = rel_d[2:] if rel_d.startswith("./") else rel_d
            if is_ignored(rel_d, spec, patterns):
                continue
            pruned.append(d)
        dirnames[:] = pruned

        for f in filenames:
            if not f.endswith(".py"):
                continue
            full_path = Path(dirpath) / f
            rel_path = os.path.relpath(full_path, root)
            rel_path_norm = rel_path.replace(os.sep, "/")
            if is_ignored(rel_path_norm, spec, patterns):
                continue
            results.append(full_path)
    return results


# ---------------------------------------------------------------------------
# Git churn (how often a file changes) -- used with complexity to build a
# real hotspot signal (complexity x churn), the same core idea CodeScene
# uses, though this is a simplified standalone approximation, not their
# product or their exact biomarker set.
# ---------------------------------------------------------------------------

def get_git_churn(root: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--pretty=format:", "--name-only"],
            capture_output=True, text=True, check=True,
        )
    except Exception:
        return {}
    churn = defaultdict(int)
    for line in out.stdout.splitlines():
        line = line.strip()
        if line:
            churn[line] += 1
    return churn


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------

def analyze_file(path: Path, root: Path, churn_map):
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    try:
        source = path.read_text(errors="ignore")
    except Exception as e:
        return {"file_path": rel, "error": f"could not read file: {e}"}

    result = {"file_path": rel}

    # --- Raw metrics (LOC, SLOC, comments, blanks) ---
    try:
        raw = radon_raw_analyze(source)
        result["raw"] = {
            "loc": raw.loc,
            "sloc": raw.sloc,
            "comments": raw.comments,
            "blank": raw.blank,
            "comment_ratio_pct": round(100 * raw.comments / raw.sloc, 1) if raw.sloc else 0.0,
        }
    except Exception as e:
        result["raw"] = {"error": str(e)}

    # --- Maintainability Index ---
    try:
        mi_score = mi_visit(source, multi=True)
        result["maintainability_index"] = {
            "score": round(mi_score, 2),
            "rank": mi_rank(mi_score),
        }
    except Exception as e:
        result["maintainability_index"] = {"error": str(e)}

    # --- Halstead volume/difficulty/effort (feeds MI, useful standalone too) ---
    try:
        hal = h_visit(source)
        total = hal.total
        result["halstead"] = {
            "volume": round(total.volume, 1),
            "difficulty": round(total.difficulty, 2),
            "effort": round(total.effort, 1),
        }
    except Exception as e:
        result["halstead"] = {"error": str(e)}

    # --- Churn ---
    result["git_commits_touching_file"] = churn_map.get(rel, 0)

    return result


def analyze_functions_with_lizard(paths, root: Path):
    """
    Uses lizard's Python API directly (not subprocess + text parsing) so
    file paths and function names can never be conflated the way they
    were in the old complex.py.
    """
    str_paths = [str(p) for p in paths]
    functions = []
    per_file_cc = defaultdict(list)

    for file_info in lizard.analyze_files(str_paths):
        rel = os.path.relpath(file_info.filename, root).replace(os.sep, "/")
        for func in file_info.function_list:
            cc = func.cyclomatic_complexity
            entry = {
                "file_path": rel,
                "function_name": func.name,
                "nloc": func.nloc,
                "cyclomatic_complexity": cc,
                "cc_band": cc_band(cc),
                "parameter_count": func.parameter_count,
                "start_line": func.start_line,
                "end_line": func.end_line,
            }
            functions.append(entry)
            per_file_cc[rel].append(cc)

    return functions, per_file_cc


# ---------------------------------------------------------------------------
# Duplication -- heuristic only. This is NOT a real clone-detection engine
# (that would be jscpd, PMD/CPD, or SonarQube's own duplication analysis).
# It flags exact-match line blocks >= MIN_BLOCK lines repeated across the
# codebase, which will under-count near-duplicates and structural clones.
# Treat this section as a rough signal, not a defensible metric.
# ---------------------------------------------------------------------------

MIN_BLOCK = 6


def find_exact_duplicate_blocks(file_texts):
    block_locations = defaultdict(list)
    for rel, lines in file_texts.items():
        stripped = [l.strip() for l in lines]
        for i in range(len(stripped) - MIN_BLOCK + 1):
            block = tuple(stripped[i:i + MIN_BLOCK])
            if all(b == "" for b in block):
                continue
            block_locations[block].append((rel, i + 1))

    duplicates = []
    for block, locations in block_locations.items():
        distinct_files = {loc[0] for loc in locations}
        if len(locations) > 1 and len(distinct_files) >= 1:
            # only report cross-file OR same-file-repeated-3x+ to reduce noise
            if len(distinct_files) > 1 or len(locations) >= 3:
                duplicates.append({
                    "lines": len(block),
                    "occurrences": len(locations),
                    "locations": [f"{loc[0]}:{loc[1]}" for loc in locations],
                })
    duplicates.sort(key=lambda d: d["occurrences"], reverse=True)
    return duplicates[:50]  # cap for report size


# ---------------------------------------------------------------------------
# Aggregation -- simple average vs LOC-weighted average. CodeScene's own
# docs explicitly warn a simple average can misrepresent a codebase (their
# published example: 7.33 simple average vs 2.18 weighted average for the
# same repo). This script reports both and labels which is which.
# ---------------------------------------------------------------------------

def approximate_health_score(mi_score, max_cc):
    """
    A standalone 1-10 approximation, NOT a CodeScene score and not claimed
    to match CodeScene's proprietary weighting or biomarker set. Blends
    normalized MI and worst-function CC into a single number so files can
    be ranked against each other, nothing more.

    DEVH-97: the CC half saturates at max_cc > 50 (cc_component pins to 1
    regardless of whether max_cc is 51 or 5000), so this blended number
    loses information above that band. Do not use it as a per-file
    pass/fail threshold -- compare mi_rank and max_cc directly for that
    (see per_file_detail, hotspots_top15, worst_maintainability_top15).
    """
    mi_component = max(0, min(10, mi_score / 10))
    if max_cc <= 10:
        cc_component = 10
    elif max_cc <= 20:
        cc_component = 7
    elif max_cc <= 50:
        cc_component = 4
    else:
        cc_component = 1
    return round((mi_component * 0.5) + (cc_component * 0.5), 2)


def build_report(root: Path):
    py_files = discover_python_files(root)
    if not py_files:
        print("No Python files found after applying .gitignore exclusions.")
        sys.exit(1)

    churn_map = get_git_churn(root)
    functions, per_file_cc = analyze_functions_with_lizard(py_files, root)

    file_reports = []
    file_texts = {}
    for path in py_files:
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        entry = analyze_file(path, root, churn_map)

        max_cc = max(per_file_cc.get(rel, [0]))
        avg_cc = round(sum(per_file_cc.get(rel, [0])) / len(per_file_cc[rel]), 2) if per_file_cc.get(rel) else 0
        entry["cyclomatic_complexity"] = {
            "max": max_cc,
            "avg": avg_cc,
            "function_count": len(per_file_cc.get(rel, [])),
        }

        mi_score = entry.get("maintainability_index", {}).get("score", 0)
        entry["approx_health_score_1_to_10"] = approximate_health_score(mi_score, max_cc)

        file_reports.append(entry)

        try:
            file_texts[rel] = path.read_text(errors="ignore").splitlines()
        except Exception:
            pass

    # R18 fix (DEVH-19): git churn's whole-history commit count is degenerate (every currently
    # tracked file shares one value, this repo's real state today per GOALS.json C5) whenever
    # the tree's real history is a squashed/rewritten distribution rather than an organic
    # commit-per-change history. Multiplying by a constant does not change the RANKING
    # hotspots_top15 sorts by, but "complexity x git churn" misdescribes a single-signal number
    # as compound when churn contributes nothing but a fixed multiplier -- and R1's file-
    # selection ranking reads this field. Detected from the real distribution rather than
    # hardcoded to churn==1 (GOALS.json F12): len(set(churn_values)) <= 1 catches "every file
    # shares one churn value" regardless of what that value happens to be.
    churn_values = [f["git_commits_touching_file"] for f in file_reports]
    churn_is_degenerate = len(set(churn_values)) <= 1
    hotspot_signal_key = "hotspot_signal_complexity_only" if churn_is_degenerate else "hotspot_signal"
    for entry in file_reports:
        max_cc = entry["cyclomatic_complexity"]["max"]
        if churn_is_degenerate:
            entry["hotspot_signal_complexity_only"] = round(max_cc, 1)
        else:
            entry["hotspot_signal"] = round(max_cc * entry["git_commits_touching_file"], 1)

    # weighted vs simple average health (weighted by SLOC, CodeScene-style)
    valid = [f for f in file_reports if "error" not in f.get("raw", {})]
    simple_avg = round(sum(f["approx_health_score_1_to_10"] for f in valid) / len(valid), 2) if valid else 0
    total_sloc = sum(f["raw"].get("sloc", 0) for f in valid)
    weighted_avg = (
        round(sum(f["approx_health_score_1_to_10"] * f["raw"].get("sloc", 0) for f in valid) / total_sloc, 2)
        if total_sloc else 0
    )

    hotspots = sorted(file_reports, key=lambda f: f.get(hotspot_signal_key, 0), reverse=True)[:15]
    worst_mi = sorted(
        [f for f in valid if "error" not in f.get("maintainability_index", {})],
        key=lambda f: f["maintainability_index"]["score"]
    )[:15]
    worst_cc_functions = sorted(functions, key=lambda f: f["cyclomatic_complexity"], reverse=True)[:20]

    duplicates = find_exact_duplicate_blocks(file_texts)

    report = {
        "meta": {
            "root": str(root),
            "python_files_analyzed": len(py_files),
            "total_functions_analyzed": len(functions),
            "thresholds_source": {
                "cyclomatic_complexity": "McCabe 1976 / NIST 500-235; bands: 1-10 low, "
                                          "11-20 moderate, 21-50 high, 50+ very high/untestable",
                "maintainability_index": "radon's documented mi_rank(): A>=20, 10<=B<20, C<10 "
                                          "(0-100 scale) -- see radon.readthedocs.io",
                "health_score_1_to_10": "standalone approximation for THIS script only, "
                                         "not a CodeScene score. Whole-repo ranking use only "
                                         "(DEVH-97) -- it saturates above max_cc=50, so a "
                                         "per-file pass/fail threshold judgment should compare "
                                         "mi_rank and max_cc directly instead.",
            },
            "hotspot_signal_degenerate_churn": churn_is_degenerate,
        },
        "aggregate": {
            "simple_average_health_1_to_10": simple_avg,
            "sloc_weighted_average_health_1_to_10": weighted_avg,
            "note": "CodeScene's own docs warn simple averages misrepresent a codebase "
                    "(their published example: 7.33 simple vs 2.18 weighted, same repo). "
                    "Trust the weighted number more.",
        },
        "hotspots_top15": [
            {
                "file_path": f["file_path"],
                hotspot_signal_key: f[hotspot_signal_key],
                "max_cc": f["cyclomatic_complexity"]["max"],
                "git_commits": f["git_commits_touching_file"],
                "mi_score": f.get("maintainability_index", {}).get("score"),
                "mi_rank": f.get("maintainability_index", {}).get("rank"),
            }
            for f in hotspots
        ],
        "worst_maintainability_top15": [
            {
                "file_path": f["file_path"],
                "mi_score": f["maintainability_index"]["score"],
                "mi_rank": f["maintainability_index"]["rank"],
                "max_cc": f["cyclomatic_complexity"]["max"],
                "sloc": f["raw"].get("sloc"),
            }
            for f in worst_mi
        ],
        "worst_functions_by_cc_top20": worst_cc_functions,
        "duplicate_blocks_heuristic": duplicates,
        "per_file_detail": file_reports,
    }
    if churn_is_degenerate:
        constant_churn = churn_values[0] if churn_values else 0
        report["meta"]["hotspot_signal_note"] = (
            f"git churn is constant at {constant_churn} across every analyzed file (R18, "
            f"DEVH-19) -- the hotspot signal below carries complexity only and is emitted as "
            f"hotspot_signal_complexity_only, not hotspot_signal, so a reader does not mistake "
            f"a single-signal number for the compound complexity-x-churn one the field name "
            f"would otherwise imply."
        )
    return report


def write_txt_summary(report, out_path: Path):
    lines = []
    m = report["meta"]
    a = report["aggregate"]
    lines.append("=" * 70)
    lines.append("FOREMAN QUALITY BASELINE -- SUMMARY")
    lines.append("=" * 70)
    lines.append(f"Files analyzed: {m['python_files_analyzed']}")
    lines.append(f"Functions analyzed: {m['total_functions_analyzed']}")
    lines.append("")
    lines.append(f"Simple average health (1-10):        {a['simple_average_health_1_to_10']}")
    lines.append(f"SLOC-weighted average health (1-10):  {a['sloc_weighted_average_health_1_to_10']}")
    lines.append(f"  -> {a['note']}")
    lines.append("")
    degenerate_churn = m.get("hotspot_signal_degenerate_churn", False)
    signal_key = "hotspot_signal_complexity_only" if degenerate_churn else "hotspot_signal"
    lines.append("-" * 70)
    if degenerate_churn:
        lines.append("TOP 15 HOTSPOTS (complexity only -- git churn is constant across every "
                      "analyzed file, see meta.hotspot_signal_note)")
    else:
        lines.append("TOP 15 HOTSPOTS (complexity x git churn -- fix these first)")
    lines.append("-" * 70)
    for h in report["hotspots_top15"]:
        lines.append(
            f"  {h['file_path']}  signal={h[signal_key]}  "
            f"max_cc={h['max_cc']}  commits={h['git_commits']}  "
            f"MI={h['mi_score']} ({h['mi_rank']})"
        )
    lines.append("")
    lines.append("-" * 70)
    lines.append("TOP 15 WORST MAINTAINABILITY INDEX SCORES")
    lines.append("-" * 70)
    for w in report["worst_maintainability_top15"]:
        lines.append(f"  {w['file_path']}  MI={w['mi_score']} ({w['mi_rank']})  max_cc={w['max_cc']}  SLOC={w['sloc']}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("TOP 20 MOST COMPLEX FUNCTIONS (by cyclomatic complexity)")
    lines.append("-" * 70)
    for f in report["worst_functions_by_cc_top20"]:
        lines.append(
            f"  {f['file_path']}::{f['function_name']}  "
            f"CC={f['cyclomatic_complexity']} ({f['cc_band']})  NLOC={f['nloc']}"
        )
    lines.append("")
    lines.append("-" * 70)
    lines.append(f"EXACT-DUPLICATE BLOCKS (heuristic, >= {MIN_BLOCK} lines) -- top matches")
    lines.append("-" * 70)
    if not report["duplicate_blocks_heuristic"]:
        lines.append("  None found at this block size.")
    for d in report["duplicate_blocks_heuristic"][:15]:
        lines.append(f"  {d['lines']} lines, {d['occurrences']} occurrences: {', '.join(d['locations'][:4])}")
    lines.append("")
    lines.append("=" * 70)
    lines.append("THRESHOLD SOURCES (see JSON report meta.thresholds_source for full text)")
    lines.append("=" * 70)

    out_path.write_text("\n".join(lines))


def _formula_version() -> str:
    """
    Queried from live installed state every run, not hardcoded -- so a radon
    or lizard version bump that silently moves every MI/CC number in a
    report is visible without needing a tool SHA change or a tree diff
    (GOALS.json C2/F8). hotspot=v1 and health=v1 tag the two formulas this
    script itself authors (max_cc * churn, and the 0.5/0.5 MI+CC blend);
    they change only if this script's own analysis logic changes, which is
    out of scope for this copy.
    """
    radon_version = importlib.metadata.version("radon")
    lizard_version = importlib.metadata.version("lizard")
    return f"radon={radon_version};lizard={lizard_version};hotspot=v1;health=v1"


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    if not root.exists():
        print(f"Path does not exist: {root}")
        sys.exit(1)

    try:
        provenance = compute_provenance(__file__, _formula_version())
    except ProvenanceError as e:
        print(f"Cannot compute report provenance, refusing to write an unverifiable report: {e}")
        sys.exit(1)

    print(f"Analyzing {root} ...")
    report = build_report(root)
    report.update(provenance)

    json_path = Path.cwd() / "quality_baseline_report.json"
    txt_path = Path.cwd() / "quality_baseline_report.txt"

    json_path.write_text(json.dumps(report, indent=2))
    write_txt_summary(report, txt_path)

    print(f"Wrote {json_path}")
    print(f"Wrote {txt_path}")
    print()
    print(f"SLOC-weighted average health: {report['aggregate']['sloc_weighted_average_health_1_to_10']}/10")
    print(f"Top hotspot: {report['hotspots_top15'][0]['file_path'] if report['hotspots_top15'] else 'n/a'}")


if __name__ == "__main__":
    main()
