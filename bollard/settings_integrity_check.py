#!/usr/bin/env python3
"""SessionStart integrity check: settings.json's hooks block vs. a hash-pinned canonical copy.

CHV2-2. Covers the 2026-09-03 20:00 settings.json deletion incident (a fail-first test's
cleanup path computed the file to unlink from the same vulnerable expression as the code under
test, collapsed to the absolute path, and removed the live global settings file -- see
POSTMORTEM-ORCHESTRATOR-STALL-20260904.md section 3) and issue #62486 (settings.json partially
rewritten mid-session, hooks stripped). Neither incident had any SessionStart check that would
tell the next session the file was missing or changed; this is that check.

Read-only against the live settings.json -- this hook never writes it. FORE-287's own auto-mode
classifier already blocks every session from editing ~/.claude/settings.json directly, and
restoring it correctly is the operator's call, not this hook's.

The canonical reference is a real, restorable copy plus a hash (reference/settings-hooks-
canonical.json, committed under git alongside this script) -- the pre-fore300 backup pattern
(settings.json.pre-fore300-bak-* in ~/.claude), made deliberate rather than left as an ad hoc
stray file outside version control.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
REFERENCE_PATH = Path(__file__).resolve().parent / "reference" / "settings-hooks-canonical.json"


def canonical_hash(hooks_block):
    """Order-independent within each event's handler list, and across event types.

    Same choice this project's own ARCHITECTURE.md made for its interfaces-block graph hash
    (FORE-329 section 'a live contradiction, resolved here'): a hash a human can flip by
    reordering an unrelated handler is a worse property than needing an explicit re-pin. What
    this check actually cares about is "did a handler get added, removed, or point somewhere
    different," not "did two independent gates swap positions in the same event's list."
    """
    normalized = {
        event: sorted(json.dumps(h, sort_keys=True) for h in handlers)
        for event, handlers in hooks_block.items()
    }
    blob = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def load_reference():
    try:
        return json.loads(REFERENCE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def main(data):
    # CHV2-11: this check is registered on both SessionStart and UserPromptSubmit (registration
    # is re-read from disk mid-session, confirmed empirically, FORE-322 -- a mid-session loss
    # needs the same catch a fresh session gets, not just one at startup). Every inject() call
    # below used to hardcode "SessionStart" as the event name regardless of which event actually
    # fired it -- correct only by accident when this only ever ran at SessionStart. Real,
    # load-bearing bug for this ticket specifically: a UserPromptSubmit-triggered alert would
    # have reported hookSpecificOutput.hookEventName as "SessionStart", mismatching the real
    # triggering event. Fixed by reading the actual event name from the hook's own input rather
    # than assuming it.
    event = data.get("hook_event_name") or "SessionStart"

    reference = load_reference()
    if reference is None:
        hc.inject(
            event,
            f"[SETTINGS-INTEGRITY ALERT] No canonical reference found at {REFERENCE_PATH}. "
            f"The live settings.json hooks block cannot be verified against anything, so hook "
            f"enforcement for this session is unverified. Establish a reference with "
            f"`python3 {Path(__file__).name} --pin` against a known-good settings.json.",
        )
        hc.mark("settings-integrity-no-reference")
        return
    try:
        live_text = SETTINGS_PATH.read_text()
    except OSError:
        hc.inject(
            event,
            f"[SETTINGS-INTEGRITY ALERT] {SETTINGS_PATH} is MISSING. This is the exact shape "
            f"of the 2026-09-03 incident (CHV2-2): a fail-first test's cleanup path collapsed to "
            f"this exact file and unlinked it. Every PreToolUse/PostToolUse/Stop gate registered "
            f"only in that file is NOT firing for this session. Restoring it correctly is the "
            f"operator's call -- FORE-287's classifier blocks every session from editing it "
            f"directly; do not attempt to recreate it yourself.",
        )
        hc.mark("settings-integrity-missing")
        return
    try:
        live = json.loads(live_text)
    except json.JSONDecodeError as exc:
        hc.inject(
            event,
            f"[SETTINGS-INTEGRITY ALERT] {SETTINGS_PATH} exists but is not valid JSON ({exc}). "
            f"This matches issue #62486's shape (settings.json partially rewritten mid-session, "
            f"hooks stripped). Hook enforcement state for this session is unknown.",
        )
        hc.mark("settings-integrity-invalid-json")
        return
    live_hooks = live.get("hooks", {})
    live_hash = canonical_hash(live_hooks)
    if live_hash == reference.get("hash"):
        return  # intact case: no output, matching session_flag_check.py's own convention
    pinned_events = set(reference.get("hooks", {}).keys())
    live_events = set(live_hooks.keys())
    removed = pinned_events - live_events
    added = live_events - pinned_events
    detail = []
    if removed:
        detail.append(f"event type(s) missing vs. pinned: {sorted(removed)}")
    if added:
        detail.append(f"event type(s) new vs. pinned: {sorted(added)}")
    if not detail:
        # CHV2-135. Event-type sets match, but the hash still differs, so canonical_hash's own
        # per-handler normalization guarantees at least one shared event type's handler set
        # differs -- the generic "differs within at least one" text collapsed a STRIPPED handler
        # (a real incident, CHV2-2-shaped -- investigate before any re-pin) and an ADDED handler
        # (safe, re-pin) into byte-identical wording. A reader following the alert's own
        # recommended action against a stripped-handler case would silently bless the incident.
        # Reuses canonical_hash()'s own per-handler normalization rather than inventing a second
        # one, so "differs" here means exactly what made the hash differ.
        handler_removed_events = []
        handler_added_events = []
        for shared_event in sorted(pinned_events & live_events):
            pinned_set = {json.dumps(h, sort_keys=True)
                         for h in reference.get("hooks", {}).get(shared_event, [])}
            live_set = {json.dumps(h, sort_keys=True) for h in live_hooks.get(shared_event, [])}
            if pinned_set - live_set:
                handler_removed_events.append(shared_event)
            if live_set - pinned_set:
                handler_added_events.append(shared_event)
        if handler_removed_events:
            detail.append(
                f"handler(s) MISSING within event type(s) {handler_removed_events} vs. pinned "
                f"-- do NOT assume this is safe to re-pin. This matches CHV2-2's stripped-"
                f"handler shape; investigate what removed it before treating the current state "
                f"as intentional")
        if handler_added_events:
            detail.append(
                f"handler(s) NEW within event type(s) {handler_added_events} vs. pinned -- "
                f"likely safe if intentional, re-pin to accept")
        if not detail:
            # Structurally unreachable given canonical_hash's own construction (see docstring
            # above), kept as a fail-loud backstop rather than silently falling through with an
            # empty detail list if that invariant is ever broken by a future edit.
            detail.append("same event types present and per-handler sets match by this "
                          "comparison, yet the canonical hash differs -- the hash and this "
                          "per-handler check have diverged; treat as unverified")
    hc.inject(
        event,
        f"[SETTINGS-INTEGRITY ALERT] {SETTINGS_PATH}'s hooks block does not match the pinned "
        f"canonical copy (pinned {reference.get('pinned_at', 'unknown')}). "
        f"{'; '.join(detail)}. If this is an intentional hook change, re-pin with "
        f"`python3 {Path(__file__).name} --pin`. If not, treat hook enforcement for this "
        f"session as unverified rather than assumed correct.",
    )
    hc.mark("settings-integrity-mismatch")


def pin_current():
    """CLI-only path, not part of the SessionStart hook flow: `python3 settings_integrity_
    check.py --pin` reads the live settings.json and writes it as the new canonical reference.
    Requires a human to run it deliberately -- there is no automatic re-pin on mismatch, because
    a hook that silently re-trusts whatever it just found different is not a check."""
    try:
        live = json.loads(SETTINGS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[settings_integrity_check] cannot pin: {SETTINGS_PATH} unreadable ({exc})",
              file=sys.stderr)
        sys.exit(1)
    hooks_block = live.get("hooks", {})
    reference = {
        "pinned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(SETTINGS_PATH),
        "hash": canonical_hash(hooks_block),
        "hooks": hooks_block,
    }
    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_PATH.write_text(json.dumps(reference, indent=2, sort_keys=True) + "\n")
    print(f"[settings_integrity_check] pinned {REFERENCE_PATH} from {SETTINGS_PATH}, "
          f"hash {reference['hash'][:12]}")


if __name__ == "__main__":
    if "--pin" in sys.argv:
        pin_current()
    else:
        hc.run(main)
