#!/usr/bin/env python3
"""Shared component-resolution library for Foreman's two hard PreToolUse gates
(architecture_gate.py, goals_freeze_gate.py). The fuller tier-triage / config-registry /
payload-contract predicates from v0.2 SS04 are proven in coupling_ext_proof.py but not wired
as live hooks yet (out of scope for this pass, per the v0.3 work order's "what NOT to touch").

CORRECTION FROM v0.2, found while building this, not while designing it: v0.2 SS01 claimed
both hard hooks share ONE predicate, isImplementationPath(). Building it for real exposed why
that can't be literally true. isImplementationPath() resolves a path to a DECLARED component --
but before ARCHITECTURE.md exists, the component map is empty by construction, so every path
resolves to "not implementation" and the architecture gate could never fire on the exact
bootstrap case it exists to catch (writing real implementation code before ARCHITECTURE.md
exists at all). That's a real bug, not a hypothetical -- confirmed by hand-tracing the
predicate against an empty component map, then closed here.

The fix: two related predicates, not one, kept in the same module so they can't silently drift
apart on the parts they DO share (control-filename exclusion, .foreman/ scoping):
  - is_pre_architecture_scope() -- architecture_gate.py. No component map needed: gated if the
    path is nested at least one directory below the project root and isn't a control file. A
    directory-depth heuristic, not component resolution, because before ARCHITECTURE.md exists
    there are no declared components to resolve against.
  - is_implementation_path()   -- goals_freeze_gate.py. Component-map-based, as before. Valid
    by the time this gate is reached, because architecture_gate.py already forced ARCHITECTURE.md
    to exist first -- so a component map is guaranteed to be populated (or the write was already
    denied upstream).
See foreman-design.html v0.3 SS01/SS09 for the corrected write-up.

SELF-SCOPING, ON PURPOSE. These gates register in a project's own .claude/settings.json, not
~/.claude/settings.json -- but even so, find_project_root() returning None (no .foreman/
marker) is the first thing both gates check, so a copy-paste into any other project's settings
fails safe (silent, not deny) rather than blocking work that never opted in.

Component-map parsing is deliberately NOT a YAML parser. ARCHITECTURE.md's component block
(see foreman-design.html SS04) is one name-per-line with a JSON array value -- valid JSON on the
right-hand side -- so a regex extracts the line and json.loads() the array. Adding a real YAML
dependency for one array-of-strings-per-line format would be the "second parser" the whole
coupling-engine design argues against building.

Component globs are prefix matchers, not full glob semantics. Every glob in this design's own
examples is "dir/**" (match everything under a directory) -- fnmatch's "*" already matches
across "/", which is the wrong semantics for a directory boundary, so implementing real glob
matching would be solving a problem the design doesn't have yet. If a future ARCHITECTURE.md
needs a real glob (e.g. "**/*.py" under a component), this will need revisiting; documented
here so that need is visible when it happens rather than silently mismatching.
"""

import json
import os
import re
import sys
from pathlib import Path

COMPONENT_BLOCK_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(\[.*\])\s*$")

# Filenames Foreman treats as pre-implementation control artifacts, never gated regardless of
# which component directory they happen to sit in.
CONTROL_FILENAMES = {"ARCHITECTURE.md", "ARCHITECTURE-REVIEW.md", "SCOPE.md", "GOALS.json"}


def find_project_root(cwd):
    """Walk up from cwd looking for a .foreman/ marker directory. None if not found.

    This is the opt-in gate for both hard hooks: a project that never created .foreman/ is a
    project that never adopted Foreman, and must never be blocked by it.
    """
    if not cwd:
        return None
    p = Path(cwd).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".foreman").is_dir():
            return candidate
    return None


def parse_component_map(project_root):
    """Read the component: [glob, ...] block from ARCHITECTURE.md. {} if the file or block
    doesn't exist yet -- that is the normal pre-architecture state, not an error."""
    arch_path = Path(project_root) / "ARCHITECTURE.md"
    try:
        text = arch_path.read_text()
    except OSError:
        return {}
    in_block = False
    seen_first_block = False
    components = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```yaml components"):
            if seen_first_block:
                # A second '```yaml components' block exists after the first has already
                # closed. Only the first block's declarations are ever used (kept, not
                # merged -- see DEVH-41: merging would be a bigger behavior change than this
                # fix is scoped to make); this disclosure is what was previously missing.
                # Disclosed, not swallowed -- same precedent the malformed-JSON branch below
                # already uses: silently dropping a second block's component declarations is
                # the exact "looks alive, does nothing" failure this whole project exists to
                # catch (found live: the parse_component_map bug an earlier ARCHITECTURE.md
                # write hit had no warning at all when it happened).
                print(
                    f"[component_coupling] {arch_path} has more than one "
                    f"'```yaml components' block; only the first is used, later block(s) "
                    f"are ignored", file=sys.stderr,
                )
                break
            in_block = True
            continue
        if in_block and stripped.startswith("```"):
            in_block = False
            seen_first_block = True
            continue
        if not in_block:
            continue
        m = COMPONENT_BLOCK_RE.match(stripped)
        if not m:
            continue
        name, globs_json = m.groups()
        try:
            globs = json.loads(globs_json)
        except json.JSONDecodeError as exc:
            # Disclosed, not swallowed: a malformed line here silently un-declares a component,
            # which means the gate silently stops applying to that component's files -- the
            # exact "looks alive, does nothing" failure this whole project exists to catch.
            print(f"[component_coupling] ARCHITECTURE.md component line for {name!r} is not "
                  f"valid JSON ({exc}); this component will not be recognized until it's fixed",
                  file=sys.stderr)
            continue
        if isinstance(globs, list) and all(isinstance(g, str) for g in globs):
            components[name] = globs
        else:
            print(f"[component_coupling] ARCHITECTURE.md component {name!r} value is not a "
                  f"list of strings; skipped", file=sys.stderr)
    return components


def _prefix_of(glob):
    """"ticketing/store/**" -> "ticketing/store/". Strips trailing globstar/star only."""
    return glob.rstrip("*")


def component_of(rel_path, component_map):
    """Which declared component owns this project-relative path. None if it matches none --
    that is not an error, it means the path isn't inside any declared component yet."""
    rel_path = str(rel_path)
    best = None
    best_len = -1
    for name, globs in component_map.items():
        for g in globs:
            prefix = _prefix_of(g)
            if rel_path.startswith(prefix) and len(prefix) > best_len:
                best, best_len = name, len(prefix)
    return best


def component_root(name, component_map):
    """Directory prefix for a declared component, e.g. "store" -> "ticketing/store/".

    A bare-filename glob (no trailing wildcard, e.g. "hooks/architecture_gate.py") is a real,
    intended shape -- a single-file component -- not an error, so this can't just assume every
    glob is a directory prefix. Found live during an early design-and-scope pass: ARCHITECTURE.md
    declared the three already-shipped hooks as bare files, and the old version of this function
    returned the file path itself as the "root", which goals_freeze_gate.py then joined with
    "GOALS.json" to get a path like ".../architecture_gate.py/GOALS.json" -- uncreatable, since
    architecture_gate.py is a regular file. That's a permanent deadlock: once wired, editing any
    of those three files would deny forever, with a remedy the gate itself can't be satisfied by.
    Caught by architecture review before any hook was wired, not found live in production.
    """
    globs = component_map.get(name) or []
    if not globs:
        return None
    prefix = _prefix_of(globs[0])
    if prefix.endswith("/"):
        return prefix
    parent = str(Path(prefix).parent)
    return "" if parent == "." else parent + "/"


def _true_case(path):
    """Best-effort real on-disk casing for each path component that already exists; any
    component that doesn't exist yet (e.g. a file about to be created) is left exactly as
    typed, since there's no prior on-disk casing to disagree with.

    On a case-insensitive, case-preserving filesystem (macOS APFS default),
    Path.resolve() does not case-normalize -- MONITORING/dash.py and monitoring/dash.py resolve
    to the same inode but stay different strings. component_of()'s prefix match is a plain
    str.startswith(), so a retyped-case write silently fails to match its own declared
    component and goals_freeze_gate.py treats it as ungated instead of denying it -- a bypass,
    not a false positive, so it fails in the dangerous direction.

    This resolves real casing instead of casefolding the comparison so behavior on a genuinely
    case-SENSITIVE filesystem (Linux) is unchanged: an exact match always wins first below, so
    Component/x.py and component/x.py stay distinct there, exactly as they already are on disk.
    A directory this can't list (permission error, mid-delete) falls back to the typed casing
    rather than raising -- the existing, already-established fail posture in this module.
    """
    parts = path.parts
    if not parts:
        return path
    resolved = Path(parts[0])
    for part in parts[1:]:
        candidate = resolved / part
        try:
            if candidate.exists():
                match = None
                for entry in os.listdir(resolved):
                    if entry == part:
                        match = entry
                        break
                    if match is None and entry.lower() == part.lower():
                        match = entry
                resolved = resolved / (match if match is not None else part)
            else:
                resolved = candidate
        except OSError:
            resolved = candidate
    return resolved


def _relative_or_none(file_path, project_root):
    try:
        resolved = _true_case(Path(file_path).resolve())
        root = _true_case(Path(project_root).resolve())
        return resolved.relative_to(root)
    except ValueError:
        return None  # outside the project entirely -- never gated by either predicate


def _is_control(rel):
    """Shared exclusion for both predicates: Foreman's own control artifacts, .foreman/ itself,
    and any dotfile/dotdir at the project root (.claude/, .git/, .github/, .vscode/, ...).

    The dotdir exclusion is a real fix, not a style choice -- found while reviewing this file
    after building it. Without it, is_pre_architecture_scope() would gate .claude/settings.json
    itself (one directory deep, not a declared control filename), meaning a project could never
    touch its own hook registration until ARCHITECTURE.md and a review existed -- exactly the
    kind of bootstrap deadlock the earlier correction in this module's docstring exists to avoid.
    Dotdirs are tooling/config by convention, never implementation, so excluding the whole class
    is the right level of fix rather than special-casing .claude/ alone.
    """
    if rel.name in CONTROL_FILENAMES:
        return True
    return rel.parts[0].startswith(".")


def is_implementation_path(file_path, project_root, component_map):
    """goals_freeze_gate.py's predicate. True only for a write inside a DECLARED component
    directory, on a file that isn't one of Foreman's own control artifacts. Requires
    component_map to already be populated -- valid post-architecture-gate, not before."""
    rel = _relative_or_none(file_path, project_root)
    if rel is None or _is_control(rel):
        return False, None
    comp = component_of(str(rel), component_map)
    return (comp is not None), comp


def is_pre_architecture_scope(file_path, project_root):
    """architecture_gate.py's predicate. No component map needed -- gated if the path is
    nested at least one directory below project_root and isn't a control file or dotdir.
    Deliberately coarser than component resolution: before ARCHITECTURE.md exists there is
    nothing to resolve against, so this has to work off directory depth alone. Top-level project
    files (README, LICENSE, pyproject.toml, ARCHITECTURE.md/SCOPE.md themselves) and anything
    under a dotdir stay writable so early bootstrap and tooling config aren't blocked; anything
    else nested one level down is treated as implementation code in waiting."""
    rel = _relative_or_none(file_path, project_root)
    if rel is None or _is_control(rel):
        return False
    return len(rel.parts) > 1


# Fix: the `>` must sit at a real shell-operator position -- start of segment or
# preceded by whitespace -- not merely anywhere `(?<!\d)` allowed it through. Before this,
# an angle-bracket embedded in ordinary command TEXT with no shell meaning at all (an HTML
# closing tag like `</div>`, a `-->` comment-close, a bare `>` in a comparison snippet)
# still matched, because the only thing excluded was a digit immediately before it (the
# fd-redirect case, `2>`). `</div>foo` has `v` before the `>`, not a digit, so the old regex
# read it as a redirect into a garbage target built from whatever followed. A real shell
# redirect is always preceded by whitespace or starts the command; text-internal `>` never
# is. This does not depend on the quote-span check below (that check only excludes a `>`
# sitting *inside* a quoted argument) -- this case is unquoted command text, the quote check
# alone does not see it. Same fail-toward-NOT-gating direction as the rest of this function:
# tightening the precedent narrows false positives and cannot introduce a new false negative
# beyond the pre-existing space-before-`>` convention essentially every real agent-issued
# command already follows.
BASH_REDIRECT_RE = re.compile(r'(?:^|(?<=\s))(?<!\d)>{1,2}\s*(?!&\d)("[^"]+"|\'[^\']+\'|\S+)')
BASH_TEE_RE = re.compile(r'\btee\b(?:\s+-a)?\s+("[^"]+"|\'[^\']+\'|\S+)')
# Review finding (FATAL, live-confirmed): an earlier version of this pass added
# install/ln here to catch `install -m 0755 src bin/ffmpeg` (the real gif-smith incident's
# actual command) -- but "install" is also a common PACKAGE MANAGER subcommand (npm/pip/
# yarn/brew/cargo/gem/apt/make install), and adding it to the trigger this shared function
# uses caused architecture_gate.py and goals_freeze_gate.py -- both already shipped and
# live-enforcing in every Foreman-gated project -- to falsely DENY ordinary `npm install
# @scope/pkg` and `pip install -e .` commands. Reverted to cp|mv only, its original,
# already-verified scope. dependency_provenance_gate.py's own install/ln coverage no longer
# needs a shared or local trigger-regex extension at all -- its detection was rearchitected
# (clint-eastwood's review, round 2) to be manifest-driven: it walks the corpus for lines
# that mention one of its OWN declared artifact paths together with a write-verb signal,
# rather than trying to extract every possible write target the way this function does. Not
# shared here again without its own regression test against both existing consumers first
# (a separate, broader question covers whether recursion-style improvements belong in the
# shared function at all).
BASH_CPMV_RE = re.compile(r"\b(?:cp|mv)\b")
BASH_SED_INPLACE_RE = re.compile(r"\bsed\b[^\n]*-i\b")

# Resolution: ported from dependency_provenance_gate.py's INVOKED_SCRIPT_RE /
# MAX_SCRIPT_READ_BYTES verbatim (duplicated, not imported -- this module has no cross-hook
# import of that sibling, matching its own established convention of keeping each hook
# self-contained). That gate's own comment on this pattern: deliberately loose, because
# over-matching there just means reading one more script's text (cheap, read-only). The same
# reasoning applies here for the SAME direction of error -- recursing into more candidate
# scripts only adds write-target COVERAGE (closes false negatives), it does not change this
# function's existing false-positive risk profile, which lives entirely in the redirect/tee/
# cpmv/sed patterns applied to whatever text gets scanned. See extract_bash_write_targets's
# docstring for why this was safe to port where the "install"/"ln" trigger-regex extension
# (the review FATAL noted above) was not: that was a NEW match class on the outer
# command's own text, which directly risked false-denying ordinary package-manager commands.
# This is recursion into what's already scanned, using patterns already live in this file.
INVOKED_SCRIPT_RE = re.compile(
    r'(?:\b(?:bash|sh|source)\s+)?("[^"]+\.sh"|\'[^\']+\.sh\'|\S+\.sh)\b'
)
MAX_SCRIPT_READ_BYTES = 1_000_000


def _invoked_script_segments(seg, project_root):
    """One level of recursion into a directly-invoked local .sh script: if `seg`
    invokes one, return its own lines as additional segments to scan for write targets, so a
    write hidden inside `bash wrapper.sh` is no longer invisible to this function -- the
    confirmed, reproduced gap this exists to close (a known fixture: a command with
    no write pattern in its own text, whose invoked script writes to a gated path, produced
    zero denial from either hard gate).

    Bounded exactly like the pattern above's own recursion: ONE level only (an invoked script invoking
    a third is not chased), the script must resolve INSIDE project_root (refuses a
    symlinked-out or absolute-path escape -- reading a script to decide whether to recurse
    into it is the safe direction for resolve()+relative_to(), unlike using it to decide
    whether a WRITE lands inside the project), must be a real file under the size cap, and
    any read/resolve failure returns no extra segments -- fails toward NOT gating, same
    direction as the rest of this function. project_root=None (not a Foreman project, or the
    caller has no cwd to resolve one from) also returns no extra segments; there is nothing to
    contain the read to.
    """
    if project_root is None:
        return []
    m = INVOKED_SCRIPT_RE.search(seg)
    if not m:
        return []
    raw = m.group(1).strip('"').strip("'")
    script_path = Path(raw)
    base = project_root
    try:
        if not script_path.is_absolute():
            script_path = base / script_path
        script_path = script_path.resolve()
        script_path.relative_to(Path(project_root).resolve())
        if not script_path.is_file() or script_path.stat().st_size > MAX_SCRIPT_READ_BYTES:
            return []
        return script_path.read_text().splitlines()
    except (OSError, ValueError, RuntimeError, UnicodeDecodeError):
        return []  # unreadable, outside the project, or too large -- skip, don't crash


CD_SEGMENT_RE = re.compile(r"^\s*cd\b")
# Captures the cd argument so an ABSOLUTE cd can become a known resolution base for later
# relative targets in the same command, instead of unconditionally dropping them (found by an
# adversarial review 2026-08-22: `cd /proj && echo x > ARCHITECTURE.md` silently walked past the
# gate because a relative target after ANY cd -- absolute or relative -- was dropped). A
# RELATIVE cd keeps the original drop behavior: it still cannot be resolved without
# interpreting shell control flow.
CD_ARG_RE = re.compile(r"^\s*cd\s+(\S+)")


def _quoted_spans(text):
    """Character ranges of `text` sitting inside a single- or double-quoted string.

    A write-detection regex's TRIGGER character (the `>`, the word "tee",
    "cp"/"mv", the `-i` flag) must be checked against these before being trusted -- the shell
    never interprets `>` specially inside quotes, so a `>` appearing only inside a quoted
    argument to some OTHER command (e.g. `comment --body "denied on > web/index.html"`) is
    prose, not a redirect. Reproduced: without this check, extract_bash_write_targets could
    not tell a genuine in-repo write from a TESSERA comment merely QUOTING one -- both
    resolved to the identical target.

    Not a shell tokenizer: no backslash-escape tracking inside double quotes, no $'...' or
    backtick/command-substitution awareness. Good enough for the reproduced case (a literal
    quoted string argument) without claiming completeness -- same posture as the rest of this
    function, which is explicit that it fails toward NOT gating on anything it can't model.
    """
    spans = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            end = text.find(ch, i + 1)
            if end == -1:
                spans.append((i, n))  # unterminated quote: treat the rest as quoted
                break
            spans.append((i, end + 1))
            i = end + 1
        else:
            i += 1
    return spans


def _inside_quotes(pos, spans):
    return any(start <= pos < end for start, end in spans)


def extract_bash_write_targets(command, cwd):
    """Best-effort file paths a Bash command would write or overwrite. Regex-based, not a
    shell parser -- mirrors guard_destructive.py's existing convention (must be fast, pure
    regex, no network) rather than adding a shell-parsing dependency for this.

    Exists because an earlier review found the real gap here: architecture_gate.py and
    goals_freeze_gate.py only ever matched Edit|Write, so an agent denied on Write could walk
    straight past both gates with `printf ... > path` -- reproduced 3/3 trials, zero Foreman
    hook involvement on the Bash path, confirmed on disk and in the verdict ledger. This covers
    the patterns that reproduction and ordinary agent behavior actually exercise: shell
    redirection (>, >>, skipping fd-duplication like `2>&1`), `tee [-a]`, cp/mv's destination
    argument, and `sed -i`'s in-place target -- now including ONE level of recursion into a
    directly-invoked local .sh script's own body, so `bash wrapper.sh` can no
    longer hide a write neither hard gate would otherwise see; a quote-awareness check
    so a `>` appearing only inside a quoted argument to some OTHER command
    is not read as a real redirect; and a `cd`-safety check so a RELATIVE
    target in a segment that follows a `cd` earlier in the same command -- which this function
    cannot safely resolve without interpreting shell control flow -- is dropped rather than
    wrongly resolved against the session's own unchanged cwd. Both of those were the same
    reproduced defect: `cd SCRATCH && printf 'hi' > web/index.html` resolved to the identical
    (wrong) in-repo target as literal prose merely quoting that same string, indistinguishably
    from a genuine in-repo write -- the gate could not tell any of the three apart.

    Known, disclosed blind spots -- not solved further here: heredocs piped into a subshell,
    `python3 -c "...open(path,'w')..."`, tar/unzip/git-checkout style extraction-as-write, and
    exotic quoting (backslash escapes inside double quotes, $'...', command substitution). A
    gap here fails toward NOT gating (same direction a shell-parser mistake would fail), so
    this raises the bar without claiming to be complete.
    """
    if not command:
        return []
    project_root = find_project_root(cwd)
    # (raw_target, cd_unsafe) pairs. cd_unsafe=True means a `cd` appeared in an earlier
    # segment of THIS SAME command -- see the cd-safety check in the docstring above -- so a
    # RELATIVE target here cannot be safely resolved and gets dropped at resolution time,
    # never at collection time, because an ABSOLUTE target in the same segment is unaffected
    # by an unknown cwd and must still be gated.
    # (raw_target, cd_unsafe, cd_base) triples. cd_base is the resolved absolute directory of
    # the most recent ABSOLUTE `cd` seen in an earlier segment of this same command, or None if
    # no cd has been seen yet or the most recent one was relative (cd_unsafe=True in that case,
    # matching the original drop behavior).
    raw_targets = []
    # Review finding: a bare newline ends a command in real shell semantics and was
    # NOT in this split class -- for a genuinely multi-line command string (a heredoc body, a
    # multi-statement command joined by literal newlines rather than ';') this collapsed
    # everything after the first line into one giant segment, and the cp/mv tail-token
    # heuristic then grabbed the last token of the WHOLE blob instead of the actual
    # destination. Confirmed near-inert for this function's two existing consumers
    # (architecture_gate.py, goals_freeze_gate.py), which only ever see a single logical Bash
    # command's own text -- this only changes behavior for a command that already spans
    # multiple lines, which splitting more finely can only make MORE precise, never less.
    segments = list(re.split(r"[;&|\n]", command))
    cd_seen = False
    cd_base = None
    for seg in segments:
        if CD_SEGMENT_RE.match(seg):
            cd_seen = True
            arg_match = CD_ARG_RE.match(seg)
            arg = arg_match.group(1).strip('"').strip("'") if arg_match else ""
            if arg and Path(arg).is_absolute():
                try:
                    cd_base = Path(arg).resolve()
                except (OSError, RuntimeError):
                    cd_base = None  # unresolvable absolute arg -- fall back to unsafe-drop
            else:
                cd_base = None  # relative (or unparseable) cd -- unresolvable, same as before
            continue  # a bare `cd` has no write pattern of its own to scan for
        # Recursion happens BEFORE the outer loop's own scan below reuses `seg` for the
        # invoked-script detection, so a script's own lines get pattern-scanned the identical
        # way the outer command's lines do -- one code path, not two divergent ones. A `cd`
        # INSIDE an invoked script is not modeled here, matching the disclosed scope
        # for the same recursion (dependency_provenance_gate.py's build_events docstring).
        segments_to_scan = [seg, *_invoked_script_segments(seg, project_root)]
        for s in segments_to_scan:
            spans = _quoted_spans(s)
            for m in BASH_REDIRECT_RE.finditer(s):
                if not _inside_quotes(m.start(), spans):
                    raw_targets.append((m.group(1), cd_seen, cd_base))
            m = BASH_TEE_RE.search(s)
            if m and not _inside_quotes(m.start(), spans):
                raw_targets.append((m.group(1), cd_seen, cd_base))
            cpmv = BASH_CPMV_RE.search(s)
            sed = BASH_SED_INPLACE_RE.search(s)
            trigger = cpmv or sed
            if trigger and not _inside_quotes(trigger.start(), spans):
                # Quote-aware tokenizer, same reasoning as the redirect/tee patterns above --
                # seg.split() would break a quoted destination containing a space into several
                # tokens and grab the wrong tail one.
                tokens = re.findall(r'"[^"]+"|\'[^\']+\'|\S+', s)
                if tokens and not tokens[-1].startswith("-"):
                    raw_targets.append((tokens[-1], cd_seen, cd_base))

    base = Path(cwd) if cwd else Path.cwd()
    resolved = []
    for raw, cd_unsafe, target_cd_base in raw_targets:
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            continue
        candidate = Path(raw)
        if cd_unsafe and not candidate.is_absolute():
            if target_cd_base is not None:
                # An ABSOLUTE cd preceded this target -- resolve against the known real
                # directory instead of dropping it (M1 fix, see CD_ARG_RE above).
                try:
                    resolved.append((target_cd_base / candidate).resolve())
                except (OSError, RuntimeError):
                    continue
                continue
            continue  # relative cd, still unresolvable -- unchanged behavior
        try:
            resolved.append((candidate if candidate.is_absolute() else base / candidate).resolve())
        except (OSError, RuntimeError):
            continue  # unresolvable target: drop it, fail toward NOT gating (see docstring)
    return resolved
