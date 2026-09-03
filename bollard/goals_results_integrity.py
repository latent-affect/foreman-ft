"""DEVH-84: results[] append-only in GOALS.json is convention, not enforcement -- this makes
it a mechanism. goals_freeze_gate.py already hashes and denies on criteria[] mutation; results[]
(the per-criterion verification history) has no equivalent. Two independent checks, on purpose:

  check_append_only(old_results, new_results) -- the append-only invariant itself. A prior row
  being superseded (a new row for the same criterion_id, per this file's own convention: "append
  a new row, don't overwrite this one") is fine; a prior row being edited, reordered, or deleted
  is not. Enforced as a strict tail-extension: new_results[:len(old_results)] must equal
  old_results exactly, element for element.

  check_evidence_quality(results) -- every row, not just newly appended ones, must carry the
  minimum shape a real verification record needs: a criterion_id, a status in this file's own
  vocabulary (MET/NOT_MET/UNVERIFIED), a non-empty evidence string past a placeholder length,
  a recorded timestamp, and a verified_by attribution. This is the mechanical stand-in for what
  reviewer attention was catching by hand: a MET claim with no real evidence behind it.

Deliberately NOT cross-checking the two: whether a row's evidence actually PROVES its stated
status is not something regex/length checks can verify, and reaching for the by-session record
or another file to corroborate would repeat the exact over-coupling mistake bob_write_gate.py's
own Case 8 comment already documents a regression for (DEVH-85/cf's fixture) -- keep each
artifact's own internal check self-contained.
"""

def current_by_criterion(results):
    """DEVH-78: results[] rows are append-only (check_append_only above), so a criterion_id
    with more than one row -- supersession, per this file's own stated convention -- has no
    field marking which row is current. That was legible only from array order plus prose
    ("Second C16 row...", "Supersedes the NOT_MET row above it"). Editing old rows to add a
    marker would violate append-only itself, so this derives the answer instead of storing it:
    last row wins, same rule the prose already states everywhere it supersedes anything.

    Returns {criterion_id: index}, mapping each criterion_id present in results[] to the index
    of its current (most recently appended) row. Non-dict rows and rows with no criterion_id
    are skipped rather than raising, matching check_evidence_quality's tolerance of malformed
    rows -- this function answers "what's current", not "is this row well-formed"."""
    current = {}
    for i, row in enumerate(results):
        if isinstance(row, dict):
            cid = row.get("criterion_id")
            if cid is not None:
                current[cid] = i
    return current


MIN_EVIDENCE_LEN = 40
VALID_STATUSES = {"MET", "NOT_MET", "UNVERIFIED"}
# verified_by is deliberately NOT required: checked against this file's own real committed
# history (test_current_committed_results_pass_evidence_quality) and the earliest 4 rows in
# bollard/GOALS.json predate that convention -- it wasn't load-bearing from row 0, so requiring
# it here would retroactively fail real, legitimate historical data rather than catch a defect.
REQUIRED_STRING_FIELDS = ("criterion_id", "status", "evidence", "recorded")


def check_append_only(old_results, new_results):
    """Returns a list of violation strings; empty means the invariant holds. old_results is
    the previously-committed results[] array, new_results is the proposed/current one."""
    violations = []

    if len(new_results) < len(old_results):
        violations.append(
            f"new results[] has {len(new_results)} rows, fewer than the prior {len(old_results)} "
            f"-- rows were removed."
        )
        return violations

    prefix = new_results[:len(old_results)]
    for i, (old_row, new_row) in enumerate(zip(old_results, prefix)):
        if old_row != new_row:
            old_id = old_row.get("criterion_id") if isinstance(old_row, dict) else None
            violations.append(
                f"results[{i}] (criterion_id={old_id!r}) changed since it was committed -- "
                f"append-only requires superseding with a new row, not editing an existing one."
            )

    return violations


def check_evidence_quality(results):
    """Returns a list of (index, violation string) for rows that don't meet the minimum bar
    for a real verification record. Checks every row, not just newly appended ones -- a thin
    row from history is still a thin row."""
    violations = []

    for i, row in enumerate(results):
        if not isinstance(row, dict):
            violations.append((i, "row is not a JSON object"))
            continue

        for field in REQUIRED_STRING_FIELDS:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                violations.append((i, f"missing or empty required field {field!r}"))

        status = row.get("status")
        if isinstance(status, str) and status not in VALID_STATUSES:
            violations.append(
                (i, f"status {status!r} is not one of {sorted(VALID_STATUSES)}")
            )

        evidence = row.get("evidence")
        if isinstance(evidence, str) and 0 < len(evidence.strip()) < MIN_EVIDENCE_LEN:
            violations.append(
                (i, f"evidence is only {len(evidence.strip())} chars (< {MIN_EVIDENCE_LEN}) -- "
                    f"too short to be a real verification record, reads as a placeholder")
            )

    return violations
