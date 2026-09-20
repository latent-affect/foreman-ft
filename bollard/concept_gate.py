#!/usr/bin/env python3
"""Foreman TPM stage-gate, instance 1 of 7 (FORE-71) -- no Write/Edit/Bash-write of a Foreman
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

PIN RE-VERIFICATION (FORE-198). The package's `graded_content_sha` records what the gate owner
actually graded. Until 2026-08-27 nothing ever read it back, so `docs/PRD.md` grew from the
pinned 1570 lines to 2254 with this gate reporting open and saying nothing. It now recomputes
each pinned artifact's live sha256 on every invocation and reports the result in its rule_id,
plus a `GATE_PIN_DRIFT` row on the append-only audit plane the first time a given drift state is
seen. The gate still OPENS on drift -- REQ-41/ARCHITECTURE.md section 7.6 policy (c) is explicit
that drift does not auto-invalidate a gate, and that a party who did not cause it accepts it on
the record instead. The change is that the drift stops being invisible, not that the gate gets
stricter.

WHY THIS HOOK DOES NOT WRITE `.foreman/gate-drift-ledger.json` ITSELF. That file holds signed,
human-reasoned acceptance attestations, as a single JSON array. Appending to it means a
read-modify-write of the whole document; several Claude Code sessions run against one repo at
once, so two concurrent detections can silently drop one of the existing signed entries. Putting
that write on a security gate's hot path also means a bug in the record-keeping can fail the
gate open, trading enforcement for bookkeeping in the wrong direction. And 7.6 wants a party who
REASONS about the drift, which a hook cannot do -- it can only observe. So this hook detects and
surfaces; materializing a ledger entry stays a separate, deliberate step, the same division
`stamp_ship_charter.py` already uses for SHIP-CHARTER.json's commit_hash.

KNOWN, UNFIXED LIMITATION (adversarial review, 2026-08-22, real finding not a hedge): the package
file itself is not gated by anything -- a builder denied by this hook can simply write
`{"decision": "go"}` to the package path in the same session and proceed. The "gate owner is a
separate context" discipline in TPM-GATE-SCOPE.md and foreman:tpm's SKILL.md is a process
convention this hook cannot mechanically enforce, the same "existence not quality" division of
labor architecture_gate.py already accepts for ARCHITECTURE-REVIEW.md's content. Stated here
plainly rather than implied as closed.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402
import audit_lib  # noqa: E402

RULE_ID = "FOREMAN-CONCEPT-GATE"
PACKAGE_RELPATH = ".foreman/tpm-gate-concept.json"
OPEN_DECISION = "go"

# FORE-198. The package records a fingerprint of what the gate owner actually graded. Nothing
# ever re-read it, so docs/PRD.md went from the pinned 1570 lines to 2254 with the gate still
# reporting open and saying nothing -- REQ-41/ARCHITECTURE.md section 7.6's "a pin that nothing
# re-verifies is a comment, not a control", found at this hook.
PIN_FIELD = "graded_content_sha"
DRIFT_MARKER_RELPATH = ".foreman/pin-drift-reported.json"
PIN_OK = "ok"
PIN_DRIFT = "drift"
PIN_ARTIFACT_MISSING = "artifact-missing"
PIN_KEY_UNRESOLVABLE = "key-unresolvable"
# Most actionable first. A package with several pins reports its worst state, and the per-key
# detail travels in the audit row regardless -- the rule_id is a summary, not the record.
PIN_PRECEDENCE = (PIN_DRIFT, PIN_ARTIFACT_MISSING, PIN_KEY_UNRESOLVABLE, PIN_OK)


def _resolve_pin_key(key, project_root):
    """Resolve one graded_content_sha key to a real path, STRICTLY.

    Accepts a project-relative path or an absolute path, and nothing else. The real package in
    foreman-v2 carries a key that is neither -- `QUALITY-BAR.md (canonical, /Users/m5/...)`,
    a path with prose wrapped around it. That key is reported as unresolvable rather than
    parsed, deliberately: teaching a gate to extract paths out of free-form annotation means
    guessing which file a human meant, and a wrong guess here hashes the wrong artifact and
    reports a confident, incorrect answer. The fix for that key belongs in the package format
    (write a clean path), not in this parser. Returns None when the key is not a plain path."""
    if not isinstance(key, str) or not key.strip():
        return None
    # Annotation guard runs FIRST, for both shapes. An absolute key with prose wrapped around
    # it is exactly as unresolvable as a relative one, and running this check only on the
    # relative branch made such a key fall through to Path(), fail to open, and report
    # pin-artifact-missing -- the right conclusion for the wrong reason, and a rule_id that
    # tells a reader the artifact vanished when really the key was never a path.
    # (foreman-v2-66, adversarial review of 155fc17, F4.)
    if any(ch in key for ch in "()") or key != key.strip() or " " in key:
        return None
    candidate = Path(key)
    if candidate.is_absolute():
        return candidate
    return project_root / key


def _pin_findings(project_root, package):
    """One finding per pinned artifact. Never raises for a per-key problem -- an artifact that
    cannot be hashed produces a finding SAYING so, because "could not check" reported as silence
    is the exact failure this whole ticket is about."""
    pins = package.get(PIN_FIELD)
    if not isinstance(pins, dict) or not pins:
        return []
    findings = []
    for key, pin in pins.items():
        finding = {"pin_key": key, "pinned_sha256": None, "pinned_lines": None,
                   "live_sha256": None, "live_lines": None, "resolved_path": None}
        if isinstance(pin, dict):
            finding["pinned_sha256"] = pin.get("sha256")
            finding["pinned_lines"] = pin.get("lines")
        resolved = _resolve_pin_key(key, project_root)
        if resolved is None:
            finding["state"] = PIN_KEY_UNRESOLVABLE
            finding["detail"] = "pin key is not a plain relative or absolute path"
            findings.append(finding)
            continue
        finding["resolved_path"] = str(resolved)
        try:
            blob = resolved.read_bytes()
        except OSError as exc:
            finding["state"] = PIN_ARTIFACT_MISSING
            finding["detail"] = f"pinned artifact could not be read ({exc})"
            findings.append(finding)
            continue
        finding["live_sha256"] = hashlib.sha256(blob).hexdigest()
        finding["live_lines"] = blob.count(b"\n")
        if not finding["pinned_sha256"]:
            finding["state"] = PIN_ARTIFACT_MISSING
            finding["detail"] = "pin entry carries no sha256 to compare against"
        elif finding["live_sha256"] == finding["pinned_sha256"]:
            finding["state"] = PIN_OK
            finding["detail"] = "live content still matches the graded fingerprint"
        else:
            finding["state"] = PIN_DRIFT
            finding["detail"] = "live content differs from what the gate owner graded"
        findings.append(finding)
    return findings


def _worst_state(findings):
    for state in PIN_PRECEDENCE:
        if any(f["state"] == state for f in findings):
            return state
    return PIN_OK


def _already_reported(project_root, findings):
    """True when this exact set of (key -> state/live hash) has already been written to the
    audit plane from this project, so an unchanged drift is not re-emitted on every write.

    Deliberately fails toward RE-REPORTING: an unreadable or malformed marker returns False.
    Over-reporting a real drift is noise; under-reporting it is the bug this hook is closing."""
    marker = project_root / DRIFT_MARKER_RELPATH
    try:
        seen = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(seen, dict):
        return False
    for f in findings:
        prior = seen.get(f["pin_key"])
        if not isinstance(prior, dict):
            return False
        if prior.get("state") != f["state"] or prior.get("live_sha256") != f["live_sha256"]:
            return False
    return True


def _mark_reported(project_root, findings):
    """Atomic (tmp + os.replace) so a concurrent session never observes a half-written marker.
    Any failure is swallowed on purpose: the marker is a de-duplication convenience, and losing
    it costs a duplicate audit row, which is the safe direction. It must never break the gate."""
    marker = project_root / DRIFT_MARKER_RELPATH
    payload = {f["pin_key"]: {"state": f["state"], "live_sha256": f["live_sha256"],
                              "reported_at": audit_lib.now_iso()} for f in findings}
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, marker)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _check_pins(file_path, project_root, package, data):
    """Re-verify the package's own recorded fingerprints against live content, and surface any
    drift. Returns the rule suffix for the caller to set.

    DOES NOT DENY, on purpose. REQ-41/ARCHITECTURE.md 7.6 policy (c) is explicit: drift does not
    auto-invalidate the gate, because re-running every gate on every edit of a document that
    changes hourly is unaffordable against REQ-4's cost ceiling. A named party who did not cause
    the drift accepts it on the record instead. This hook's whole job is to make sure that party
    knows there is something to accept.

    DOES NOT WRITE .foreman/gate-drift-ledger.json either -- see the module docstring."""
    findings = _pin_findings(project_root, package)
    if not findings:
        return "gate-open-no-pin"
    state = _worst_state(findings)
    if state == PIN_OK:
        return "gate-open"
    if not _already_reported(project_root, findings):
        audit_lib.audit_append(
            "GATE_PIN_DRIFT",
            session_id=data.get("session_id"),
            cwd=data.get("cwd"),
            severity="high",
            gate="concept",
            rule_id=RULE_ID,
            package=str(project_root / PACKAGE_RELPATH),
            triggering_write=str(file_path),
            worst_state=state,
            findings=findings,
            policy="REQ-41/ARCHITECTURE.md 7.6 policy (c): surfaced, not auto-invalidated. "
                   "A party who did not cause the drift must accept it on the record "
                   "(.foreman/gate-drift-ledger.json) before this gate is trusted as current.",
        )
        _mark_reported(project_root, findings)
    return f"pin-{state}"


def _check(file_path, project_root, data):
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

    # Gate is open on decision+stage. Everything below re-verifies the package's own pins and
    # never denies -- it only decides which rule_id this open verdict carries.
    try:
        suffix = _check_pins(file_path, project_root, package, data)
    except Exception as exc:  # noqa: BLE001 -- see below
        # A bug in the drift check must not take the GATE down with it. run() would catch this
        # and fail open, which is correct for the gate but would lose the fact that the pin
        # check never ran. Recorded as its own rule instead of collapsing into gate-open, so
        # "checked and clean" stays distinguishable from "never checked" -- the same distinction
        # this hook exists to restore.
        hc.set_rule(f"{RULE_ID}:pin-check-error")
        print(f"[concept_gate] pin drift check failed ({exc!r}); gate still open",
              file=sys.stderr)
        return False
    hc.set_rule(f"{RULE_ID}:{suffix}")
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

    cwd = data.get("cwd")

    if tool_name in ("Edit", "Write"):
        file_path = (data.get("tool_input") or {}).get("file_path")
        if not file_path:
            return
        targets = [file_path]
    else:
        # tool_name == "Bash": same FORE-1 coverage architecture_gate.py already has -- a denied
        # Edit/Write is trivially routed around with a shell redirect, tee, cp/mv, or sed -i.
        command = (data.get("tool_input") or {}).get("command", "")
        targets = list(cc.extract_bash_write_targets(command, cwd))

    # FORE-573. project_root used to come from find_project_root(cwd) -- the WRITER's location --
    # and _is_architecture_doc then asked whether the target was THAT project's ARCHITECTURE.md.
    # A write to a nested child project's own ARCHITECTURE.md is not the parent's, so the gate
    # reported not-in-scope and stayed silent while the child's concept stage was still open.
    #
    # Fired, parent and child both real Foreman projects with empty ledgers (concept not closed
    # in either):
    #   cwd=CHILD,  CHILD/ARCHITECTURE.md    DENY     the gate works when cwd matches
    #   cwd=PARENT, PARENT/ARCHITECTURE.md   DENY     the ordinary case
    #   cwd=PARENT, CHILD/ARCHITECTURE.md    SILENT   the cross-project fail-open
    #   cwd=CHILD,  CHILD/notes.md           SILENT   the negative control
    #
    # The target is resolved ONCE here, against the payload cwd, and the absolute path is what
    # both the root derivation and the scope test see. Resolving separately in two places is how
    # a relative path ends up measured against two different bases -- cwd for RESOLUTION is a use
    # the target genuinely needs, cwd for JURISDICTION is the defect.
    gated_any = False
    saw_a_project = False
    for raw_target in targets:
        try:
            candidate = Path(raw_target)
            if not candidate.is_absolute():
                candidate = (Path(cwd) if cwd else Path.cwd()) / candidate
            resolved_target = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        target_root = cc.project_root_for_target(str(resolved_target), cwd)
        if target_root is None:
            continue  # not inside any Foreman project -- that project never opted in
        saw_a_project = True
        if not _is_architecture_doc(str(resolved_target), target_root):
            continue
        gated_any = True
        if _check(str(resolved_target), target_root, data):
            return
        # N9-3's clobber, same shape architecture_gate.py carried until 2026-08-27: _check()
        # has already set the specific open-path rule (gate-open / pin-drift / ...) and
        # hc.set_rule is last-writer-wins on a module global, so re-setting it here would
        # discard it. Known limitation carried over verbatim: with several gated targets this
        # is last-target-wins rather than an aggregate rule.

    if not saw_a_project:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return
    if not gated_any:
        hc.set_rule(f"{RULE_ID}:not-in-scope")


if __name__ == "__main__":
    hc.run(main)
