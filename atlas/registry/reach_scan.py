"""ATLASSN-135 -- the second, independent derivation for D4 reconciliation (ARCHITECTURE.md
section 34.4, GOALS.json C9). architecture_parse.py (ATLASSN-127) is the first derivation, a
line-by-line state machine tracking fence open/close. This module is the second, required to
be genuinely independent -- 34.4's own words: "structural fenced-block parse; independent
line-regex path, separate modules sharing no helper." Sharing no helper is not a style
preference: C9's whole point is that two independently-written parsers looking at the same
bytes should agree, and agreement between two paths that secretly share code is not evidence
of anything -- the same blind spot would just agree with itself. So this module extracts each
fenced block with ONE multiline regex per call rather than a line-iteration state machine,
parses each entry line with a SINGLE generic `name: value` regex plus a post-hoc
json.loads()+isinstance() type check rather than two block-kind-specific compiled patterns,
defines its own exception hierarchy under different names, and re-derives its own frozen-HEAD
git read rather than importing read_frozen_architecture. Per architecture_parse.py's own
"NOTE ON I10 / ATLASSN-135": do not refactor the two into shared code later. The independence
is the check. Authored by a different session than architecture_parse.py's author, per the
operator's own standing methodology for exactly this situation.
"""

import json
import re
import subprocess
from pathlib import Path

ARCHITECTURE_FILENAME = "ARCHITECTURE.md"
FREEZE_MARKER_PATH = ".foreman/frozen.json"
BLOCK_KINDS = ("components", "interfaces", "reach")
ENTRY_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.+)$")


class ReachScanError(Exception):
    """Base for every failure this module raises."""


class FreezeMarkerAbsent(ReachScanError):
    """No freeze marker at committed HEAD -- nothing ratified to scan."""


class ArchitectureDocUnreadable(ReachScanError):
    """ARCHITECTURE.md could not be read at committed HEAD."""


class BlockAbsent(ReachScanError):
    """No fenced block of the requested kind exists anywhere in the document."""


class BlockCorrupt(ReachScanError):
    """A line inside a fenced block does not parse, or its value is the wrong shape."""


def block_pattern(kind):
    if kind not in BLOCK_KINDS:
        raise ValueError(f"unknown block kind {kind!r}; expected one of {BLOCK_KINDS}")
    return re.compile(r"```yaml[ \t]+" + re.escape(kind) + r"[ \t]*\r?\n(.*?)```", re.DOTALL)


def entry_lines_of(block_text):
    """Non-empty, non-comment lines inside one fenced block's captured text."""
    for raw in block_text.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def value_shape_ok(kind, value):
    if kind == "components":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def shape_description(kind):
    return "a JSON array of strings" if kind == "components" else "a quoted-key JSON object"


def scan_block(text, kind):
    """{name: value} for every entry across EVERY fenced block of `kind` in `text`. Every
    occurrence counts -- independently re-derived via re.finditer over the whole document
    rather than a single-pass line iterator, so both modules reach the same "every block
    counts" property by different mechanisms."""
    pattern = block_pattern(kind)
    matches = list(pattern.finditer(text))
    if not matches:
        raise BlockAbsent(
            f"ARCHITECTURE.md declares no ```yaml {kind} block; refusing to report an empty "
            f"{kind} map, which a caller cannot distinguish from a document that legitimately "
            f"declares nothing"
        )
    entries = {}
    for match in matches:
        for line in entry_lines_of(match.group(1)):
            entry_match = ENTRY_LINE_RE.match(line)
            if not entry_match:
                raise BlockCorrupt(
                    f"a line inside a ```yaml {kind} block does not parse as `name: "
                    f"{shape_description(kind)}`: {line!r}"
                )
            name, value_text = entry_match.groups()
            try:
                value = json.loads(value_text)
            except json.JSONDecodeError as exc:
                raise BlockCorrupt(
                    f"{kind} entry {name!r} has a value that is not valid JSON ({exc}); the "
                    f"bare-key YAML form is not accepted -- quote the keys"
                ) from exc
            if not value_shape_ok(kind, value):
                raise BlockCorrupt(f"{kind} entry {name!r} is not {shape_description(kind)}")
            if name in entries:
                raise BlockCorrupt(
                    f"{kind} entry {name!r} is declared twice; the later declaration would "
                    f"silently win, so this refuses instead"
                )
            entries[name] = value
    return entries


def scan_components(text):
    return scan_block(text, "components")


def scan_interfaces(text):
    return scan_block(text, "interfaces")


def scan_reach(text):
    return scan_block(text, "reach")


def git_show_head(repo_root, rel_path):
    """Independent re-derivation of "read this file at committed HEAD" -- check_output +
    CalledProcessError rather than architecture_parse.run_git's run()+returncode check, a
    genuinely different call shape around the same git primitive."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), "show", f"HEAD:{rel_path}"],
            stderr=subprocess.PIPE, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ArchitectureDocUnreadable(
            f"could not read {rel_path} at HEAD of {repo_root}: "
            f"{(exc.stderr or '').strip() or 'git exited ' + str(exc.returncode)}"
        ) from exc


def freeze_marker_present(repo_root):
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"HEAD:{FREEZE_MARKER_PATH}"],
        capture_output=True,
    )
    return proc.returncode == 0


def read_frozen_text(repo_root, architecture_filename=ARCHITECTURE_FILENAME):
    repo_root = Path(repo_root)
    if not freeze_marker_present(repo_root):
        raise FreezeMarkerAbsent(
            f"{repo_root} has no {FREEZE_MARKER_PATH} at committed HEAD, so it has no ratified "
            f"state to present; refusing to read its working tree instead"
        )
    return git_show_head(repo_root, architecture_filename)


def scan_frozen_repo(repo_root, architecture_filename=ARCHITECTURE_FILENAME):
    """Same public shape as architecture_parse.parse_frozen_repo() -- {components, interfaces,
    reach, reach_declared} -- so the reconciler can swap either derivation in without caring
    which one it holds. Independently re-derived, not shared."""
    text = read_frozen_text(repo_root, architecture_filename)
    try:
        reach = scan_reach(text)
        reach_declared = True
    except BlockAbsent:
        reach = {}
        reach_declared = False
    return {
        "components": scan_components(text),
        "interfaces": scan_interfaces(text),
        "reach": reach,
        "reach_declared": reach_declared,
    }


class MalformedReachEdge(ReachScanError):
    """A reach edge is missing a field edge_canonical_id needs -- refused by name, not a bare
    KeyError. Written independently from architecture_parse.MalformedReachEdge (same name,
    different class, subclasses THIS module's own base -- no shared import)."""


def edge_canonical_id(name, edge):
    """Same registry join-key convention (quad: repo:path:direction:name) as
    architecture_parse.edge_canonical_id -- written independently. Colon-joining is safe:
    reach entry names match [A-Za-z_][\\w-]* and cannot contain a colon, so the four fields
    never collide at a separator -- revisit this function if that name pattern is ever
    loosened to admit one."""
    for field in ("repo", "path", "direction"):
        if field not in edge:
            raise MalformedReachEdge(
                f"reach edge {name!r} has no {field!r} field -- cannot compute its canonical id"
            )
    return edge["repo"] + ":" + edge["path"] + ":" + edge["direction"] + ":" + name
