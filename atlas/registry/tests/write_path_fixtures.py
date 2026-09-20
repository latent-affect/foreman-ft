"""Shared fixture builders for the ATLASSN-131 write-path test suite (test_write_path_role,
test_write_path_evidence, test_write_path_component, test_write_dependency_failure). Real
anchors verified against the live corpus 2026-09-12 -- see
OBSERVED-BEHAVIOUR-WRITE-PATH-ATLASSN-131-20260912.md.

Sharing fixtures across these four modules does NOT touch C9's no-shared-helper discipline:
C9 is about two independent DERIVATIONS of the same answer needing to not share code, and
these four modules all exercise one production module rather than deriving anything.
"""

import hashlib
import json
from pathlib import Path

from atlas.registry import migrate

REAL_TRANSCRIPT_SESSION = "1498f956-3cc1-44d4-b0a5-502d309ae8ef"
REAL_TRANSCRIPT_PATH = (
    f"/Users/m5/.claude/projects/-Users-m5-dev-atlas-sonnet/{REAL_TRANSCRIPT_SESSION}.jsonl")
REAL_ANCHOR_TOOL_USE_ID = "toolu_01TUWPbWVJLR8Xca5AfFjT7R"
REAL_ANCHOR_SECOND_TOOL_USE_ID = "toolu_01SMDyW8iywH12NJ1vqZ8ie8"
REAL_ANCHOR_COMPONENT = "registry"
REAL_PROJECT_ROOT = "/Users/m5/dev/atlas-sonnet"

# A real DENIED act, verified 2026-09-12 to reject evidence-denied. Resolves to `warehouse`,
# not `registry` -- assert against warehouse when driving this one through clause (c).
REAL_DENIED_TOOL_USE_ID = "toolu_01SXufegoffbre6JfNsxyAiZ"
REAL_DENIED_SESSION = "060d3cb3-be77-49a5-88cb-93d17b975c2e"
REAL_DENIED_COMPONENT = "warehouse"

# A real act whose hook ERRORED, verified 2026-09-12 to reject evidence-hook-error.
#
# Chosen over the other eight for a reason worth keeping: this act has real PostToolUse rows
# INCLUDING a PostToolUse error, so it passes the completeness check and ONLY the error check
# can reject it. The obvious alternative (toolu_012pc4hHQNDyVDNQ97UbQ9Kh /
# affb7b0c-982a-48b1-a57d-7db80cd85135) is PreToolUse-only, so it would also reject as
# evidence-incomplete and lands on evidence-hook-error only because the error check happens to
# run first -- an order-dependent probe rather than a discriminating one.
#
# Only five acts in the whole ledger carry both an error row and a PostToolUse row, and none
# is a Write/Edit resolving to a declared component, so this can only be probed at
# resolve_evidence level, never end-to-end through write_assertion (clause (c) runs first and
# would reject at the transcript).
REAL_ERRORED_TOOL_USE_ID = "toolu_018muSh3UGapuRXtsMtg3qrn"
REAL_ERRORED_SESSION = "57b15b76-172c-4217-879d-dc6cfc7ffabc"

# ATLASSN-165 companion fixture: REAL_DENIED_SESSION's OWN real transcript, for tests that need
# clause (c) to actually resolve REAL_DENIED_TOOL_USE_ID against it. The anchor transcript
# (REAL_TRANSCRIPT_PATH) never contains REAL_DENIED_TOOL_USE_ID at all -- confirmed by direct
# scan, 2026-09-16 -- so a probe that wants clause (c) to reach component-mismatch (rather than
# fail earlier at evidence-target-unresolved, or now at the ATLASSN-165 binding check) needs
# this session's own file, not the anchor's.
REAL_DENIED_TRANSCRIPT_PATH = (
    f"/Users/m5/.claude/projects/-Users-m5-dev-atlas-sonnet/{REAL_DENIED_SESSION}.jsonl")


def seed_json_file(directory, stem, **fields):
    """Write `fields` as <directory>/<stem>.json. A generic JSON-file writer, nothing more.

    THIS DELIBERATELY DOES NOT KNOW WHAT A DISPATCH RECORD IS, and the reason is a real
    guard rather than a style preference. ATLASSN-125's
    test_no_writer_function_exists_in_atlas_registry scans every .py under atlas/registry for
    a function whose name pairs "dispatch_record" with a minting verb, because §34.7 puts
    record authorship with the dispatching authority and a writer living inside the consuming
    component collapses producer and consumer into one freeze domain. An earlier version of
    this helper was called write_dispatch_record and tripped that probe -- correctly.

    Renaming it to something the substring check misses would have defeated the guard by
    wordplay. Instead the CAPABILITY is gone: this component now ships no helper that knows
    the dispatch-record shape, and each probe spells its own record out at the call site,
    which also makes the fixtures easier to read against §34.7's four clauses.

    `stem` is the file name; the record's own `record_id` field travels in **fields, so a
    probe can write a record whose stored id disagrees with its filename.
    """
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / f"{stem}.json").write_text(json.dumps(fields), encoding="utf-8")


def write_sessions_stream(path, lines):
    Path(path).write_text(
        "\n".join(json.dumps(line) for line in lines) + ("\n" if lines else ""),
        encoding="utf-8")


def session_line(session_id, ts, event="SessionStart"):
    return {"ts": ts, "claude_version": "2.1.226 (Claude Code)", "branch": "", "cwd": "/tmp",
            "session_id": session_id, "event": event}


def write_config_and_decision(tmp_dir, structural_s=86401, in_flight_s=3601, window_s=1801):
    """Deliberately odd numbers so a hardcoded default could never masquerade as
    config-derived. If a probe ever sees 86400 or 3600 here, the value came from code."""
    tmp_dir = Path(tmp_dir)
    decision_path = tmp_dir / "decision-record.md"
    decision_path.write_text(
        f"Operator decision: structural_s={structural_s}, in_flight_s={in_flight_s}, "
        f"go_recency_window_s={window_s}.", encoding="utf-8")
    config_path = tmp_dir / "config.json"
    config_path.write_text(json.dumps({
        "lease_defaults": {"structural_s": structural_s, "in_flight_s": in_flight_s},
        "go_recency_window_s": window_s,
        "decision_record_path": str(decision_path),
        "decision_record_sha256": hashlib.sha256(decision_path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return config_path


def create_store(path):
    """Via migrate.create_store, not a bare ddl.connect + create_schema.

    migrate owns the full ceremony including stamping PRAGMA user_version, and availability.py
    returns registry-unavailable on a version mismatch -- so a store built the short way is not
    the same artifact a real deployment has, and a probe touching the availability matrix would
    diverge from production for a reason that has nothing to do with what it is testing.

    create_store returns an OPEN connection; closing it here is the difference between a
    fixture and a file-descriptor leak across a suite that builds one store per test.
    """
    connection = migrate.create_store(path)
    connection.close()
    return path


# ---------------------------------------------------------------------------------------------
# ATLASSN-131 item 3: the anchor battery was BLIND to duplicate-id ambiguity, because the anchor
# transcript carries none. Measured 2026-09-12 over all 2,207 transcripts under
# ~/.claude/projects: the anchor (1498f956) has 265 distinct tool_use ids and 0 duplicates, and
# corpus-wide only 4 ids duplicate within one file (all in
# -Users-m5-dev-gif-smith/5c325c56-2cc4-4190-9aae-fdca99232a8e.jsonl).
#
# WHY THOSE 4 CANNOT DISCHARGE THIS PROBE, stated rather than quietly worked around: all four are
# WebSearch/TaskUpdate blocks carrying no `file_path`, so clause (c) collects zero candidates for
# them and refuses `evidence-target-unresolved` -- a different refusal, proving nothing about
# ambiguity. There is no real Write/Edit-shaped duplicate anywhere in the corpus.
#
# So the probe builds its duplicate the way the harness itself does: by COPYING A REAL RECORD
# forward, which is exactly the resume/fork mechanism section 34.1b names as the cause of
# copied-provenance duplication. The bytes being duplicated are real transcript bytes read from
# the real anchor, not a hand-written block, so the fixture cannot carry a field production rows
# lack -- the anti-fixture trap C3 exists to close.
# ---------------------------------------------------------------------------------------------

def real_record_carrying_tool_use(transcript_path, tool_use_id):
    """The verbatim raw line of the real transcript whose message content holds `tool_use_id`.

    Returns the line as text, unparsed and unmodified. A caller that wants to mutate it parses
    it itself, so the untouched case stays byte-identical to production.
    """
    with open(transcript_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped or tool_use_id not in stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            for block in ((record.get("message") or {}).get("content") or []):
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("id") == tool_use_id):
                    return stripped
    raise AssertionError(
        f"no real record in {transcript_path} carries tool_use id {tool_use_id!r} -- the fixture "
        f"anchor has moved and this probe would otherwise pass vacuously")


def transcript_with_duplicated_real_record(destination, tool_use_id, second_file_path=None,
                                           transcript_path=None):
    """Write a transcript that is the REAL anchor transcript plus one extra copy of the real
    record carrying `tool_use_id`, so that id appears in exactly two tool_use blocks.

    `second_file_path` None copies the record verbatim -- the copy names the same target, which
    is the case that separates a count rule from a disagreeing-values rule. Passing a path
    rewrites only the copy's `input.file_path`, leaving every other field of the real record
    alone.

    Returns the destination path.
    """
    transcript_path = transcript_path or REAL_TRANSCRIPT_PATH
    original = Path(transcript_path).read_text(encoding="utf-8")
    duplicate = real_record_carrying_tool_use(transcript_path, tool_use_id)
    if second_file_path is not None:
        record = json.loads(duplicate)
        for block in ((record.get("message") or {}).get("content") or []):
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id):
                block.setdefault("input", {})["file_path"] = second_file_path
        duplicate = json.dumps(record)
    destination = Path(destination)
    destination.write_text(
        original + ("" if original.endswith("\n") else "\n") + duplicate + "\n",
        encoding="utf-8")
    return destination


# ATLASSN-133 needs FOUR real evidence acts in one harness, not two: an assert burns one, its
# revocation burns a second, the re-verification that clears the forward gap burns a third, and
# the G4 replay probe needs a fourth to attempt. Clause (b)'s same-session rule means they must
# all come from the anchor session, and they do -- 29 of its 74 file_path-bearing blocks pass
# both clause (b) and clause (c) against the frozen architecture (enumerated 2026-09-12).
# All four resolve to `registry`.
REAL_ANCHOR_THIRD_TOOL_USE_ID = "toolu_01RoSCKvQUUN1GyNieiy6JyM"
REAL_ANCHOR_FOURTH_TOOL_USE_ID = "toolu_01HWeviuthUqMeB72c9eyC88"

# Two of the three `registers` edges the frozen ARCHITECTURE actually declares. Used verbatim so
# the reconciler probes run against the real declaration block rather than a seeded one -- if the
# architecture's reach block changes, these probes fail loudly instead of testing a fiction.
DECLARED_EDGE_GATE_CLIENT = "claude-hooks-v2:hooks/:registers:registry_gate_client_wired"
DECLARED_EDGE_WRITE_GUARD = "claude-hooks-v2:hooks/:registers:registry_write_guard_wired"


def reach_for(*edge_ids):
    """ATLASSN-152 / D1g. A reach map whose canonicalized ids are exactly `edge_ids`, built by
    splitting each quad on ':' -- the inverse of architecture_parse.edge_canonical_id's own
    colon join. Lets every existing write_assertion() test keep the exact edge_id it already
    asserts against (real or synthetic) while supplying the `reach` parameter D1g now requires,
    without each test needing to know the reach-map shape or reach into the real document.

    Not a bypass of D1g: these modules are not testing D1g (that is test_write_path_edge_id.py's
    job), so their fixture simply needs to legitimately declare the edge it already uses -- the
    same reason they pass a synthetic `components` dict rather than the real ARCHITECTURE.md's."""
    reach = {}
    for edge_id in edge_ids:
        repo, path, direction, name = edge_id.split(":", 3)
        reach[name] = {"repo": repo, "path": path, "direction": direction}
    return reach
