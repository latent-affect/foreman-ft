#!/usr/bin/env python3
"""stage_order_gate.py -- FORE-484. Builds `hooks/stage_order_gate/GOALS.json`'s draft
(FORE-308 lineage, criteria C1-C7, failure criteria F1-F2; criteria_frozen_at is null there,
so nothing here is authorized until that freezes -- this is a proposal, not a landing).

PDP.md section 2 R6: "Stage N cannot open until stage N-1 closed. The relation lives in
exactly one file, .foreman/pipeline.json, and one hook reads it." PDP.md section 6: for any
write inside a scope declared in .foreman/pipeline.json, resolve the owning stage and DENY
unless .foreman/ledger.jsonl holds a close or skip event for every predecessor, with artifact
hashes that still match. "It fails closed. Unreadable pipeline, unreadable ledger, ambiguous
stage, missing predecessor: deny."

pipeline.json's and ledger.jsonl's own schemas are NOT invented here -- GOALS.json's own
out_of_scope names PDP-RATIONALE.md section 11 as their one real source, and this file reads
exactly that shape (a top-level "stages" list; each stage carries "id", "requires", "scopes";
ledger rows carry "event" in {"stage_close","stage_skip"}, "stage", and for a close row
"decision" and "artifacts").

OQ1 (GOALS.json's own open question, not decided there): does this gate run ALONGSIDE
architecture_gate.py/goals_freeze_gate.py, or replace them? THIS PROPOSAL ANSWERS ALONGSIDE,
explicitly, not silently -- see the accompanying rationale for why. Nothing here touches
either existing gate.

DISCLOSED SEQUENCING DEPENDENCY (GOALS.json's own out_of_scope, restated because it changes
what "correct" looks like on first run): no `foreman close/verdict/skip/ablate` CLI exists
anywhere (FORE-351, open, re-checked at proposal time). Nothing writes stage_close/stage_skip
rows today. A project that registers this gate with an empty or absent ledger.jsonl will see
every write past its pipeline's first declared stage denied -- correctly, not as a bug, and
this proposal does not include any settings.json registration for exactly that reason.

DENY-or-nothing (F1, GOALS.json failure criterion): this file never calls hook_common's ask
helper anywhere -- checked by F1's own stated detection method (grep the hook file for the
literal call, zero occurrences required) before this proposal was sent, and this sentence is
deliberately worded not to contain that literal substring itself, so a grep for it does not
false-positive on this docstring. Every ambiguity here fails toward deny; the absence of a
decision is the allow, matching architecture_gate.py's and goals_freeze_gate.py's own
established convention in this suite.

NAMING NOTE, disclosed rather than silent: this repo's own sibling gates (architecture_gate.py,
goals_freeze_gate.py, review_events_ledger_guard.py) use a leading underscore for their
module-private helper functions (`_check`, `_prefix_of`, `_relative_or_none`, ...). This file
does not, on the proposing session's own standing instruction (never a leading underscore in
emitted code, any project) -- a harder rule than local file convention. Names below that would
otherwise have been `_foo` are `foo` instead, distinguished by being unexported from nothing in
particular (Python has no real privacy either way); this is a deliberate, disclosed deviation
from this file's own neighbors' naming style, not an oversight.

FORE-552 FIX (blocking FORE-524's registration): this gate used to accept only
("Edit", "Write", "Bash"), and its direct-write branch read `tool_input["file_path"]`
literally. Two consequences, both measured against the real hook with real payloads rather
than reasoned about: a NotebookEdit write into a stage whose predecessor was never closed
returned NO decision, and so did MultiEdit. On a gate whose own failure criterion F1 is
deny-or-nothing, that is two unguarded write channels into a gated stage -- fail-OPEN, the
one direction this gate may not fail. It is CHV2-48's exact class, which cost this suite six
guards silently no-op'ing on every real NotebookEdit call.

Fixed by adopting the shape the sibling guard in this same repo already ships and has had
reviewed: ledger_write_guard.py accepts ("Edit", "Write", "NotebookEdit", "MultiEdit") and
resolves the target through hook_common.target_path(), which returns `file_path` or
`notebook_path` (NotebookEdit's real payload field) -- the shared-helper half of the CHV2-48
fix. Reused rather than reinvented, so the two guards cannot drift apart on which channels
they cover.

BOTH tool_name checks had to widen together, and this is the part that is easy to get wrong:
widening only the outer guard would let NotebookEdit/MultiEdit fall past the direct-write
branch into the Bash branch below, which reads `tool_input["command"]`, finds nothing, and
goes silent -- the same fail-open with a longer path to it.

REGISTRATION IS NOT IMPLIED BY THIS FIX. Whoever registers this gate under FORE-524 should
use the sibling's matcher, `Edit|Write|MultiEdit|NotebookEdit|Bash` -- the one
review_events_ledger_guard.py is already registered with -- so the registration states which
channels it means. A fixed hook that is never called is not a fixed channel.

CORRECTION, FORE-616 (2026-09-13). This paragraph used to justify that with "a PreToolUse matcher
of `Edit|Write|Bash` never invokes this hook for NotebookEdit or MultiEdit no matter what the code
accepts." That is FALSE, and it was stated here as load-bearing. Matchers are matched UNANCHORED,
so `Edit` matches `MultiEdit` at index 5 and `NotebookEdit` at index 8, and a bare Edit covers
both.

Measured against the production verdict ledger rather than argued: 918,224 rows, and two
independent handlers recorded real MultiEdit invocations while every registration governing those
calls named only `Edit|Write|NotebookEdit` -- guard_prodconfig.py on 2026-08-26 and scan_write.py
on 2026-09-11, both with cwd=/tmp so only ~/.claude/settings.json could apply.

The recommendation above survives; its stated reason did not. The real reason to spell the tools
out is legibility, not coverage -- and the sharper consequence runs the other way: a matcher is
BROADER than its author probably intended, so `Edit` also catches any future tool whose name
contains it. Anchoring (`^(Edit|Write|Bash)$`, a form this machine's settings already use once)
is how to say what was meant.

QA-BOB ROUND-1 FIX (B2, blocking): resolve_relative_path() used to call `Path(file_path)
.resolve()` unconditionally, which resolves a RELATIVE file_path against the hook PROCESS's
own os.getcwd() -- not against the hook PAYLOAD's own `cwd` field (the agent's logical working
directory at the time of the tool call). When the two diverge, a relative file_path silently
resolves to the wrong location and, if that wrong location falls outside project_root, this
gate fails OPEN (returns "not gated" for a write it should have gated). Bob's review
reproduced this with a three-arm control proving it is genuine cwd-dependence, not mere
relative-path-blindness, and found the identical bug already live in
component_coupling.py's own private _relative_or_none() helper (a separate ticket, not this
one). Fixed by threading `cwd` through explicitly and resolving a relative file_path against
it first -- the same `base = Path(cwd) if cwd else Path.cwd()` pattern
component_coupling.extract_bash_write_targets() already uses, reused here rather than
reinvented.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402

RULE_ID = "FOREMAN-STAGE-ORDER-GATE"
PIPELINE_RELPATH = Path(".foreman") / "pipeline.json"
LEDGER_RELPATH = Path(".foreman") / "ledger.jsonl"

# A stage_close row is only a satisfied predecessor if its own decision was a real go --
# PDP.md section 8's two go grades, both of which open the next stage ("go(unbound) is a real
# verdict, not a failure... it opens the next stage now"). Anything else (recycle, hold, kill,
# or a row this gate doesn't recognize) is not.
SATISFYING_CLOSE_DECISIONS = frozenset({"go", "go(unbound)"})


def file_sha256(path):
    """"sha256:<hex>", or None if the file can't be read -- same prefix convention
    goals_freeze_gate.py's criteria_hash() and PDP.md section 8's own file_sha256 use."""
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def resolve_relative_path(file_path, project_root, cwd):
    """Project-relative POSIX-style string, or None if file_path resolves outside
    project_root entirely (never gated by this hook in that case).

    A relative file_path is resolved against `cwd` -- the hook PAYLOAD's own cwd field, the
    agent's logical working directory at the time of the tool call -- never against the hook
    subprocess's own os.getcwd(), which need not match it. QA-Bob round-1 finding B2: a plain
    `Path(file_path).resolve()` silently resolves a relative path against the WRONG base and
    fails OPEN when the two diverge; the fix mirrors component_coupling.py's own
    extract_bash_write_targets() pattern (`base = Path(cwd) if cwd else Path.cwd()`) rather
    than inventing a new one.

    Deliberately simpler than component_coupling.py's own private path-resolution helper in
    one other respect: the known residual (case-only retyping on a case-insensitive,
    case-preserving filesystem bypassing a str.startswith prefix match -- FORE-128's finding)
    is not re-closed here, disclosed rather than silently narrower."""
    base = Path(cwd) if cwd else Path.cwd()
    candidate = Path(file_path)
    absolute = candidate if candidate.is_absolute() else (base / candidate)
    try:
        resolved = absolute.resolve()
        root = Path(project_root).resolve()
        rel = resolved.relative_to(root)
    except ValueError:
        return None
    return rel.as_posix()


def load_pipeline(project_root):
    """(stages_by_id: dict, error: str|None). error set means the caller must deny -- this
    function never returns a usable dict alongside a non-None error."""
    path = project_root / PIPELINE_RELPATH
    try:
        raw = path.read_text()
    except OSError as exc:
        return None, f"could not be read ({exc})"
    try:
        blob = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"is not valid JSON ({exc})"
    if not isinstance(blob, dict) or not isinstance(blob.get("stages"), list):
        return None, "has no top-level 'stages' list"
    stages = {}
    for entry in blob["stages"]:
        stage_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(stage_id, str) or not stage_id:
            return None, "has a stage entry with no valid string 'id'"
        stages[stage_id] = entry
    return stages, None


def load_ledger_rows(project_root):
    """(rows: list, error: str|None). Append-only JSONL. F2 (GOALS.json failure criterion): a
    single malformed line anywhere denies rather than being silently skipped -- this function
    either returns every row or returns an error, never a partial list with no signal that a
    line was dropped."""
    path = project_root / LEDGER_RELPATH
    if not path.is_file():
        return None, "does not exist"
    try:
        text = path.read_text()
    except OSError as exc:
        return None, f"could not be read ({exc})"
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            return None, f"line {lineno} is not valid JSON ({exc})"
    return rows, None


def resolve_stage(rel_path, stages):
    """(stage_id_or_None, ambiguous_ids_or_None). C1/C3: a path matching scopes declared by
    more than one stage is a pipeline.json authoring error, not a runtime accident to resolve
    by longest-prefix-wins the way component_coupling.py's component_of() does for components
    -- pipeline.json's stages are supposed to partition scope space, so any overlap denies."""
    matched = []
    for stage_id, entry in stages.items():
        for scope in entry.get("scopes") or []:
            prefix = str(scope).rstrip("*")
            if rel_path.startswith(prefix):
                matched.append(stage_id)
                break
    if not matched:
        return None, None
    if len(matched) > 1:
        return None, sorted(matched)
    return matched[0], None


def latest_event_for_stage(rows, stage_id):
    """Latest stage_close/stage_skip row naming stage_id, or None. "Latest" by list order:
    ledger.jsonl is append-only and PDP.md section 8 says a superseding row is never a
    deletion ("superseded in the ledger, never deleted"), so a later row for the same stage
    (a recycle, then a re-close) is the one that governs."""
    latest = None
    for row in rows:
        if isinstance(row, dict) and row.get("event") in ("stage_close", "stage_skip") \
                and row.get("stage") == stage_id:
            latest = row
    return latest


def predecessor_status(row, project_root):
    """(ok: bool, reason: str|None) for one predecessor's most recent ledger row.

    C5: a stage_skip row satisfies exactly as a stage_close row does -- PDP.md section 7's own
    text ("a skip is never a pass" refers to its later ship-gate accounting, not to whether it
    unblocks the next stage; unblocking is the entire point of an explicit skip existing).

    C4: a stage_close row's recorded artifact hashes are re-verified against CURRENT disk
    content, not trusted from the row alone -- a predecessor's artifact edited or reverted
    after its close event recorded a hash must re-deny downstream writes."""
    if row is None:
        return False, "missing-row"
    event = row.get("event")
    if event == "stage_skip":
        return True, None
    if event == "stage_close":
        if row.get("decision") not in SATISFYING_CLOSE_DECISIONS:
            return False, "not-a-go"
        artifacts = row.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            return False, "malformed-artifacts"
        for rel, expected_hash in artifacts.items():
            actual_hash = file_sha256(project_root / rel)
            if actual_hash != expected_hash:
                return False, f"stale-artifact-hash:{rel}"
        return True, None
    return False, "unrecognized-event"


def build_deny_message(file_path, stage_id, predecessor_id, reason):
    """C6: names the path, the resolved stage, which predecessor blocks and why, and the two
    literal next commands -- PDP.md section 6's own "the deny message is the instruction"."""
    if reason == "missing-row":
        blocker = f"no stage_close or stage_skip row for predecessor '{predecessor_id}' in .foreman/ledger.jsonl"
    elif reason == "not-a-go":
        blocker = f"predecessor '{predecessor_id}' has a stage_close row whose decision is not a go"
    elif reason == "malformed-artifacts":
        blocker = f"predecessor '{predecessor_id}'s stage_close row has a malformed 'artifacts' field"
    elif isinstance(reason, str) and reason.startswith("stale-artifact-hash:"):
        rel = reason.split(":", 1)[1]
        blocker = (
            f"predecessor '{predecessor_id}' closed, but its recorded artifact {rel} is "
            f"stale -- it no longer matches the hash captured at close time"
        )
    else:
        blocker = f"predecessor '{predecessor_id}' is not closed ({reason})"
    return (
        "Foreman: write denied.\n"
        f"  path       {file_path}\n"
        f"  stage      {stage_id}\n"
        f"  blocked by {blocker}\n"
        f"  next       foreman close --stage {predecessor_id}\n"
        f'  or skip    foreman skip --stage {predecessor_id} --reason "..." --acceptor <session>\n'
    )


def check_path(file_path, project_root, stages, cwd):
    """True if denied (caller should stop), False if open for this specific path."""
    rel_path = resolve_relative_path(file_path, project_root, cwd)
    if rel_path is None:
        return False

    # CHV2-90. The control plane is not stage content and must never be attributed to a stage.
    # resolve_stage() below prefix-matches every target against every declared scope, and a
    # catch-all scope matches everything -- "*".rstrip("*") is "", and every path startswith("").
    # So a stage declaring one swallowed .foreman/ledger.jsonl and .foreman/pipeline.json, and a
    # legitimate close of an UNRELATED stage was denied over the catch-all stage's own unmet
    # requires. Reproduced before this fix: with a catch-all stage whose requires was unmet, both
    # control-plane writes denied, while a real content file under the same scope denied too --
    # correctly, which is why that third case is the control the fix must not change.
    #
    # EXCLUSION RATHER THAN CONTENT-SNIFFING, and the reason is a design point rather than an
    # implementation convenience. The alternative is to read which stage the ledger row NAMES and
    # check that stage's requires instead. That fights stage_skip head-on: a skip exists to
    # unblock precisely when its predecessor is not closed, so re-checking requires at the moment
    # of recording would deny the very event the mechanism is for. foreman_close_skip.py is the
    # one process allowed to append here and does its own verification before writing; these rows
    # should not pass through a content stage's predecessor check under any name.
    #
    # Compared as .as_posix(), matching what resolve_relative_path already returns, rather than
    # as a raw Path.
    if rel_path in (LEDGER_RELPATH.as_posix(), PIPELINE_RELPATH.as_posix()):
        return False

    stage_id, ambiguous = resolve_stage(rel_path, stages)
    if ambiguous:
        hc.set_rule(f"{RULE_ID}:ambiguous-stage")
        hc.deny(
            f"Foreman: {file_path} matches scopes declared by more than one stage in "
            f".foreman/pipeline.json ({', '.join(ambiguous)}). Overlapping stage scopes are "
            f"a pipeline.json authoring error -- narrow the scopes so each path belongs to "
            f"exactly one stage before writing here."
        )
        return True
    if stage_id is None:
        return False  # not inside any declared stage's scope -- not this gate's concern

    entry = stages[stage_id]
    requires = entry.get("requires") or []
    if not requires:
        return False  # first stage in the chain: nothing to check, opens immediately

    ledger_rows, ledger_error = load_ledger_rows(project_root)
    if ledger_error:
        hc.set_rule(f"{RULE_ID}:ledger-unreadable")
        hc.deny(
            f"Foreman: {project_root / LEDGER_RELPATH} {ledger_error}. Fix "
            f".foreman/ledger.jsonl before writing into stage '{stage_id}'."
        )
        return True

    for predecessor_id in requires:
        row = latest_event_for_stage(ledger_rows, predecessor_id)
        ok, reason = predecessor_status(row, project_root)
        if not ok:
            hc.set_rule(f"{RULE_ID}:blocked-by-{predecessor_id}")
            hc.deny(build_deny_message(file_path, stage_id, predecessor_id, reason))
            return True
    return False


def stages_for_root(project_root, cache):
    """(stages, outcome) for one project root, memoised in `cache`.

    outcome is None when the project has a readable pipeline and this gate applies; otherwise it
    is the rule suffix explaining why it does not ("not-opted-in") or a ("pipeline-unreadable",
    message) pair that the caller must turn into a deny.

    FORE-568: this used to run ONCE against the writer's own project, before any target was
    looked at. Loading it per TARGET root is what makes the gate consult the pipeline of the
    project that actually owns the file being written.
    """
    key = str(project_root)
    if key in cache:
        return cache[key]
    pipeline_path = Path(project_root) / PIPELINE_RELPATH
    if not pipeline_path.is_file():
        # No pipeline.json declared: this project has not opted into stage ordering yet.
        # Not one of C1-C7's named ambiguities -- pipeline.json's own existence is the entry
        # condition R6 describes ("the relation lives in exactly one file"), so its absence
        # is "never adopted," not "adopted and broken."
        result = (None, "not-opted-in")
    else:
        stages, pipeline_error = load_pipeline(project_root)
        if pipeline_error:
            result = (None, ("pipeline-unreadable",
                             f"Foreman: {pipeline_path} exists but {pipeline_error}. Fix "
                             f".foreman/pipeline.json before writing here."))
        else:
            result = (stages, None)
    cache[key] = result
    return result


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "NotebookEdit", "MultiEdit", "Bash"):
        return

    cwd = data.get("cwd")

    if tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        file_path = hc.target_path(data.get("tool_input"))
        if not file_path:
            return
        targets = [file_path]
    else:
        # tool_name == "Bash": same FORE-1 rationale every sibling gate in this suite already
        # applies -- a denied Edit/Write is trivially routed around with a shell redirect, tee,
        # cp/mv, or sed -i.
        command = (data.get("tool_input") or {}).get("command", "")
        targets = list(cc.extract_bash_write_targets(command, cwd))
        if not targets:
            return

    # FORE-568. project_root used to be resolved ONCE from the payload cwd -- the writer's own
    # location -- and every target was then checked against THAT project's pipeline. Fired, with
    # a nested child project inside a parent, both real Foreman projects with their own pipelines
    # declaring implementation-requires-design-scope and empty ledgers:
    #
    #   cwd=CHILD,  target in CHILD/src    DENY      the gate works when cwd matches
    #   cwd=PARENT, target in PARENT/src   DENY      the ordinary case
    #   cwd=PARENT, target in CHILD/src    SILENT    the cross-project fail-open
    #   cwd=CHILD,  target out of scope    SILENT    the negative control
    #
    # The child's own pipeline never governed its own file: the gate asked whether the child's
    # path fell inside the PARENT's declared scopes, which it does not, and went quiet. Writing
    # across a project boundary is ordinary agent behaviour, so this was a routine fail-open.
    #
    # cwd is still passed to check_path, which needs it to resolve a relative target against the
    # writer's directory. That is a use of cwd the target genuinely requires; deciding which
    # project governs was not.
    cache = {}
    saw_a_project = False
    gated_any = False
    for target in targets:
        target_root = cc.project_root_for_target(target, cwd)
        if target_root is None:
            continue  # this target is not inside any Foreman project -- not this gate's concern
        saw_a_project = True
        stages, outcome = stages_for_root(target_root, cache)
        if isinstance(outcome, tuple):
            hc.set_rule(f"{RULE_ID}:{outcome[0]}")
            hc.deny(outcome[1])
            return
        if outcome is not None:
            continue  # that project has not opted in
        if check_path(target, target_root, stages, cwd):
            return
        gated_any = True

    if not saw_a_project:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return
    if gated_any:
        hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
