"""Vendored, independent copy of the path-component longest-match algorithm in
atlas/resolve/matcher.py -- NOT an import of it. hookclient's zero-dependency constraint
(ARCHITECTURE.md section 4) forbids importing any other atlas.* component, since none of them
carry the same zero-dependency guarantee transitively. GOALS.json C5 requires this to produce
identical output to atlas.resolve.matcher.resolve_cwd on the same input, verified by a
cross-check test in atlas/hookclient/tests/ (test code only -- the shipped module still never
imports atlas.resolve)."""


def _components(path_str):
    # Deliberately not pathlib.Path here either -- pathlib IS stdlib so it would not violate
    # the zero-third-party-dependency constraint, but using it would make this module textually
    # identical to resolve/matcher.py's own implementation, which defeats the point of an
    # independently-verified vendor copy. A plain string split, normalized, is different code
    # that must independently agree with the original on every test fixture.
    parts = [p for p in path_str.split("/") if p not in ("", ".")]
    return tuple(parts)


def root_matches(cwd, root):
    cwd_parts = _components(cwd)
    root_parts = _components(root)
    if len(cwd_parts) < len(root_parts):
        return False
    return cwd_parts[: len(root_parts)] == root_parts


def resolve_cwd_against_roots(cwd, roots_map):
    """roots_map: the index.json 'roots' dict, {root_path: {"prefixes": [...], "resolution": ...}}.
    Returns (resolution, candidates) where resolution is 'unique'/'ambiguous'/'unregistered'.
    Longest matching root wins; a tie at the same depth is ambiguous across the UNION of all
    prefixes registered at every root tied for longest -- matches section 7's contract: proceed
    with the candidate list, never pick one."""
    best_depth = -1
    candidates = []
    for root, entry in roots_map.items():
        if not isinstance(root, str) or not isinstance(entry, dict):
            continue
        prefixes = entry.get("prefixes")
        if not isinstance(prefixes, list):
            continue
        if root_matches(cwd, root):
            depth = len(_components(root))
            if depth > best_depth:
                best_depth = depth
                candidates = list(prefixes)
            elif depth == best_depth:
                candidates.extend(prefixes)
    if not candidates:
        return "unregistered", []
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        return "unique", candidates
    return "ambiguous", candidates
