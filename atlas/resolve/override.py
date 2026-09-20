"""Reads a root's .foreman/tessera-prefix override file: one line naming a prefix, checked into
that root's own repository. ARCHITECTURE.md section 3's original motivating case was a root
shared by two registered projects (AREM/FORE), but the document's own words -- "ATLAS reads
that override where present and prefers it over the registry" -- and the real, pre-existing
tessera_resolver.py precedent (imported by preflight_blocking_gate.py, which validates an
override against ANY registered prefix, not just tied candidates at one root) both support a
wider scope than "ambiguous-only" (ATLASSN-9, resolved -- see run.py's resolve_one). A malformed
override (unreadable, empty, or naming a prefix that isn't registered anywhere) is reported, not
silently treated as absent (F4's failure signature)."""

from pathlib import Path

OVERRIDE_RELATIVE_PATH = Path(".foreman") / "tessera-prefix"


class OverrideResult:
    __slots__ = ("prefix", "present", "valid", "detail")

    def __init__(self, prefix, present, valid, detail):
        self.prefix = prefix
        self.present = present
        self.valid = valid
        self.detail = detail


def read_override(root_path, valid_prefixes):
    """valid_prefixes: the set of project_prefix strings this override is allowed to name --
    callers pass ALL registered prefixes (matching tessera_resolver.py's own `known_prefixes`
    check), not narrowed to whatever the naive matcher found tied at this specific root, since
    an override's whole point is to name the correct answer even when the naive match was
    wrong, not merely to pick among what the naive match already proposed. An override naming
    something outside that set is invalid, reported as such, not silently applied or ignored."""
    override_path = Path(root_path) / OVERRIDE_RELATIVE_PATH
    if not override_path.is_file():
        return OverrideResult(None, present=False, valid=False, detail="no override file")
    try:
        content = override_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return OverrideResult(None, present=True, valid=False, detail=f"unreadable: {exc}")
    if not content:
        return OverrideResult(None, present=True, valid=False, detail="override file is empty")
    if content not in valid_prefixes:
        return OverrideResult(
            content, present=True, valid=False,
            detail=f"override names {content!r}, which is not a registered TESSERA prefix",
        )
    return OverrideResult(content, present=True, valid=True, detail="applied")
