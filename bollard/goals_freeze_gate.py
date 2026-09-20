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

FORE-510: this gate used to fail OPEN on any path resolving to NO declared component -- the
write passed silently (`:not-implementation-path`), which is how stage_order_gate.py and
ledger_write_guard.py got fully implemented before ARCHITECTURE.md ever declared them: a
wholly undeclared component bypasses the entire architecture/design discipline by simply
never being named. Now, for a project that has opted into strict component scope
(cc.STRICT_SCOPE_MARKER), a SOURCE-file write resolving to no declared component DENIES
unless the path is covered by an explicit, reasoned out-of-scope declaration
(cc.OUT_OF_SCOPE_ZONES_RELPATH, or an exact .foreman/ungated.txt entry). Without the marker,
behavior is unchanged except the pass-through is no longer silent: rule
`:undeclared-component-path-unenforced` records every write strict mode WOULD have denied,
the same adoption-measurement pattern as architecture_gate.py's ledger-*-unenforced rules.
Why opt-in rather than an unconditional flip: measured 2026-09-07 (independently re-measured
by the Opus cold-falsification review, which corrected the first pass's static counts), 71 of
81 .foreman-marked projects on this machine register both hard gates, 74,767 of their source
files currently resolve to no declared component, and 7 projects have ZERO declared
components -- the review live-fired the gate at real uncovered paths in the three largest
(deny, deny, deny), so an unconditional flip really would have bricked them.

DISCLOSED LIMITATIONS of the strict deny -- "strict default-deny" ships narrower than it
reads, and each residual is named here on purpose (Opus review F1/F3):
  * SOURCE_EXTENSIONS files only (the implementation-code gap FORE-510 is about); non-source
    writes to undeclared paths still pass. Known extension gaps at review time: .zsh (on a
    zsh-primary machine), .php, .ipynb, .scala, .tf are all absent from SOURCE_EXTENSIONS.
  * The Bash arm inherits extract_bash_write_targets' fail-toward-NOT-gating blind spots,
    which were chosen when the default was allow and NOT re-litigated here: a relative target
    after a relative `cd` (`cd undeclared && printf 'x' > x.py`) and an interpreter-mediated
    write (`python3 -c "open(...,'w')"`) both pass strict mode silently. Closing shell-parse
    blind spots is its own ticket, not this one.
  * The top-level dotdir carve-out (_is_control) exempts .github/, .claude/, and every other
    root dotdir from strict mode -- correct for bootstrap (a project must be able to touch
    its own .claude/settings.json pre-architecture), but real implementation code does live
    in .github/scripts and .claude/hooks in this ecosystem.
FORE-510's ticket names both hard gates; this change deliberately touches only this one.
architecture_gate.py stops gating per-path the moment ARCHITECTURE.md exists (repo-wide
artifact existence only), so the post-declaration undeclared-path gap belongs entirely here.
"""

import hashlib
import json
import sys
from collections import namedtuple
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
    # FORE-CHV2-102: was unconditionally "GOALS.json" -- see component_coupling.goals_json_path's
    # own docstring for why that collided fourteen declared components onto one shared file and
    # left hooks/agent_dispatch_gate.GOALS.json (a real, already-frozen, hash-valid file) dead on
    # disk since FORE-462. A component whose root isn't shared resolves to the exact same path as
    # before this fix -- see that function's own docstring for why this is safe.
    goals_path = cc.goals_json_path(component, comp_root, project_root, component_map)

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


RootState = namedtuple(
    "RootState",
    "project_root component_map malformed strict zones zones_malformed ungated collisions")


def state_for_root(project_root, cache):
    """Every piece of per-PROJECT state this gate consults, loaded once per root and memoized
    for the life of one payload.

    FORE-577, AND THE PART OF IT THAT IS NOT JUST MOVING find_project_root. FORE-510 loaded all
    of this ONCE PER INVOCATION and carried a comment saying the hoisting was deliberate. That
    was correct while a call resolved exactly one project root. It stops being correct the
    moment a call can implicate two, because every one of these is per-PROJECT: hoisted, the
    gate reads project A's strict-mode setting, out-of-scope zones, ungated list and
    shared-root collisions and applies them to a path in project B. That is the same class of
    error this ticket exists to remove, reintroduced one level down inside the fix itself.

    Fired before this moved, with a nested child project inside a strict parent, both real
    Foreman projects with their own frozen components:

        cwd=PARENT (strict), write a .py in the NON-STRICT CHILD    DENY
        cwd=CHILD,           write a .py in the strict PARENT       SILENT

    The first is a false deny -- a file judged against a strict setting its own project never
    opted into -- and the second is the same fail-open this ticket is about, reached through
    the strict predicate rather than through the component map. Both flip with this move.

    THE OLD COMMENT IS REWRITTEN, NOT LEFT IN PLACE, on purpose: it documented the hoisting as
    an optimisation, so a later reader finding the load inside a loop would otherwise assume it
    drifted there and hoist it back.

    SEQUENCING CONSTRAINT (Opus review F2, made mechanical per QA-Bob): claude-hooks-v2 itself
    must NOT create the strict marker until FORE-508/509 (the shared-GOALS.json bare-file
    collision) is resolved -- a newly-declared component in a shared root inherits the
    already-frozen shared GOALS.json and never gets criteria of its own, so strict mode's
    remedy would buy naming but not design-and-scope there. This is not left as prose:
    shared_component_roots() detects the collision live, and deny_undeclared() re-scopes its own
    remedy whenever one exists, so the constraint stays honest without anyone re-reading a
    ticket.

    FORE-339: parse_component_map returns (map, malformed). This gate's established posture is
    DENY-or-nothing with every ambiguity failing toward DENY (unreadable GOALS.json, missing
    freeze, hash mismatch -- never ASK anywhere in this file), so a malformed component
    declaration gets the same treatment. Scoped narrowly by its caller -- only denies when the
    write ISN'T attributable to any known-good component in THIS root AND a malformed
    declaration exists here, since that is precisely the case a malformed line could be hiding
    (component_of() cannot match a glob that failed to parse).
    """
    if project_root not in cache:
        component_map, malformed = cc.parse_component_map(project_root)
        strict = cc.strict_scope_enabled(project_root)
        zones, zones_malformed = cc.parse_out_of_scope_zones(project_root)
        ungated, unused_ungated_malformed = cc.parse_ungated(project_root)
        cache[project_root] = RootState(
            project_root=project_root, component_map=component_map, malformed=malformed,
            strict=strict, zones=zones, zones_malformed=zones_malformed, ungated=ungated,
            collisions=cc.shared_component_roots(component_map) if strict else {})
    return cache[project_root]


def malformed_names(state):
    return ", ".join(m["name"] for m in state.malformed)


def deny_malformed_map(state, path_desc):
    hc.set_rule(f"{RULE_ID}:component-map-malformed")
    hc.deny(
        f"Foreman: {path_desc} isn't recognized as inside any declared component, "
        f"but ARCHITECTURE.md's components block has a malformed entry ({malformed_names(state)}) "
        f"that failed to parse. This write may belong to one of those components -- "
        f"fix the malformed declaration(s) before writing here."
    )


def undeclared_in_root(state, path):
    return cc.undeclared_source_path(path, state.project_root, state.component_map,
                                     state.zones, state.ungated)


def deny_undeclared(state, path_desc):
    """FORE-510: the strict-mode default-deny for a source write resolving to no declared
    component. Names both real remedies, and discloses ignored malformed zone entries
    rather than silently dropping them (a zone without a reason is never honored)."""
    malformed_note = ""
    if state.zones_malformed:
        count = len(state.zones_malformed)
        malformed_note = (
            f" NOTE: {count} malformed entr{'y' if count == 1 else 'ies'} in "
            f"{cc.OUT_OF_SCOPE_ZONES_RELPATH} (missing ' -- <reason>') "
            f"{'was' if count == 1 else 'were'} ignored -- a zone without a reason is "
            f"never honored."
        )
    if state.collisions:
        shared = "; ".join(f"{root or './'} shared by {', '.join(names)}"
                           for root, names in sorted(state.collisions.items()))
        malformed_note += (
            f" WARNING (FORE-508/509): this project has declared components sharing one "
            f"component root ({shared}), so declaring a NEW component in a shared root "
            f"would inherit that root's already-frozen GOALS.json and never get its own "
            f"criteria -- the declare-a-component remedy above buys naming but NOT "
            f"design-and-scope until the collision is resolved. Prefer an out-of-scope "
            f"zone entry for genuinely-non-component files meanwhile."
        )
    hc.set_rule(f"{RULE_ID}:undeclared-component-path")
    hc.deny(
        f"Foreman: {path_desc} is a source file that resolves to NO declared component in "
        f"ARCHITECTURE.md's components block, and this project has opted into strict "
        f"component scope ({cc.STRICT_SCOPE_MARKER}). An undeclared component bypasses "
        f"every architecture/design gate by never being named (FORE-510). Either declare "
        f"the component in ARCHITECTURE.md's components block and run "
        f"foreman:design-and-scope, or -- if this path is genuinely not a component (docs "
        f"tooling, tests, scratch) -- add it or its directory prefix (trailing '/') to "
        f"{cc.OUT_OF_SCOPE_ZONES_RELPATH} with ' -- <reason>'.{malformed_note}"
    )


def classify_targets(targets, cwd, cache):
    """Group this payload's write targets by the project that OWNS each one, and split each
    group into gated (attributable to a declared component) and unattributed.

    FORE-577. project_root used to be resolved ONCE from the payload cwd -- the writer's own
    location -- and every target was then attributed against THAT project's component map and
    checked against THAT project's GOALS.json. Fired, two sibling Foreman projects where one
    component is frozen and the other is not:

        cwd=A, target in A's unfrozen component    DENY     the gate works when cwd matches
        cwd=B, target in A's unfrozen component    SILENT   the cross-project fail-open
        cwd=A, target in B's frozen component      SILENT   the polarity control

    The project whose criteria were never frozen never got asked about a write into its own
    component. Returns an ordered mapping so the deny that fires is deterministic.
    """
    by_root = {}
    for target in targets:
        target_root = cc.project_root_for_target(target, cwd)
        if target_root is None:
            continue  # not inside any Foreman project -- not this gate's concern
        by_root.setdefault(target_root, []).append(target)

    classified = {}
    for target_root, root_targets in by_root.items():
        state = state_for_root(target_root, cache)
        gated, unattributed = [], []
        for target in root_targets:
            is_impl, component = cc.is_implementation_path(target, target_root,
                                                           state.component_map)
            if is_impl:
                gated.append((target, component))
            else:
                unattributed.append(target)
        classified[target_root] = (state, gated, unattributed)
    return classified


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    cwd = data.get("cwd")
    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        targets = [file_path]
    else:
        # tool_name == "Bash": same FORE-1 rationale as architecture_gate.py -- a denied
        # Edit/Write is trivially routed around with a shell redirect, tee, cp/mv, or sed -i.
        command = (data.get("tool_input") or {}).get("command", "")
        targets = list(cc.extract_bash_write_targets(command, cwd))

    classified = classify_targets(targets, cwd, {})
    if not classified:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    # THE THREE PASSES BELOW PRESERVE AN ORDERING THAT WAS A REAL BUG FIX, and they extend it
    # across roots rather than quietly re-scoping it to one. FORE-510 records that undeclared
    # targets used to be inspected only when NO target in the command was gated, so in strict
    # mode a single command touching both a gated-open declared path and an undeclared path
    # slipped the undeclared half through. Strict undeclared denies FIRST, regardless of what
    # else the command touches -- and now regardless of which other PROJECT it touches, since a
    # single-root reading of "what else the command touches" would reopen the same hole one
    # level up.
    for state, gated, unattributed in classified.values():
        if not gated and state.malformed and unattributed:
            deny_malformed_map(state, unattributed[0])
            return

    undeclared_seen = False
    for state, gated, unattributed in classified.values():
        undeclared = [t for t in unattributed if undeclared_in_root(state, t)]
        if not undeclared:
            continue
        undeclared_seen = True
        if state.strict:
            deny_undeclared(state, undeclared[0])
            return

    any_gated = False
    for target_root, (state, gated, unattributed) in classified.items():
        for target, component in gated:
            any_gated = True
            if _check(target, target_root, component, state.component_map):
                return

    if undeclared_seen:
        # Not opted in: no deny, but no silence either -- FORE-510's adoption signal, same
        # pattern as architecture_gate.py's ledger-*-unenforced rules. Set AFTER the _check loop
        # on purpose -- _check() itself sets :gate-open on every open target, and
        # last-writer-wins on hc.set_rule would clobber this rarer, more valuable row if it were
        # set first (the same rule-clobbering class as architecture_gate.py's N9-3 fix).
        hc.set_rule(f"{RULE_ID}:undeclared-component-path-unenforced")
        return
    if not any_gated:
        hc.set_rule(f"{RULE_ID}:not-implementation-path")
        return
    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
