#!/usr/bin/env python3
"""Foreman TPM stage-gate, instance 1 of 7 -- no Write/Edit/Bash-write of a Foreman
project's root ARCHITECTURE.md until a TPM concept-stage gate-review package exists at that root
AND carries decision == "go".

Same discipline as architecture_gate.py, one stage earlier: DENY-or-nothing, project-local
registration only (never ~/.claude/settings.json -- see architecture_gate.py's own comment on why
a user-wide hard gate would instantly break every project that hasn't opted in), existence-and-
decision-field check only, not quality judgment of the package's content. That judgment belongs to
the gate-owner persona who wrote the package (foreman:tpm, TPM-GATE-SCOPE.md), not to this hook.

Package convention: `<project_root>/.foreman/tpm-gate-concept.json`, written by foreman:tpm. Real
schema fields this hook reads: `decision` (one of go/kill/hold/recycle, Cooper's Stage-Gate model
-- only the exact string "go" opens the gate; kill/hold/recycle all deny, on purpose, so a Hold is
never silently read as a Kill or vice versa by anything downstream of this hook) and `stage`
(must equal "concept" -- a package copy-pasted from a different stage's gate must not silently
open this one). Everything else in the package (gate_owner_persona, tessera_ticket_ids, summary,
decided_at) is for a human or TPM's own future re-invocation to read, not for this hook to
interpret.

KNOWN, UNFIXED LIMITATION (adversarial review, 2026-08-22, real finding not a hedge): the package
file itself is not gated by anything -- a builder denied by this hook can simply write
`{"decision": "go"}` to the package path in the same session and proceed. The "gate owner is a
separate context" discipline in TPM-GATE-SCOPE.md and foreman:tpm's SKILL.md is a process
convention this hook cannot mechanically enforce, the same "existence not quality" division of
labor architecture_gate.py already accepts for ARCHITECTURE-REVIEW.md's content. Stated here
plainly rather than implied as closed.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402

RULE_ID = "FOREMAN-CONCEPT-GATE"
PACKAGE_RELPATH = ".foreman/tpm-gate-concept.json"
OPEN_DECISION = "go"


def _check(file_path, project_root):
    """The actual gate logic, shared by the direct Edit/Write path and the Bash-target loop
    below. Returns True if it denied (caller should stop there), False if the gate is open for
    this specific path."""
    package_path = project_root / PACKAGE_RELPATH

    if not package_path.is_file():
        hc.set_rule(f"{RULE_ID}:no-package")
        hc.deny(
            f"Foreman: {file_path} looks like ARCHITECTURE.md at {project_root}, but no TPM "
            f"concept-stage gate-review package exists at {package_path} yet. Run foreman:tpm "
            f"for the concept stage first."
        )
        return True

    try:
        package = json.loads(package_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # Fails toward DENY, not silently allowing an unreadable package through -- same
        # fail-safe direction as architecture_gate.py's own review-file stat failure.
        hc.set_rule(f"{RULE_ID}:package-unreadable")
        hc.deny(
            f"Foreman: {package_path} exists but could not be read as JSON ({exc}). Fix or "
            f"regenerate it via foreman:tpm before writing ARCHITECTURE.md."
        )
        return True

    stage = package.get("stage")
    if stage != "concept":
        hc.set_rule(f"{RULE_ID}:wrong-stage-{stage or 'missing'}")
        hc.deny(
            f"Foreman: {package_path}'s stage field is '{stage or '(missing)'}', not 'concept'. "
            f"A gate-review package from a different stage does not open this gate."
        )
        return True

    decision = package.get("decision")
    if decision != OPEN_DECISION:
        hc.set_rule(f"{RULE_ID}:decision-{decision or 'missing'}")
        hc.deny(
            f"Foreman: {package_path}'s concept-stage decision is "
            f"'{decision or '(missing)'}', not 'go'. A kill/hold/recycle decision does not open "
            f"this gate -- resolve it via foreman:tpm before writing ARCHITECTURE.md."
        )
        return True

    hc.set_rule(f"{RULE_ID}:gate-open")
    return False


def _is_architecture_doc(file_path, project_root):
    """The one thing this hook protects: the project-root ARCHITECTURE.md itself. Deliberately
    narrower than architecture_gate.py's is_pre_architecture_scope() -- that hook covers
    implementation code broadly; this one covers only the single file that starts the next
    stage, so the two hooks' scopes don't overlap or double-deny the same write."""
    try:
        resolved = (project_root / file_path).resolve() if not Path(file_path).is_absolute() \
            else Path(file_path).resolve()
    except OSError:
        # An unresolvable path can't be a match by definition -- fails toward "not this hook's
        # concern" (permissive on THIS predicate only), not toward a crash. The write itself
        # will fail on the same broken path regardless of what this hook decides.
        return False
    target = (project_root / "ARCHITECTURE.md").resolve()
    if resolved == target:
        return True
    # Case-fold fallback (FATAL, adversarial review 2026-08-22): Path.resolve() does not
    # normalise case, and this machine's filesystem is case-insensitive, so a lowercase
    # `architecture.md` satisfies is_file(ARCHITECTURE.md) downstream while matching neither
    # the exact-case check above nor architecture_gate.py's case-sensitive CONTROL_FILENAMES --
    # a gate that enforces nothing against a shift-key. Fails CLOSED on a case collision even on
    # a case-sensitive volume, where this would deny a genuinely distinct same-named-but-cased
    # file; that tradeoff is deliberate and belongs in the commit message, not a footnote.
    return resolved.parent == target.parent and resolved.name.lower() == "architecture.md"


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    project_root = cc.find_project_root(data.get("cwd"))
    if project_root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return  # this project never opted in -- never gated

    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        if not _is_architecture_doc(file_path, project_root):
            hc.set_rule(f"{RULE_ID}:not-in-scope")
            return
        if _check(file_path, project_root):
            return
        hc.set_rule(f"{RULE_ID}:gate-open")
        return

    # tool_name == "Bash": same coverage architecture_gate.py already has -- a denied
    # Edit/Write is trivially routed around with a shell redirect, tee, cp/mv, or sed -i.
    command = (data.get("tool_input") or {}).get("command", "")
    targets = cc.extract_bash_write_targets(command, data.get("cwd"))
    gated_targets = [t for t in targets if _is_architecture_doc(t, project_root)]
    if not gated_targets:
        hc.set_rule(f"{RULE_ID}:not-in-scope")
        return
    for target in gated_targets:
        if _check(target, project_root):
            return
    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
