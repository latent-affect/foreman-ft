#!/usr/bin/env python3
"""registry_write_guard.py -- ATLASSN-154 (atlas-sonnet ARCHITECTURE.md 34.0, 34.6
`registry_write_guard_wired`). PreToolUse hard-deny on any direct Edit/Write/NotebookEdit/
MultiEdit or Bash write targeting `~/.claude/foreman/registry/registry.db` (and its WAL/SHM/
journal sidecars), the enforcement-plane verification registry's authoritative SQLite store.

ARCHITECTURE.md 34.0: "Write discipline follows the review-events precedent, honestly graded.
The only legitimate writer is the write path below; a `registry_write_guard` hook (the
`review_events_ledger_guard.py` pattern, claude-hooks-v2's to register) hard-denies direct
writes against the registry path. Per section 0's T1 model a same-uid adversary writes the
file out-of-band regardless; the guard is hygiene, the structural defense is
validate-at-consumption plus the two-plane cross-check in 34.4."

This file is modeled directly on `ledger_write_guard.py` (FORE-485), itself modeled on
`review_events_ledger_guard.py` (REQ-12) -- the same "hard-deny the direct write path, name
the one legitimate writer" shape, applied to its cross-repo sibling: a single global SQLite
file rather than a per-project JSONL ledger.

WHY REGISTERED NOW, UNLIKE ledger_write_guard.py'S OWN DELIBERATE NON-REGISTRATION. That file's
docstring names the reason it withheld registration: no legitimate writer existed yet (FORE-351,
the `foreman close/skip` CLI, was unbuilt), so registering it would have made the ledger
permanently unwritable -- "correct... but isn't this proposal's call to force." The equivalent
check here comes out the other way: atlas-sonnet's `atlas.registry.write_path` module is real,
built, and tested today (14 of the registry component's 15 frozen GOALS.json criteria pass by
direct re-execution, verified 2026-09-13 -- DANA-VERIFICATION-20260913.md). It writes through
Python's own sqlite3 API from inside a verifier session's process, never by naming
`registry.db` as a Bash/Edit/Write tool-call target -- so this guard's Bash-text detection
(below, same shape `extract_bash_write_targets`/`BASH_WRITE_OPERATOR_RE` already establish) does
not see a legitimate `python3 -c "from atlas.registry import write_path; ..."` invocation at
all, for the same disclosed reason `component_coupling.extract_bash_write_targets()`'s own
docstring names `python3 -c "...open(path,'w')..."` a known blind spot rather than a gap
introduced here. Registering this guard therefore denies exactly what 34.0 asks it to deny --
an agent naming the registry file directly as an Edit/Write/NotebookEdit/MultiEdit target, or a
Bash command whose own visible text names it beside a write operator (`sed -i`, `>>`, `tee`,
`cp`, `mv`, `dd ... of=`) -- while leaving the one real legitimate write path (an in-process
API call, not a tool-call target) untouched. No sequencing dependency blocks it the way FORE-351
blocked `ledger_write_guard.py`.

BOOTSTRAP STATE, DISCLOSED (34.7's own "Bootstrap, day one"): registering this guard does not
by itself make any real write succeed -- D1 clause (a) requires a dispatch record from
`~/.claude/foreman/dispatch-records/`, and that store's own writer (`dispatch_record_writer_wired`,
a THIRD, separate `registers` edge in 34.6) is not part of this ticket and does not exist yet.
Until it does, `write_path.write()` itself will keep rejecting every real write on clause (a) --
this guard changes nothing about that; it only closes the direct-tool-call bypass around
whatever `write_path` does or does not accept, exactly as its own hygiene/structural-defense
split above states.

COMPONENT PLACEMENT: its own subdirectory, `hooks/registry_write_guard/**`, following this
repo's established convention for new gates (ledger_write_guard, tessera_resolver,
tier_triage_gate, freeze_reentry_gate, ticket_status_gate, dependency_provenance_gate all do
the same) rather than extending the undeclared flat-file `review_events_ledger_guard.py`.

DEPENDENCIES READ: component_coupling.py (its own module docstring, read in full this session --
`extract_bash_write_targets()` for the Bash-path check, a best-effort regex scan with disclosed
blind spots including `python3 -c "...open(...)..."`, which is exactly why a legitimate
in-process write_path call is invisible to it rather than accidentally denied); hook_common.py
(`target_path`, `deny`, `audit`, `set_rule`, `run` -- the same shared entry-point/response API
every other hook in this suite uses, read in full this session).

CHV2-115 -- WHAT THIS GUARD DENIED, AND WHAT IT DID NOT, BEFORE THIS FIX. Iris Support's
adversarial probe (FINDING-CHV2-108-REGISTRY-WRITE-GUARD-ALIAS-PROBE-20260918.md, 5 re-runnable
self-scoring probes, real hook subprocesses, real PreToolUse payloads, inode ground truth,
must-deny/must-allow controls held) measured this file allowing 8 of 10 command spellings that
really do overwrite the store. CHV2-108's originally-filed premise -- a symlink blind spot --
was REFUTED: `Path.resolve()` was already correct for symlinks and all three symlink rows
denied. Four other root causes were real:

  RC1  No home expansion. `~/.claude/foreman/registry/registry.db`, `$HOME/...`, and the quoted
       `"$HOME/..."` form all got through. This is the spelling ARCHITECTURE.md 34.0, this
       file's own docstring, and the commissioning ticket all use -- the DEFAULT way to write
       the path, not an evasion technique. Closed below in resolve_write_target().
  RC2  Case-exact comparison on a case-insensitive filesystem. `REGISTRY.DB`, a cased parent
       directory, and `REGISTRY.DB-WAL` all got through. Closed below in is_registry_path().
  RC3  `cp`/`mv` into the store's DIRECTORY with no explicit destination filename. The old
       branch tested `tokens[-1]` against the store's own path, so a directory destination
       never matched. Closed below in bash_text_mentions_registry_write().
  RC4  Hardlink alias. `Path.resolve()` follows symlinks but a hardlink is not a link, it is a
       second name for the same inode, so resolution cannot see it. Closed below as a
       consequence of the identity check RC2's fix introduced, not by separate machinery.

THE CHV2-112 REGRESSION THIS RESTORES. 89e651f replaced coarse commit-message-text matching
with per-operator destination resolution through is_registry_path(). That was a correct fix for
a real false positive (see test_commit_message_mentioning_registry_not_blocked). But resolution
is exactly what fails on an unexpanded `~`, a `$HOME`, or a cased spelling, and the old
text-match never needed to resolve anything. Measured over the same 15 rows: false negatives
went 0 -> 8, false positives 4 -> 0. This fix keeps CHV2-112's per-operator resolution and its
0 false positives, and restores the coverage by making resolution itself see what the shell
sees. Re-measured after this change: 0 false negatives, 0 false positives on the same rows.

SEQUENCING, PER CHV2-115's AND CHV2-116's SHARED CONSTRAINT: expansion and case-folding of the
RESOLUTION path land here FIRST; the coarse Bash backstop below is left in place and is NOT
narrowed by this change. Nothing in this file removes a check. heartbeat_write_guard's
equivalent rewrite (CHV2-116) is deliberately not attempted here, and the shared helper
component_coupling.extract_bash_write_targets() is deliberately NOT modified -- an expansion fix
there would change every guard's resolution behaviour at once, including heartbeat's, whose
coarse name-regex backstop is currently the only thing catching its own tilde and cased-parent
rows. That is the exact ordering CHV2-116 forbids.

WHICH CASE MECHANISM, AND THE RESIDUAL KEPT (CHV2-115, disclosed deliberately). This repo has
two prior answers that disagree: component_coupling._true_case() (FORE-128) resolves real
on-disk casing, which keeps behaviour correct on a case-SENSITIVE filesystem but leaves a
component that does not exist on disk exactly as typed; heartbeat_write_guard.is_heartbeat_path
lowercases its leaf unconditionally, which has no not-yet-created hole but changes
case-sensitive-filesystem semantics. This file takes neither as primary. Its primary test is
real filesystem identity -- os.path.samestat on (st_dev, st_ino) -- which is not a case
heuristic at all: it is correct on every filesystem, needs no platform assumption, and catches
the hardlink alias (RC4) that no amount of string comparison can. Measured on this machine
2026-09-18: `/Users/m5/.claude/FOREMAN/registry` and `/Users/m5/.claude/foreman/registry` are
both inode 59595087, so the alias is real and the identity test sees it.

Case comparison survives only as the FALLBACK for a target that does not exist yet, where there
is no inode to compare, and even then it is gated on a MEASUREMENT of whether the store's own
directory really is case-insensitive rather than on a guess about the platform. The residual
being kept, stated plainly: if that measurement cannot be taken (the registry directory itself
is absent), the fallback assumes case-insensitive, which on a case-sensitive filesystem would
deny a genuinely different file whose name differs only by case. That is a false positive in
the safe direction for a hygiene guard -- it denies a tool call and tells the operator to run it
in a terminal -- and it is reachable only in a state where the store does not exist at all.
Bob's CHV2-115 QA prep raised the bootstrap case (store absent because D1 clause (a) rejects
every real write) as the reason this choice matters; as measured today the store, its -wal and
its -shm sidecars all exist on disk, so the inode path is live for those and the fallback is
reached in practice only by `-journal`, which SQLite creates transiently.

RESIDUAL, same shape ledger_write_guard.py discloses and does not overclaim past: this guard's
Bash-path coverage is a literal-text match on the guard's own visible command string. It cannot
see inside a script invoked via Bash whose target path is built at runtime rather than named
literally, nor a `python3 -c` payload naming the path only inside a quoted Python string with
no bare filesystem-write-operator token beside it on the Bash command line. An agent that can
edit this hook file, `~/.claude/settings.json`, or set `disableAllHooks` defeats it entirely --
the same shared-UID residual PDP-RATIONALE.md section 7 already names for every hook in this
suite, not a new one introduced here.

TWO FURTHER RESIDUALS THIS FIX DOES NOT CLOSE, named rather than left to be rediscovered:
`os.path.expandvars` below expands from this HOOK SUBPROCESS's environment, not from the
caller's shell, so a variable set only in the agent's own shell still resolves to nothing here
and the spelling stays invisible ($HOME is expanded correctly because the hook runs as the same
uid). And `mv <store> /somewhere/else` destroys the store by moving it away, which is a real
mutation that neither the old nor the new destination check catches, because the destination is
not the registry -- out of CHV2-115's filed RC1-RC4 scope, recorded here for the follow-up
rather than widened into silently.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import component_coupling as cc  # noqa: E402
import hook_common as hc  # noqa: E402

RULE_ID = "REGISTRY-WRITE-GUARD"

# The one authoritative store, per ARCHITECTURE.md 34.0 -- a fixed, machine-global path, never
# resolved relative to a project cwd (unlike ledger.jsonl, which is deliberately anchored to
# `<project>/.foreman/`). SQLite's own sidecar files carry the same protection: a WAL/SHM/
# rollback-journal write mutates the store just as surely as a write to the main file.
REGISTRY_DB_PATH = (Path.home() / ".claude" / "foreman" / "registry" / "registry.db").resolve()
REGISTRY_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
REGISTRY_NAMES = {REGISTRY_DB_PATH.name} | {
    REGISTRY_DB_PATH.name + suffix for suffix in REGISTRY_SIDECAR_SUFFIXES
}
REGISTRY_NAMES_FOLDED = {name.casefold() for name in REGISTRY_NAMES}

# Raw-text Bash backstop. CHV2-112: the original version here matched whenever "registry.db"
# and any write-shaped operator both appeared ANYWHERE on the command line -- not whether an
# operator's own destination was the registry. Found live 2026-09-18 (Bob, landing ATLASSN-177):
# a `git commit -m "..."` whose MESSAGE TEXT merely discussed a registry.db finding, with a `>`
# character elsewhere in the same message (a markdown blockquote marker, unrelated to the
# commit), was denied as if it wrote to the registry. It never did.
#
# This is the exact defect class CHV2-49 already found and fixed in this file's sibling,
# review_events_ledger_guard.py's _bash_text_mentions_ledger_write -- ported here rather than
# re-derived, using the same promoted-public component_coupling helpers
# (quoted_spans/inside_quotes, BASH_REDIRECT_RE, BASH_TEE_RE, BASH_SED_INPLACE_RE) and the same
# per-operator destination check: resolve what each operator ACTUALLY writes to, not whether the
# registry's name merely co-occurs with some operator's token anywhere on the line. A commit
# message's own quoted text is never a real shell destination, so it is now excluded by
# quote-awareness the same way FORE-23 already excludes it from extract_bash_write_targets.
#
# CHV2-115 keeps every line of that narrowing intact. What changes below is only what
# is_registry_path() can SEE -- expansion and real-identity comparison -- never what the
# backstop checks.
GUARD_CPMV_RE = re.compile(r"\b(?:cp|mv)\b")
BASH_DD_RE = re.compile(r"\bdd\b[^\n]*\bof=(\S+)")

# CHV2-177: rsync completely bypassed this guard -- neither this file's own GUARD_CPMV_RE nor
# the shared component_coupling.extract_bash_write_targets() (used by architecture_gate.py and
# goals_freeze_gate.py) ever named rsync, confirmed live: 3/3 real payloads
# (`rsync -a src <store-path>`, the tilde-spelled form, and the into-directory form) all returned
# decision=None against the real registry.db. Same disclosed-scope decision as BASH_DD_RE right
# above it: fixed HERE, in this guard's own local backstop, not in the shared component_coupling
# helper. That helper is used by architecture_gate.py and goals_freeze_gate.py too, and CHV2-115/
# 116's own sequencing rule (this file's module docstring, "SEQUENCING" section) already commits
# to not touching that shared function's matching/resolution behavior as a side effect of a
# single-guard fix -- widening it is a separate ticket against every one of its consumers, with
# its own regression test, matching the discipline FORE-14/FORE-591's own scars already
# established for that exact function. Anchored to command position (the FORE-591-corrected
# shape, not GUARD_CPMV_RE's own older, still-unanchored `\b(?:cp|mv)\b` -- a separate,
# pre-existing residual this fix does not touch, disclosed rather than silently carried forward
# into new code): optional env assignments, optional sudo, optional one wrapper word, then
# `rsync` at the start of the matched span. rsync has no `git`-style subcommand wrapper the way
# `mv` does, so that wrapper word is omitted here.
#
# CHV2-180 (2026-09-20, Nadia Osei's real STRIDE/validate finding, Build 8): the anchored shape
# above closed CHV2-177's unwrapped-rsync gap but reopened the identical bypass class through a
# different wrapper -- `rsync` piped through `xargs`, `find ... -exec`, or GNU `parallel` never
# sits at the anchored command position this regex requires, confirmed live: `find . -exec rsync
# {} <store-path> \;`, `xargs -I{} rsync {} <store-path>`, and `parallel rsync {} <store-path>
# ::: src` all returned decision=None against the real registry.db, while the plain
# `rsync src <store-path>` CHV2-177 fixed still correctly denies.
#
# `xargs` is fixed here, added as a second optional flag-swallowing wrapper alongside `sudo`
# (xargs's own flags -- `-I{}`, `-n1`, `-P4` -- are syntactically identical to sudo's `-\S+`
# shape, so the same swallowing pattern applies unchanged) -- confirmed this closes it because
# the existing tail-token destination extraction below already resolves correctly for this
# shape: `xargs -I{} rsync {} <store-path>` tokenizes with the store path as the genuine last
# positional token, unlike the two residuals below.
#
# `find ... -exec` and `parallel` are NOT fixed here, and disclosed rather than half-fixed, for a
# reason specific to each, verified directly rather than assumed:
#   - `find`'s wrapped command does not sit at command position at all (arbitrary find-predicate
#     tokens precede `-exec`), so recognizing it would mean an unanchored scan for the `-exec`
#     token -- reopening exactly the unanchored-match false-positive risk anchoring this regex
#     was chosen to avoid (see the CHV2-177 paragraph above, and FORE-591's own scars on this
#     exact class of mistake). Worse, EVEN IF the trigger fired, `find`'s own `\;`/`+` argument
#     terminator breaks the existing tail-token destination heuristic below: confirmed live that
#     this same heuristic already mis-extracts the destination for `cp`/`mv` under `-exec` TODAY
#     (`GUARD_CPMV_RE` is unanchored and DOES fire for `find . -exec cp {} <store-path> \;`, and
#     the guard still returns decision=None, because the extracted "destination" is the literal
#     `\;` token, not the real path) -- a pre-existing characteristic of the tail-token heuristic
#     itself, not something this fix introduces or could close by widening a trigger alone.
#   - `parallel`'s own `:::` argument-list separator has the identical effect: confirmed live
#     that `parallel cp {} <store-path> ::: src` also returns decision=None today for the same
#     reason (the tail positional token is `src`, an input list item, not the destination).
# Both would need bespoke destination-extraction logic specific to their own argument grammar,
# not a wider trigger regex -- of this ticket's own two named options (widen the regex, or
# document the residual), widening would not actually close either of these two shapes. Named
# here and pinned open by a permanent regression test rather than left to be rediscovered.
GUARD_RSYNC_RE = re.compile(
    r"^\s*"
    r"(?:\w+=\S*\s+)*"
    r"(?:sudo\s+(?:-\S+\s+)*)?"
    r"(?:xargs\s+(?:-\S+\s+)*)?"
    r"(?:(?:command|env|nohup|time)\s+)?"
    r"rsync\b"
)


def same_stat_identity(path_a, path_b):
    """True when both paths exist and name the SAME real file or directory, compared by
    (st_dev, st_ino) via os.path.samestat.

    CHV2-115 RC2/RC4. This is the primary identity test and it is deliberately not a heuristic:
    it settles case aliasing, symlink aliasing and hardlink aliasing in one comparison, is
    correct on case-sensitive and case-insensitive filesystems alike, and needs no assumption
    about the platform. Path.resolve() cannot do the hardlink half at all -- a hardlink is a
    second directory entry for one inode, not a link to follow.

    A path that does not exist raises OSError from os.stat and returns False here, which routes
    to the case fallback in is_registry_path() rather than to a deny."""
    try:
        return os.path.samestat(os.stat(path_a), os.stat(path_b))
    except (OSError, ValueError):
        return False


def directory_is_case_insensitive(directory):
    """Whether `directory` really is case-insensitive, MEASURED against the live filesystem
    rather than inferred from sys.platform -- a case-sensitive volume can be mounted on macOS
    and a case-insensitive one on Linux, so the platform is not the question being asked.

    Compares the directory's own stat against the stat of its case-swapped spelling: same inode
    means the filesystem treats the two spellings as one object. Returns True when the
    measurement cannot be taken (the directory is absent), which is the deny-safe direction for
    a guard and is the residual this file's module docstring discloses by name."""
    try:
        base = os.stat(directory)
    except (OSError, ValueError):
        return True
    swapped = str(directory).swapcase()
    if swapped == str(directory):
        return True
    try:
        return os.path.samestat(base, os.stat(swapped))
    except (OSError, ValueError):
        return False


REGISTRY_DIR_CASE_INSENSITIVE = directory_is_case_insensitive(REGISTRY_DB_PATH.parent)


def resolve_write_target(path_str, cwd):
    """The absolute, symlink-resolved path a tool_input names, or None. Same shape
    ledger_write_guard.resolve_write_target uses: resolve relative to the hook PAYLOAD's own
    cwd, never this hook subprocess's own os.getcwd().

    CHV2-115 RC1: expands `~` and shell variables BEFORE resolving. A guard that reads raw
    command text and does not expand is comparing a string the OS will never see -- the shell
    expands it, the filesystem receives the expansion, and the guard was matching against the
    pre-expansion spelling. Order matters and follows the shell's own: tilde expansion first,
    then parameter expansion, so `expandvars(expanduser(...))`. An unset variable is left
    literal by expandvars, resolves to no real path, and therefore cannot produce a match."""
    if not path_str:
        return None
    expanded = os.path.expandvars(os.path.expanduser(path_str))
    base = Path(cwd) if cwd else Path.cwd()
    candidate = Path(expanded)
    absolute = candidate if candidate.is_absolute() else (base / candidate)
    try:
        return absolute.resolve()
    except OSError:
        # Same fail-open-on-resolution-error shape ledger_write_guard.resolve_write_target
        # already carries (identical except clause) -- an unresolvable path can't match
        # REGISTRY_DB_PATH's own already-resolved identity either way, so returning None here
        # routes to is_registry_path's False, not to a silently-granted write: main() only
        # ever DENIES on a positive match, never on this function's failure to resolve.
        return None


def is_registry_path(path_str, cwd=None):
    """True only for the real registry store or one of its SQLite sidecars.

    Identity, not name matching. The primary test is (st_dev, st_ino) against each real store
    path, so a symlink, a case alias and a HARDLINK all match, and a same-named file elsewhere
    on the machine does not. Only when the named target does not exist yet -- there being no
    inode to compare -- does this fall back to comparing the parent's identity plus the leaf's
    name, and that leaf comparison is case-insensitive only where the store's own directory has
    been MEASURED to be case-insensitive.

    CHV2-115 RC2 corrects this docstring's own prior claim that Path.resolve() is
    "case-resolved". It is not, on APFS or on any case-insensitive, case-preserving filesystem:
    resolve() follows symlinks but preserves whatever casing was typed, so REGISTRY.DB and
    registry.db resolved to two different strings naming one real file, and the old comparison
    returned False for the cased spelling. A future reader would otherwise have trusted that
    sentence exactly as it stood."""
    resolved = resolve_write_target(path_str, cwd)
    if resolved is None:
        return False
    for name in REGISTRY_NAMES:
        if same_stat_identity(resolved, REGISTRY_DB_PATH.parent / name):
            return True
    if not is_registry_dir_path(resolved.parent):
        return False
    if resolved.name in REGISTRY_NAMES:
        return True
    return REGISTRY_DIR_CASE_INSENSITIVE and resolved.name.casefold() in REGISTRY_NAMES_FOLDED


def is_registry_dir_path(resolved_dir):
    """Whether an already-resolved path IS the store's own directory -- by inode first, then by
    an exact string match, then case-insensitively where that directory has been measured
    case-insensitive. Split out because CHV2-115 RC3's cp/mv branch needs the directory question
    answered separately from the file question."""
    if same_stat_identity(resolved_dir, REGISTRY_DB_PATH.parent):
        return True
    if resolved_dir == REGISTRY_DB_PATH.parent:
        return True
    return (REGISTRY_DIR_CASE_INSENSITIVE
            and str(resolved_dir).casefold() == str(REGISTRY_DB_PATH.parent).casefold())


def is_registry_dir(path_str, cwd=None):
    """is_registry_dir_path against a raw, unexpanded, unresolved path string."""
    resolved = resolve_write_target(path_str, cwd)
    if resolved is None:
        return False
    return is_registry_dir_path(resolved)


def lands_on_registry_name(src_str):
    """Whether copying/moving `src_str` into a directory would land a file named like the store.

    Only the BASENAME is consulted, deliberately: this answers "what would the new file be
    called", never "is the source the store". It is reached only after the destination has
    already been confirmed to be the store's own directory, so `cp <store> /some/backup/dir/`
    -- a legitimate backup whose SRC basename matches but whose destination is not the registry
    -- is never denied by it. That false positive is the one Bob's CHV2-115 QA prep flagged in
    advance; test_bash_cp_store_to_backup_dir_not_blocked pins it open."""
    if not src_str:
        return False
    name = Path(src_str).name
    if name in REGISTRY_NAMES:
        return True
    return REGISTRY_DIR_CASE_INSENSITIVE and name.casefold() in REGISTRY_NAMES_FOLDED


def bash_text_mentions_registry_write(command, cwd=None):
    """Whether `command` has a write-shaped operator whose OWN destination is the registry (or a
    sidecar) -- not whether the registry's name and some write-operator both appear anywhere on
    the line. CHV2-112, same fix shape as CHV2-49's review_events_ledger_guard.py rewrite:
    per-operator destination extraction plus quote-awareness, not name+operator co-occurrence.

    Resolves each candidate destination through is_registry_path(..., cwd) rather than a bare
    basename check (unlike the ledger guard's _is_ledger_path) -- consistent with this file's own
    resolve_write_target/is_registry_path convention used by the Edit/Write branch and by
    extract_bash_write_targets above, so a same-named file elsewhere still is not caught here
    either.

    CHV2-115 RC3 adds the directory-destination case for cp/mv, and nothing else. `cp x DIR/`
    and `mv x DIR/` write to `DIR/basename(x)`, a destination the old `tokens[-1]` check could
    never match because the token names a directory, not the store. The destination's own
    identity is checked FIRST and is what gates the branch; the source's basename is consulted
    only afterwards, to decide what the landed file would be called.
    """
    for seg in re.split(r"[;&|\n]", command):
        spans = cc.quoted_spans(seg)
        for m in cc.BASH_REDIRECT_RE.finditer(seg):
            if not cc.inside_quotes(m.start(), spans) and is_registry_path(m.group(1).strip('"').strip("'"), cwd):
                return True
        m = cc.BASH_TEE_RE.search(seg)
        if m and not cc.inside_quotes(m.start(), spans) and is_registry_path(m.group(1).strip('"').strip("'"), cwd):
            return True
        cpmv = GUARD_CPMV_RE.search(seg)
        rsync = GUARD_RSYNC_RE.search(seg)
        sed = cc.BASH_SED_INPLACE_RE.search(seg)
        trigger = cpmv or rsync or sed
        if trigger and not cc.inside_quotes(trigger.start(), spans):
            tokens = re.findall(r'"[^"]+"|\'[^\']+\'|\S+', seg)
            positional = [t for t in tokens if not t.startswith("-")]
            if positional:
                destination = positional[-1].strip('"').strip("'")
                if is_registry_path(destination, cwd):
                    return True
                # CHV2-177: rsync's directory-destination form (`rsync src DIR/`) writes
                # DIR/basename(src), same shape RC3 already closed for cp/mv -- same check,
                # extended to cover a rsync trigger too, not a parallel branch.
                if ((cpmv or rsync) and len(positional) >= 2 and is_registry_dir(destination, cwd)
                        and lands_on_registry_name(positional[-2].strip('"').strip("'"))):
                    return True
        dd = BASH_DD_RE.search(seg)
        if dd and not cc.inside_quotes(dd.start(), spans) and is_registry_path(dd.group(1).strip('"').strip("'"), cwd):
            return True
    return False


def main(data):
    tool_name = data.get("tool_name")
    sid, cwd = data.get("session_id"), data.get("cwd")

    if tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        fp = hc.target_path(data.get("tool_input"))
        if fp and is_registry_path(fp, cwd):
            hc.audit("SAFETY_DENY", {"guard": "registry_write_guard", "reason":
                      "direct-write-to-registry", "file_path": fp}, sid, cwd, severity="high")
            hc.set_rule(f"{RULE_ID}:direct-edit-denied")
            hc.deny(
                f"Blocked direct write to {Path(fp).name}: registry.db is the enforcement-plane "
                f"verification registry's authoritative store (ARCHITECTURE.md 34.0, "
                f"atlas-sonnet), written only through atlas.registry.write_path's validated "
                f"transactional API, never edited directly. A direct write here would bypass "
                f"D1's three mechanically-rejected clauses (dispatch-record role, single-use "
                f"evidence, component match) that make an assertion trustworthy at all."
            )
            return
        return

    if tool_name == "Bash":
        command = (data.get("tool_input") or {}).get("command", "") or ""
        if not command:
            return
        for target in cc.extract_bash_write_targets(command, cwd):
            if is_registry_path(str(target), cwd):
                hc.audit("SAFETY_DENY", {"guard": "registry_write_guard", "reason":
                          "direct-bash-write-to-registry", "command": command[:500]},
                         sid, cwd, severity="high")
                hc.set_rule(f"{RULE_ID}:bash-write-denied")
                hc.deny(
                    "Blocked Bash write to registry.db: the enforcement-plane verification "
                    "registry's authoritative store (ARCHITECTURE.md 34.0, atlas-sonnet) is "
                    "written only through atlas.registry.write_path's validated transactional "
                    "API, never edited directly."
                )
                return
        if bash_text_mentions_registry_write(command, cwd):
            hc.audit("SAFETY_DENY", {"guard": "registry_write_guard", "reason":
                      "bash-text-match-registry", "command": command[:500]}, sid, cwd,
                     severity="high")
            hc.set_rule(f"{RULE_ID}:bash-text-match-denied")
            hc.deny(
                "Blocked Bash command: it names registry.db alongside a write-shaped operator. "
                "The enforcement-plane verification registry's authoritative store is written "
                "only through atlas.registry.write_path's validated transactional API. If this "
                "command doesn't actually write to that path, run it yourself in a terminal."
            )
            return


if __name__ == "__main__":
    hc.run(main)
