#!/usr/bin/env python3
"""Shared evidence-binding primitives for Foreman's hard gates -- replacing the
first floor-by-floor patch attempt after Clint Eastwood's architecture pass found two real
problems in it before any code shipped: F-2/GOALS.json binding turned out to already be live
(goals_freeze_gate.py), and both toy hashers would have false-denied all 76 real GOALS.json on
this machine by hashing the whole document instead of just criteria[]. Lesson carried forward
here, not just noted: every primitive below is verified against REAL files on this machine
before being trusted, the same discipline that caught that bug in the first place.

First real consumer: A3 (architecture review binds to ARCHITECTURE.md's current content) --
see review_binds_architecture() and architecture_gate.py's :review-unbound / :review-mismatch
rules. F5 (freeze binds to the architecture hash, with a git-history amendment anchor) is a
deliberate follow-up, not built here -- it needs real git-diff parsing this module doesn't
have yet, and conflating it with A3 in one pass is exactly the "different clock, different
question" mistake Clint flagged about ship_readiness_gate.py.

No field this module touches is ever silently excluded from a hash by default. If a future
caller needs to exclude something, that has to be an explicit, named argument at the call
site -- Clint's finding on the toy library's DEFAULT_EXCLUDE_KEYS (which excluded an approver-
identity field, frozen_by, by default) is the reason this module carries no default-exclude
list at all.
"""

import hashlib
import re
from pathlib import Path


def file_sha256(path):
    """Real sha256 hex digest of a file's raw bytes. Returns None on any read failure (missing
    file, permission error, directory) -- never raises. Callers decide what a None means for
    their own DENY/ASK/silent posture; this function only ever reports what it could verify."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def extract_labeled_hash(text, label):
    """Find a '**<label>:** sha256:<64hex>' line -- the same bold-label header convention
    ARCHITECTURE-REVIEW.md already uses for **Reviewer:**, **Date:**, **Scope:** (confirmed
    against a real review file before this convention was chosen, not invented from scratch).
    Returns the bare 64-hex-character digest, or None if the label is absent or malformed."""
    pattern = r"\*\*" + re.escape(label) + r":\*\*\s*sha256:([0-9a-f]{64})"
    m = re.search(pattern, text)
    return m.group(1) if m else None


def review_binds_architecture(review_path, architecture_path, label="Reviews-Architecture-SHA256"):
    """A3: does review_path's header contain a hash that matches architecture_path's CURRENT
    content right now? Returns (verdict, reason) where verdict is one of:
      "bound"     -- the label is present and matches; the review is current.
      "unbound"   -- the review exists but has no labeled hash line yet (the pre-A3 state
                     every real project is in today -- NOT the same as a mismatch, see
                     architecture_gate.py's separate rule_ids for the two).
      "mismatch"  -- the label is present but does not match; the architecture changed since
                     this review was written and it needs a fresh pass.
      "error"     -- a file is missing or unreadable; reason names which one and why.
    Never raises. The three-way split (unbound / mismatch / error) exists specifically so a
    caller can treat "hasn't adopted this yet" differently from "adopted it and it's stale" --
    collapsing those into one boolean is exactly the ambiguity risk flagged during the F-2
    toy-model review.
    """
    if not Path(architecture_path).is_file():
        return "error", f"{architecture_path} does not exist"
    if not Path(review_path).is_file():
        return "error", f"{review_path} does not exist"
    try:
        review_text = Path(review_path).read_text(encoding="utf-8")
    except OSError as exc:
        return "error", f"{review_path} could not be read: {exc}"

    stored = extract_labeled_hash(review_text, label)
    current = file_sha256(architecture_path)
    if current is None:
        return "error", f"{architecture_path} could not be hashed"

    if stored is None:
        return "unbound", f"no **{label}:** sha256:<hex> line found in {review_path}"
    if stored != current:
        return "mismatch", (
            f"review says {stored[:12]}..., architecture is now {current[:12]}... "
            f"-- re-review before trusting this file"
        )
    return "bound", "review hash matches architecture's current content"


def stamp_review_hash(review_path, architecture_path, label="Reviews-Architecture-SHA256"):
    """Migration/authoring helper, NOT called by any gate: compute the current architecture
    hash and return the exact header line a real review file should add, in the file's own
    existing convention. Does not write anything -- the caller (a human, or foreman:architecture
    itself once this is wired into that skill) decides where in the header block it goes."""
    digest = file_sha256(architecture_path)
    if digest is None:
        return None
    return f"**{label}:** sha256:{digest}"
