"""Cross-project bug-ticket routing table. Explicit data, checked at import time
against real paths -- not inferred at runtime, per the operator's own instruction.

Four cases, the operator's own table verbatim, extended with an explicit "no match" case
(the operator's table didn't name one; leaving classify() to fall through silently would
have been the exact kind of collapsed-state bug this whole project keeps finding):

| Bug location                                  | Routes to                        |
|------------------------------------------------|-----------------------------------|
| Hook shared globally across all Foreman projects| AREM (agent-remediation)          |
| Foreman's own pipeline/skill logic              | FORE (Foreman's own project)      |
| Hook specific to one project's build            | that project's own resolved prefix|
| TESSERA's own code                              | TESS (recursive, expected)        |
| Doesn't match any of the above                  | None -- a real, surfaced "uncategorized" case, not a silent guess |

The first two both live under ~/.claude/hooks/ or ~/.claude/skills/foreman/ -- the split is
NOT "is it in this directory" but "is it Foreman-specific code," matching the scope paragraph
already drawn in ARCHITECTURE.md ('Not the whole ~/.claude/hooks/ directory -- that also
holds unrelated guards... belonging to agent-remediation, not Foreman').

CLAUDE_ROOT/TESSERA_ROOT default to /path/to/... tokens. install-dev-harness.sh replaces
those tokens. CLAUDE_ROOT / TESSERA_CWD env vars override.
"""

import os
from pathlib import Path

CLAUDE_ROOT = Path(os.environ.get("CLAUDE_ROOT", "/path/to/home/.claude")).expanduser().resolve()
TESSERA_ROOT = Path(os.environ.get("TESSERA_CWD", "/path/to/ticket-system")).expanduser().resolve()

# Components declared as Foreman's own, per ARCHITECTURE.md's components block. A file is
# "Foreman's own pipeline/skill logic" if it's one of these, or anywhere under skills/foreman/.
FOREMAN_OWN_HOOK_STEMS = {
    "architecture_gate.py", "goals_freeze_gate.py", "component_coupling.py", "hook_common.py",
    "concept_gate.py", "preflight_blocking_gate.py", "ship_readiness_gate.py",
    "verdict_ledger.py", "foreman_evidence.py", "stamp_ship_charter.py",
}
FOREMAN_OWN_DIRS = {
    "tessera_resolver", "tier_triage_gate", "freeze_reentry_gate", "ticket_status_gate",
    "cross_project_routing", "dependency_provenance_gate", "local_review", "lib",
}


def relative_or_none(path, root):
    try:
        return Path(path).resolve().relative_to(root)
    except ValueError:
        return None


def classify(file_path, registered_project_resolver=None):
    """Returns a TESSERA prefix string, or None if the path matches none of the four cases.

    registered_project_resolver, if given, is a callable(project_root) -> resolve() result
    (see tessera_resolver.py) used for the "project-specific hook" case -- injected rather
    than imported directly, so this module stays independently testable without a live
    TESSERA store.
    """
    p = Path(file_path).resolve()

    rel_to_tessera = relative_or_none(p, TESSERA_ROOT)
    if rel_to_tessera is not None:
        return "TESS"

    rel_to_claude = relative_or_none(p, CLAUDE_ROOT)
    if rel_to_claude is not None:
        parts = rel_to_claude.parts
        if parts and parts[0] == "skills" and len(parts) > 1 and parts[1] == "foreman":
            return "FORE"
        if parts and parts[0] == "hooks" and len(parts) > 1:
            if parts[1] in FOREMAN_OWN_HOOK_STEMS or parts[1] in FOREMAN_OWN_DIRS:
                return "FORE"
            return "AREM"
        # Anything else under ~/.claude (settings.json, other skills, tools/, etc.) is
        # genuinely uncategorized by this table -- not silently AREM or FORE.
        return None

    if registered_project_resolver is not None:
        # Walk up looking for a .foreman/ marker the same way find_project_root does, then
        # resolve THAT root's own TESSERA prefix -- "that project's own resolved prefix".
        for candidate in (p, *p.parents):
            if (candidate / ".foreman").is_dir():
                result = registered_project_resolver(candidate)
                if result.get("status") == "ok":
                    return result["prefix"]
                return None
    return None


# Structural dependency map for the pre-flight gate: which OTHER prefixes must also be
# checked for blocking tickets before starting build work in a given project. Every
# Foreman-managed project depends on FORE (Foreman's own gating logic governs it) and AREM
# (the shared, globally-registered guard hooks run in every session regardless of project).
# FORE and AREM don't depend on themselves; excluded explicitly rather than filtered at
# call time, so the map is the single source of truth, not a rule plus an exception.
DEPENDS_ON = {
    "FORE": {"AREM"},
    "AREM": set(),
    "TESS": {"FORE", "AREM"},
}
DEFAULT_DEPENDS_ON = {"FORE", "AREM"}


def depends_on(prefix):
    return DEPENDS_ON.get(prefix, DEFAULT_DEPENDS_ON)
