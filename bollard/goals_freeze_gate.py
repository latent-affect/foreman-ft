#!/usr/bin/env python3
"""Foreman hard gate 2 of 2 -- no Write/Edit inside a declared component's implementation path
until THAT component's GOALS.json criteria are frozen: criteria_frozen_at is set and
integrity.criteria_hash_at_freeze matches a fresh hash of criteria[] (foreman-design.html v0.2
SS01 "The two hard hooks in front of implementation", resolving the v0.2 SS09 open question
toward per-component GOALS.json -- see v0.3 SS01).

Same posture as architecture_gate.py: DENY-or-nothing, project-local registration only,
.foreman/ marker self-check as defense in depth. Shares isImplementationPath() via
component_coupling.py -- one predicate, not two gates quietly disagreeing about their own
scope (v0.2 SS01).

Hash convention: sha256 of json.dumps(criteria, sort_keys=True, separators=(",", ":")),
hex digest, prefixed "sha256:" -- matching the "sha256:..." placeholder already in
GOALS.template.json's integrity block. A component whose GOALS.json exists but was never
frozen (criteria_frozen_at absent) is treated identically to a missing GOALS.json: denied,
not defaulted through.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402

RULE_ID = "FOREMAN-GOALS-FREEZE-GATE"


def criteria_hash(criteria):
    canonical = json.dumps(criteria, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _check(file_path, project_root, component, component_map):
    """The actual gate logic, shared by the direct Edit/Write path and the Bash-target loop
    below. Returns True if it denied (caller should stop there), False if the gate is open
    for this specific path."""
    comp_root = cc.component_root(component, component_map)
    goals_path = project_root / comp_root / "GOALS.json"

    if not goals_path.is_file():
        hc.set_rule(f"{RULE_ID}:no-goals-json")
        hc.deny(
            f"Foreman: component '{component}' has no GOALS.json at {goals_path}. Run "
            f"foreman:design-and-scope for this component before writing implementation files."
        )
        return True

    try:
        goals = json.loads(goals_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # Fails toward DENY, not toward silently allowing an unreadable GOALS.json through --
        # the same fail-safe direction as architecture_gate.py's review-file stat failure.
        hc.set_rule(f"{RULE_ID}:goals-json-unreadable")
        hc.deny(
            f"Foreman: {goals_path} exists but could not be read as JSON ({exc}). Fix or "
            f"regenerate it before writing implementation files in component '{component}'."
        )
        return True

    frozen_at = goals.get("criteria_frozen_at")
    stored_hash = (goals.get("integrity") or {}).get("criteria_hash_at_freeze")
    criteria = goals.get("criteria") or []

    if not frozen_at or not stored_hash:
        hc.set_rule(f"{RULE_ID}:not-frozen")
        hc.deny(
            f"Foreman: {goals_path}'s criteria are not frozen (criteria_frozen_at or "
            f"integrity.criteria_hash_at_freeze missing). Freeze the criteria before writing "
            f"implementation files in component '{component}'."
        )
        return True

    fresh_hash = criteria_hash(criteria)
    if fresh_hash != stored_hash:
        hc.set_rule(f"{RULE_ID}:hash-mismatch")
        hc.deny(
            f"Foreman: {goals_path}'s criteria changed since freeze (hash mismatch -- "
            f"expected {stored_hash}, computed {fresh_hash}). A criteria change after freeze "
            f"is an amendment event (GOALS.json's own amendments[] convention), not a silent "
            f"edit. Re-freeze deliberately before writing implementation files in component "
            f"'{component}'."
        )
        return True

    hc.set_rule(f"{RULE_ID}:gate-open")
    return False


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    project_root = cc.find_project_root(data.get("cwd"))
    if project_root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    component_map = cc.parse_component_map(project_root)

    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        is_impl, component = cc.is_implementation_path(file_path, project_root, component_map)
        if not is_impl:
            hc.set_rule(f"{RULE_ID}:not-implementation-path")
            return
        _check(file_path, project_root, component, component_map)
        return

    # tool_name == "Bash": same rationale as architecture_gate.py -- a denied Edit/Write
    # is trivially routed around with a shell redirect, tee, cp/mv, or sed -i.
    command = (data.get("tool_input") or {}).get("command", "")
    targets = cc.extract_bash_write_targets(command, data.get("cwd"))
    gated = []
    for target in targets:
        is_impl, component = cc.is_implementation_path(target, project_root, component_map)
        if is_impl:
            gated.append((target, component))
    if not gated:
        hc.set_rule(f"{RULE_ID}:not-implementation-path")
        return
    for target, component in gated:
        if _check(target, project_root, component, component_map):
            return
    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
