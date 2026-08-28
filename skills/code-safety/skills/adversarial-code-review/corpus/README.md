# Seeded-bug corpus

A measuring instrument, not example code. Every file under `seeded/` contains
INTENTIONAL defects catalogued by line in `manifest.json`. Do not "fix" them.

## Why

Before this existed, the skill's efficacy was anecdotal: it found real bugs on
real repos, which establishes the true-positive rate is nonzero and says nothing
about what it missed. For a merge gate the miss rate is the number that matters.

## Run

    python3 corpus/run_corpus.py

Exit 0 when every mechanical seed is caught and no control is falsely flagged;
exit 1 otherwise. Suitable for CI.

## Two tiers

- **mechanical** -- `scan.py` is expected to catch these. Graded automatically.
- **reasoning** -- no regex can reach these (wrong-but-present field reads,
  cross-file contract breaks, injection, complexity). Printed as an unscored
  checklist; they benchmark the model-driven pass, not the tool.

## Calibrate before believing the number

The first run reported 43% false-negative. Two of three "misses" were an
off-by-one artifact (the tool reports the `except` line, the manifest recorded
the body line) and only one was a real gap. The runner now allows a 3-line
tolerance. If you add seeds, sanity-check a failure before treating it as a
tool bug -- a corpus can be miscalibrated in the pessimistic direction just as
easily as a scanner can be wrong in the optimistic one.

## Adding seeds

Put the defect in a file under `seeded/`, mark it with a `SEED:` comment naming
the check and tier, add a manifest entry with the line number, and re-run. Add a
`CONTROL:` case alongside anything likely to over-trigger -- false positives are
tracked too, and a scanner that flags everything scores 100% catch rate while
being useless.
