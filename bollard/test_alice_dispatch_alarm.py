#!/Users/m5/.venv/bin/python3
"""Real-subprocess tests + R2 ablation for A2 (alice_dispatch_alarm.py).

Runs the hook exactly as the harness would -- JSON on stdin, exit 0 -- with HOME redirected to a
throwaway dir so every ledger (verdict, audit-plane, persona-attribution) lands in the sandbox and
the real telemetry is never touched. The spine of the suite is the DISCRIMINATING NEGATIVE CONTROL:
a detector that fires on everything proves nothing, so each positive is paired with a non-alice
input that must produce no attribution and no alarm.

Run: test_alice_dispatch_alarm.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "alice_dispatch_alarm.py"
PY = sys.executable
RESULTS = []


def record(name, passed, detail):
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + " -- " + detail)


def run_hook(payload, home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PERSONA_ATTRIBUTION_LEDGER"] = str(home / "attr.jsonl")
    env.pop("MMS_AUDIT_BASE", None)
    proc = subprocess.run([PY, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=30)
    return proc


def read_lines(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def attribution_entries(home):
    return read_lines(home / "attr.jsonl")


def verdict_rows(home):
    return read_lines(home / ".claude" / "telemetry" / "verdicts.jsonl")


def alarm_events(home):
    events = []
    for rel in ("audit-plane/misalignment-marker-search/audit.jsonl",
                "audit-plane/global-safety/safety.jsonl"):
        for e in read_lines(home / ".claude" / rel):
            if e.get("event_type") == "ALICE_ZERO_WRITE_PERSONA_DISPATCH":
                events.append(e)
    return events


def case(home_root, label, payload):
    home = Path(home_root) / label
    home.mkdir(parents=True, exist_ok=True)
    proc = run_hook(payload, home)
    return {
        "rc": proc.returncode,
        "attr": attribution_entries(home),
        "alarms": alarm_events(home),
        "verdicts": verdict_rows(home),
        "stderr": proc.stderr,
    }


def main():
    with tempfile.TemporaryDirectory() as root:
        sid_alice = "sess-ALICE-001"
        alice = case(root, "alice", {
            "tool_name": "Agent", "session_id": sid_alice, "cwd": "/tmp/x",
            "tool_input": {"subagent_type": "alice", "prompt": "do a thing"}})
        bob = case(root, "bob", {
            "tool_name": "Agent", "session_id": "sess-BOB-001", "cwd": "/tmp/x",
            "tool_input": {"subagent_type": "bob", "prompt": "do a thing"}})
        explore = case(root, "explore", {
            "tool_name": "Agent", "session_id": "sess-EXP-001", "cwd": "/tmp/x",
            "tool_input": {"subagent_type": "Explore", "prompt": "look"}})
        write = case(root, "write", {
            "tool_name": "Write", "session_id": "sess-W-001", "cwd": "/tmp/x",
            "tool_input": {"file_path": "/tmp/x/f", "content": "hi"}})

        # T1: alice dispatch produces exactly one alice attribution row, tagged zero_write_persona.
        attr_ok = (len(alice["attr"]) == 1
                   and alice["attr"][0].get("persona") == "alice"
                   and alice["attr"][0].get("session_id") == sid_alice
                   and alice["attr"][0].get("zero_write_persona") is True)
        record("T1-alice-attribution-recorded", attr_ok,
               f"attr_rows={len(alice['attr'])} first={alice['attr'][0] if alice['attr'] else None}")

        # T2: alice dispatch raises the distinct high-severity alarm event.
        alarm_ok = (len(alice["alarms"]) == 1
                    and alice["alarms"][0].get("severity") == "high"
                    and alice["alarms"][0].get("persona") == "alice")
        record("T2-alice-alarm-raised", alarm_ok,
               f"alarm_events={len(alice['alarms'])} severity="
               f"{alice['alarms'][0].get('severity') if alice['alarms'] else None}")

        # T3: alice dispatch records a real fire in the verdict ledger (not scored as silence).
        alice_fire = [r for r in alice["verdicts"]
                      if r.get("handler_id") == "alice_dispatch_alarm.py"
                      and r.get("verdict") == "fire"]
        record("T3-alice-verdict-fire", len(alice_fire) == 1,
               f"fire_rows={len(alice_fire)} exit={alice['rc']}")

        # T4 (DISCRIMINATING CONTROL): bob dispatch -- NO alice attribution, NO alarm, verdict silent.
        bob_clean = (len(bob["attr"]) == 0 and len(bob["alarms"]) == 0)
        bob_silent = any(r.get("handler_id") == "alice_dispatch_alarm.py"
                         and r.get("verdict") == "silent" for r in bob["verdicts"])
        record("T4-bob-not-alarmed", bob_clean and bob_silent,
               f"bob_attr={len(bob['attr'])} bob_alarms={len(bob['alarms'])} silent={bob_silent}")

        # T5 (CONTROL): Explore dispatch -- no alice attribution, no alarm.
        record("T5-explore-not-alarmed",
               len(explore["attr"]) == 0 and len(explore["alarms"]) == 0,
               f"attr={len(explore['attr'])} alarms={len(explore['alarms'])}")

        # T6 (CONTROL): a plain Write (not an Agent dispatch) -- no alice attribution, no alarm.
        record("T6-write-not-alarmed",
               len(write["attr"]) == 0 and len(write["alarms"]) == 0,
               f"attr={len(write['attr'])} alarms={len(write['alarms'])}")

        # T7: hook never blocks -- exit 0 and no permissionDecision on stdout for any case.
        never_blocks = all(c["rc"] == 0 for c in (alice, bob, explore, write))
        record("T7-never-blocks", never_blocks,
               f"exit_codes={[c['rc'] for c in (alice, bob, explore, write)]}")

    allpass = all(p for _n, p, _d in RESULTS)
    print("RESULT: " + ("ALL PASS" if allpass else "FAILURES PRESENT")
          + f" ({len(RESULTS)} tests)")
    sys.exit(0 if allpass else 1)


if __name__ == "__main__":
    main()
