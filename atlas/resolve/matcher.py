"""Path-component-aware longest-match project resolution. ARCHITECTURE.md section 3: naive
string-prefix matching collides on five real registered root pairs (example-project/-ft,
example-audio-app/-ft/-prod, example-tool/-playground) because one root is a
literal string prefix of the other while never being its path-component ancestor. This module
compares Path component lists, never str.startswith."""

from pathlib import Path


def _components(path_str):
    return Path(path_str).parts


def root_matches(cwd, root):
    """True if root equals cwd, or is a real path-component ancestor of cwd. Normalizes via
    pathlib so a trailing-slash or double-slash inconsistency between the two strings does not
    produce a false non-match (F3's failure signature)."""
    cwd_parts = _components(cwd)
    root_parts = _components(root)
    if len(cwd_parts) < len(root_parts):
        return False
    return cwd_parts[: len(root_parts)] == root_parts


def resolve_cwd(cwd, projects):
    """projects: iterable of (project_prefix, source_root) pairs, source_root non-NULL.
    Returns (resolution, matched_root, candidates) where resolution is 'unique', 'ambiguous',
    or 'unregistered'; candidates is the list of project_prefix strings tied at the longest
    matching depth (empty when unregistered, length 1 when unique, length >=2 when ambiguous).
    Longest-match: a deeper (more path components) registered root wins over a shallower one
    that also matches; two roots at the SAME depth both matching is a genuine ambiguity, not
    resolved by any tiebreak -- that is the real AREM/FORE shape (both register the identical
    source_root, depth ties exactly)."""
    best_depth = -1
    candidates = []
    matched_root = None
    for prefix, root in projects:
        if root is None:
            continue
        if root_matches(cwd, root):
            depth = len(_components(root))
            if depth > best_depth:
                best_depth = depth
                candidates = [prefix]
                matched_root = root
            elif depth == best_depth:
                candidates.append(prefix)
    if not candidates:
        return "unregistered", None, []
    if len(candidates) == 1:
        return "unique", matched_root, candidates
    return "ambiguous", matched_root, sorted(candidates)
