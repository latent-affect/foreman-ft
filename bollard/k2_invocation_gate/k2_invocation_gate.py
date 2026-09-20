#!/usr/bin/env python3
"""k2_invocation_gate.py -- FORE-533.

Builds hooks/k2_invocation_gate/GOALS.json's frozen criteria (C1-C3; F1-F2). Per
ARCHITECTURE.md's component row: "denies recording a mechanism-backed requirement `closed` (a
stage-close/ticket-close/goals-freeze write) unless a real invocation-evidence record (a live
probe result) already exists for it. Built-but-unwired becomes undefinable rather than merely
undetected." REQ-2/REQ-5 (docs/PRD.md).

MATCHER (C1) -- two write shapes, both PreToolUse `Edit|Write|Bash`:
  (a) an Edit/Write, or a Bash-mediated write via component_coupling.extract_bash_write_targets,
      whose target resolves to a GOALS.json file AND whose new content adds a results[] entry
      with status:"MET" for a criterion that carries mechanism_backed:true in that same file.
  (b) a Bash command invoking the real tessera CLI's status-closing transition against a ticket
      whose TESSERA record carries custom_fields.mechanism_backed == true.
A write matching neither shape is silent -- this suite's established convention
(architecture_gate.py, goals_freeze_gate.py, stage_order_gate.py all stay silent on a non-match
rather than asking).

DISCLOSED DIVERGENCE FROM GOALS.json's OWN C1 PROSE, not silently reconciled: GOALS.json's C1
statement guesses the tessera-cli closing verb as "`update ... --status closed` or `close ...`".
Neither subcommand exists in the real, live CLI (ticket-system/tessera/api/cli.py, read
directly: add_parser calls at lines 113-358 list `create`, `get`, `list`, `comment`,
`transition`, `reassign-project`, `link`, `set-field`, ... -- no `update`, no `close`). The one
real status-closing verb is `transition <ticket_id> --actor <actor> --status closed`
(cli.py:156-165). F1's own detection method requires this gate be tested "against the REAL
command/write shapes this repo's own tooling actually produces", so this file matches the real
`transition ... --status closed` shape, not GOALS.json's guessed one -- the same
disclosed-correction posture ARCHITECTURE.md's own "TESSERA resolution" section already uses
for a structurally identical prose-vs-code divergence in tessera_resolver.py.

DISCLOSED ARCHITECTURE GAP, not routed around silently: this component's ARCHITECTURE.md row
lists its Calls as component_coupling and hook_common only, and the Reach section declares no
k2-to-tessera-cli cross-repo edge (unlike alice_bob_pipeline's own declared `tessera_cli` Reach
edge). But GOALS.json's frozen C1(b) requires knowing a real ticket's
custom_fields.mechanism_backed value, which has no answer without a real TESSERA query --
ticket_mechanism_backed() below does that query, duplicating tessera_resolver.py's own
run_cli() subprocess pattern rather than importing it (component_coupling.py's own header note
on its FORE-15 port already establishes this suite's convention: "duplicated, not imported --
this module has no cross-hook import of that sibling, matching its own established convention
of keeping each hook self-contained"). This is the same shape as ARCHITECTURE.md's own
`efficacy_to_verdictledger` precedent: "the component owner correctly did not land this edit
unilaterally... and flagged it back for this document's author to land." Flagged in FORE-533's
own report, not landed into ARCHITECTURE.md by this session -- a new Reach edge
(`k2_to_tessera_cli`) is real follow-up work for whoever owns that document next.

EVIDENCE CHECK (C2): DENY-or-nothing, never ASK, matching this whole suite's convention.
Missing file, unreadable JSON, or probe_result != "pass" all deny.

OVERRIDE (C3, REQ-5): `.foreman/k2-override.json`, present and unexpired, allows the write
through with NO evidence required -- but every use is logged with kind="k2-override-used" and
target=the override file's own path, so a later audit can find it (K1's own unlogged
break-glass is exactly the gap REQ-5 exists to not repeat here).

FORE-543/FORE-544 FIX (Alice), both root-caused to the same ~20-line function,
ticket_mechanism_backed(): it used to return a bare bool, so "TESSERA query failed / could not
be reached" and "TESSERA query succeeded and the ticket really is not mechanism_backed"
collapsed into the identical `False`. match_tessera_close() then treated that `False` as "not a
match", so a live TESSERA outage (or DB lock, or subprocess crash) resulted in the write
falling straight through to the silent no-match branch -- evaluate()'s own override/evidence
check was never even reached, the opposite direction from bob_write_gate.py's posture on the
identical external-dependency-failure shape (FORE-543). Separately, the TESSERA_STUB_ENV
test-only escape hatch fired on the bare presence of that env var with no real-test-runner
signal required, unlike verdict_ledger.py's own resolve_origin() idiom (FORE-544). Fixed
together: ticket_mechanism_backed() now returns a tri-state (True/False/None, None
meaning "could not determine"), match_tessera_close() only treats a CONFIRMED `False` as
"not a match" (None now routes into evaluate()'s fail-closed evidence/override check, same as
True), and the stub path requires a real test-runner signal before it is trusted at all --
absent that signal it is ignored outright (falls through to the real query) rather than denied,
which fully neutralizes the leftover-env-var attack shape without turning an unrelated stale
export into a spurious production deny.

ADVERSARIAL-CODE-REVIEW FOLLOW-UP (ADVERSARIAL-REVIEW-FORE-543-544-20260915.md, check 4,
SERIOUS): the FORE-543 fix's own new test-injectable seam, K2_TESSERA_DB_PATH_OVERRIDE, was
left ungated -- unlike TESSERA_STUB_ENV, which the FORE-544 fix above gated behind a real-
test-runner signal in the same pass. Anything able to set that env var in the hook's real
launch environment could redirect ticket_mechanism_backed()'s query itself to an attacker-
controlled sqlite file, forcing a False/None result and collapsing match_tessera_close() to
"not a match" -- the identical silent-bypass shape FORE-543 fixed for the query-failure path,
reopened on this seam instead. Fixed here: DB_PATH now resolves through
_resolve_tessera_db_path(), which applies the exact same real-test-runner guard
(PYTEST_CURRENT_TEST or "unittest" in sys.modules) as TESSERA_STUB_ENV -- absent that signal,
K2_TESSERA_DB_PATH_OVERRIDE is ignored outright and DB_PATH resolves to the real production
path, matching this file's now-consistent posture on both of its test-only escape hatches.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402

RULE_ID = "FOREMAN-K2-INVOCATION-GATE"

OVERRIDE_RELPATH = Path(".foreman") / "k2-override.json"
EVIDENCE_DIR_RELPATH = Path(".foreman") / "invocation-evidence"

# ---- TESSERA (C1 shape b) --------------------------------------------------

REAL_TESSERA_DB_PATH = "/Users/m5/dev/ticket-system/data/tessera.db"


def _resolve_tessera_db_path():
    """Adversarial-code-review follow-up (ADVERSARIAL-REVIEW-FORE-543-544-20260915.md, check 4,
    SERIOUS): K2_TESSERA_DB_PATH_OVERRIDE (the FORE-543 fix's own new test-injectable seam) was
    left ungated when TESSERA_STUB_ENV, its sibling escape hatch, was gated behind a real-test-
    runner signal in the same pass (FORE-544). Anything able to set this env var in the hook's
    real launch environment could redirect the TESSERA query to an attacker-controlled sqlite
    file, forcing a False/None result and collapsing match_tessera_close() to "not a match" --
    the identical silent-bypass shape FORE-543 fixed for the query-failure path, reopened on
    this seam instead. Same guard as TESSERA_STUB_ENV now applies here, for symmetry: absent a
    real-test-runner signal, the override is ignored outright (falls through to the real
    production path) rather than trusted from an arbitrary invocation environment."""
    override = os.environ.get("K2_TESSERA_DB_PATH_OVERRIDE")
    if override and (os.environ.get("PYTEST_CURRENT_TEST") or "unittest" in sys.modules):
        return override
    return REAL_TESSERA_DB_PATH


# FORE-543 test-injectable seam, same convention as write_gate.py's WRITE_GATE_DISPATCH_ROOT:
# an env var override lets tests exercise a genuine TESSERA-unreachable failure via a real
# subprocess dispatch, without depending on or corrupting the live production tessera.db.
# Gated by _resolve_tessera_db_path() (above) behind the same real-test-runner signal
# TESSERA_STUB_ENV uses below, so it is never honored in production regardless of what set it.
DB_PATH = _resolve_tessera_db_path()
TESSERA_CWD = "/Users/m5/dev/ticket-system"

# See module docstring: the real closing verb, confirmed against cli.py's own argparse
# definition, not GOALS.json's guessed "update"/"close" verbs.
TESSERA_MODULE_RE = re.compile(r"\bpython3?\b[^\n;&|]*-m\s+tessera\.api\.cli\b")
TESSERA_TRANSITION_RE = re.compile(r"\btransition\s+(\S+)")
STATUS_CLOSED_RE = re.compile(r'--status[= ]+["\']?closed["\']?')

# TEST-ONLY escape hatch. This suite's real subprocess-dispatch test convention (see
# test_k2_invocation_gate.py's own docstring) cannot safely depend on, or mutate, the live
# production TESSERA database for a ticket fixture whose custom_fields.mechanism_backed value
# a test needs to control -- exactly the class of incident this repo's own operating memory
# already warns about (test traffic landing in real production data/ledgers). Gated by an
# explicit env var an ordinary hook invocation never sets, the same "env-var-gated test
# behavior" idiom verdict_ledger.py's own resolve_origin() already uses for PYTEST_CURRENT_TEST.
# FORE-544: gated further, below, behind a real-test-runner signal -- see
# ticket_mechanism_backed()'s own docstring.
TESSERA_STUB_ENV = "K2_TESSERA_STUB_PATH"


def run_tessera_cli(args, timeout=10):
    """(ok, parsed_json_or_None, stderr_text). Duplicated from tessera_resolver.py's own
    run_cli(), not imported -- see module docstring's "DISCLOSED ARCHITECTURE GAP" section."""
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", "-m", "tessera.api.cli", "--db", DB_PATH, *args],
            cwd=TESSERA_CWD, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, None, repr(exc)
    if proc.returncode != 0:
        return False, None, proc.stderr.strip()
    try:
        return True, json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return False, None, f"unparseable stdout: {exc}"


def ticket_mechanism_backed(ticket_id):
    """True (confirmed mechanism_backed), False (confirmed NOT mechanism_backed), or None
    (could not determine -- TESSERA unreachable, a subprocess failure, unparseable stdout, or an
    unusable/untrusted stub). FORE-543: the prior version collapsed False and None into the same
    bare False, so a live TESSERA outage was indistinguishable from a real negative lookup, and
    match_tessera_close() (below) treated both identically as "not a match" -- C2's entire
    evidence requirement was silently skipped rather than degraded. Callers MUST treat None as
    "needs gating" (fail closed, matching bob_write_gate.py's posture for the identical
    external-dependency-failure shape), never fold it back into False. This is a deliberate
    reversal from this function's own prior docstring, which claimed a bare-False-on-any-
    failure was correct because "C2's own evidence check is what fails closed" -- that claim
    was false in practice, because match_tessera_close() short-circuited before evaluate() (and
    therefore C2) was ever reached.

    FORE-544: the K2_TESSERA_STUB_PATH test-only escape hatch previously fired on the bare
    presence of that env var alone, with no check that a real test runner was the one setting
    it -- reachable from an ordinary leftover shell export surviving in a shell's ancestry,
    confirmed live by Iris Chen's pre-registration probe (FORE-544 comment thread). Now requires
    the same real-test-runner signal verdict_ledger.py's own resolve_origin() already
    establishes as this suite's idiom: `"unittest" in sys.modules` (an in-process caller) or
    `PYTEST_CURRENT_TEST` set (the signal this suite's own subprocess-dispatched tests set
    explicitly, since a freshly-exec'd child interpreter never imports unittest itself just by
    being launched as `python3 k2_invocation_gate.py`). Absent that signal, the stub is not
    trusted and not treated as a failure either -- this function logs it and falls through to
    the real query, acting exactly as if the var were unset. That neutralizes the leftover-
    env-var attack shape without turning an unrelated stale export into a spurious production
    deny for a ticket that a real query would have resolved just fine.

    Every failure path here is logged to stderr with the discarded error detail, per FORE-543's
    own stated fix direction ("log the discarded _err value regardless of the fix chosen") --
    the prior version received run_tessera_cli()'s real stderr/exception text as `_err` and
    discarded it unconditionally."""
    stub_path = os.environ.get(TESSERA_STUB_ENV)
    if stub_path:
        if os.environ.get("PYTEST_CURRENT_TEST") or "unittest" in sys.modules:
            try:
                stub = json.loads(Path(stub_path).read_text())
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                print(f"[k2_invocation_gate] {TESSERA_STUB_ENV}={stub_path!r} set by a real "
                      f"test runner but unusable ({exc}) -- treating as could-not-determine",
                      file=sys.stderr)
                return None
            return bool(stub.get(ticket_id, False))
        print(f"[k2_invocation_gate] {TESSERA_STUB_ENV} is set but no real-test-runner signal "
              f"is present (FORE-544) -- ignoring it and querying the real TESSERA CLI instead",
              file=sys.stderr)
    ok, data, err = run_tessera_cli(["get", ticket_id])
    if not ok or not isinstance(data, dict):
        print(f"[k2_invocation_gate] TESSERA query for {ticket_id!r} failed, cannot determine "
              f"mechanism_backed (FORE-543): {err}", file=sys.stderr)
        return None
    cf = data.get("custom_fields") or {}
    value = cf.get("mechanism_backed")
    return value is True or value == "true"


def match_tessera_close(command):
    """ticket_id, or None if this command isn't a real tessera-cli close-to-closed transition
    that needs gating.

    FORE-543: only a CONFIRMED-negative ticket_mechanism_backed() result (`False`) counts as
    "not a match" now. `None` (could not determine) routes the write into evaluate()'s own
    fail-closed override/evidence check instead of silently skipping it -- the same posture as
    a confirmed `True`, matching bob_write_gate.py's posture for the identical external-
    dependency-failure shape."""
    if not command or not TESSERA_MODULE_RE.search(command):
        return None
    m = TESSERA_TRANSITION_RE.search(command)
    if not m:
        return None
    if not STATUS_CLOSED_RE.search(command):
        return None
    ticket_id = m.group(1).strip('"').strip("'")
    if not ticket_id:
        return None
    if ticket_mechanism_backed(ticket_id) is False:
        return None
    return ticket_id


# ---- GOALS.json results[]:MET write (C1 shape a) ---------------------------

HEREDOC_RE = re.compile(r"<<[-]?['\"]?(\w+)['\"]?\n(.*?)\n\1\b", re.DOTALL)


def _met_ids(obj):
    out = set()
    for r in (obj.get("results") or []):
        if isinstance(r, dict) and r.get("status") == "MET" and isinstance(r.get("id"), str):
            out.add(r["id"])
    return out


def _mechanism_backed_map(obj):
    out = {}
    for c in (obj.get("criteria") or []):
        if isinstance(c, dict) and isinstance(c.get("id"), str):
            out[c["id"]] = bool(c.get("mechanism_backed"))
    return out


def newly_met_mechanism_backed_ids(old_text, new_text):
    """Criterion ids newly present in new_text's results[]:MET that were NOT MET in old_text,
    restricted to criteria new_text's own criteria[] marks mechanism_backed:true. A malformed
    new_text can't be gated on (nothing to check), so it returns [] -- fails toward NOT
    matching, same direction as every other predicate in this suite."""
    try:
        new_obj = json.loads(new_text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    if not isinstance(new_obj, dict):
        return []
    old_obj = {}
    if old_text:
        try:
            parsed = json.loads(old_text)
            if isinstance(parsed, dict):
                old_obj = parsed
        except (json.JSONDecodeError, ValueError):
            old_obj = {}
    old_met = _met_ids(old_obj)
    new_met = _met_ids(new_obj)
    mech = _mechanism_backed_map(new_obj)
    result = []
    for cid in sorted(new_met - old_met):
        # MUTANT-EXCISION:C1-MECHANISM-FILTER-START -- see ablation/ two-arm fixtures
        if not mech.get(cid):
            continue
        # MUTANT-EXCISION:C1-MECHANISM-FILTER-END
        result.append(cid)
    return result


def resolve_goals_json_write_content(data):
    """(old_text, new_text) for an Edit/Write targeting a GOALS.json file, or None if this
    isn't that shape or the resulting content can't be determined.

    Edit's resulting content is reconstructed by applying old_string->new_string once against
    the CURRENT on-disk file -- the same single-replace semantics the Edit tool itself applies.
    Disclosed blind spot: if old_string doesn't appear verbatim in the current file (a stale
    edit, or a second Edit already applied since read), this returns None -- fails toward NOT
    matching rather than guessing at a reconstruction."""
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    file_path = hc.target_path(tool_input)
    if not file_path or Path(file_path).name != "GOALS.json":
        return None
    try:
        old_text = Path(file_path).read_text()
    except OSError:
        old_text = None
    if tool_name == "Write":
        new_text = tool_input.get("content")
        if not isinstance(new_text, str):
            return None
    elif tool_name == "Edit":
        old_string, new_string = tool_input.get("old_string"), tool_input.get("new_string")
        if old_text is None or not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        if old_string not in old_text:
            return None
        new_text = old_text.replace(old_string, new_string, 1)
    else:
        return None
    return old_text, new_text, Path(file_path)


def resolve_goals_json_bash_write(command, cwd):
    """(old_text, new_text) for a Bash-mediated write whose target resolves to a real
    GOALS.json path, or None. Best-effort, disclosed blind spot: this can only reconstruct the
    resulting content for a heredoc-body write (`cat > GOALS.json <<'EOF' ... EOF`) -- an
    inline echo/printf/sed-i write to a GOALS.json file is not confidently reconstructable as a
    full JSON document here and is left unmatched, same "fails toward NOT gating" direction
    extract_bash_write_targets's own docstring already takes for its own disclosed blind spots."""
    if not command:
        return None
    targets = cc.extract_bash_write_targets(command, cwd)
    goals_targets = [t for t in targets if t.name == "GOALS.json"]
    if not goals_targets:
        return None
    m = HEREDOC_RE.search(command)
    if not m:
        return None
    new_text = m.group(2)
    try:
        old_text = goals_targets[0].read_text()
    except OSError:
        old_text = None
    return old_text, new_text, goals_targets[0]


# ---- C2: invocation-evidence lookup -----------------------------------------

def evidence_root_for_target(target_path):
    """The Foreman project that OWNS this GOALS.json, or None if it is not inside one.

    FORE-614. main() used to compute find_project_root(cwd) ONCE, from the writer's payload cwd,
    and hand that same root to every evidence lookup. With a child project nested inside a
    parent, a write to the CHILD's GOALS.json carrying a payload cwd of the PARENT was checked
    against the PARENT's .foreman/invocation-evidence/.

    Fired, three arms, and the third is what makes it worse than "consults the wrong directory":

      no evidence anywhere                      DENIED   (the gate does match and does deny)
      forged 'pass' in the PARENT's dir         ALLOWED  (the bypass)
      real 'pass' in the CHILD's OWN dir        DENIED   (still denied)

    So it is not that the gate looked in one wrong place as well as the right one. The child's
    own evidence never entered the decision at all, in either direction: a forgery in the parent
    satisfied it and a genuine record in the child did not. A criterion could be marked MET with
    zero real backing, on the strength of a file an unrelated project's tree happened to contain
    under a colliding key.

    Same root cause and same fix shape as FORE-613 on defeater_ledger.is_defeaters_path(): a
    question about the TARGET was being answered from the writer's cwd. Anchor on the target.

    Returns None when the target is not inside any Foreman project. Callers treat that as a
    denial rather than a pass -- see evaluate_for_target(). That is a real behaviour change and
    it is the fail-closed direction: a GOALS.json outside every Foreman project has no evidence
    directory that could ever back it, and marking a mechanism_backed criterion MET there is
    exactly what this gate exists to stop."""
    try:
        return cc.find_project_root(str(Path(target_path).resolve().parent))
    except (OSError, RuntimeError, ValueError):
        return None


def evidence_status(project_root, key):
    """('pass', None) | ('fail', reason). Missing file, unreadable JSON, non-dict content, or
    probe_result != 'pass' all fail -- DENY-or-nothing (never ASK), matching this suite's
    established convention."""
    path = Path(project_root) / EVIDENCE_DIR_RELPATH / f"{key}.json"
    try:
        text = path.read_text()
    except OSError:
        return "fail", f"no invocation-evidence record at {path}"
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return "fail", f"{path} is not valid JSON ({exc})"
    if not isinstance(obj, dict):
        return "fail", f"{path} does not contain a JSON object"
    # MUTANT-EXCISION:C2-EVIDENCE-CHECK-START -- see ablation/ two-arm fixtures
    if obj.get("probe_result") != "pass":
        return "fail", f"{path} records probe_result={obj.get('probe_result')!r}, not 'pass'"
    # MUTANT-EXCISION:C2-EVIDENCE-CHECK-END
    return "pass", None


# ---- C3: override -----------------------------------------------------------

def load_override(project_root):
    """(override_obj_or_None, override_path). None means "no usable override" -- absent,
    unreadable, malformed, or expired all collapse to the same "treat as no override" outcome
    (C3: "an expired override is treated identically to no override at all")."""
    path = Path(project_root) / OVERRIDE_RELPATH
    try:
        text = path.read_text()
    except OSError:
        return None, path
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None, path
    if not isinstance(obj, dict):
        return None, path
    expiry = obj.get("expiry")
    if not isinstance(expiry, str):
        return None, path
    try:
        # "Z"-suffixed UTC (this repo's own timestamp convention, e.g. this file's own
        # GOALS.json "created": "...Z") is not accepted by datetime.fromisoformat() on the
        # Python 3.9 this hook actually runs under (fixed only in 3.11) -- same normalization
        # gate_document_ticket_staleness_check.py and telemetry_liveness.py already apply for
        # the identical reason, reused here rather than reinvented.
        expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return None, path
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    # MUTANT-EXCISION:C3-EXPIRY-CHECK-START -- see ablation/ two-arm fixtures
    if expiry_dt < datetime.now(timezone.utc):
        return None, path
    # MUTANT-EXCISION:C3-EXPIRY-CHECK-END
    return obj, path


def deny_message(kind, key, reason):
    return (
        "Foreman: write denied (k2_invocation_gate, FORE-533).\n"
        f"  shape  {kind}\n"
        f"  key    {key}\n"
        f"  reason {reason}\n"
        f"  next   record a real invocation-evidence file at "
        f".foreman/invocation-evidence/{key}.json "
        f'(schema: {{"probe_result": "pass", "probed_at": ISO8601, "probe_command": str, '
        f'"evidence_sha256": str}}), or use the documented override at '
        f".foreman/k2-override.json (schema: {{\"overridden_by\": str, \"reason\": str, "
        f'"ticket": str, "expiry": ISO8601}}) if this really is a legitimate exception.\n'
    )


def evaluate(project_root, data, kind, key):
    """Runs the C3 override check, then C2's evidence check, for one matched (kind, key)."""
    override_obj, override_path = load_override(project_root)
    if override_obj is not None:
        hc.set_rule(f"{RULE_ID}:override-used")
        # REQ-5's own stated verification, verbatim: "exercising it produces a log entry a
        # later audit can find." Explicit direct call (not left to hc.run()'s own automatic
        # tail) so `target` -- the override file's own path -- travels into the ledger row;
        # hc.run()'s automatic "fire" path never threads `target` through (only its "stolen"
        # path does, which does not fit this event's own semantics).
        hc.record_verdict(
            data, "fire", kind="k2-override-used", decision="allow",
            rule_id=f"{RULE_ID}:override-used", target=str(override_path),
            reason=f"K2 override consumed for {kind} {key}: {override_obj.get('reason', '')}",
        )
        return  # passthrough -- no hc.deny/hc.ask, the tool call proceeds
    status, reason = evidence_status(project_root, key)
    if status != "pass":
        hc.set_rule(f"{RULE_ID}:{kind}-denied")
        hc.deny(deny_message(kind, key, reason))
        return
    hc.set_rule(f"{RULE_ID}:{kind}-evidence-pass")
    # allow: no deny/ask emitted -- passthrough, silent, matching this suite's convention.


def main(data):
    tool_name = data.get("tool_name")
    if tool_name not in ("Edit", "Write", "Bash"):
        return

    cwd = data.get("cwd")
    project_root = cc.find_project_root(cwd)
    if project_root is None:
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    if tool_name in ("Edit", "Write"):
        resolved = resolve_goals_json_write_content(data)
        if resolved is None:
            hc.set_rule(f"{RULE_ID}:no-match")
            return
        old_text, new_text, target = resolved
        # FORE-614: the evidence that backs THIS GOALS.json lives in the project that owns it,
        # which is not always the project the writer is standing in.
        evidence_root = evidence_root_for_target(target)
        for cid in newly_met_mechanism_backed_ids(old_text, new_text):
            if evidence_root is None:
                hc.set_rule(f"{RULE_ID}:goals-json-target-outside-any-project")
                hc.deny(deny_message("goals-json", cid,
                                     f"{target} is not inside any Foreman project, so no "
                                     f"invocation-evidence record could back it"))
                return
            evaluate(evidence_root, data, "goals-json", cid)
            if hc.EMITTED_KIND == "deny":
                return
        hc.set_rule(f"{RULE_ID}:no-match")
        return

    # tool_name == "Bash"
    command = (data.get("tool_input") or {}).get("command", "")

    ticket_id = match_tessera_close(command)
    if ticket_id is not None:
        evaluate(project_root, data, "tessera-ticket", ticket_id)
        return

    resolved = resolve_goals_json_bash_write(command, cwd)
    if resolved is not None:
        old_text, new_text, target = resolved
        evidence_root = evidence_root_for_target(target)  # FORE-614, same reasoning as above
        for cid in newly_met_mechanism_backed_ids(old_text, new_text):
            if evidence_root is None:
                hc.set_rule(f"{RULE_ID}:goals-json-target-outside-any-project")
                hc.deny(deny_message("goals-json", cid,
                                     f"{target} is not inside any Foreman project, so no "
                                     f"invocation-evidence record could back it"))
                return
            evaluate(evidence_root, data, "goals-json", cid)
            if hc.EMITTED_KIND == "deny":
                return

    hc.set_rule(f"{RULE_ID}:no-match")


if __name__ == "__main__":
    hc.run(main)
