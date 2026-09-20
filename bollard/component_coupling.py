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
from collections import namedtuple
from pathlib import Path

COMPONENT_BLOCK_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(\[.*\])\s*$")

# FORE-54/FORE-99: the interfaces block's value is a quoted-key JSON object, not the array
# COMPONENT_BLOCK_RE expects -- confirmed against every real ```yaml interfaces block on this
# machine (e.g. bay-area's ARCHITECTURE.md: "Interface values are quoted-key JSON objects,
# required by json.loads. The bare-key form ... is silently skipped by the parser").
INTERFACE_BLOCK_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(\{.*\})\s*$")

# A2 (QUALITY-BAR.md 2.1): the source-file universe a coverage check walks. An extension
# allowlist, not "every tracked file" -- measured live 2026-08-23 that counting every tracked
# file (including README/LICENSE/HANDOFF/go.mod/go.sum/*.md) floods the uncovered set with
# project meta-files no component glob was ever meant to own, which would make the ratio never
# reach 1.0 regardless of real architecture drift -- exactly the "invisible by disuse because
# it never says anything true" failure QUALITY-BAR.md's F4 finding already warns about at a
# different layer. Restricting to real source extensions reproduced the earlier session's own
# spot-measurement exactly on 2 of 3 real projects (hyphy: 6 uncovered, atlas-sonnet: 5
# uncovered, both exact) and within one file on the third (bay-area: 17 vs. 16) -- see FORE-54's
# ticket comment for the full reconciliation.
SOURCE_EXTENSIONS = {
    ".py", ".go", ".js", ".jsx", ".ts", ".tsx", ".sh", ".bash", ".rb", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".kt", ".swift", ".pl", ".lua",
    ".sql", ".applescript", ".scpt", ".mjs", ".cjs", ".m", ".mm",
    # FORE-510 (Opus review F3, Bob's fix-not-disclose pass): measured gaps closed rather
    # than disclosed -- .zsh was absent on a zsh-primary machine while .sh/.bash were present,
    # and these four others were the review's remaining named holes. Adding extensions widens
    # A2's coverage universe and strict mode's deniable set; zero projects had opted into
    # strict at the time this landed, so the live effect at landing is coverage-report-only.
    ".zsh", ".php", ".ipynb", ".scala", ".tf",
}

# Directories walk_source_files() never descends into, beyond the dotdir-anywhere rule below.
# None of these can hold a project's own declared-component source; they hold generated output
# or third-party code the project doesn't own and A2 was never meant to gate.
NOISE_DIR_NAMES = {
    "node_modules", "__pycache__", "vendor", "dist", "build", "target",
    ".mypy_cache", ".pytest_cache", ".tox", "venv", ".venv", "env",
}

CoverageReport = namedtuple("CoverageReport", "total covered uncovered ratio malformed_ungated")

# Filenames Foreman treats as pre-implementation control artifacts, never gated regardless of
# which component directory they happen to sit in.
#
# FORE-238/FORE-205: PRD.md added 2026-08-28. REQ-16 establishes PRD authorship as a
# pre-architecture stage, same as SCOPE.md, but the exemption never covered it -- so a nested
# docs/PRD.md was gated exactly like implementation code, forcing tessera-v2 to relocate its
# live PRD.md to project root just to keep writing to it (docs/PRD.md:2026-08-27 tombstone).
# That relocation then broke ship_readiness_gate.py's hardcoded docs/PRD.md read (FORE-238) --
# two hooks disagreeing about where PRD.md lives, with nothing checking they agreed. Adding it
# here, at any depth like the other three, is the root-cause fix; ship_readiness_gate.py's
# matching path-resolution fix is the companion half.
CONTROL_FILENAMES = {"ARCHITECTURE.md", "ARCHITECTURE-REVIEW.md", "SCOPE.md", "GOALS.json",
                      "PRD.md"}

# FORE-510: strict component scope -- the default-deny flip for writes that resolve to NO
# declared component. Per-project OPT-IN via marker file, same pattern as architecture_gate.py's
# REVIEW_BINDING_MARKER, and for the same measured reason that gate's own comment records for
# review binding: flipping this unconditionally would have immediate live blast radius. Measured
# 2026-09-07, corrected by the Opus cold-falsification review's independent re-measurement with
# this same module (the first pass undercounted): 71 of 81 .foreman-marked projects on this
# machine register both hard gates via their .claude/settings.json, 74,767 source files across
# them currently resolve to no declared component, and 7 projects have ZERO declared components
# -- the review live-fired this gate at real uncovered paths in the three largest (14k-28k
# source files each; deny, deny, deny), so an unconditional flip would hard-deny essentially
# every source write there on day one (the CHV2-22 incident class, at scale). A project that has
# NOT opted in is no longer silent about the gap either: goals_freeze_gate.py records an
# `undeclared-component-path-unenforced` rule for every write the flip WOULD have denied, so
# non-adoption is measurable the same way review-binding adoption already is.
STRICT_SCOPE_MARKER = ".foreman/strict-component-scope"

# FORE-510: the explicit out-of-scope allowlist that makes strict mode livable -- a real,
# reasoned declaration that a region of the tree is deliberately NOT a component (docs
# tooling, test scaffolding, scratch work), replacing the implicit default-allow the flip
# removes. Same "<path> -- <reason>" line convention as .foreman/ungated.txt, with one
# addition: an entry ending in "/" is a directory-prefix zone covering everything under it;
# any other entry is an exact project-relative file path. An entry without a reason is
# malformed and NEVER honored -- in strict mode a malformed zone fails toward DENY, matching
# parse_ungated()'s own "a bare path with nothing explaining it satisfies nothing" posture.
OUT_OF_SCOPE_ZONES_RELPATH = ".foreman/out-of-scope-zones.txt"


def resolve_keeping_leaf(path):
    """Resolve a write target's DIRECTORY without following the target itself.

    FORE-588. The whole-path .resolve() this replaces follows every symlink INCLUDING the leaf,
    which is exactly wrong for a write target: the thing being written is the path as named, not
    whatever that name currently points at. A governed file inside a project, replaced by a
    symlink pointing outside it, resolved to the OUTSIDE path -- so jurisdiction landed outside
    the project and the gate went silent on a write to a path it governs.

    Measured on the live tree before the fix, one governed project and one outside directory:

        .resolve().parent   ->  <outside>            the leaf followed, jurisdiction lost
        .parent.resolve()   ->  <project>/src        the leaf kept, jurisdiction correct

    The parent IS resolved, deliberately. A symlinked DIRECTORY component in the path is an
    ordinary filesystem arrangement and must still land in whatever project really holds it;
    only the final component is left literal, because only the final component is the thing the
    command names as its destination.

    Also the right answer for a target that does not exist yet, which is the common case for a
    write: the leaf need not exist for this to work, and the parent normally does.
    """
    path = Path(path)
    return path.parent.resolve() / path.name


def project_root_for_target(target_path, cwd=None):
    """The Foreman project that owns the file at `target_path`, or None if it is not inside one.

    THE JURISDICTION RULE, and the one nine tickets were filed against. A gate decides what it
    governs from the TARGET, never from the writer's payload cwd. find_project_root(cwd) answers
    "which project is the writer standing in", which is a different question and is only
    accidentally the same answer -- it diverges the moment a session writes across a project
    boundary, which is ordinary behaviour, not an evasion.

    Fired before this existed (stage_order_gate, nested child project inside a parent, both real
    Foreman projects with their own pipelines):

        cwd=CHILD,  target in CHILD/src    DENY      the gate works when cwd matches
        cwd=PARENT, target in PARENT/src   DENY      the ordinary case
        cwd=PARENT, target in CHILD/src    SILENT    the cross-project fail-open
        cwd=CHILD,  target out of scope    SILENT    the negative control

    The child's own pipeline never governed its own file. Same shape ledger_write_guard's
    is_ledger_path() already avoids by anchoring on the target's own parent directory, and the
    same fix FORE-613 and FORE-614 made one gate at a time.

    Resolves the target first, so a relative path, a `..` segment or a symlinked parent all land
    in the project that actually holds the file. `.parent` rather than the file itself because a
    write target need not exist yet -- the directory it goes in does.

    Returns None rather than falling back to the writer's project. A caller that cannot find a
    governing project should decide that case explicitly; silently substituting the writer's
    project is the defect this function exists to remove, and a fallback would reintroduce it
    under a new name.
    """
    if not target_path:
        return None
    try:
        candidate = Path(target_path)
        if not candidate.is_absolute():
            # A RELATIVE target resolves against the PAYLOAD's cwd, never the hook process's own.
            # Caught by stage_order_gate's test_b2_relative_file_path_resolves_against_payload_cwd
            # after a first version of this helper used a bare .resolve() -- which is the exact
            # defect that gate's own resolve_relative_path() had already been fixed for, in its
            # own docstring, and which I reintroduced one layer up inside the helper meant to fix
            # a different jurisdiction bug. cwd for RESOLUTION is a use the target genuinely
            # needs; cwd for JURISDICTION is the one this function exists to remove.
            candidate = (Path(cwd) if cwd else Path.cwd()) / candidate
        # FORE-588. This was candidate.resolve().parent -- the WHOLE path resolved, then the
        # parent taken. For a governed file that is really a symlink pointing out of the project,
        # that followed the leaf first and handed back the OUTSIDE directory, so this function
        # returned None and every caller treated a write it governs as none of its business.
        # My own defect, introduced with this helper in FORE-568, and the docstring above made it
        # harder to see rather than easier: it explains why the PARENT is used (a write target
        # need not exist yet) and reads as if that settled the leaf question too. It did not.
        # Reproduced live before fixing, both spellings side by side, on the same symlink.
        return find_project_root(str(resolve_keeping_leaf(candidate).parent))
    except (OSError, RuntimeError, ValueError):
        return None


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
    """FORE-339 fix. Returns (components, malformed).
    `malformed` is a list of {name, reason} dicts for every line that named a component but
    failed to parse or failed the shape check -- callers MUST inspect it rather than trust an
    empty-looking components dict."""
    arch_path = Path(project_root) / "ARCHITECTURE.md"
    try:
        text = arch_path.read_text()
    except OSError:
        return {}, []
    in_block = False
    components = {}
    malformed = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```yaml components"):
            in_block = True
            continue
        if in_block and stripped.startswith("```"):
            # FORE-260 instance 2: this used to `break` the whole scan on the first closing
            # fence, silently dropping every later ```yaml components block in the file. There
            # is no rule limiting a project to one such block, so a second block's components
            # went undeclared with no error -- the same "looks alive, does nothing" shape this
            # module's own malformed-line handling exists to avoid. Reset and keep scanning for
            # a possible second opening fence instead of stopping.
            in_block = False
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
            malformed.append({"name": name, "reason": f"invalid JSON: {exc}"})
            continue
        if isinstance(globs, list) and all(isinstance(g, str) for g in globs):
            components[name] = globs
        else:
            print(f"[component_coupling] ARCHITECTURE.md component {name!r} value is not a "
                  f"list of strings; skipped", file=sys.stderr)
            malformed.append({"name": name, "reason": "value is not a list of strings"})
    return components, malformed


def parse_interfaces_map(project_root):
    """A1 (QUALITY-BAR.md 2.1): "parse_interfaces_map() a dict" -- read the
    `name: {"producer": ..., "consumer": ..., ["trust": ...]}` block from ARCHITECTURE.md's
    ```yaml interfaces fence. {} if the file or block doesn't exist yet, same "absence is the
    normal pre-declaration state, not an error" posture as parse_component_map().

    Same regex-plus-json.loads() convention as parse_component_map(), for the same reason: one
    more real YAML parser is not worth adding for one object-per-line format. Values MUST be
    quoted-key JSON -- confirmed against every real interfaces block on this machine, and
    matches bay-area's own ARCHITECTURE.md comment on the same point: "the bare-key form ... is
    not valid JSON and is silently skipped by the parser, which un-declares the interface with
    no error anywhere."
    """
    arch_path = Path(project_root) / "ARCHITECTURE.md"
    try:
        text = arch_path.read_text()
    except OSError:
        return {}, []
    in_block = False
    interfaces = {}
    malformed = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```yaml interfaces"):
            in_block = True
            continue
        if in_block and stripped.startswith("```"):
            break
        if not in_block:
            continue
        m = INTERFACE_BLOCK_RE.match(stripped)
        if not m:
            continue
        name, obj_json = m.groups()
        try:
            obj = json.loads(obj_json)
        except json.JSONDecodeError as exc:
            # Same disclosure posture as parse_component_map()'s own malformed-line handling:
            # a bad line here silently un-declares the interface, so it prints rather than
            # swallows.
            print(f"[component_coupling] ARCHITECTURE.md interface line for {name!r} is not "
                  f"valid JSON ({exc}); this interface will not be recognized until it's fixed",
                  file=sys.stderr)
            malformed.append({"name": name, "reason": f"invalid JSON: {exc}"})
            continue
        if isinstance(obj, dict) and "producer" in obj and "consumer" in obj:
            interfaces[name] = obj
        else:
            print(f"[component_coupling] ARCHITECTURE.md interface {name!r} value is missing "
                  f"'producer' and/or 'consumer'; skipped", file=sys.stderr)
            malformed.append({"name": name, "reason": "missing producer and/or consumer"})
    return interfaces, malformed


def walk_source_files(project_root):
    """Every real file under project_root whose extension is in SOURCE_EXTENSIONS, as a
    project-relative string. The source-file universe A2's coverage check walks.

    Deliberately NOT git-aware -- no `git ls-files`, no `.gitignore` parsing. A git-tracked-only
    universe would create a real bypass for exactly the threat this check exists to catch: an
    agent writes real, uncommitted implementation code outside any declared component, and a
    tracked-only coverage check would never see it until (if ever) it gets committed. This has
    to see the working tree as it exists right now, at PreToolUse time, possibly before the
    project's first commit even happens.

    Prunes any dotdir at ANY depth (not just the project root the way _is_control() does for a
    single already-resolved path) plus NOISE_DIR_NAMES, so this never descends into .git
    internals, node_modules, vendor, build output, or a virtualenv. A gap here fails toward
    UNDER-counting the universe (a file this walk never visits can't be flagged uncovered
    either), the same "fail toward NOT gating" direction the rest of this module already uses.
    """
    root = Path(project_root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in NOISE_DIR_NAMES]
        for fname in filenames:
            if Path(fname).suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            full = Path(dirpath) / fname
            try:
                rel = full.relative_to(root)
            except ValueError:
                continue  # not actually under root -- can't happen from os.walk(root), kept
                          # defensive rather than assumed
            found.append(str(rel))
    return sorted(found)


def parse_ungated(project_root):
    """Read `.foreman/ungated.txt`: real, checked-in A2 exclusions, one per line, each
    requiring a one-line reason (QUALITY-BAR.md A2: "on a checked-in .foreman/ungated.txt with
    a one-line reason"). Convention matches the one real ungated.txt on this machine today
    (atlas-sonnet's, excluding its two `.githooks/` files): "<path> -- <reason>".

    Returns (valid, malformed): valid is {rel_path: reason}; malformed is the list of raw lines
    that named a path but carried no non-empty reason after the separator. A malformed entry is
    NOT treated as covered -- a project can't satisfy A2 by listing a bare path with nothing
    explaining it, the same "a boolean/field that's always satisfiable carries zero bits"
    finding QUALITY-BAR.md's F4 already made about `verifiable` at a different layer. Comment
    lines (# prefix) and blank lines are skipped, not counted as malformed.
    """
    return parse_reasoned_path_lines(Path(project_root) / ".foreman" / "ungated.txt")


def parse_reasoned_path_lines(path):
    """FORE-510: the one parser for .foreman's reasoned-path-list files -- extracted verbatim
    from parse_ungated()'s original body so ungated.txt and out-of-scope-zones.txt cannot
    drift on line convention, the same single-definition reasoning this module already applies
    to its shared predicates. Returns (valid, malformed): valid is {entry: reason}; malformed
    is the list of raw lines that named a path but carried no non-empty reason. Comment lines
    (# prefix) and blank lines are skipped, not counted as malformed. A missing file is the
    normal not-declared state, not an error."""
    valid = {}
    malformed = []
    try:
        text = Path(path).read_text()
    except OSError:
        return valid, malformed
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if " -- " in line:
            rel, reason = line.split(" -- ", 1)
        elif " # " in line:
            rel, reason = line.split(" # ", 1)
        else:
            malformed.append(raw_line)
            continue
        rel, reason = rel.strip(), reason.strip()
        if not rel or not reason:
            malformed.append(raw_line)
            continue
        valid[rel] = reason
    return valid, malformed


def parse_out_of_scope_zones(project_root):
    """FORE-510: read the explicit out-of-scope allowlist (OUT_OF_SCOPE_ZONES_RELPATH -- see
    that constant's comment for the format and why it exists). Returns (valid, malformed),
    same contract as parse_ungated(). A malformed entry is never honored: in strict mode that
    fails toward DENY, and the gate's deny message discloses that malformed entries were
    ignored rather than silently dropping them."""
    return parse_reasoned_path_lines(Path(project_root) / OUT_OF_SCOPE_ZONES_RELPATH)


def in_out_of_scope_zone(rel_path, zones):
    """FORE-510: is this project-relative path inside any valid declared out-of-scope zone?
    An entry ending in "/" is a directory prefix (plain startswith, same prefix semantics as
    component_of() -- deliberately not glob matching, per this module's own header note); any
    other entry must match the path exactly."""
    rel_path = str(rel_path)
    for entry in zones:
        if entry.endswith("/"):
            if rel_path.startswith(entry):
                return True
        elif rel_path == entry:
            return True
    return False


def strict_scope_enabled(project_root):
    """FORE-510: has this project opted into strict component scope (the default-deny flip
    for writes resolving to no declared component)? Marker-file check, same shape as
    architecture_gate.py's REVIEW_BINDING_MARKER opt-in.

    Opus review F4: a marker that is PRESENT but unusable (a directory, a dangling symlink)
    fails CLOSED -- strict is treated as ON, with a stderr disclosure -- rather than silently
    reading as absent. This change already makes an unreadable zones file fail closed; two
    ambiguity cases resolving in two different directions would be an internal inconsistency
    on exactly the guarantee surface this ticket is about. os.path.lexists() rather than
    exists() because a dangling symlink reports False under exists()."""
    marker = Path(project_root) / STRICT_SCOPE_MARKER
    if marker.is_file():
        return True
    if os.path.lexists(marker):
        print(f"[component_coupling] {marker} exists but is not a regular file (directory or "
              f"dangling symlink); treating strict component scope as ENABLED (fail-closed) "
              f"until it is fixed", file=sys.stderr)
        return True
    return False


def undeclared_source_path(file_path, project_root, component_map, zones, ungated):
    """FORE-510: the strict-scope predicate. True only when ALL of these hold -- the path
    resolves inside the project; it is not a control artifact or dotdir path (_is_control);
    its extension is in SOURCE_EXTENSIONS; it resolves to NO declared component
    (component_of); it is not inside any valid declared out-of-scope zone; and it is not a
    valid exact ungated.txt entry (a validly-ungated file is declared non-component by
    definition -- one concept, two granularities).

    Scoped to SOURCE_EXTENSIONS on purpose, and this is a disclosed limitation, not an
    oversight: the gap FORE-510 closes is undeclared IMPLEMENTATION code (A2's own universe
    definition), and gating every .md/.json/handoff write would block ordinary project
    operation for no coverage gain against that gap. A non-source write to an undeclared path
    stays pass-through even in strict mode.

    Reuses _relative_or_none/_is_control/component_of rather than re-deriving any of them --
    a parallel re-implementation here is exactly the drift this module exists to prevent."""
    rel = _relative_or_none(file_path, project_root)
    if rel is None or _is_control(rel):
        return False
    if rel.suffix.lower() not in SOURCE_EXTENSIONS:
        return False
    rel_str = str(rel)
    if component_of(rel_str, component_map) is not None:
        return False
    if rel_str in ungated:
        return False
    return not in_out_of_scope_zone(rel_str, zones)


def compute_coverage(project_root, component_map):
    """A2 (QUALITY-BAR.md 2.1): every real source file is inside a declared component or
    validly, explicitly ungated; coverage ratio must be 1.0.

    Walks the real file tree (walk_source_files -- not git-tracked-only, see its own docstring
    for why), classifies every match via component_of() -- the identical predicate
    goals_freeze_gate.py already trusts for the same question about a single path, not a
    parallel re-implementation of it -- and treats a VALID `.foreman/ungated.txt` entry as
    covered. A malformed ungated entry does not count as covered; it is surfaced separately via
    `malformed_ungated` on the returned CoverageReport so it can't silently satisfy A2.
    """
    ungated, malformed = parse_ungated(project_root)
    # FORE-510 coherence: a valid declared out-of-scope zone is the prefix-level statement of
    # the same fact ungated.txt states per-file, so A2 coverage honors it too -- otherwise the
    # write gate and the coverage check would disagree about the same path, the exact
    # two-predicates-drifting failure this module's header warns about. No zones file means no
    # behavior change here. Malformed zone entries are NOT honored (same as malformed ungated
    # lines) but are not added to malformed_ungated either -- CoverageReport's shape is a
    # consumed contract, and zone malformedness is surfaced at the gate instead.
    zones, zones_malformed = parse_out_of_scope_zones(project_root)  # noqa: F841 -- see comment above
    files = walk_source_files(project_root)
    total = len(files)
    uncovered = []
    for rel in files:
        if component_of(rel, component_map) is not None:
            continue
        if rel in ungated:
            continue
        if in_out_of_scope_zone(rel, zones):
            continue
        uncovered.append(rel)
    covered = total - len(uncovered)
    ratio = (covered / total) if total else 1.0
    return CoverageReport(total=total, covered=covered, uncovered=uncovered, ratio=ratio,
                          malformed_ungated=malformed)


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
    glob is a directory prefix. Found live during FORE-2/FORE-3 design-and-scope: ARCHITECTURE.md
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


def goals_json_path(component, comp_root, project_root, component_map):
    """FORE-CHV2-102: the bare-file GOALS.json collision's actual resolution fix. When
    `component`'s resolved root is one this project's OTHER declared components also resolve
    to (per shared_component_roots() above), this returns a component-keyed filename --
    <comp_root><component>.GOALS.json -- instead of the shared <comp_root>GOALS.json every
    colliding component would otherwise silently be graded against. Matches the naming
    convention hooks/agent_dispatch_gate.GOALS.json already uses on disk (FORE-462, a real,
    already-frozen, hash-valid GOALS.json that this exact resolution gap left permanently
    unread until now -- confirmed live by recomputing its criteria_hash_at_freeze and finding
    it matches).

    A component whose root is NOT shared is completely unaffected: this returns the exact
    same <comp_root>GOALS.json path goals_freeze_gate.py has always used for it, so every
    non-colliding component's behavior is byte-identical to before this fix. Only the roots
    shared_component_roots() actually flags change path at all.

    comp_root is passed in rather than recomputed, matching goals_freeze_gate.py's own
    existing call shape (it already calls component_root() once per check and would otherwise
    redundantly re-walk component_map a second time here for the same answer)."""
    shared = shared_component_roots(component_map)
    members = shared.get(comp_root)
    if members and component in members:
        return project_root / comp_root / f"{component}.GOALS.json"
    return project_root / comp_root / "GOALS.json"


def shared_component_roots(component_map):
    """FORE-510 / FORE-508/509: the component roots that TWO OR MORE declared components
    resolve to -- the mechanical detector for the bare-file GOALS.json collision
    (claude-hooks-v2's seven hooks/-root components sharing one hooks/GOALS.json is the live
    instance). Returns {root: [component names]} for every shared root, {} when each
    component has its own. This exists so the sequencing constraint "do not rely on strict
    mode's declare-a-component remedy while a collision is live" can be CHECKED at gate time
    rather than remembered from ticket prose -- a constraint with no detection trigger stays
    honest only as long as someone keeps re-reading it (QA-Bob's F3 lesson, frozen tonight in
    two GOALS.json files)."""
    roots = {}
    for name in component_map:
        root = component_root(name, component_map)
        if root is None:
            continue
        roots.setdefault(root, []).append(name)
    return {root: sorted(names) for root, names in roots.items() if len(names) > 1}


def _true_case(path):
    """Best-effort real on-disk casing for each path component that already exists; any
    component that doesn't exist yet (e.g. a file about to be created) is left exactly as
    typed, since there's no prior on-disk casing to disagree with.

    FORE-128: on a case-insensitive, case-preserving filesystem (macOS APFS default),
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


# FORE-21 fix: the `>` must sit at a real shell-operator position -- start of segment or
# preceded by whitespace -- not merely anywhere `(?<!\d)` allowed it through. Before this,
# an angle-bracket embedded in ordinary command TEXT with no shell meaning at all (an HTML
# closing tag like `</div>`, a `-->` comment-close, a bare `>` in a comparison snippet)
# still matched, because the only thing excluded was a digit immediately before it (the
# fd-redirect case, `2>`). `</div>foo` has `v` before the `>`, not a digit, so the old regex
# read it as a redirect into a garbage target built from whatever followed. A real shell
# redirect is always preceded by whitespace or starts the command; text-internal `>` never
# is. This does not depend on FORE-23's quote-span check (that check only excludes a `>`
# sitting *inside* a quoted argument) -- this case is unquoted command text, the quote check
# alone does not see it. Same fail-toward-NOT-gating direction as the rest of this function:
# tightening the precedent narrows false positives and cannot introduce a new false negative
# beyond the pre-existing space-before-`>` convention essentially every real agent-issued
# command already follows.
BASH_REDIRECT_RE = re.compile(r'(?:^|(?<=\s))(?<!\d)>{1,2}\s*(?!&\d)("[^"]+"|\'[^\']+\'|\S+)')
BASH_TEE_RE = re.compile(r'\btee\b(?:\s+-a)?\s+("[^"]+"|\'[^\']+\'|\S+)')
# FORE-14 review finding (FATAL, live-confirmed): an earlier version of this pass added
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
# (FORE-15 covers the broader question of whether recursion-style improvements belong in the
# shared function at all).
# FORE-591 + FORE-579. This was r"\b(?:cp|mv)\b" -- a bare word-boundary match anywhere in a
# segment, not anchored to COMMAND POSITION. DEMONSTRATED collision, which the ticket asked for
# rather than assuming from structural analogy: `npm install cp-cli` extracted "cp-cli" as a write
# target, on both interpreters. `ffmpeg -codec mv -o /tmp/out.mp4` extracted "out.mp4" the same
# way. Any command whose text merely CONTAINS the standalone word cp or mv fires the tail-token
# heuristic below.
#
# Anchoring is also what makes adding `ln` safe, and that is why these are one change rather than
# two. See this module's own comment above: an earlier install/ln extension was reverted because
# it false-DENIED ordinary `npm install @scope/pkg` and `pip install -e .`. That failure was this
# same unanchored-match defect, not something particular to ln -- `npm ln` no longer matches here
# because `npm` is the command and `ln` is an argument.
#
# Command position, given segments are already split on [;&|\n] before this is applied:
#   optional leading whitespace
#   optional environment assignments   FOO=1 BAR=2 cp src dst
#   optional sudo with its own flags
#   optional ONE wrapper word from a closed list -- git is the load-bearing one, since `git mv`
#     is a real write the old unanchored pattern did catch and a naive anchor would drop
# The list is closed deliberately: every entry is a wrapper that takes a command as its argument.
# A package manager is not on it, which is the whole point.
#
# FORE-579: `ln` added, so planting a symlink via Bash is no longer invisible to every gate that
# relies on this function. Measured before the fix: redirect, cp and mv all detected; `ln -s`,
# `ln -sf` and bare `ln` all returned ZERO targets, on both interpreters.
#
# FORE-589 resolves the edge this comment used to disclose: `ln -s target` with no explicit link
# name creates ./basename(target), while the tail-token heuristic took the LAST token, which in
# that form is the SOURCE. The named group above is what lets the loop below tell which command
# matched, so ln can be parsed by argument position instead.
#
# WORTH RECORDING ABOUT THAT DISCLOSURE RATHER THAN JUST DELETING IT: it said the spelling
# "yields the wrong target rather than none" and stopped there. What it never said is that the
# wrong target becomes a DENY INPUT for architecture_gate and goals_freeze_gate, so a command
# that was previously ungated could be denied over a path nothing writes to. The residual was
# disclosed; that consequence was not, and that was the half that touched a real user.
#
# GNU -t/--target-directory is not supported here -- it doesn't exist on macOS ln and isn't
# verified ground truth on this platform; a GNU `ln -t destdir a.txt` will extract the wrong
# target. Also not attempted: `ln a b c dir/`, three or more positionals hard-linked into a
# directory, a different argument-count shape from the ones handled below.
BASH_WRITE_CMD_RE = re.compile(
    r"^\s*"
    r"(?:\w+=\S*\s+)*"
    r"(?:sudo\s+(?:-\S+\s+)*)?"
    r"(?:(?:git|command|env|nohup|time)\s+)?"
    r"(?P<writecmd>cp|mv|ln)\b"
)
BASH_SED_INPLACE_RE = re.compile(r"\bsed\b[^\n]*-i\b")

# FORE-15 resolution: ported from dependency_provenance_gate.py's INVOKED_SCRIPT_RE /
# MAX_SCRIPT_READ_BYTES verbatim (duplicated, not imported -- this module has no cross-hook
# import of that sibling, matching its own established convention of keeping each hook
# self-contained). That gate's own comment on this pattern: deliberately loose, because
# over-matching there just means reading one more script's text (cheap, read-only). The same
# reasoning applies here for the SAME direction of error -- recursing into more candidate
# scripts only adds write-target COVERAGE (closes false negatives), it does not change this
# function's existing false-positive risk profile, which lives entirely in the redirect/tee/
# cpmv/sed patterns applied to whatever text gets scanned. See extract_bash_write_targets's
# docstring for why this was safe to port where the "install"/"ln" trigger-regex extension
# (the FORE-14 review FATAL, noted above) was not: that was a NEW match class on the outer
# command's own text, which directly risked false-denying ordinary package-manager commands.
# This is recursion into what's already scanned, using patterns already live in this file.
INVOKED_SCRIPT_RE = re.compile(
    r'(?:\b(?:bash|sh|source)\s+)?("[^"]+\.sh"|\'[^\']+\.sh\'|\S+\.sh)\b'
)
MAX_SCRIPT_READ_BYTES = 1_000_000


def _invoked_script_segments(seg, project_root):
    """One level of recursion into a directly-invoked local .sh script (FORE-15): if `seg`
    invokes one, return its own lines as additional segments to scan for write targets, so a
    write hidden inside `bash wrapper.sh` is no longer invisible to this function -- the
    confirmed, reproduced gap this ticket exists to close (FORE-14's fixture: a command with
    no write pattern in its own text, whose invoked script writes to a gated path, produced
    zero denial from either hard gate).

    Bounded exactly like FORE-14's own recursion: ONE level only (an invoked script invoking
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
# relative targets in the same command, instead of unconditionally dropping them (M1, FORE-71
# adversarial review 2026-08-22: `cd /proj && echo x > ARCHITECTURE.md` silently walked past the
# gate because a relative target after ANY cd -- absolute or relative -- was dropped). A
# RELATIVE cd keeps the original drop behavior (FORE-23): it still cannot be resolved without
# interpreting shell control flow.
CD_ARG_RE = re.compile(r"^\s*cd\s+(\S+)")


def quoted_spans(text):
    """Character ranges of `text` sitting inside a single- or double-quoted string.

    FORE-23 finding 2: a write-detection regex's TRIGGER character (the `>`, the word "tee",
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

    Promoted to public (CHV2-49): review_events_ledger_guard.py's Bash text-backstop is a second real consumer of this exact quote-awareness, reusing it rather than re-deriving a second copy of FORE-23's fix.
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


def inside_quotes(pos, spans):
    return any(start <= pos < end for start, end in spans)


def ln_last_is_directory(last_positional, cd_seen, cd_base, base):
    """Does `ln`'s last positional name a directory that exists right now?

    FORE-589. `ln -s src existingdir` creates existingdir/basename(src), so the tail token is the
    containing directory rather than the link. Nothing short of asking the filesystem can tell
    that apart from `ln -s src newname`, where the tail token IS the link -- the two commands are
    textually identical in shape and differ only in what is on disk.

    ASKS ONLY WHEN THE ANSWER IS SAFE TO TRUST, reusing this function's existing cd rule rather
    than inventing a second one: a relative candidate in a segment that follows a RELATIVE `cd`
    cannot be resolved without interpreting shell control flow, so it is not probed at all and the
    caller falls back to unchanged tail-token behaviour. Guessing a base there would be the same
    defect FORE-23 removed, arriving through a new door.

    A False here is always the conservative direction: it yields the old behaviour, which is
    correct for the explicit-link-name form and merely imprecise for the other.
    """
    candidate = Path(last_positional.strip('"').strip("'"))
    if not candidate.is_absolute():
        if cd_seen and cd_base is None:
            return False  # relative cd, unresolvable -- same drop rule as the resolution loop
        candidate = (cd_base if cd_base is not None else base) / candidate
    try:
        return candidate.is_dir()
    except (OSError, RuntimeError, ValueError):
        # Path.is_dir() absorbs most OSErrors itself and returns False, so this is defence rather
        # than a path with a known trigger -- labelled as such rather than counted as handled.
        return False


class TargetList(list):
    """A list[Path] for every existing purpose (iteration, indexing, str()/.is_file() per
    element, len()) -- so all 24 production call sites keep working unchanged (P2, FORE-590).
    Plus one attribute, `.unresolvable`, a tuple of (raw_text, best_effort_path) pairs for
    targets that could not be genuinely resolved (FORE-590 symlink loops, FORE-595 NUL bytes)
    -- flagged, never silently dropped, never crashed past. Coercing through list(...) or
    concatenation loses this attribute; no current call site does either (checked)."""

    unresolvable = ()


def extract_bash_write_targets(command, cwd):
    """Best-effort file paths a Bash command would write or overwrite. Regex-based, not a
    shell parser -- mirrors guard_destructive.py's existing convention (must be fast, pure
    regex, no network) rather than adding a shell-parsing dependency for this.

    Exists because FORE-1 found the real gap here: architecture_gate.py and
    goals_freeze_gate.py only ever matched Edit|Write, so an agent denied on Write could walk
    straight past both gates with `printf ... > path` -- reproduced 3/3 trials, zero Foreman
    hook involvement on the Bash path, confirmed on disk and in the verdict ledger. This covers
    the patterns that reproduction and ordinary agent behavior actually exercise: shell
    redirection (>, >>, skipping fd-duplication like `2>&1`), `tee [-a]`, cp/mv's destination
    argument, and `sed -i`'s in-place target -- now including ONE level of recursion into a
    directly-invoked local .sh script's own body (FORE-15), so `bash wrapper.sh` can no
    longer hide a write neither hard gate would otherwise see; a quote-awareness check
    (FORE-23 finding 2) so a `>` appearing only inside a quoted argument to some OTHER command
    is not read as a real redirect; and a `cd`-safety check (FORE-23 finding 1) so a RELATIVE
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
    # matching the original FORE-23 drop behavior).
    raw_targets = []
    # FORE-14 review finding: a bare newline ends a command in real shell semantics and was
    # NOT in this split class -- for a genuinely multi-line command string (a heredoc body, a
    # multi-statement command joined by literal newlines rather than ';') this collapsed
    # everything after the first line into one giant segment, and the cp/mv tail-token
    # heuristic then grabbed the last token of the WHOLE blob instead of the actual
    # destination. Confirmed near-inert for this function's two existing consumers
    # (architecture_gate.py, goals_freeze_gate.py), which only ever see a single logical Bash
    # command's own text -- this only changes behavior for a command that already spans
    # multiple lines, which splitting more finely can only make MORE precise, never less.
    segments = list(re.split(r"[;&|\n]", command))
    # FORE-589: hoisted above the per-segment loop so the ln branch can ask whether a candidate
    # last positional is an existing directory inline. It used to be computed at the resolution
    # stage below. Moving it changes no behaviour -- it depends on nothing the loop computes.
    base = Path(cwd) if cwd else Path.cwd()
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
                except (OSError, RuntimeError, ValueError):
                    cd_base = None  # unresolvable absolute arg -- fall back to unsafe-drop
            else:
                cd_base = None  # relative (or unparseable) cd -- unresolvable, same as before
            continue  # a bare `cd` has no write pattern of its own to scan for
        # Recursion happens BEFORE the outer loop's own scan below reuses `seg` for the
        # invoked-script detection, so a script's own lines get pattern-scanned the identical
        # way the outer command's lines do -- one code path, not two divergent ones. A `cd`
        # INSIDE an invoked script is not modeled here, matching FORE-14's own disclosed scope
        # for the same recursion (dependency_provenance_gate.py's build_events docstring).
        segments_to_scan = [seg, *_invoked_script_segments(seg, project_root)]
        for s in segments_to_scan:
            spans = quoted_spans(s)
            for m in BASH_REDIRECT_RE.finditer(s):
                if not inside_quotes(m.start(), spans):
                    raw_targets.append((m.group(1), cd_seen, cd_base))
            m = BASH_TEE_RE.search(s)
            if m and not inside_quotes(m.start(), spans):
                raw_targets.append((m.group(1), cd_seen, cd_base))
            cpmv = BASH_WRITE_CMD_RE.search(s)
            sed = BASH_SED_INPLACE_RE.search(s)
            trigger = cpmv or sed
            if trigger and not inside_quotes(trigger.start(), spans):
                # Quote-aware tokenizer, same reasoning as the redirect/tee patterns above --
                # seg.split() would break a quoted destination containing a space into several
                # tokens and grab the wrong tail one.
                if cpmv:
                    # FORE-589. Tokenize only what FOLLOWS the matched command word, so the
                    # env-assignment/sudo/wrapper prefix the anchor already skipped does not have
                    # to be filtered out a second time. A positional is any token not starting
                    # with "-" -- `-s` was never the trigger for this bug, argument COUNT is, so
                    # `ln a` (a bare hard link, no flags at all) has the identical shape.
                    arg_tokens = re.findall(r'"[^"]+"|\'[^\']+\'|\S+', s[cpmv.end():])
                    positional_args = [t for t in arg_tokens if not t.startswith("-")]
                    is_ln = cpmv.group("writecmd") == "ln"
                    if is_ln and len(positional_args) == 1:
                        # `ln -s sub/src.txt` and `ln sub/src.txt` create ./basename(source) in
                        # the current directory. The tail token here is the SOURCE, so appending
                        # it gated a path nothing writes to and missed the one that gets written.
                        # Appended as a RELATIVE name on purpose: the cd_base/base resolution at
                        # the bottom of this function already knows how to resolve one.
                        source = positional_args[0].strip('"').strip("'")
                        raw_targets.append((Path(source).name, cd_seen, cd_base))
                    elif is_ln and len(positional_args) >= 2 and ln_last_is_directory(
                            positional_args[-1], cd_seen, cd_base, base):
                        # `ln -s src existingdir` creates existingdir/basename(src), not
                        # `existingdir`. Only asked when the candidate is safely resolvable --
                        # see ln_last_is_directory for the cd-ambiguity rule it shares with the
                        # rest of this function.
                        last = positional_args[-1].strip('"').strip("'")
                        source = positional_args[-2].strip('"').strip("'")
                        raw_targets.append((str(Path(last) / Path(source).name),
                                            cd_seen, cd_base))
                    elif positional_args:
                        # Two or more positionals with an explicit link name, and every cp/mv
                        # command: unchanged tail-token behaviour, which is already correct for
                        # `ln -s target linkname`, `ln target linkname`, `cp a b` and `mv a b`.
                        raw_targets.append((positional_args[-1], cd_seen, cd_base))
                else:
                    tokens = re.findall(r'"[^"]+"|\'[^\']+\'|\S+', s)
                    if tokens and not tokens[-1].startswith("-"):
                        raw_targets.append((tokens[-1], cd_seen, cd_base))

    resolved = TargetList()
    unresolvable = []

    def _resolve_flagging_unresolvable(raw_text, fallback):
        """FORE-590/595. Flags, never silently drops. Two independent failure classes:

        (1) resolve_keeping_leaf() itself raises -- a symlink loop (OSError/RuntimeError) or a
        NUL byte in a PARENT path component (ValueError), since that function resolves the
        parent via a real stat/lstat call.

        (2) A NUL byte in the LEAF component -- checked directly as a string test, REAL GAP
        FOUND WHILE VERIFYING THE DECIDED FIX, not present in the original proposal:
        resolve_keeping_leaf() (FORE-588) deliberately never touches the leaf with a syscall
        (that is the whole point of the function -- see its own docstring), so a NUL byte
        confined to the leaf (the common case for a redirect `> name`) resolves without ever
        raising. Confirmed by direct test: a NUL in a parent segment raises ValueError today; a
        NUL in the leaf does not, silently. A direct string check, independent of exception
        behaviour or byte position, is what the original design doc's own criterion recommended
        ("ask interpreter-independent questions") and is what actually delivers "flagged as
        unresolvable regardless of how the byte arrives" -- the decided text's own intent --
        which the exception-only mechanism alone does not, for this specific arrival position.
        """
        if "\x00" in raw_text:
            resolved.append(fallback)
            unresolvable.append((raw_text, fallback))
            return
        try:
            resolved.append(resolve_keeping_leaf(fallback))
        except (OSError, RuntimeError, ValueError):
            resolved.append(fallback)
            unresolvable.append((raw_text, fallback))

    for raw, cd_unsafe, target_cd_base in raw_targets:
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            continue
        candidate = Path(raw)
        if cd_unsafe and not candidate.is_absolute():
            if target_cd_base is not None:
                # An ABSOLUTE cd preceded this target -- resolve against the known real
                # directory instead of dropping it (M1 fix, see CD_ARG_RE above).
                _resolve_flagging_unresolvable(raw, target_cd_base / candidate)
                continue
            continue  # relative cd, still unresolvable -- unchanged FORE-23 behavior
        _resolve_flagging_unresolvable(raw, candidate if candidate.is_absolute() else base / candidate)
    resolved.unresolvable = tuple(unresolvable)
    return resolved
