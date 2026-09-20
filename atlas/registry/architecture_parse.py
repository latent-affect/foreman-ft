"""ATLASSN-127 -- the single frozen-ARCHITECTURE parser for the registry component.

Three criteria (C3, C8, C9 of atlas/registry/GOALS.json, frozen 2026-09-12T14:52:00Z,
criteria_hash sha256:929345e6...) each assert that they resolve a path or a scope through the
SAME parser module, checked by import identity. Building this once, first, is what makes those
assertions satisfiable; building it three times is the defect they exist to catch.

WHAT THIS PARSES. The three fenced blocks of a frozen, ratified ARCHITECTURE.md:

    ```yaml components     name: ["dir/**", ...]                 (JSON array value)
    ```yaml interfaces     name: {"producer": ..., ...}          (quoted-key JSON object)
    ```yaml reach          name: {"repo": ..., "direction": ...} (quoted-key JSON object)

Deliberately NOT a YAML parser, following component_coupling.py's own reasoning: the format is
one name per line with a JSON value on the right-hand side, so a regex plus json.loads() covers
it exactly. Adding a YAML dependency for an array-of-strings-per-line format would be the
"second parser" that design argues against. Zero third-party imports, stdlib only.

MULTIPLE BLOCKS OF THE SAME KIND ARE NORMAL AND ALL OF THEM COUNT. atlas-sonnet's own
ARCHITECTURE.md declares seven components at line 446 and an eighth (registry) at line 3921, in
a second block. A scan that stops at the first closing fence silently drops the later block --
FORE-260 instance 2, already fixed once in component_coupling.parse_component_map() and not
reintroduced here.

TWO DELIBERATE DIVERGENCES FROM component_coupling.py, both stricter, both load-bearing for a
consumer that makes fail-closed DENY decisions:

1. Malformed input raises; it never degrades to a partial or empty result. The incumbent returns
   (entries, malformed) and documents that "callers MUST inspect it" -- a contract a caller can
   forget, whose failure mode is a component silently going undeclared. Every consumer here
   feeds a fail-closed path (GOALS C4: no dependency outage converts to an accepted write; C6:
   malformed field values deny), so the parser refuses rather than hands back something
   plausible. An absent block is BlockMissing, a line inside a block that does not parse is
   BlockMalformed, and neither is ever an empty dict.

2. Path matching is path-component-aware, not str.startswith(). The incumbent's component_of()
   compares a raw string prefix, which is correct for every glob that ends in a separator but
   wrong for one that does not: a component declared "atlas" would claim "atlas-sonnet/x.py".
   That is G5's prefix-collision residual from the design-scope falsification, and C2 of this
   ticket pins it with an explicit control. See component_of() below.

FROZEN SOURCE, NEVER A WORKING TREE (section 34.4). Reads resolve at the repo's committed HEAD
with its freeze marker present, so a dirty or mid-freeze repo presents its last ratified state
rather than whatever a session happens to have uncommitted. A repo with no freeze marker is
refused by name, not read opportunistically.

NOTE ON I10 / ATLASSN-135. C9 requires a SECOND, genuinely independent derivation in a separate
module sharing no helper with this one. That belongs to I10. Do not refactor the two into shared
code later -- the independence IS the check, and collapsing them silently converts a real
cross-check into one derivation agreeing with itself.
"""

import json
import re
import subprocess
from pathlib import Path

ARCHITECTURE_FILENAME = "ARCHITECTURE.md"
FREEZE_MARKER_PATH = ".foreman/frozen.json"

# Same convention component_coupling.py established and every real block on this machine follows:
# a bare name, a colon, then a JSON value. Components carry an array; interfaces and reach carry
# a quoted-key object (the bare-key YAML form is NOT accepted by json.loads and is a defect in
# the document, which is why it raises here instead of being skipped).
ENTRY_NAME = r"[A-Za-z_][\w-]*"
ARRAY_ENTRY_RE = re.compile(r"^(" + ENTRY_NAME + r"):\s*(\[.*\])\s*$")
OBJECT_ENTRY_RE = re.compile(r"^(" + ENTRY_NAME + r"):\s*(\{.*\})\s*$")

BLOCK_KINDS = ("components", "interfaces", "reach")


class FrozenArchitectureError(Exception):
    """Base for every failure this module raises. Callers that need to keep scanning other
    repos catch this one and emit a finding; callers on a gate path let it propagate."""


class FreezeMarkerMissing(FrozenArchitectureError):
    """The repo has no freeze marker at HEAD, so it has no ratified state to present."""


class ArchitectureUnreadable(FrozenArchitectureError):
    """ARCHITECTURE.md could not be read at the repo's committed HEAD."""


class BlockMissing(FrozenArchitectureError):
    """The document declares no block of the requested kind at all."""


class BlockMalformed(FrozenArchitectureError):
    """A line inside a block does not parse, or its JSON value is the wrong shape."""


def normalize_rel_path(rel_path):
    """Project-relative, POSIX separators, no leading "./" or "/", no trailing "/"."""
    text = str(rel_path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/").rstrip("/")


def glob_prefix(glob):
    """The directory a wildcard glob covers, with no trailing separator.

    "atlas/registry/**" -> "atlas/registry".  "**" -> "" (the whole project).
    Returns None for a glob carrying no wildcard, which is a single-file declaration and is
    matched exactly rather than as a prefix (component_root()'s docstring in
    component_coupling.py confirms the bare-filename form is intended, not an error).
    """
    if "*" not in glob:
        return None
    return glob.rstrip("*").rstrip("/")


def glob_matches(rel_path, glob):
    """Path-component-aware match. This is the G5 fix and C2's whole point.

    A raw str.startswith() says "atlas-sonnet/atlas/x.py" is covered by a component declared
    "atlas", because the characters line up. They are different directories. Matching only at a
    path boundary -- equal to the prefix, or the prefix followed by "/" -- is what makes the
    comparison about paths instead of about strings.
    """
    path = normalize_rel_path(rel_path)
    prefix = glob_prefix(glob)
    if prefix is None:
        return path == normalize_rel_path(glob)
    if prefix == "":
        return True
    return path == prefix or path.startswith(prefix + "/")


def component_of(rel_path, components):
    """Which declared component owns this project-relative path, or None if none does.

    None is a real answer, not an error: it means the path is not inside any declared component
    yet. Longest declaration wins when two overlap, so a nested component beats its parent.
    """
    best = None
    best_specificity = -1
    for name, globs in components.items():
        for glob in globs:
            if not glob_matches(rel_path, glob):
                continue
            prefix = glob_prefix(glob)
            specificity = len(glob) if prefix is None else len(prefix)
            if specificity > best_specificity:
                best, best_specificity = name, specificity
    return best


def iter_block_lines(text, kind):
    """Yield (line_number, stripped_line) for every line inside every block of `kind`.

    Every block, not the first: a document may declare a component set in one place and extend
    it in another, and both are real declarations.
    """
    if kind not in BLOCK_KINDS:
        raise ValueError(f"unknown block kind {kind!r}; expected one of {BLOCK_KINDS}")
    opening = "```yaml " + kind
    inside = False
    found_any = False
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not inside and stripped.startswith(opening):
            inside = True
            found_any = True
            continue
        if inside and stripped.startswith("```"):
            inside = False
            continue
        if inside:
            yield number, stripped
    if not found_any:
        raise BlockMissing(
            f"ARCHITECTURE.md declares no ```yaml {kind} block; refusing to report an empty "
            f"{kind} map, which a caller cannot distinguish from a document that legitimately "
            f"declares nothing"
        )


def parse_block(text, kind, entry_re, shape_check, shape_description):
    entries = {}
    for number, line in iter_block_lines(text, kind):
        if not line or line.startswith("#"):
            continue
        match = entry_re.match(line)
        if not match:
            raise BlockMalformed(
                f"ARCHITECTURE.md line {number} inside a ```yaml {kind} block does not parse as "
                f"`name: {shape_description}`: {line!r}"
            )
        name, value_json = match.groups()
        try:
            value = json.loads(value_json)
        except json.JSONDecodeError as exc:
            raise BlockMalformed(
                f"ARCHITECTURE.md line {number}: {kind} entry {name!r} has a value that is not "
                f"valid JSON ({exc}); the bare-key YAML form is not accepted -- quote the keys"
            ) from exc
        if not shape_check(value):
            raise BlockMalformed(
                f"ARCHITECTURE.md line {number}: {kind} entry {name!r} is not {shape_description}"
            )
        if name in entries:
            raise BlockMalformed(
                f"ARCHITECTURE.md line {number}: {kind} entry {name!r} is declared twice; the "
                f"later declaration would silently win, so this refuses instead"
            )
        entries[name] = value
    return entries


def is_list_of_strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def is_string_keyed_object(value):
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def parse_components(text):
    """{component_name: [glob, ...]} from every ```yaml components block in `text`."""
    return parse_block(text, "components", ARRAY_ENTRY_RE, is_list_of_strings,
                       "a JSON array of strings")


def parse_interfaces(text):
    """{interface_name: {producer, consumer, trust, ...}} from every interfaces block."""
    return parse_block(text, "interfaces", OBJECT_ENTRY_RE, is_string_keyed_object,
                       "a quoted-key JSON object")


def parse_reach(text):
    """{edge_name: {repo, path, direction, pinned_at, ...}} from every reach block.

    Every declared field is preserved verbatim, including `direction: registers` -- the
    reconciler's forward-gap check reads it, and dropping unknown keys here would silently
    narrow what a later section 34.4 run can see.
    """
    return parse_block(text, "reach", OBJECT_ENTRY_RE, is_string_keyed_object,
                       "a quoted-key JSON object")


def run_git(repo_root, arguments):
    """git, with no shell, returning CompletedProcess. Separated so the frozen-read path has
    exactly one place where a subprocess is spawned."""
    return subprocess.run(
        ["git", "-C", str(repo_root)] + list(arguments),
        capture_output=True, text=True, check=False,
    )


def path_exists_at_head(repo_root, rel_path):
    return run_git(repo_root, ["cat-file", "-e", f"HEAD:{rel_path}"]).returncode == 0


def read_frozen_architecture(repo_root, architecture_filename=ARCHITECTURE_FILENAME):
    """The ratified ARCHITECTURE.md text for a repo: committed HEAD, freeze marker required.

    Section 34.4: reconciliation reads every frozen, ratified ARCHITECTURE.md "at each repo's
    committed HEAD with its freeze marker present, never from working trees, so a mid-freeze or
    dirty repo simply presents its last ratified state." Both halves are enforced here, because
    a caller that forgets either one gets a plausible answer built from unratified bytes.
    """
    repo_root = Path(repo_root)
    if not path_exists_at_head(repo_root, FREEZE_MARKER_PATH):
        raise FreezeMarkerMissing(
            f"{repo_root} has no {FREEZE_MARKER_PATH} at committed HEAD, so it has no ratified "
            f"state to present; refusing to read its working tree instead"
        )
    result = run_git(repo_root, ["show", f"HEAD:{architecture_filename}"])
    if result.returncode != 0:
        raise ArchitectureUnreadable(
            f"could not read {architecture_filename} at HEAD of {repo_root}: "
            f"{result.stderr.strip() or 'git exited ' + str(result.returncode)}"
        )
    return result.stdout


def parse_frozen_repo(repo_root, architecture_filename=ARCHITECTURE_FILENAME):
    """Convenience for the common consumer shape: read the ratified doc once, parse all three
    blocks from that single text, so no consumer can accidentally mix a components map from one
    revision with a reach map from another.

    `reach` is optional because a ratified architecture may legitimately declare no cross-repo
    edges; `components` and `interfaces` are not, and their absence raises.

    The absence is REPORTED, not swallowed. `reach_declared` distinguishes "this document has no
    reach block" from "this document declares a reach block containing nothing" -- the same
    distinction parse_block() refuses to blur for components, and one a reconciler counting
    forward gaps has to be able to make. Collapsing both to an empty dict here would hand the
    section 34.4 check a silent zero, which is the shape it exists to detect.
    """
    text = read_frozen_architecture(repo_root, architecture_filename)
    try:
        reach = parse_reach(text)
        reach_declared = True
    except BlockMissing:
        reach = {}
        reach_declared = False
    return {
        "components": parse_components(text),
        "interfaces": parse_interfaces(text),
        "reach": reach,
        "reach_declared": reach_declared,
    }


class MalformedReachEdge(FrozenArchitectureError):
    """A reach edge is missing a field edge_canonical_id needs -- refused by name, not a bare
    KeyError. A crash is not a refusal (found live, ATLASSN-135: the original implementation
    raised KeyError on the corrupt-A test fixture, which has no `path`)."""


def edge_canonical_id(name, edge):
    """The registry's own join key for a reach edge (AI-5 schema doc amendment, re-frozen
    2026-09-12 17:05Z): repo:path:direction:name -- the QUAD form. The triple alone
    (repo:path:direction) is not unique on this document's own real content:
    registry_gate_client_wired and registry_write_guard_wired share an identical triple and
    differ only by declaration name, so a triple-keyed join silently merges two distinct
    wiring claims into one (found live, ATLASSN-135, Bob atlas-sonnet). The declaration name
    is part of the canonical identity, not metadata. Colon-joining is safe: ENTRY_NAME's own
    pattern ([A-Za-z_][\\w-]*) cannot contain a colon, so the four fields can never collide at
    a separator -- if that name pattern is ever loosened to admit a colon, this join breaks
    silently, so revisit this function if it is."""
    missing = [field for field in ("repo", "path", "direction") if field not in edge]
    if missing:
        raise MalformedReachEdge(
            f"reach edge {name!r} is missing required field(s) {missing} -- cannot compute "
            f"its canonical id"
        )
    return f"{edge['repo']}:{edge['path']}:{edge['direction']}:{name}"
