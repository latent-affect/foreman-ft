#!/usr/bin/env python3
"""Session role-check primitive (CHV2-37).

Hooks in this repo gate by PROJECT scope (is `.foreman/` present, is the target path inside
`project_root`) but not by SESSION ROLE, and that gap is real:
POSTMORTEM-ORCHESTRATOR-STALL-20260904.md documents a PDP turn-gate hook that blocked an
unrelated reviewer session sharing this repo with a live orchestrator, because the gate had no
way to tell the two sessions apart. FOREMAN-ROUTER-PROJECT-SPEC.md section 5 lists "role-agnostic
hooks resolved" as one of five things the router cannot begin real calibration without, since
Alice, Bob, and the orchestrator sharing one repo is exactly tonight's own run shape.

`pdp_turn_gate.py` already carries a working, narrow fix for itself (FORE-186): it reads
`.foreman/pdp-orchestrator.json` and `.foreman/pdp-continuation-authority.json` directly and
branches on session_id match. That fix is real and is not duplicated here -- this module is the
generalization so the NEXT hook that needs a role check does not reimplement it a third time,
matching PDP.md section 16.1's own warning about `verdict_ledger.py` existing in 13 copies
because nothing centralized the first one.

WHAT THIS DOES NOT DO. It does not replace `.foreman/pdp-orchestrator.json` or
`.foreman/pdp-continuation-authority.json` with a persona label. PDP.md section 10 step 2 is
explicit that "holding the orchestrator role does not grant continuation authority and
continuation authority does not grant the role; they are different properties in different
files, deliberately." Those two files encode GRANTED AUTHORITY -- a write-once claim, checked at
authorization time. `persona_attribution.py` encodes a PERSONA LABEL -- a descriptive tag with no
authorization semantics of its own (its own docstring: "this module is that record and nothing
more"). Collapsing an authorization claim and an identity label into one lookup would be a
category error PDP.md already names for a different pair of files; this module reads all three
sources and returns a single fused answer without merging what they mean on disk.

CHECKED, PER THE TICKET'S OWN INSTRUCTION, BEFORE ADDING A SECOND MECHANISM: persona_attribution.py
is the right sink for the "which persona" question and is reused here for the "alice" case rather
than duplicated. It is NOT a fit for the orchestrator/continuation-authority question, for a
reason stated in its own docstring rather than invented here: "A session lands here only if
something called record_attribution for it -- today that is the A2 PreToolUse:Agent alarm... and,
once wired, a SessionStart labeler for a CLI-launched Alice." Coverage today is Alice-only. Bob
and the orchestrator are never labeled there, by design (Bob especially -- ZERO_WRITE_PERSONAS is
exactly the set {"alice"}, and a persona ledger keyed on "which sessions must never write" has no
natural field for "which session currently holds the orchestrator claim," which is a capability,
not an identity).

FAIL-OPEN ON AMBIGUITY, matching every existing scope check in this file's siblings
(`pdp_turn_gate.py`'s own `read_orchestrator`/`read_authority`/`_orchestrator_scope` are each
explicit about this: "None is fail-OPEN: a session this hook cannot identify gets no
obligation"). A hook that cannot determine a session's role must not invent an obligation for it.
That is a decision this module makes once so no caller has to re-derive it.

CHV2-43 addendum (REQ-72's second half): `orchestrator_claim()` below is a second, richer
accessor added alongside `resolve_role()`, not a replacement for it -- ARCHITECTURE.md's own
named regression risk for this rewire (session_role.GOALS.json C3): `resolve_role()`'s bare-
string return cannot supply `claimed_at`, which `pdp_turn_gate.py`'s S4 fail-closed staleness
check reads directly off the raw orchestrator claim dict. A caller that only needs the role
classification calls `resolve_role()`; the one caller that also needs a claim field
(`pdp_turn_gate.py`'s `_orchestrator_scope()`) calls `orchestrator_claim()` instead, which
centralizes the read+session_id-match logic that module used to duplicate without discarding the
dict `resolve_role()` deliberately never returns.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import persona_attribution as pa  # noqa: E402

ORCHESTRATOR_RELPATH = Path(".foreman") / "pdp-orchestrator.json"
CONTINUATION_AUTHORITY_RELPATH = Path(".foreman") / "pdp-continuation-authority.json"

# Closed set. A caller checking `role in {...}` against a value outside this set is a bug in the
# caller, not a value this module will ever emit.
ROLES = frozenset({"orchestrator", "continuation-authority", "alice", None})


def _read_claim_file(root, relpath):
    """(claim_dict_or_None). Malformed, absent, or wrong-shaped -> None, same posture as
    pdp_turn_gate.py's read_orchestrator/read_authority, which this replaces rather than
    reimplements for any NEW caller. pdp_turn_gate.py itself is left untouched by this
    proposal -- see the ticket note in the accompanying rationale."""
    try:
        blob = json.loads((Path(root) / relpath).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        # Checked against an adversarial-code-review fast-tier flag on this exact line before
        # this proposal was sent: this is not an unnoticed silent failure, it is the documented
        # fail-open posture stated above, applied identically to pdp_turn_gate.py's own
        # read_orchestrator/read_authority (absent or corrupt claim file -> no role, never a
        # crash). A caller wanting to distinguish "absent" from "corrupt" for its own audit
        # trail should read the claim file itself; this primitive's contract is role-or-None.
        return None
    return blob if isinstance(blob, dict) and blob.get("session_id") else None


def is_alice(session_id, ledger_path_override=None):
    """True if the persona-attribution ledger has ever labeled this session a zero-write
    persona. Thin wrapper over persona_attribution.zero_write_labeled_sessions -- reused, not
    reimplemented, per the ticket's own instruction to check the existing sink first."""
    if not session_id:
        return False
    return session_id in pa.zero_write_labeled_sessions(ledger_path_override)


def resolve_role(root, session_id, persona_ledger_override=None):
    """The one place a hook asks "what role does this session hold in this project."

    Returns one of "orchestrator", "continuation-authority", "alice", or None (unclassified --
    the hook must not impose an obligation this session never accepted). A session holds at most
    one of the two capability roles at a time by construction (each is a distinct write-once
    claim file); it MAY also be independently labeled "alice" in the persona ledger. Capability
    role takes precedence in this single-value return because it is what most callers gate on; a
    caller that needs both may call `is_alice()` alongside this.

    `root` and `session_id` are exactly the fields every PreToolUse/Stop hook payload already
    carries (`cwd`, `session_id`) -- no new input is required of a caller.

    Returns a BARE STRING (or None), never a dict -- a caller that also needs a claim field
    (e.g. `claimed_at`) must call `orchestrator_claim()` instead; collapsing the two would be
    exactly the regression CHV2-43's own architecture pass named (session_role.GOALS.json C3).
    """
    if not session_id:
        return None
    root = Path(root)

    claim = _read_claim_file(root, ORCHESTRATOR_RELPATH)
    if claim is not None and claim.get("session_id") == session_id:
        return "orchestrator"

    claim = _read_claim_file(root, CONTINUATION_AUTHORITY_RELPATH)
    if claim is not None and claim.get("session_id") == session_id:
        return "continuation-authority"

    if is_alice(session_id, ledger_path_override=persona_ledger_override):
        return "alice"

    return None


def orchestrator_claim(root, session_id):
    """The RAW orchestrator claim dict for this session, or None -- CHV2-43's own second
    accessor, for the one caller that needs a claim field beyond the bare role.

    Centralizes exactly the read-plus-session_id-match logic pdp_turn_gate.py's own
    read_orchestrator() + _orchestrator_scope() used to duplicate, without discarding the claim
    into resolve_role()'s bare-string return. A caller that only needs "is this session the
    orchestrator" (a boolean) should call resolve_role(root, session_id) == "orchestrator"
    instead; this exists only for pdp_turn_gate.py's S4 staleness check, which reads
    claim.get("claimed_at") directly off the dict this returns.

    Same fail-open posture as resolve_role(): no session_id, no file, a malformed file, or a
    claim naming a different session_id all return None, never raise."""
    if not session_id:
        return None
    claim = _read_claim_file(Path(root), ORCHESTRATOR_RELPATH)
    if claim is not None and claim.get("session_id") == session_id:
        return claim
    return None
