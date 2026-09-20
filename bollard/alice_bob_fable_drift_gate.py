#!/usr/bin/env python3
"""PROVENANCE drift gate for hooks/alice_bob_fable/ (CHV2-40, per Clint Eastwood's FORE-325
fork verdict, comment 2122 and its CHV2-39 resync-precondition follow-up, comment 2128).

hooks/alice_bob_fable/ is a PROMOTED ARTIFACT as of CHV2-39: the anchor
(/Users/Shared/alice-bob-rebuild-root/alice-bob-rebuild, hooks/alice_bob/) is canonical, this
directory is a one-way resync target, and Clint's verdict names the standing rule explicitly:
"no direct commits to hooks/alice_bob_fable/ except promotion commits naming an anchor SHA."
CHV2-41 writes that rule where a human/session reading the directory sees it (README.md); this
gate is the mechanical half -- the check that fails loudly if it's violated, rather than relying
on the README alone.

WHY THE ENFORCEMENT IS "MATCHES THE LIVE ANCHOR", NOT "MATCHES THE PINNED SHA". A gate that
denied on `content != <hash recorded at the last resync>` would deny the NEXT legitimate resync
too, the moment the anchor advances -- exactly wrong. The real distinguishing question is never
"does this match some past snapshot," it is "is this content something the anchor itself
currently contains." So this gate reads the anchor's OWN current file, live, off disk, and
compares byte-for-byte. A write that reproduces the anchor's current content is a resync,
whoever/whatever performs it. A write that does not is a hand-edit, full stop -- CHV2-40's own
scope note (this is about THIS pair, not a general provenance model) makes that binary check
sufficient; it does not need to reconstruct WHY content differs, only THAT it does.
`.foreman/alice-bob-fable-provenance.json` (this ticket's other half) is the durable, human-
readable RECORD of which anchor commit the last real resync used -- record-keeping, not this
gate's own enforcement input.

`Edit` IS DENIED OUTRIGHT for any in-scope path, unconditionally, never merely disadvantaged.
This mirrors the ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md's own already-documented lesson (section
4.4.5, "`Edit` reintroduced N1 in miniature"): verifying an Edit call against a byte-exact
target would require this gate to re-implement Edit's old_string/new_string application itself,
or accept a real TOCTOU window between a read-only check and the tool's own write -- the same
defect class that design doc already found and fixed by removing the shape rather than trying
to model it safely. `Write` carries the full intended content in `tool_input.content` and needs
no such reconstruction, so it is the only tool this gate can verify directly.

FAIL-CLOSED IF THE ANCHOR ITSELF CANNOT BE READ. Every other scope-determination check in this
hooks/ directory (pdp_turn_gate.py's own read_orchestrator/read_authority, for two examples)
fails OPEN on ambiguity, on the stated principle that a hook which cannot identify its own scope
must not invent an obligation. That principle does not transfer here: this gate's SCOPE (is the
target path under hooks/alice_bob_fable/) is unambiguous the moment `tool_input.file_path`
resolves, so the open question is never "does this obligation apply" -- it is "can the promised
mechanism confirm this write is legitimate," and answering "yes" to that from an unreadable
anchor would be asserting a comparison this gate never actually performed. A promoted-artifact
directory that quietly stops verifying anything the moment its source of truth is unreachable is
a worse failure mode than a same-directory write blocked until the anchor is reachable again.

ENV OVERRIDES exist for test isolation only, same pattern every sibling hook in this directory
already uses (DISPATCH_WRITER_TESSERA_DB_PATH, PERSONA_ATTRIBUTION_LEDGER, etc.) -- never meant
to be set in a real invocation, and never read from anything payload-derived.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402

RULE_ID = "CHV2-40-ALICE-BOB-FABLE-DRIFT"

DEPLOY_ROOT_ENV = "ALICE_BOB_FABLE_DEPLOY_ROOT"
ANCHOR_ROOT_ENV = "ALICE_BOB_FABLE_ANCHOR_ROOT"

DEFAULT_DEPLOY_ROOT = Path("/Users/m5/dev/claude-hooks-v2/hooks/alice_bob_fable")
DEFAULT_ANCHOR_ROOT = Path(
    "/Users/Shared/alice-bob-rebuild-root/alice-bob-rebuild/hooks/alice_bob")

ANCHOR_REPO_HINT = "/Users/Shared/alice-bob-rebuild-root/alice-bob-rebuild"


def deploy_root():
    override = os.environ.get(DEPLOY_ROOT_ENV)
    return Path(os.path.realpath(override)) if override else Path(
        os.path.realpath(DEFAULT_DEPLOY_ROOT))


def anchor_root():
    override = os.environ.get(ANCHOR_ROOT_ENV)
    return Path(os.path.realpath(override)) if override else Path(
        os.path.realpath(DEFAULT_ANCHOR_ROOT))


def in_scope_relpath(file_path, root):
    """None if file_path does not resolve under root or falls inside a __pycache__ segment
    (generated, never a source of truth for either side); the relative path otherwise."""
    if not file_path:
        return None
    try:
        real = Path(os.path.realpath(file_path))
        rel = real.relative_to(root)
    except (OSError, ValueError):
        return None
    if "__pycache__" in rel.parts:
        return None
    return rel


def main(data):
    if data.get("hook_event_name") not in (None, "PreToolUse"):
        return
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write"):
        return
    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    root = deploy_root()
    rel = in_scope_relpath(file_path, root)
    if rel is None:
        return

    if tool_name == "Edit":
        hc.deny(
            f"{RULE_ID}: {rel} is inside hooks/alice_bob_fable/, a promoted artifact "
            f"(CHV2-41) resynced one-way from the anchor at {ANCHOR_REPO_HINT} -- Edit is "
            f"refused here unconditionally, not merely disadvantaged: verifying a partial edit "
            f"against the anchor would mean re-implementing Edit's own old_string/new_string "
            f"application, the exact defect ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md section 4.4.5 "
            f"already found and fixed by removing the shape rather than modeling it. Edit the "
            f"anchor's real file directly, land it there under the anchor's own process, then "
            f"resync (Write, whole-file, matching the anchor's new content exactly)."
        )
        return

    # tool_name == "Write"
    content = tool_input.get("content")
    anchor = anchor_root()
    anchor_file = anchor / rel
    if content is None:
        hc.deny(
            f"{RULE_ID}: {rel} -- this Write call carries no readable content to verify "
            f"against the anchor. Fail-closed: a promoted-artifact write this gate cannot "
            f"check is refused, not silently allowed."
        )
        return
    try:
        anchor_bytes = anchor_file.read_bytes()
    except OSError as exc:
        hc.deny(
            f"{RULE_ID}: {rel} -- could not read the anchor's own file at {anchor_file} to "
            f"verify this write against it ({exc}). Fail-closed: this gate's whole mechanism "
            f"is comparison against the live anchor, and an unreachable anchor means that "
            f"comparison did not happen, not that it passed."
        )
        return
    if content.encode("utf-8") != anchor_bytes:
        hc.deny(
            f"{RULE_ID}: {rel} -- content does not match the anchor's current file at "
            f"{anchor_file} byte-for-byte. hooks/alice_bob_fable/ is resync-only (CHV2-41): "
            f"edit the anchor at {ANCHOR_REPO_HINT}, land it there under the anchor's own "
            f"ticket/review process, then resync this directory from it (matching CHV2-39's "
            f"own precedent -- one promotion commit naming the anchor SHA, diff-clean against "
            f"the anchor, all 6 component suites green before committing)."
        )
        return

    hc.set_rule(f"{RULE_ID}:matches-anchor")
    return


if __name__ == "__main__":
    hc.run(main)
