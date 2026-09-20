#!/usr/bin/env python3
"""write_gate_confirm -- the PostToolUse half of write_gate (C4), absorbing what a separate
write_confirm component held in the prior track's decomposition (FORE-399/J-C9).

Appends confirmations only. NEVER a decision input for the once-only claim -- that claim is
decided entirely at PreToolUse time by write_gate_decision.py's own atomic O_CREAT|O_EXCL
claim file, independent of whether this script ever runs, crashes, or has its own output
deleted afterward. Recomputes edit_key from the REAL, current on-disk bytes at PostToolUse
time -- never trusts a payload-supplied edit_key.

REAL DEFECT FIXED (FORE-403, folded into the FORE-409/410 integration pass): this script used
to read DISPATCH_ROOT and FILE_PATH from sys.argv, but Claude Code's real PostToolUse contract
passes per-invocation data on stdin as JSON, with a FIXED command line the harness itself
controls -- there is no mechanism for the harness to construct a different argv per invocation.
Confirmed as a real regression, not an inherited limitation, against the quarantined precedent
this pipeline's own components elsewhere cite as prior art: bob_write_confirm.py correctly does
`data = hc.read_input()`. As originally written, this script would have silently no-op'd (or
errored on the wrong argv shape) on every single real write. Fixed to match hook_common's real
`read_input()` contract, matching the quarantined precedent's own correct pattern.

J-C13 (FORE-415) -- gate_to_verdict_ledger DESIGN NOTE, this module's half. This is the ONE
component in the whole pipeline whose own product IS a write (the confirmation record under
confirmations/), matching hook_common.stolen()'s own documented definition exactly ("the hook
took ownership and there is no observable continuation. Its product was a WRITE, not a
decision") -- so a successful confirm() call records verdict="stolen", target=the confirmation
file this call itself wrote (never the original edited file -- stolen()'s own docstring is
explicit that target is "the path" THIS hook wrote, so a downstream consumer can mechanically
assert "the path at target changed within the session"). The no-file_path early return (not
every PostToolUse event is even a Write) records verdict="silent" -- examined, correctly nothing
to confirm. A caught OSError records verdict="error" -- the confirmation was NOT successfully
written, which is a real operational failure this fail-open-tolerant PostToolUse half still must
not silently under-report as either "did nothing" (silent) or "wrote something" (stolen).
"""
import json
import os
import sys
import time
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
FABLE_F_DIR = THIS_DIR.parent / "contract"
sys.path.insert(0, str(FABLE_F_DIR))
import alice_proposal_contract as apc  # noqa: E402

HOOK_COMMON_DIR = Path.home() / ".claude" / "hooks"
sys.path.insert(0, str(HOOK_COMMON_DIR))
import hook_common  # noqa: E402 -- read_input() and record_verdict(); never a decision surface

DEFAULT_DISPATCH_ROOT = Path.home() / ".claude" / "foreman" / "alice-bob-dispatch"


def confirm(dispatch_root, file_path, confirmations_dir_override=None, once_only_dir_override=None):
    real_path = str(Path(os.path.realpath(file_path)))
    actual_bytes = Path(real_path).read_bytes()
    actual_hash = apc.post_image_sha256(actual_bytes.decode("utf-8", errors="surrogateescape"))
    real_edit_key = apc.edit_key(real_path, actual_hash)

    # FORE-435: write_gate_decision.py's own decision (and its once-only claim file, named by
    # this SAME edit_key formula -- see check_content_binding()/claim_once_only()) is bound to
    # os.path.realpath(edit["path"]) computed at CHECK time. The real Write tool call that
    # follows writes to the LITERAL path, re-resolved by the OS at THIS (write) time -- if a
    # symlink component changed in that window (TOCTOU), real_path here diverges from what was
    # actually checked even though the literal path string never changed, and the claim this
    # write's own real_edit_key would need to match simply will not exist. Re-deriving the same
    # key from the REAL, post-write path and REAL, post-write disk bytes and checking the claim
    # is a genuine re-verification at write time, never a re-trust of the check-time decision.
    once_only_dir = Path(once_only_dir_override) if once_only_dir_override \
        else Path(dispatch_root) / "once-only"
    toctou_verified = (once_only_dir / f"{real_edit_key}.claim").is_file()

    confirmations_dir = Path(confirmations_dir_override) if confirmations_dir_override \
        else Path(dispatch_root) / "confirmations"
    confirmations_dir.mkdir(parents=True, exist_ok=True)
    record = {"path": real_path, "post_image_sha256": actual_hash, "edit_key": real_edit_key,
              "toctou_verified": toctou_verified}
    confirmation_path = confirmations_dir / f"{real_edit_key}.json"
    # FORE-436: confirmation records name the real edited path -- 0o600, matching the pipeline's
    # other content-bearing dispatch artifacts, via os.open instead of Path.write_text's
    # umask-derived default mode.
    fd = os.open(str(confirmation_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(record).encode("utf-8"))
    finally:
        os.close(fd)
    record["confirmation_path"] = str(confirmation_path)
    return record


def extract_file_path(payload):
    """The real PostToolUse payload shape for a Write tool call carries the target path under
    tool_input.file_path -- the same field write_gate_decision.py's own PreToolUse payload
    parsing already reads for the Write tool shape, kept consistent between the two hook halves
    deliberately."""
    tool_input = payload.get("tool_input") or {}
    return tool_input.get("file_path")


def main(argv=None):
    """Real stdin contract: reads exactly one JSON payload via hook_common.read_input(), the
    same real input path every PostToolUse hook in this harness uses -- never sys.argv, which
    the harness has no mechanism to vary per invocation.

    J-C13 (FORE-415): records exactly one verdict_ledger row per invocation -- see this module's
    own top-of-file design note for the stolen/silent/error mapping."""
    started = time.time()
    payload = hook_common.read_input()
    file_path = extract_file_path(payload)
    if not file_path:
        print(json.dumps({"ok": False, "reason": "PostToolUse payload carries no tool_input.file_path"}))
        hook_common.record_verdict(payload, "silent", duration_ms=int((time.time() - started) * 1000))
        return 0  # PostToolUse: never a decision surface, a confirmation failure is not a deny

    dispatch_root = os.environ.get("WRITE_GATE_DISPATCH_ROOT", str(DEFAULT_DISPATCH_ROOT))
    try:
        record = confirm(dispatch_root, file_path)
        print(json.dumps({"ok": True, "edit_key": record["edit_key"],
                           "toctou_verified": record["toctou_verified"]}))
        if record["toctou_verified"]:
            hook_common.record_verdict(payload, "stolen", kind="confirm",
                                        duration_ms=int((time.time() - started) * 1000),
                                        target=record["confirmation_path"])
        else:
            # FORE-435: the real, post-write path+hash has no matching PreToolUse claim -- the
            # write landed somewhere (or as something) this gate's own check never actually
            # verified. PostToolUse cannot deny or undo an already-completed write, but it must
            # not silently record this as an ordinary, verified confirmation either.
            print(f"[write_gate_confirm] TOCTOU: no matching check-time claim for edit_key "
                  f"{record['edit_key']} at {record['path']} -- check-time and write-time "
                  "path/content resolution diverged", file=sys.stderr)
            hook_common.record_verdict(payload, "stolen", kind="confirm-toctou-mismatch",
                                        duration_ms=int((time.time() - started) * 1000),
                                        target=record["confirmation_path"])
        return 0
    except OSError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        hook_common.record_verdict(payload, "error", kind=type(exc).__name__,
                                    duration_ms=int((time.time() - started) * 1000))
        return 0


if __name__ == "__main__":
    sys.exit(main())
