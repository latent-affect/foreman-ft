#!/usr/bin/env python3
"""Shared evidence-binding primitives for Foreman's hard gates -- FORE-65, replacing the
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
import json
import math
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


INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def blank_code_regions(text):
    """`text` with fenced blocks and inline code spans replaced by blank lines/spaces.

    FORE-593. extract_labeled_hash's last-match rule is right and stays, but it had no awareness
    of document structure, so ANY text matching the pattern was eligible -- including an example
    inside a fence. Demonstrated against the real functions: a review whose two genuine passes
    both claim the OLD architecture hash, plus an appendix illustrating the mechanism with the
    CURRENT hash inside triple backticks, made review_binds_architecture return "bound". No pass
    had reviewed the current architecture and the predicate said one had.

    The realistic path is not an attack. It is a thorough reviewer documenting the binding
    mechanism in their own write-up -- which is exactly what a careful review OF a binding system
    contains -- and then stamping their honest review as the normal final step. That path runs
    straight THROUGH the ledger backstop rather than around it: the backstop catches an
    edit-without-stamp, and stamping is the legitimate reviewer's last action.

    INLINE SPANS ARE BLANKED TOO, not just fences. Measured: the same false "bound" reproduces
    with the illustration written as `**Label:** sha256:...` in running prose, and a reviewer
    quoting the line mid-sentence is at least as likely as one setting it in a fence.

    Line-based rather than a single regex, so an UNTERMINATED fence (a file that opens one and
    ends) is treated as code to EOF rather than silently re-admitting everything after it.

    DISCLOSED RESIDUAL: a labeled hash written as plain prose, in no code markup at all, is still
    indistinguishable from a claim and still wins if it comes last. Nothing in the text says which
    it is. Closing that needs a real header-position rule -- only lines in a pass's own header
    block count -- which is a format decision for whoever owns the review convention, not
    something to infer here. A 4-space-indented code block is also not treated as code, because
    telling one from an ordinary list continuation line needs a real Markdown parser.
    """
    out = []
    fence = None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if fence is None and (stripped.startswith("```") or stripped.startswith("~~~")):
            fence = stripped[:3]
            out.append("\n")
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            out.append("\n")
            continue
        out.append(INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line))
    return "".join(out)


def extract_labeled_hash(text, label):
    """Find a '**<label>:** sha256:<64hex>' line -- the same bold-label header convention
    ARCHITECTURE-REVIEW.md already uses for **Reviewer:**, **Date:**, **Scope:** (confirmed
    against a real review file before this convention was chosen, not invented from scratch).
    Returns the bare 64-hex-character digest, or None if the label is absent or malformed.

    Uses the LAST match, not the first. Real bug found live (agent-remediation-25, 2026-08-27,
    verifying a different fix before recommending .foreman/review-binding-enabled be created):
    ARCHITECTURE-REVIEW.md is one growing file with each falsification pass appended in order,
    each carrying its own labeled hash line -- re.search's first-match behavior would bind
    against whichever pass's hash line comes FIRST in the file, not the most recent one, even
    once every pass uses the correct format. Confirmed by direct repro before this fix: a
    synthetic two-pass review with a stale hash first and the current hash second returned the
    stale hash. Enabling the binding gate before this fix would have risked a false "mismatch"
    against a stale-but-first hash, or a coincidental false "bound" -- either way, checking the
    wrong pass's claim."""
    pattern = r"\*\*" + re.escape(label) + r":\*\*\s*sha256:([0-9a-f]{64})"
    matches = list(re.finditer(pattern, blank_code_regions(text)))
    return matches[-1].group(1) if matches else None


NUMBER_RE = r"-?\d+(?:\.\d+)?"


def extract_labeled_number(text, label):
    """Find a '**<label>:** <int-or-float>' line -- generalizes extract_labeled_hash's own
    bold-label convention to a bare numeric value instead of a sha256:<64hex> string. FORE-672
    (REQ-68): extract_labeled_hash's regex (above) was hard-coded to `sha256:[0-9a-f]{64}` and
    structurally cannot match a bare number -- confirmed directly, not assumed: this module's
    own test suite asserts extract_labeled_hash(text, label) returns None on input this
    function parses correctly, proving the new parser is genuinely separate code rather than a
    silent reuse that happens to work on this input by accident. Same code-region-blanking
    protection as extract_labeled_hash (FORE-593): reuses blank_code_regions() so a numeric
    label illustrated inside a fence or inline span doesn't count as a live claim either.

    Returns a float, or None if the label is absent or its value isn't a bare int/float.

    Uses the LAST match, not the first -- same convention extract_labeled_hash already
    established, for the same reason: one growing document can carry more than one same-label
    block, and the most recent one describes the current claim. second_derivation_agrees()
    below inherits this behavior for both the primary and the Second-Derivation label; FORE-672's
    C3 requires that inherited choice be stated explicitly rather than left implicit, which this
    docstring and the paired test (test_foreman_evidence_second_derivation.py's
    MultipleBlocksTests) both do.

    FORE-682: the match is anchored with a trailing `(?=\\s|$)` lookahead, so a value like
    "42abc" is REJECTED (returns None) rather than silently truncated to 42.0. Found live by
    Iris Chen (BUILD5-I1 integration probing against the real cross-repo call path): without
    the anchor, NUMBER_RE's unanchored match let re.finditer simply stop at the last digit and
    drop everything after it, so a genuinely corrupted value that happens to have a valid
    number as a PREFIX (truncated output, a stray trailing character from a templating bug,
    string concatenation gone wrong) silently passed as that prefix's value -- defeating this
    module's own documented contract ("None if ... its value isn't a bare int/float") and
    second_derivation_agrees()'s whole purpose of catching exactly this kind of corruption. The
    lookahead requires whitespace (which already includes newline, so no re.MULTILINE flag is
    needed) or end-of-string immediately after the numeric match; it is zero-width, so it does
    not consume that character and does not change what a well-formed value parses to."""
    pattern = r"\*\*" + re.escape(label) + r":\*\*\s*(" + NUMBER_RE + r")(?=\s|$)"
    matches = list(re.finditer(pattern, blank_code_regions(text)))
    return float(matches[-1].group(1)) if matches else None


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


def review_binds_architecture_via_ledger(review_path, architecture_path, ledger_path,
                                          artifact_relpath=None,
                                          label="Reviews-Architecture-SHA256"):
    """REQ-12: the ledger-consult replacement for review_binds_architecture()'s self-reported
    hash. Two claims, both required, because they answer different questions:

    (1) REVIEW INTEGRITY -- has review_path's own content been tampered with (or restamped by
        hand, in-band) since it was independently hashed? Checked against
        `.foreman/review-events.jsonl` -- append-only, populated only by stamp_review_event.py,
        hard-denied against direct Write/Edit by review_events_ledger_guard.py -- rather than
        trusting a hash line embedded in review_path's OWN text, which the same UID that wrote
        the review could also have typed, matching a swapped-in restamp.

    (2) ARCHITECTURE BINDING -- does review_path's own labeled claim (the same
        `**Reviews-Architecture-SHA256:** sha256:<hex>` line extract_labeled_hash() reads for
        review_binds_architecture()'s self-reported check) still match architecture_path's
        CURRENT content?

    CHV2-28: an earlier version of this function only checked (1), via `current =
    file_sha256(review_path)` compared to the ledger's stored hash -- so once a review was
    ledger-verified, ARCHITECTURE.md could be edited afterward with no fresh review and this
    function kept reporting "bound" forever, having never looked at architecture_path at all.
    A real regression against review_binds_architecture(), the function this one is documented
    to replace, which DID catch that case. Fixed by requiring both checks: (1) proves the
    review artifact's bytes are what they were at stamp time (REQ-12's actual contribution --
    the self-report can't be a UID-swapped forgery); (2) proves those bytes still make a
    binding claim about architecture_path's CURRENT content (review_binds_architecture()'s
    original job, now resting on a ledger-verified review file instead of a bare self-report).

    Returns (verdict, reason):
      "bound"    -- (1) and (2) both hold: a ledger entry proves review_path's current content
                    is the same content that was independently hashed at stamp time, AND that
                    content's own labeled claim matches architecture_path right now.
      "unbound"  -- no ledger entry exists yet for this artifact (today's real, pre-adoption
                    state for every project on this machine, not an error), OR (1) holds but
                    review_path's current text carries no {label} line at all -- ledger-verified
                    but never made a binding claim to check.
      "mismatch" -- either (1) fails (review_path's content no longer matches what was stamped
                    -- untrustworthy regardless of what it currently claims) or (1) holds but
                    (2) fails (the labeled claim no longer matches architecture_path's current
                    content) -- the architecture changed since this review was written.
      "error"    -- review_path or architecture_path is missing/unreadable, or the ledger file
                    exists but is not valid JSONL throughout.

    Uses the LAST matching ledger entry, not the first -- same reasoning as
    extract_labeled_hash's own fix: a real ledger accumulates one entry per stamp over the
    artifact's life, and only the most recent one describes the artifact's current expected
    state."""
    review_path = Path(review_path)
    if not review_path.is_file():
        return "error", f"{review_path} does not exist"

    if not Path(architecture_path).is_file():
        return "error", f"{architecture_path} does not exist"

    review_current = file_sha256(review_path)
    if review_current is None:
        return "error", f"{review_path} could not be hashed"

    arch_current = file_sha256(architecture_path)
    if arch_current is None:
        return "error", f"{architecture_path} could not be hashed"

    if artifact_relpath is None:
        artifact_relpath = review_path.name

    ledger_path = Path(ledger_path)
    if not ledger_path.is_file():
        return "unbound", f"no ledger at {ledger_path} -- this artifact has never been stamped"

    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return "error", f"{ledger_path} could not be read: {exc}"

    matching = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            return "error", f"{ledger_path} line {i + 1} is not valid JSON: {exc}"
        if isinstance(event, dict) and event.get("artifact") == str(artifact_relpath):
            matching.append(event)

    if not matching:
        return "unbound", (
            f"no entry for artifact={artifact_relpath!r} in {ledger_path} -- this artifact has "
            f"never been stamped"
        )

    latest = matching[-1]
    stored = latest.get("sha256")
    if stored != review_current:
        return "mismatch", (
            f"ledger says {str(stored)[:12]}..., {review_path.name} is now "
            f"{review_current[:12]}... -- re-stamp after re-reviewing before trusting this file"
        )

    # (1) passed: review_path's current content is provably what was independently hashed at
    # stamp time. Now (2): does THAT content's own labeled claim still match architecture_path?
    review_text = review_path.read_text(encoding="utf-8")
    claimed_arch_hash = extract_labeled_hash(review_text, label)
    if claimed_arch_hash is None:
        return "unbound", (
            f"{review_path.name} is ledger-verified but carries no **{label}:** sha256:<hex> "
            f"line -- nothing to compare against {architecture_path}"
        )
    if claimed_arch_hash != arch_current:
        return "mismatch", (
            f"{review_path.name} claims architecture {claimed_arch_hash[:12]}..., "
            f"{Path(architecture_path).name} is now {arch_current[:12]}... -- re-review before "
            f"trusting this file"
        )
    return "bound", (
        f"ledger entry matches {review_path.name}'s current content, and its claimed "
        f"architecture hash matches {Path(architecture_path).name}'s current content"
    )


def second_derivation_agrees(text, label, rel_tol=0.01, abs_tol=1e-9):
    """FORE-672 (REQ-68): does a '**<label>:** <number>' value agree with its sibling
    '**<label>-Second-Derivation:** <number>' line, within tolerance? Both values are read via
    extract_labeled_number() above -- last-match, same convention as extract_labeled_hash.

    MULTIPLE SAME-LABEL BLOCKS (C3): handled by inheriting extract_labeled_number's own
    last-match rule for both the primary and the Second-Derivation label, stated explicitly
    here rather than left implicit. This is NOT DEVH-41's defect shape repeated in new code --
    DEVH-41 names extract_labeled_hash's MISSING uniqueness check as the real, live defect (no
    rule existed at all, not "last wins vs. first wins"); this function's parser already has an
    explicit, tested rule (see test_foreman_evidence_second_derivation.py's
    MultipleBlocksTests), so there is nothing undefined to inherit. DEVH-41 itself stays its own
    ticket, unchanged, per FORE-672's own explicit disposition -- not fixed here.

    TOLERANCE SHAPE, explicit per FORE-672's own C2 instruction to state and justify the choice
    rather than leave it implicit: PERCENT (relative), not a fixed absolute delta, is this
    comparator's default. `rel_tol` mirrors Python's own math.isclose() parameter name and
    semantics on purpose. Justification: the named discharging instance (FORE-672's own
    retrofit into build-gauntlet-dashboard.py, a raw recount vs. an aggregation-query count)
    spans values of different magnitude across different dashboards and time windows -- a fixed
    absolute delta would be simultaneously too loose for a count in the thousands and too strict
    for one in the single digits, while a percent tolerance scales with magnitude by
    construction. `abs_tol` exists only for the near-zero case (e.g. 0 vs. 0, or 0 vs. 1, where
    a relative tolerance is undefined or degenerate) -- the same reason math.isclose() itself
    carries both parameters rather than only rel_tol, not a new idea invented here. A caller
    that genuinely wants a fixed absolute delta instead can pass rel_tol=0.0 with a real
    abs_tol; that shape is supported, just not the default.

    Returns (verdict, reason):
      "agrees"    -- both values parsed and are within tolerance of each other.
      "disagrees" -- both values parsed but differ beyond tolerance.
      "error"     -- either label's value is missing or unparseable; reason names which.
    Never raises. Boundary is inclusive, matching math.isclose()'s own <=-shaped comparison: a
    pair exactly at the stated tolerance agrees; this REQ's own ablation fixture
    (foreman_evidence_second_derivation_ablation.py) proves one increment past it disagrees,
    before the agreeing case is trusted, per the operator's standing test-your-own-test-code
    rule and PDP.md section 2 R2."""
    primary = extract_labeled_number(text, label)
    second_label = f"{label}-Second-Derivation"
    second = extract_labeled_number(text, second_label)
    if primary is None:
        return "error", f"no **{label}:** <number> line found"
    if second is None:
        return "error", f"no **{second_label}:** <number> line found"
    if math.isclose(primary, second, rel_tol=rel_tol, abs_tol=abs_tol):
        return "agrees", (
            f"{primary} and {second} agree within rel_tol={rel_tol}, abs_tol={abs_tol}"
        )
    return "disagrees", (
        f"{primary} and {second} disagree beyond rel_tol={rel_tol}, abs_tol={abs_tol} "
        f"-- re-derive before trusting this count"
    )


def stamp_review_hash(review_path, architecture_path, label="Reviews-Architecture-SHA256"):
    """Migration/authoring helper, NOT called by any gate: compute the current architecture
    hash and return the exact header line a real review file should add, in the file's own
    existing convention. Does not write anything -- the caller (a human, or foreman:architecture
    itself once this is wired into that skill) decides where in the header block it goes."""
    digest = file_sha256(architecture_path)
    if digest is None:
        return None
    return f"**{label}:** sha256:{digest}"
