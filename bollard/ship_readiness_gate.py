#!/usr/bin/env python3
"""FORE-17: hard gate on a real `git push` -- "ready to publish/push" is a different bar than
"criteria are frozen and met", which architecture_gate.py/goals_freeze_gate.py already enforce
mid-build. Jon's decision (b), 2026-08-20: a foreman:ship-readiness skill stage produces
.foreman/SHIP-CHARTER.json (originally five checks: no open S0/S1 tickets, test suite green with
recorded evidence, a stretch-test pass tied to and versioned alongside the suite, known
limitations disclosed, at least one documented dogfood pass; REQ-20 adds a sixth,
prd_satisfaction, below); this hook is the thin PreToolUse check that the artifact exists, shows
all required checks passing, and is current, before a push proceeds.

Opt-in, matching ticket_status_gate's precedent (FORE-3, comment 79) for the identical
reasoning: this is a materially bigger workflow commitment than architecture_gate/
goals_freeze_gate -- it can block a push over missing evidence, not just an in-progress build --
so it must be chosen per project, not inherited by every Foreman-managed project the moment this
file exists. Marker: .foreman/ship-readiness-gate-enabled.

"Current" means SHIP-CHARTER.json's own recorded commit_hash matches the real, current
`git rev-parse HEAD` of the repo being pushed -- a charter written against an earlier commit
does not vouch for commits made since. Same class of staleness check as
goals_freeze_gate.py's criteria_hash_at_freeze, adapted from "does this document's own content
still match itself" to "does this document still describe the commit being pushed" -- a ship
charter's claims (tests green, no open S0/S1) are about the CODE, which moves, not about the
document's own text, which doesn't.

Same mechanical-existence posture as architecture_gate.py checking ARCHITECTURE-REVIEW.md: this
hook verifies the artifact exists, is fresh, and says all six checks passed. It does not and
cannot re-verify that any recorded `evidence` string is actually true -- a disclosed, accepted
limitation, not an oversight. REQ-20's prd_satisfaction check is a partial exception, see below.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402
import prd_satisfaction as ps  # noqa: E402

RULE_ID = "FOREMAN-SHIP-READINESS-GATE"
ENABLED_MARKER = "ship-readiness-gate-enabled"
CHARTER_PATH_REL = Path(".foreman") / "SHIP-CHARTER.json"
# FORE-238: PRD.md's real location is not fixed to one path. component_coupling.py's
# CONTROL_FILENAMES now exempts PRD.md at any depth (FORE-205's root-cause fix, same commit),
# so a project may legitimately keep it under docs/ OR at project root -- and at least one real
# project (tessera-v2) was forced to move it to root as a workaround before that exemption
# existed. Try root first: it's the newer, gate-driven convention, and a project that never hit
# this problem simply won't have a root PRD.md, falling through harmlessly.
PRD_PATH_REL_CANDIDATES = (Path("PRD.md"), Path("docs") / "PRD.md")
REQUIRED_CHECKS = (
    "no_open_s0_s1",
    "test_suite_green",
    "stretch_test",
    "limitations_disclosed",
    "dogfood_pass",
    "prd_satisfaction",
    "cold_pass",
)
GIT_DASH_C_RE = re.compile(r'(?:^|\s)-C\s+("[^"]+"|\'[^\']+\'|\S+)')
GIT_DIR_RE = re.compile(r'--git-dir(?:=|\s+)("[^"]+"|\'[^\']+\'|\S+)')
GIT_WORK_TREE_RE = re.compile(r'--work-tree(?:=|\s+)("[^"]+"|\'[^\']+\'|\S+)')
CD_RE = re.compile(r'(?:^|[;&]|\s)cd\s+("[^"]+"|\'[^\']+\'|\S+)')


def _tokenize(seg):
    """Quote-aware tokenizer, same convention as component_coupling.py's own cp/mv tail-token
    handling -- a quoted argument containing whitespace stays one token."""
    return [t.strip('"').strip("'") for t in re.findall(r'"[^"]+"|\'[^\']+\'|\S+', seg)]


def _is_git_push_segment(seg):
    """FORE-582. Tokenized detection replacing GIT_PUSH_RE -- scored against Clint's 10-case
    table (ARCHITECTURE-JUDGMENT-FORE-580-582-20260911.md section 4), 10/10 including both
    negative traps (`git log --grep=push`, a quoted commit message mentioning push). Restricted
    to tokens[0]'s basename ONLY, not a scan of the whole segment -- an unrestricted scan would
    false-positive on `echo git push` (git as an ARGUMENT, not the command being run), caught
    before this shipped, not after.

    Skips flags and their own values (-C/-c/--git-dir/--work-tree/--namespace/--exec-path/
    --config-env/--attr-source/--super-prefix each consume a following token, confirmed live
    against real git that these accept the bare space-separated form, not just `--flag=value`;
    any other `-`-prefixed token, including the `=` form of the above, is skipped alone) until
    the first positional argument, which must be exactly 'push'. Quote-awareness is inherent in
    the tokenizer itself (a quoted multi-word argument is one token, so it can never equal
    tokens[0]'s bare 'git') -- no separate span-checking needed, simpler than the function this
    replaces, not just different."""
    tokens = _tokenize(seg)
    if not tokens or Path(tokens[0]).name != "git":
        return False
    j = 1
    while j < len(tokens):
        t = tokens[j]
        if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                 "--config-env", "--attr-source", "--super-prefix"):
            j += 2
            continue
        if t.startswith("-"):
            j += 1
            continue
        return t == "push"
    return False


# Short options `git push` accepts that take NO argument of their own, so a bundled group made
# only of these is unambiguous. Read off `git push -h` rather than assumed: -n/--dry-run,
# -v/--verbose, -q/--quiet, -f/--force, -u/--set-upstream, -d/--delete, -4, -6. Deliberately
# does NOT include -o (push option), which consumes the rest of a bundle as its argument.
NO_ARG_SHORT_PUSH_OPTS = set("nvqfud46")


def is_dry_run(push_segment):
    """Does this push segment carry the dry-run flag, in ANY of its real spellings?

    `-n` IS `--dry-run` -- confirmed from `git push -h`, which prints them as one entry
    (`-n, --[no-]dry-run`). The proposal this landed from tested only the literal string
    "--dry-run", so `git push -n` and `git push --dry-run` would have produced OPPOSITE
    verdicts for the same command: allowed one way, denied the other, decided by spelling. That
    is the FORE-583 defect shape (a verdict that turns on how a thing was written rather than
    what it does), and it is worth closing here even though it fails in the harmless direction,
    because a gate that denies safe commands is a gate people learn to route around.

    Bundled short options are real -- `git push -nv` parses fine against live git, checked
    rather than assumed. A bundle counts only when EVERY letter in it is a known no-argument
    short option, which keeps `-o` (push option, consumes the rest of the bundle as its value)
    from ever being read as a dry-run. An unrecognized bundle is not treated as a dry-run, so
    the uncertain case denies rather than allows.

    `--no-dry-run` must not count, which the exact match on "--dry-run" already handles."""
    for token in _tokenize(push_segment):
        if token == "--dry-run" or token == "-n":
            return True
        if (len(token) > 1 and token[0] == "-" and token[1] != "-"
                and "n" in token[1:]
                and set(token[1:]) <= NO_ARG_SHORT_PUSH_OPTS):
            return True
    return False


def find_real_git_push_segment(command):
    """Returns (segment, index, all_segments) for the FIRST segment that is a real push, or
    (None, None, all_segments) if none is. Same shell-delimiter split component_coupling.py
    uses elsewhere in this suite."""
    segments = re.split(r"[;&|\n]", command)
    for i, seg in enumerate(segments):
        if _is_git_push_segment(seg):
            return seg, i, segments
    return None, None, segments


def is_real_git_push(command):
    """Backward-compatible boolean wrapper around find_real_git_push_segment() -- FORE-582
    replaced the detection MECHANISM (tokenized, not regex) but not this function's name or
    return shape, so test_ship_readiness_gate.py's own IsRealGitPushTests needs zero changes."""
    segment, _, _ = find_real_git_push_segment(command)
    return segment is not None


def resolve_push_repo_hint(segments, push_index):
    """Returns the single canonical directory the push's own repo-targeting flags intend, or
    None if none is present (caller falls back to payload cwd). Collapses every flag FORM to
    one directory before resolution ever runs (Clint's redesign,
    FORE-580-CANONICAL-DIR-CONTAINMENT-DESIGN-20260911.md) -- resolve_git_repo_root() below no
    longer mirrors git's own --git-dir/--work-tree pairing semantics at all, which is how the
    prior patch's bug happened (silently resolving to the WRITER's repo instead of the target).

    -C X / leading cd X / bare --work-tree X -> X directly.
    --git-dir=X, no work-tree given -> X's parent (the conventional <worktree>/.git layout --
    a documented residual for a bare repo or non-standard git-dir name: the containment check
    in resolve_git_repo_root() fails CLOSED on that case rather than guessing further).
    --git-dir=X AND --work-tree=Y both given -> Y (the more specific claim wins)."""
    seg = segments[push_index]
    m = GIT_DASH_C_RE.search(seg)
    if m:
        return m.group(1).strip('"').strip("'")
    work_tree_match = GIT_WORK_TREE_RE.search(seg)
    if work_tree_match:
        return work_tree_match.group(1).strip('"').strip("'")
    git_dir_match = GIT_DIR_RE.search(seg)
    if git_dir_match:
        raw = git_dir_match.group(1).strip('"').strip("'")
        return str(Path(raw).parent)
    for earlier in reversed(segments[:push_index]):
        m = CD_RE.search(earlier)
        if m:
            return m.group(1).strip('"').strip("'")
    return None


def resolve_git_repo_root(canonical_dir):
    """Runs `git -C <canonical_dir> rev-parse --show-toplevel` -- ONE canonical resolution
    path, no --git-dir/--work-tree flags mirrored in at all (that mirroring is how the prior
    patch's bug happened: --git-dir alone, from an unrelated cwd already inside a different
    real repo, silently resolved toplevel to the WRONG repo, confirmed live). Returns
    (repo_root_or_None, definitely_not_a_repo).

    CONTAINMENT CHECK (Clint's fix, verified against both the correct and the buggy case):
    canonical_dir must lie INSIDE the resolved toplevel or the result is treated as
    unresolvable -- turns ANY flag form that would silently resolve to the wrong repo into a
    deny, closing the whole CLASS of this bug rather than one instance. A rejected alternative
    (comparing `rev-parse --absolute-git-dir` against the named git-dir) was tested and found
    non-discriminating: it passes in BOTH the correct and the buggy case, because git honors
    --git-dir for that specific query while still using cwd for the work tree.

    LC_ALL=C forced: git localizes fatal messages, and definitely_not_a_repo depends on
    matching literal English stderr text.

    definitely_not_a_repo=True means git confirmed no repository exists at canonical_dir at
    all -- a different signal from an unresolvable/mismatched jurisdiction. Disclosed
    heuristic, not a structured git contract -- exit 128 is generic."""
    env = dict(os.environ, LC_ALL="C")
    try:
        out = subprocess.run(["git", "-C", str(canonical_dir), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10, env=env)
    except (subprocess.SubprocessError, OSError):
        return None, False
    if out.returncode != 0:
        not_a_repo = "not a git repository" in (out.stderr or "").lower()
        return None, not_a_repo
    toplevel = out.stdout.strip()
    if not toplevel:
        return None, False
    try:
        Path(canonical_dir).resolve().relative_to(Path(toplevel).resolve())
    except (OSError, RuntimeError, ValueError):
        # RuntimeError is NOT an OSError and is not covered by catching OSError alone. Python
        # 3.9 raises it from Path.resolve() on a symlink loop where 3.14 swallows the same loop,
        # and `python3` on this machine is unpinned, so this hook runs under both (FORE-621).
        # Uncaught here it would leave main() entirely and hit hook_common's fail-open wrapper,
        # which ALLOWS the push -- a crash-to-allow on the one irreversible outward-facing
        # action, the exact fail direction FORE-582 exists to invert.
        #
        # HONEST ABOUT ITS OWN REACHABILITY: I have not been able to reach this line with a
        # looping path. A `-C <loop>` push denies, but through git's own failure above (exit
        # 128, "Too many levels of symbolic links"), not through this except -- so the fixture
        # arm that looks like it proves this catch does not prove it. Reaching it needs
        # `git -C <dir> rev-parse` to SUCCEED while Python's own resolve() then fails on the
        # same path, which the OS having already resolved it makes unlikely outside a race or a
        # chain longer than Python's internal limit. Kept as defence on a fail-open path that
        # allows a push, and labelled as defence rather than as a demonstrated fix.
        return None, False
    return toplevel, False


def _read_prd_text(project_root):
    """Best-effort read of PRD.md (root or docs/, see PRD_PATH_REL_CANDIDATES) for citation
    authentication. Returns None on any failure (missing file, unreadable) --
    prd_satisfaction.validate_section() already treats a None prd_text as "skip citation
    authentication, still check shape" per its own docstring, so a project whose PRD.md this
    hook can't read fails toward the shallower check rather than toward denying every push
    outright over a file this specific check doesn't strictly need to open.

    FORE-238: tries each candidate location in order and returns the first that reads
    successfully -- NOT the first that exists, so a stale tombstone left behind at the old
    location (readable, but empty/wrong content) doesn't shadow the real file at the new one.
    Root is checked first because it's the newer, gate-driven convention (see
    PRD_PATH_REL_CANDIDATES's own comment)."""
    for rel in PRD_PATH_REL_CANDIDATES:
        prd_path = project_root / rel
        try:
            text = prd_path.read_text()
        except OSError:
            continue
        if text.strip():
            return text
    return None


def _validate_prd_satisfaction(project_root, charter):
    """Structural + citation validation of charter['prd_satisfaction'], via prd_satisfaction.py.
    Returns (True, []) or (False, [problem, ...]). Only called once the boolean
    checks['prd_satisfaction']['pass'] has already been confirmed true by the REQUIRED_CHECKS
    loop above -- this is the deeper check REQ-20's revised Verification text asks for on top
    of that boolean, not a replacement for it."""
    result = ps.validate_section(charter, prd_text=_read_prd_text(project_root))
    if result.get("ok"):
        return True, []
    return False, result.get("problems", ["prd_satisfaction section failed validation"])


MIN_COLD_PASS_STATEMENT_LEN = 20


def _validate_cold_pass(charter):
    """FORE-232: structural validation of charter['cold_pass'], on top of the boolean
    checks['cold_pass']['pass'] already confirmed true by the REQUIRED_CHECKS loop above --
    same relationship _validate_prd_satisfaction has to checks['prd_satisfaction']['pass'].

    Same mechanical-existence posture as everywhere else in this hook: checks that the section
    is present and shaped right, not that the reviewing session was genuinely cold. That is a
    disclosed, accepted limitation (see this file's own module docstring), not unique to this
    check -- there is no way for a PreToolUse hook to verify a claim about what another
    session's context did or did not contain.

    Required shape, per the ticket's own description of how the real cold pass was run:
    reviewer_session (who), no_prior_exposure_statement (a real sentence, not a bare "true"),
    and findings (a list, possibly empty -- a cold pass that found nothing is a valid outcome
    and must not be forced to manufacture a finding to pass shape validation)."""
    section = charter.get("cold_pass")
    if not isinstance(section, dict):
        return False, ["cold_pass section is missing or not an object"]

    problems = []
    reviewer = section.get("reviewer_session")
    if not isinstance(reviewer, str) or not reviewer.strip():
        problems.append("cold_pass.reviewer_session is missing or empty")

    statement = section.get("no_prior_exposure_statement")
    if not isinstance(statement, str) or len(statement.strip()) < MIN_COLD_PASS_STATEMENT_LEN:
        problems.append(
            f"cold_pass.no_prior_exposure_statement is missing or shorter than "
            f"{MIN_COLD_PASS_STATEMENT_LEN} characters -- a real statement of no prior "
            f"exposure, not a placeholder"
        )

    findings = section.get("findings")
    if not isinstance(findings, list):
        problems.append("cold_pass.findings is missing or not a list (empty list is fine)")

    if problems:
        return False, problems
    return True, []


# CHV2-137. `[A-Z]{2,6}` carried TWO independent defects, and together they made the
# evidence-citation check blind to 9 of the 42 project prefixes live in TESSERA today:
#
#   [A-Z]  cannot match a digit, so CHV2, DEVHR2, F9FR, FOREV2 and TESSV2 were unrecognisable --
#          including this repo's own prefix. That is the identical charset defect CHV2-124 fixed
#          in ledger_write_guard.TICKET_ID_RE, sitting in a sibling file, found only because
#          CHV2-137 was read next to it. A live instance of CHV2-142's pattern.
#   {2,6}  caps the prefix at six characters, so ATLASQA, ATLASSN, BAYAREA and SELFTEST were
#          unrecognisable too. This one has no counterpart in the ledger guard; the shared
#          charset bug and this length bug had to be found separately.
#
# A ship-readiness rationale citing a real ticket in any of those nine projects read as citing
# no ticket at all, which is a false negative in a gate whose job is to require evidence.
#
# The replacement is bounded rather than open-ended, and it was checked against the current
# regex's own false-positive surface rather than only against the prefixes it must accept: on
# HTTP-404, UTF-8, ISO-8601, COVID-19, A-1, x-1 and AB-1 the two agree exactly. Those pre-existing
# false accepts (HTTP-404 and friends already match today) are NOT introduced here and are not
# fixed here -- widening the accept set is this ticket; narrowing it is a different decision.
TICKET_CITATION_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,15}-\d+\b")
FILE_LINE_CITATION_RE = re.compile(r"\b[\w./\-]+\.\w+:\d+\b")
COMMAND_CITATION_RE = re.compile(r"`[^`\n]*\b(?:git|python3?|pytest|unittest)\b[^`\n]*`",
                                  re.IGNORECASE)
COLD_PASS_PHRASE_RE = re.compile(r"\bcold pass\b|\bno authorship exposure\b", re.IGNORECASE)


def _normalize_verdict(value):
    return str(value or "").strip().upper()


def _is_ascending_verdict_change(prior_verdict, new_verdict):
    """FORE-465/Marcus Webb's FORE-230 decision: "agreement between contaminated passes moves
    a verdict DOWN or nowhere, never up." Narrowed to the well-defined ascending pairs only, per
    the ticket's own text -- not an attempt to build a total order over every verdict
    vocabulary word."""
    prior = _normalize_verdict(prior_verdict)
    new = _normalize_verdict(new_verdict)
    if not prior or not new:
        return False
    if prior == "KILL" and new != "KILL":
        return True
    if prior == "HOLD" and new == "GO":
        return True
    if "SHIP WITH FIXES" in prior and new == "SHIP":
        return True
    return False


def _find_prior_charter_verdict(project_root, charter):
    """FORE-465: SHIP-CHARTER.json's `supersedes` field is free prose in every real example on
    disk today (e.g. "the 2026-09-05T22:20Z charter at commit 67bdcca, 1 of 6 checks passing"),
    not a structured pointer to a prior charter file -- so it cannot be resolved by parsing it.

    This looks instead for sibling SHIP-CHARTER*.json files in .foreman/, the naming convention
    this project's own real durable charters already use (e.g.
    SHIP-CHARTER-FORE329-DOGFOOD-20260905.json), and takes the most recent one -- by its own
    `generated_at` -- strictly BEFORE this charter's `generated_at`, then reads ITS `verdict`
    field directly: structured, no prose parsing needed.

    Returns None if no such file exists. That is deliberately the same "cannot determine, so
    don't flag" posture as a charter with no `supersedes` field at all -- a real, disclosed
    heuristic, not a claim that `supersedes` itself is resolved."""
    this_generated_at = charter.get("generated_at")
    if not isinstance(this_generated_at, str):
        return None
    try:
        candidates = list((project_root / ".foreman").glob("SHIP-CHARTER*.json"))
    except OSError:
        return None
    best = None
    for path in candidates:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        gen_at = data.get("generated_at")
        verdict = data.get("verdict")
        if not isinstance(gen_at, str) or not isinstance(verdict, str):
            continue
        if gen_at >= this_generated_at:
            continue
        if best is None or gen_at > best[0]:
            best = (gen_at, verdict)
    return best[1] if best else None


def _rationale_cites_cold_pass_or_evidence(charter):
    """(has_cold_pass_marker, has_citable_evidence) -- both heuristic, both disclosed as such
    per the ticket's own text: this is audit-only, not a hard parse.

    Cold-pass marker is the rationale-text phrase match ONLY, deliberately NOT
    checks.cold_pass.pass -- found empirically while testing this function, not assumed: FORE-232
    made cold_pass a REQUIRED check on every charter regardless of whether a verdict escalated,
    so checks.cold_pass.pass is True on every charter that reaches this point in main() at all
    (a charter failing that check would already have been denied earlier, per REQUIRED_CHECKS).
    Using it here would make this signal trivially always-true and this whole function would
    never flag anything. The rationale text is the only place left where "cold pass" can
    actually discriminate whether THIS SPECIFIC escalation was substantiated by one."""
    rationale = charter.get("verdict_change_rationale")
    rationale_text = rationale if isinstance(rationale, str) else ""
    has_cold_pass_phrase = bool(COLD_PASS_PHRASE_RE.search(rationale_text))
    has_evidence = bool(
        TICKET_CITATION_RE.search(rationale_text)
        or FILE_LINE_CITATION_RE.search(rationale_text)
        or COMMAND_CITATION_RE.search(rationale_text)
    )
    return has_cold_pass_phrase, has_evidence


def current_commit_hash(cwd):
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                             capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def main(data):
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd")

    push_segment, push_index, segments = find_real_git_push_segment(command)
    if push_segment is None:
        return

    if is_dry_run(push_segment):
        # Checked immediately after detection, before jurisdiction: a dry-run push takes no
        # real action regardless of which repo it targets, so gating it on repo-resolvability
        # would be a false deny for no safety reason (annoying, not dangerous, but still
        # avoidable) -- Clint's own requirement is "after detection, not at detection," not
        # "after jurisdiction."
        hc.set_rule(f"{RULE_ID}:dry-run-allowed")
        return

    canonical_raw = resolve_push_repo_hint(segments, push_index)
    base = Path(cwd) if cwd else Path.cwd()
    if canonical_raw is None:
        target_dir = base
    else:
        candidate = Path(canonical_raw)
        target_dir = candidate if candidate.is_absolute() else base / candidate

    repo_root, definitely_not_a_repo = resolve_git_repo_root(target_dir)
    if repo_root is None:
        if definitely_not_a_repo:
            hc.set_rule(f"{RULE_ID}:not-a-git-repo")
            return
        hc.set_rule(f"{RULE_ID}:repo-unresolvable")
        hc.deny(
            f"Foreman: this command looks like a real `git push`, but this hook could not "
            f"confidently resolve WHICH repository it targets (from -C/--git-dir/--work-tree, "
            f"a leading cd, or cwd) -- an unresolvable OR mismatched push denies rather than "
            f"silently allowing (FORE-580/582). Known, disclosed residuals: a `GIT_DIR=` "
            f"environment prefix, a subshell `(cd path; git push)`, a push issued by an "
            f"invoked script, or a bare repo / non-standard --git-dir layout -- if this is one "
            f"of those, resolve the repo explicitly with `git -C <path> push`, or run it "
            f"outside this agent."
        )
        return

    project_root = Path(repo_root)
    if not (project_root / ".foreman").is_dir():
        # FORE-580 section 2: jurisdiction stops AT the repo root, deliberately not
        # cc.find_project_root() -- that walks upward with no bound and would find an
        # enclosing project OUTSIDE the pushed repo, which this push does not publish and
        # should not be gated by. Measured, not assumed: 0 real Foreman projects on this
        # machine sit below their own repo root (section 2's 85-directory census).
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    if not (project_root / ".foreman" / ENABLED_MARKER).is_file():
        hc.set_rule(f"{RULE_ID}:not-opted-in")
        return

    charter_path = project_root / CHARTER_PATH_REL
    if not charter_path.is_file():
        hc.set_rule(f"{RULE_ID}:no-charter")
        hc.deny(
            f"Foreman: this project has ship-readiness gating enabled but no {charter_path}. "
            f"Run foreman:ship-readiness before pushing."
        )
        return

    try:
        charter = json.loads(charter_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # Fails toward DENY, not toward silently letting an unreadable charter through --
        # same fail-safe direction as goals_freeze_gate.py's own GOALS.json read failure.
        hc.set_rule(f"{RULE_ID}:charter-unreadable")
        hc.deny(
            f"Foreman: {charter_path} exists but could not be read as JSON ({exc}). "
            f"Regenerate it with foreman:ship-readiness before pushing."
        )
        return

    checks = charter.get("checks") or {}
    failed = [name for name in REQUIRED_CHECKS if (checks.get(name) or {}).get("pass") is not True]
    if failed:
        hc.set_rule(f"{RULE_ID}:checks-not-passing")
        hc.deny(
            f"Foreman: {charter_path} does not show all {len(REQUIRED_CHECKS)} ship-readiness "
            f"checks passing (failing or missing: {', '.join(failed)}). Run "
            f"foreman:ship-readiness again before pushing."
        )
        return

    prd_ok, prd_problems = _validate_prd_satisfaction(project_root, charter)
    if not prd_ok:
        hc.set_rule(f"{RULE_ID}:prd-satisfaction-invalid")
        hc.deny(
            f"Foreman: {charter_path}'s checks.prd_satisfaction.pass is true, but the "
            f"prd_satisfaction section itself does not structurally validate (REQ-20): "
            f"{'; '.join(prd_problems)}. Run foreman:ship-readiness again before pushing."
        )
        return

    cold_ok, cold_problems = _validate_cold_pass(charter)
    if not cold_ok:
        hc.set_rule(f"{RULE_ID}:cold-pass-invalid")
        hc.deny(
            f"Foreman: {charter_path}'s checks.cold_pass.pass is true, but the cold_pass "
            f"section itself does not structurally validate (FORE-232): "
            f"{'; '.join(cold_problems)}. Run foreman:ship-readiness again before pushing."
        )
        return

    charter_commit = charter.get("commit_hash")
    real_commit = current_commit_hash(str(repo_root))
    # FORE-580/582: checked against the REPO BEING PUSHED, not the session's cwd -- the live
    # file checked cwd here, harmless only because cwd==project_root was always true before
    # this fix (single-root jurisdiction). Now that jurisdiction is repo-derived, checking cwd
    # would silently verify staleness against the WRONG repo in exactly the cross-repo case
    # this ticket fixes. real_commit is None (git itself unavailable/errored) still fails OPEN
    # on this specific check -- same direction as before, unrelated to the jurisdiction fix.
    if real_commit is not None and charter_commit != real_commit:
        hc.set_rule(f"{RULE_ID}:stale-charter")
        hc.deny(
            f"Foreman: {charter_path} was generated against commit {charter_commit!r}, but "
            f"HEAD is now {real_commit!r} -- the charter's claims (tests green, no open "
            f"S0/S1, etc.) predate whatever changed since. Re-run foreman:ship-readiness "
            f"before pushing."
        )
        return

    # FORE-465, audit-only, non-blocking (Marcus Webb's FORE-230 decision, code half). First
    # light per Marcus's own citation of the FORE-232/TESS-182 precedent for introducing a new
    # control this way -- does not deny, only surfaces the gap in this hook's own output
    # instead of leaving it silent. Deliberately runs after every blocking check above has
    # already passed: this is about the QUALITY of the justification behind an otherwise-valid
    # charter, not a substitute for any of them.
    prior_verdict = _find_prior_charter_verdict(project_root, charter)
    if prior_verdict is not None and _is_ascending_verdict_change(prior_verdict, charter.get("verdict")):
        has_cold_pass, has_evidence = _rationale_cites_cold_pass_or_evidence(charter)
        if not (has_cold_pass or has_evidence):
            hc.audit(
                "SHIP_READINESS_UNSUBSTANTIATED_ESCALATION",
                {"prior_verdict": prior_verdict, "new_verdict": charter.get("verdict"),
                 "charter": str(charter_path)},
                data.get("session_id"), cwd, severity="medium",
            )

    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
