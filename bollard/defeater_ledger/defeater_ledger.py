#!/usr/bin/env python3
"""defeater_ledger.py -- FORE-537. Append-only record of known ways to defeat or evade this
mechanism (PRD.md REQ-14, ARCHITECTURE.md's defeater_ledger row).

Store: `.foreman/defeaters.jsonl`, one JSON object per line, schema:
    {defeater_id, signal, description, first_observed, ticket_or_incident_ref,
     status: "open"|"mitigated", appended_at}

TWO HALVES, same file, same shape as dormancy_register.py's split: a library write path
(append_defeat/read_all/current_status -- ordinary Python calls, never a Claude Code tool
call) and a PreToolUse gate (main(), reached only via hc.run() when this file is invoked as a
hook with a stdin payload). The gate denies every Edit/Write/NotebookEdit/MultiEdit/Bash
mutation that names defeaters.jsonl, unconditionally -- there is no carve-out for "this Bash
command looks like it's calling append_defeat()" because a PreToolUse hook cannot see inside a
Bash command's own process to tell a real append_defeat() call from a forged one that merely
mimics its shape. The legitimate writer is exactly what ledger_write_guard.py's own module
docstring already establishes for its sibling ledger: ordinary Python code that imports this
module directly and calls append_defeat(), never something Claude Code's own tool-call
machinery mediates -- so denying every tool-mediated write here blocks exactly the paths this
ledger needs blocked (an agent editing defeaters.jsonl by hand) without blocking the one path
it needs open (a maintenance script -- seed_defeaters.py -- or a test harness importing this
module and calling the function).

C1's append-only guarantee therefore has two enforcement layers, same as ledger_write_guard.py's
own C1-C3: (1) append_defeat() itself never opens the store for anything but O_APPEND writes and
never edits an existing line -- a status change is a NEW row referencing the original
defeater_id (C3), not an in-place edit; (2) the PreToolUse gate below hard-denies every
tool-mediated Edit/Write/Bash targeting the store, so even a compromised or careless agent
session cannot bypass (1) by editing the file directly.

C4 (this file's own falsification-amendment addition): the gate resolves its target path
through component_coupling -- find_project_root() to establish whether this project has
opted into Foreman at all (fail toward NOT gating if it hasn't, the established convention
every sibling gate in this suite already follows), and a real project-root-relative path
comparison rather than ledger_write_guard's simpler bare-basename check, so a defeaters.jsonl
that happens to live somewhere else entirely (a fixture directory, an unrelated project) isn't
swept in by name alone. component_root() resolves this component's OWN declared directory
(hooks/defeater_ledger/, per ARCHITECTURE.md) and travels on the audit record for provenance --
not used for target-path matching, since the file this gate protects (.foreman/defeaters.jsonl)
does not live inside this component's own declared glob, the same shape ledger_write_guard.py's
own component/target-file split already has. extract_bash_write_targets() is reused verbatim
for the Bash-target case (never a locally reimplemented parser), matching this file's own
constraints block.

C5: every denial routes through hook_common's deny()/verdict-ledger contract -- hc.deny() inside
hc.run()'s fail-open wrapper, which records a real row to ~/.claude/telemetry/verdicts.jsonl via
verdict_ledger.record() -- never a bare sys.exit() or print with no hook_common call. This is the
concrete mechanism behind ARCHITECTURE.md's defeater_to_hookcommon edge.

FORE-635 (Alice proposal, 2026-09-16, F5 LOW-MEDIUM): widens the Bash-path text backstop to
catch a Python-invoked, non-shell write that bypassed both extract_bash_write_targets() (a
structural SHELL-write-verb extractor -- Python's own open()/write() calls are not shell
syntax it was ever built to parse) and the pre-existing DEFEATERS_PATH_RE +
BASH_WRITE_OPERATOR_RE backstop (BASH_WRITE_OPERATOR_RE's write-verb set is shell operators --
>, >>, tee, cp, mv, sed -i, dd -- none of which appear in the reproduced gap,
`python3 -c "open('.foreman/defeaters.jsonl','w').write(...)"`). See
bash_text_mentions_python_write() below for the widened check and its own disclosed scope.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import component_coupling as cc  # noqa: E402
import hook_common as hc  # noqa: E402

RULE_ID = "FOREMAN-DEFEATER-LEDGER-WRITE-GUARD"
DEFEATERS_NAME = "defeaters.jsonl"
DEFEATERS_RELPATH = str(Path(".foreman") / DEFEATERS_NAME)
# FORE-613: the directory that IS the Foreman marker, matched on the TARGET rather than walked
# up from cwd. Same constant and same role as ledger_write_guard.py's own FOREMAN_DIR.
FOREMAN_DIR = ".foreman"
COMPONENT_NAME = "defeater_ledger"

VALID_STATUSES = frozenset({"open", "mitigated"})

# Same convention ledger_write_guard.py's own LEDGER_PATH_RE/BASH_WRITE_OPERATOR_RE use for its
# sibling ledger: a project-relative or bare-filename reference, alongside a write-shaped
# operator, is the coarse text-scan backstop for whatever the structural extractor misses.
DEFEATERS_PATH_RE = re.compile(r"(?:\.foreman[/\\])?defeaters\.jsonl", re.IGNORECASE)
BASH_WRITE_OPERATOR_RE = re.compile(
    r">>?(?!\s*&)|\btee\b|\bcp\b|\bmv\b|\bsed\s+-i\b|\bdd\b[^\n]*\bof="
)

# ---------------------------------------------------------------------------
# FORE-635: the Python-invoked-write text backstop.
# ---------------------------------------------------------------------------
#
# SCOPE, STATED RATHER THAN OVERCLAIMED, matching every other heuristic backstop in this suite's
# own posture (component_coupling.extract_bash_write_targets()'s own docstring: "a gap here
# fails toward NOT gating... this raises the bar without claiming to be complete"). This widens
# coverage for a PYTHON-invoked write whose write-shaped call appears LITERALLY in the Bash
# command's own text -- the same "text the hook can actually see" constraint every backstop here
# already accepts.
#
# WHAT THIS CLOSES: `python3 -c "open(TARGET,'w')..."`, `.write_text(...)`, `.write_bytes(...)`,
# or `os.open(..., os.O_WRONLY|...)` with both the write-shaped call AND a reference to
# defeaters.jsonl spelled out in the command's own inline `-c` text -- the exact reproduced case
# this ticket names.
#
# WHAT THIS DOES NOT CLOSE, DISCLOSED RATHER THAN SILENTLY LEFT:
#   (1) a path built at runtime (a variable, string concatenation, an environment lookup, a
#       function call) rather than a literal quoted string naming defeaters.jsonl directly --
#       the identical limitation extract_bash_write_targets() already has for the shell-native
#       case, now equally true for this Python-text heuristic.
#   (2) a SEPARATELY invoked .py script (`python3 script.py`), which this does not recurse into
#       the way extract_bash_write_targets() recurses one level into an invoked .sh script
#       (FORE-15) -- only inline `-c` text sitting in the Bash command itself is scanned. Adding
#       script-file recursion for Python specifically is real future work, not attempted here,
#       since it would need its own bounded-recursion design (file-size cap, one-level-only,
#       project-root containment) matching FORE-15's own, not a quick copy.
#   (3) any language other than Python (perl, ruby, node, a compiled one-liner) -- named as the
#       same class of gap, not silently assumed closed by this fix.
#   (4) obfuscation (base64/hex-escaped strings, string concatenation of the mode character,
#       `getattr(f, 'wr'+'ite')`, etc.).
# This raises the bar against the SPECIFIC reproduced evasion without claiming completeness.
PYTHON_INVOCATION_RE = re.compile(r"\bpython3?\b")
# A write-shaped Python call: open(...) with a mode string starting w/x/a (covers w, x, a, w+,
# wb, ab, xb, x+, a+, and combinations -- every real write-opening mode shares a leading
# w/x/a character), OR a pathlib write method that carries write intent with no mode argument
# at all, OR os.open() with a write-shaped flag -- the same low-level primitive this suite's own
# real writers (dormancy_register._atomic_write, defeater_ledger.append_defeat itself) use, so
# recognizing it here is recognizing this suite's own idiom, not a foreign one.
PYTHON_WRITE_SIGNAL_RE = re.compile(
    r"open\s*\([^)]*?,\s*['\"][wxa]"
    r"|\.write_text\s*\("
    r"|\.write_bytes\s*\("
    r"|\bos\.open\s*\([^)]*?O_(?:WRONLY|RDWR|APPEND|CREAT)"
)


def bash_text_mentions_python_write(command):
    """FORE-635: True only when the command invokes python AND contains a write-shaped Python
    call AND names defeaters.jsonl -- all three checked against the command's own raw text, the
    same class of backstop DEFEATERS_PATH_RE + BASH_WRITE_OPERATOR_RE already is for the
    shell-native case. Requiring all three (rather than write-signal-and-path alone) keeps this
    scoped to python-shaped commands specifically, so it does not duplicate or loosen the
    existing shell-operator backstop's own trigger conditions."""
    return bool(
        PYTHON_INVOCATION_RE.search(command)
        and PYTHON_WRITE_SIGNAL_RE.search(command)
        and DEFEATERS_PATH_RE.search(command)
    )


# CHV2-88 part A, refactor only. This resolution logic was written and verified inside
# test_defeater_ledger.py's C2SeedCitationTests, where it ran only at test time and only against
# the seed set. Moved here AS-IS -- same checks, same order, same return shape -- so there is one
# implementation rather than a second one written to look like the first. The test file now
# imports it.
#
# NOT WIRED INTO append_defeat(). That is part B and it is deliberately not done here: gating a
# "mitigated" claim on this would upgrade what a FROZEN component guarantees, and
# defeater_ledger/GOALS.json's own F1/F2 text currently discloses that exact gap as accepted
# rather than fixed. Landing enforcement without the matching amendment would leave the frozen
# file lying about its own done-state, which is worse than the open gap, because the next reader
# trusts that file's account. Escalated for a real amendment decision.
#
# Two things changed in the move, both forced and both stated rather than silent:
#   - the test version called self.assertTrue() on an empty sub-reference list. A module function
#     has no assertions to make, and a ref with nothing in it cannot be said to resolve, so it
#     returns False. That is also the fail-closed direction.
#   - the sub-reference helper lost its leading underscore, per this project's naming rule for
#     new code. The logic is untouched.
CITATION_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
CITATION_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
CITATION_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CITATION_DOC_SEARCH_ROOTS = (CITATION_REPO_ROOT, Path.home() / "agent-remediation")
TESSERA_REPO = Path.home() / "dev" / "ticket-system"
TESSERA_DB = "data/tessera.db"


def resolve_citation_sub_ref(sub_ref):
    """(resolved: bool, detail: str) for exactly one sub-reference (already stripped)."""
    if CITATION_COMMIT_RE.match(sub_ref):
        proc = subprocess.run(
            ["git", "cat-file", "-t", sub_ref], cwd=str(CITATION_REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
        ok = proc.returncode == 0 and proc.stdout.strip() == "commit"
        return ok, f"git cat-file -t {sub_ref} -> {proc.stdout.strip() or proc.stderr.strip()}"

    if CITATION_TICKET_RE.match(sub_ref):
        proc = subprocess.run(
            [sys.executable, "-m", "tessera.api.cli", "--db", TESSERA_DB,
             "get", sub_ref, "--compact"],
            cwd=str(TESSERA_REPO), capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return False, f"tessera get {sub_ref} failed: {proc.stderr.strip()[:300]}"
        try:
            row = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            return False, f"tessera get {sub_ref} did not return JSON"
        ok = isinstance(row, dict) and row.get("ticket_id") == sub_ref
        return ok, f"tessera get {sub_ref} -> ticket_id={row.get('ticket_id') if isinstance(row, dict) else None}"

    # Named-document ref: a real, existing file under one of the known doc roots.
    for root in CITATION_DOC_SEARCH_ROOTS:
        candidate = root / sub_ref
        if candidate.is_file():
            return True, f"file exists at {candidate}"
    return False, f"{sub_ref!r} did not resolve as a commit hash, ticket ID, or real file"


def resolve_citation(ref):
    """(all_resolved: bool, per_sub_ref_results) for a citation that may name several artifacts
    separated by commas. Does a citation point at something that actually exists -- a real commit,
    a real ticket, a real file -- rather than merely being a non-empty string."""
    sub_refs = [s.strip() for s in ref.split(",") if s.strip()]
    if not sub_refs:
        return False, [(False, f"ref {ref!r} had no sub-references to resolve")]
    results = [resolve_citation_sub_ref(sub_ref) for sub_ref in sub_refs]
    return all(ok for ok, _ in results), results


class DefeaterLedgerError(ValueError):
    """Raised by append_defeat() on any invalid field. Never lands a partial/invalid row."""


# ---------------------------------------------------------------------------
# Library half: the blessed write path. Ordinary Python calls, never a Claude Code tool call.
# ---------------------------------------------------------------------------

def default_store_path():
    project_root = cc.find_project_root(os.getcwd())
    if project_root is None:
        # This module's own repo is itself a Foreman project (.foreman/ exists at its root),
        # so this is a real fallback for library callers invoked from elsewhere in the tree,
        # not a guess -- same reasoning efficacy_meta_check.py's own periodic_main() default
        # already uses for its project_root default.
        project_root = Path(__file__).resolve().parent.parent.parent
    return Path(project_root) / ".foreman" / DEFEATERS_NAME


def _validate_row(defeater_id, signal, description, first_observed, ticket_or_incident_ref, status,
                   evidence_path=None):
    for field_name, value in (
        ("defeater_id", defeater_id), ("signal", signal), ("description", description),
        ("ticket_or_incident_ref", ticket_or_incident_ref),
    ):
        if not isinstance(value, str) or not value.strip():
            raise DefeaterLedgerError(f"{field_name} must be a non-empty string")
    if not isinstance(first_observed, str) or not first_observed.strip():
        raise DefeaterLedgerError("first_observed must be a non-empty ISO8601 string")
    try:
        datetime.fromisoformat(first_observed.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DefeaterLedgerError(f"first_observed {first_observed!r} is not valid ISO8601: {exc}") from exc
    if status not in VALID_STATUSES:
        raise DefeaterLedgerError(f"status must be one of {sorted(VALID_STATUSES)}, got {status!r}")
    # C6 (CHV2-88 / PDP section 11.6 provisional decision, orchestrator-7fe5ba, 2026-09-13):
    # a "mitigated" claim with no evidence to check is not a mitigated claim -- mirrors
    # efficacy_meta_check.py's own C2 _evidence_resolves() bar for would_have_been_caught_by
    # claims, one component over. Path.is_file() only, deliberately not resolve_citation's
    # heavier git/tessera logic (that checks ticket_or_incident_ref, a different field).
    if status == "mitigated":
        if not isinstance(evidence_path, str) or not evidence_path.strip():
            raise DefeaterLedgerError(
                "status='mitigated' requires a non-empty evidence_path -- a mitigated claim "
                "with no evidence to check is not a mitigated claim")
        try:
            resolves = Path(evidence_path).is_file()
        except OSError:
            resolves = False
        if not resolves:
            raise DefeaterLedgerError(
                f"evidence_path {evidence_path!r} does not resolve to a real file -- "
                f"status='mitigated' requires real, checkable evidence")


def append_defeat(defeater_id, signal, description, first_observed, ticket_or_incident_ref,
                   status="open", store_path=None, evidence_path=None):
    """Validate then append exactly one line -- C1. Never opens the store for anything but an
    append; a status change is a brand-new call with the same defeater_id (C3), never an edit
    of a prior line.

    Uses a single os.write() to an O_APPEND-opened fd rather than a buffered text-mode append,
    so the write of one line is atomic against a concurrent reader or a second concurrent
    appender on POSIX (a write() at or under PIPE_BUF to an O_APPEND fd cannot interleave with
    another process's write to the same fd) -- the same reasoning dormancy_register.py's own
    _atomic_write() gives for its own, differently-shaped (rewrite-the-whole-file) guarantee.

    C6: NOT retroactive. Rows appended before this requirement existed (e.g. seed_defeaters.py's
    original D2/D5 mitigated rows) have no evidence_path key at all; read_all() returns them
    as-is and this function never re-validates a row that already landed. Only new append_defeat()
    calls going forward are gated.
    """
    _validate_row(defeater_id, signal, description, first_observed, ticket_or_incident_ref, status,
                  evidence_path=evidence_path)
    resolved_store = Path(store_path) if store_path else default_store_path()
    resolved_store.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "defeater_id": defeater_id,
        "signal": signal,
        "description": description,
        "first_observed": first_observed,
        "ticket_or_incident_ref": ticket_or_incident_ref,
        "status": status,
        "evidence_path": evidence_path,
        "appended_at": datetime.now(timezone.utc).isoformat(),
    }
    line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    if len(line) > 65536:  # generous, well under a real PIPE_BUF on any platform this runs on
        raise DefeaterLedgerError("row too large to append atomically")
    fd = os.open(str(resolved_store), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return row


def read_all(store_path=None):
    """Every row in the store, in append order. A line that isn't valid JSON is skipped with a
    stderr disclosure rather than raising -- same posture efficacy_meta_check.load_verdicts()
    already applies to a multi-writer-shaped JSONL file: a torn line is an ordinary condition to
    disclose, not a reason to deny the whole read."""
    resolved_store = Path(store_path) if store_path else default_store_path()
    try:
        text = resolved_store.read_text()
    except FileNotFoundError:
        return []
    rows = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            row = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"[defeater_ledger] {resolved_store}:{lineno} is not valid JSON ({exc}); "
                  f"skipped", file=sys.stderr)
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def current_status(defeater_id, store_path=None):
    """The status of the LAST row appended for this defeater_id -- C3's read side. None if no
    row for this id exists at all."""
    latest = None
    for row in read_all(store_path=store_path):
        if row.get("defeater_id") == defeater_id:
            latest = row
    return latest["status"] if latest else None


def rows_for(defeater_id, store_path=None):
    """Every row (in append order) for this defeater_id -- lets a caller confirm a status
    change landed as a NEW row rather than an edit of the original (C3)."""
    return [row for row in read_all(store_path=store_path) if row.get("defeater_id") == defeater_id]


# ---------------------------------------------------------------------------
# Gate half: the PreToolUse hard-deny, modeled on ledger_write_guard.py.
# ---------------------------------------------------------------------------

def _resolved_path(path_str, cwd):
    try:
        base = Path(cwd) if cwd else Path.cwd()
        candidate = Path(path_str)
        return (candidate if candidate.is_absolute() else base / candidate).resolve()
    except (OSError, RuntimeError):
        return None


def is_same_inode_as_local_defeaters_store(resolved, cwd):
    """Whether `resolved` is the same real file as the defeaters store of the project `cwd` sits
    in -- the hardlink case (CHV2-157).

    Separate from the shape test on purpose: this is an ADDITIONAL deny, never a replacement for
    it, so FORE-613's cwd-jurisdiction removal stands. See is_defeaters_path's docstring for the
    full argument and for the disclosed residual."""
    if not cwd:
        return False
    try:
        project_root = cc.find_project_root(str(cwd))
    except (OSError, RuntimeError, ValueError):
        return False
    if not project_root:
        return False
    store = Path(project_root) / FOREMAN_DIR / DEFEATERS_NAME
    try:
        return os.path.samestat(os.stat(resolved), os.stat(store))
    except (OSError, ValueError):
        return False


def is_defeaters_path(path_str, cwd):
    """True only for a real Foreman defeaters store -- `<anything>/.foreman/defeaters.jsonl`.

    FORE-613. This used to resolve the target relative to find_project_root(cwd) and compare
    against DEFEATERS_RELPATH, falling back to a bare-basename match whenever that comparison
    could not be made. Anchoring on the CWD rather than on the target was wrong in both
    directions, and a five-case battery against the landed version scored TWO wrong:

      BYPASS          a nested child project's own real .foreman/defeaters.jsonl, written with
                      a payload cwd of the PARENT project, returned False. find_project_root
                      walks up from cwd and stops at the parent, so the child's store is not
                      relative_to the parent root... except it IS, and the relpath then reads
                      `nested/child-project/.foreman/defeaters.jsonl`, which is not
                      DEFEATERS_RELPATH, so it fell through to the basename branch and the real
                      store was not recognised as one. Fired live by Iris Chen against the real
                      main() on a real Write payload: no deny.

      FALSE POSITIVE  an unrelated defeaters.jsonl in a directory that is not a Foreman project
                      at all returned True, via the no-project-root fallback -- the exact
                      over-match this function's OWN docstring claimed it prevented ("an
                      unrelated defeaters.jsonl living somewhere else in the tree (e.g. a
                      fixture directory) is not swept in"). It was swept in. Same class as the
                      ~145 live observer-effect-doe/runs/**/ledger.jsonl files that FORE-524's
                      F3 found against the sibling predicate.

    So this is not a narrowing or a widening; it is both, and it is the same net move F3 made
    on ledger_write_guard.is_ledger_path(): narrower on directory, wider on case, and strictly
    more fail-closed on the real store than what it replaces.

    ANCHORING ON THE TARGET'S OWN PARENT DIRECTORY, not on a project root walked up from cwd,
    is the whole fix and is deliberate for the reason that sibling states: `.foreman/` IS the
    marker find_project_root looks for, so a file sitting directly inside one is a Foreman
    store by construction, with no walk required -- and, crucially, with no dependence on
    whichever project the WRITER happened to be standing in. A predicate about the target
    should not read the writer's cwd to decide what the target is.

    `cwd` is still accepted and still used, for resolving a RELATIVE path_str against the
    writer's directory. That is a use of cwd the target genuinely needs; deciding jurisdiction
    from it was not.

    Case-insensitive on both components, matching the sibling: macOS is case-insensitive and
    case-preserving, so `.Foreman/Defeaters.JSONL` is the same file under a different spelling
    (FORE-128's finding).

    Returns False when the path cannot be resolved at all. That matches is_ledger_path()'s own
    behaviour and is a known fail-toward-not-gating direction, tracked separately as FORE-590
    and FORE-595 for the shared extractor; it is not introduced here.

    CHV2-157: A HARDLINK NEEDS A DIFFERENT ANSWER HERE THAN IT DOES IN THE SIBLING GUARDS, and
    the difference is worth stating because the obvious port does not apply. registry_write_guard,
    heartbeat_write_guard and dormancy_register each protect ONE fixed machine-wide path, so they
    can ask "is this target the same inode as THE store". This predicate has no single store to
    point at: it recognises a SHAPE, `<anything>/.foreman/defeaters.jsonl`, and a hardlink sitting
    outside any `.foreman/` directory fails that shape test by construction. Iris Support's
    battery landed a write through exactly that: allowed, and the bytes reached the real store.

    What is added below is therefore not the sibling's check. It asks whether the target is the
    same inode as the defeaters store of the project the WRITER is standing in, which is the
    realistic shape of the attack -- a session inside a project, writing through an alias to that
    project's own store.

    STRICTLY ADDITIVE, AND THIS IS WHY IT DOES NOT REOPEN FORE-613. That fix removed cwd-derived
    JURISDICTION: it stopped asking cwd to decide what the target IS, because doing so made the
    predicate miss a nested child project's real store. The shape test above remains the sole
    decider of jurisdiction and is unchanged. This clause can only ever ADD a deny, never remove
    one, so the false negative FORE-613 closed cannot come back through it.

    RESIDUAL, DISCLOSED: a hardlink to project A's defeaters store, written from a session whose
    cwd is in project B or in no project at all, is still not caught -- there is nothing to
    compare against without searching the filesystem for every `.foreman/defeaters.jsonl`, which
    is not a check a PreToolUse hook can afford. Narrower than the gap it closes, and stated so
    nobody reads this as complete hardlink coverage.

    SEQUENCING, carried forward from CHV2-115/116: this WIDENS what the identity check sees and
    removes nothing. The coarse IGNORECASE text backstop is untouched, deliberately -- it is
    currently denying spellings the path check misses."""
    if not path_str:
        return False
    resolved = _resolved_path(path_str, cwd)
    if resolved is None:
        return False
    if (resolved.name.lower() == DEFEATERS_NAME
            and resolved.parent.name.lower() == FOREMAN_DIR):
        return True
    return is_same_inode_as_local_defeaters_store(resolved, cwd)


def bash_text_mentions_defeaters_write(command):
    return bool(DEFEATERS_PATH_RE.search(command)) and bool(BASH_WRITE_OPERATOR_RE.search(command))


def _audit_extra(cwd):
    """component_root() usage (C4): resolves this component's OWN declared directory per
    ARCHITECTURE.md's component map, carried on the audit record for provenance -- not used for
    target-path matching, since the file this gate protects (.foreman/defeaters.jsonl) does not
    live inside this component's own glob (hooks/defeater_ledger/**), the same split
    ledger_write_guard.py's component-directory-vs-protected-file already has."""
    project_root = cc.find_project_root(cwd)
    if project_root is None:
        return {}
    component_map, _malformed = cc.parse_component_map(project_root)
    return {"component_root": cc.component_root(COMPONENT_NAME, component_map)}


def main(data):
    tool_name = data.get("tool_name")
    sid, cwd = data.get("session_id"), data.get("cwd")

    if tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        fp = hc.target_path(data.get("tool_input"))
        if fp and is_defeaters_path(fp, cwd):
            hc.audit("SAFETY_DENY", {"guard": "defeater_ledger", "reason":
                      "direct-write-to-defeaters", "file_path": fp, **_audit_extra(cwd)},
                     sid, cwd, severity="high")
            hc.set_rule(f"{RULE_ID}:direct-edit-denied")
            hc.deny(
                f"Blocked direct write to {Path(fp).name}: .foreman/defeaters.jsonl is an "
                f"append-only defeater ledger (PRD.md REQ-14), written only through "
                f"defeater_ledger.append_defeat(), never edited directly. If you're recording a "
                f"new defeat or a status change, call append_defeat() from a script; if that "
                f"function doesn't cover what you need, that's a real gap to raise, not a "
                f"reason to write this file by hand."
            )
            return
        return

    if tool_name == "Bash":
        command = (data.get("tool_input") or {}).get("command", "") or ""
        if not command:
            return
        for target in cc.extract_bash_write_targets(command, cwd):
            if is_defeaters_path(str(target), cwd):
                hc.audit("SAFETY_DENY", {"guard": "defeater_ledger", "reason":
                          "direct-bash-write-to-defeaters", "command": command[:500],
                          **_audit_extra(cwd)}, sid, cwd, severity="high")
                hc.set_rule(f"{RULE_ID}:bash-write-denied")
                hc.deny(
                    "Blocked Bash write to defeaters.jsonl: this append-only defeater ledger "
                    "(PRD.md REQ-14) is written only through defeater_ledger.append_defeat(), "
                    "never edited directly."
                )
                return
        if bash_text_mentions_defeaters_write(command):
            hc.audit("SAFETY_DENY", {"guard": "defeater_ledger", "reason":
                      "bash-text-match-defeaters", "command": command[:500], **_audit_extra(cwd)},
                     sid, cwd, severity="high")
            hc.set_rule(f"{RULE_ID}:bash-text-match-denied")
            hc.deny(
                "Blocked Bash command: it names defeaters.jsonl alongside a write-shaped "
                "operator. This append-only defeater ledger (PRD.md REQ-14) is written only "
                "through defeater_ledger.append_defeat(). If this command doesn't actually "
                "write to that path, run it yourself in a terminal."
            )
            return
        if bash_text_mentions_python_write(command):
            hc.audit("SAFETY_DENY", {"guard": "defeater_ledger", "reason":
                      "bash-text-match-python-write-defeaters", "command": command[:500],
                      **_audit_extra(cwd)}, sid, cwd, severity="high")
            hc.set_rule(f"{RULE_ID}:bash-python-write-match-denied")
            hc.deny(
                "Blocked Bash command: it invokes python with what looks like a write-shaped "
                "call (open(...) with a w/x/a mode, .write_text(), .write_bytes(), or "
                "os.open() with a write flag) alongside a reference to defeaters.jsonl "
                "(FORE-635). This append-only defeater ledger (PRD.md REQ-14) is written only "
                "through defeater_ledger.append_defeat(). If this command doesn't actually "
                "write to that path, run it yourself in a terminal."
            )
            return


if __name__ == "__main__":
    hc.run(main)
