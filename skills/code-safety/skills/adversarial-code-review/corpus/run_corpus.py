#!/usr/bin/env python3
"""
Measure scan.py's false-negative rate against the seeded corpus.

    python3 corpus/run_corpus.py

Grades ONLY the mechanical tier, because that's the only tier scan.py claims to
cover. The reasoning tier is printed as an unscored checklist: those defects are
the benchmark for the model-driven review pass, and scoring a regex tool against
a cross-file contract bug would produce a meaningless failure.

Why this exists: before this corpus, the skill's efficacy was anecdotal. It found
real bugs on real repos, which tells you the true-positive rate is nonzero and
tells you nothing at all about what it missed. For a merge gate, the miss rate is
the number that matters.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCAN = ROOT.parent / "scripts" / "scan.py"
LINE_TOLERANCE = 3


def run_scan(mode, target):
    proc = subprocess.run(
        [sys.executable, str(SCAN), mode, str(target)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"  !! scan.py {mode} exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def main_stream_only(output):
    """Drop the separately-reported streams from scan output.

    proxy_markers reports three streams: the main candidate list, marker hits
    inside test paths, and implementation-marker hits that appear only inside a
    comment. The second and third are printed rather than filtered, deliberately,
    so nothing is invisibly suppressed -- but a control asserting "this must not
    be flagged" means "must not be in the MAIN list", and grading against the raw
    text would count a correctly-diverted hit as a false positive.

    The diverted blocks are indented two spaces under a `  -- N more ...` header
    and end at the next blank line, so this strips exactly those.
    """
    kept, in_diverted = [], False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("-- ") and " more " in stripped:
            in_diverted = True
            continue
        if in_diverted:
            if not stripped or stripped.startswith("==="):
                in_diverted = False
            else:
                continue
        kept.append(line)
    return "\n".join(kept)


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    seeded = ROOT / "seeded"

    output = ""
    for mode in ("silent-failures", "undefined-calls", "proxy-markers"):
        output += run_scan(mode, seeded)

    # Both seeds and controls are graded against the MAIN stream. A seed that only
    # shows up in a diverted stream is a miss (the reviewer's eye goes to the main
    # list), and a control that only shows up there is not a false positive.
    main_only = main_stream_only(output)

    mech = [s for s in manifest["seeds"] if s["tier"] == "mechanical"]
    reasoning = [s for s in manifest["seeds"] if s["tier"] == "reasoning"]

    caught, missed = [], []
    for seed in mech:
        # Tolerance of +/- LINE_TOLERANCE lines. A tool legitimately reports the
        # line of the `except` / `if err != nil` clause while a manifest naturally
        # records the line of the defective body statement. The first run of this
        # corpus scored 43% false-negative; two of the three "misses" were this
        # off-by-one artifact and only one was a real gap. Reporting the naive
        # number would have been wrong in the pessimistic direction -- exactly the
        # kind of confident-but-miscalibrated output this whole skill targets.
        basename = seed["file"].split("/")[-1]
        hit = any(f"{basename}:{seed['line'] + d}:" in main_only
                  for d in range(-LINE_TOLERANCE, LINE_TOLERANCE + 1))
        (caught if hit else missed).append(seed)

    print("=" * 70)
    print("MECHANICAL TIER (scan.py is expected to catch these)")
    print("=" * 70)
    for s in caught:
        print(f"  CAUGHT  {s['id']}  check {s['check']}  {s['file']}:{s['line']}")
    for s in missed:
        print(f"  MISSED  {s['id']}  check {s['check']}  {s['file']}:{s['line']}")
        print(f"          {s['defect']}")

    rate = len(caught) / len(mech) * 100 if mech else 0.0
    print(f"\n  Catch rate: {len(caught)}/{len(mech)} ({rate:.0f}%)")
    print(f"  FALSE-NEGATIVE RATE: {len(missed)}/{len(mech)} ({100 - rate:.0f}%)")

    print("\n" + "=" * 70)
    print("FALSE POSITIVES (controls that should NOT be flagged)")
    print("=" * 70)
    fp = 0
    for c in manifest["controls"]:
        if c["id"] == "C03":
            continue  # reviewer-judgment control; scan.py hits this by design
        basename = c['file'].split('/')[-1]
        if any(f"{basename}:{c['line'] + d}:" in main_only
               for d in range(-LINE_TOLERANCE, LINE_TOLERANCE + 1)):
            fp += 1
            print(f"  FALSE POSITIVE  {c['id']}  {c['file']}:{c['line']}")
            print(f"                  {c['note']}")
    if fp == 0:
        print("  None. All tool-graded controls correctly left alone.")

    print("\n" + "=" * 70)
    print("REASONING TIER (unscored -- benchmark for the model-driven pass)")
    print("=" * 70)
    print("  scan.py cannot reach these. Run the full skill and check by hand:")
    for s in reasoning:
        print(f"  [ ] {s['id']}  check {s['check']}  {s['file']}:{s['line']}")
        print(f"      {s['defect']}")

    print("\n  Reviewer-judgment control (not tool-graded):")
    c03 = next(c for c in manifest["controls"] if c["id"] == "C03")
    print(f"  [ ] C03  {c03['file']}:{c03['line']}")
    print(f"      {c03['note']}")

    return 1 if missed or fp else 0


if __name__ == "__main__":
    sys.exit(main())
