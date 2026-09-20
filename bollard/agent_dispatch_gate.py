#!/usr/bin/env python3
"""Agent-dispatch gate -- FORE-206. Blocks in-process Agent-tool subagent dispatch outright in
any project that registers this hook, forcing real cross-session routing (SendMessage to a
genuine peer session) instead.

THE GAP THIS EXISTS FOR (found live, 2026-08-27, during tessera-v2's first real dogfood run):
ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md section 4.4.3b states an explicit orchestrator mandate --
"never dispatch Bob in-process... always a genuine separate Claude Code session." That was
written down as a mandate, never backed by a mechanical gate. The one technical control actually
built (bob_write_gate.py case 6) only fires for a session with an OPEN Bob dispatch record inside
the Alice/sanitizer/Bob pipeline, which tessera-v2 doesn't have wired in at all. Nothing stopped
the tessera-v2 builder session from calling the Agent tool directly. This is the mechanical
backstop that was missing.

SCOPE: broader than "Bob" specifically. The operator's actual intent, confirmed live, is that a
Foreman-v2-governed build's orchestrator should never fork in-process subagents for its own task
delegation -- it should route real work to a genuine peer session (ListAgents + SendMessage),
same principle as 4.4.3b, applied to the whole build role rather than only the narrow Alice/Bob
write path. So this gate is a blanket deny on tool_name=="Agent", not conditioned on any open
dispatch record -- simpler than bob_write_gate.py's case 6 on purpose, because there is no
narrower state to key off yet.

PROJECT-SCOPED, NOT USER-WIDE. Same reasoning architecture_gate.py already gives: a user-wide
hard deny on the Agent tool would immediately block subagent use in every other project on this
machine, most of which have no such policy. Registered only in a project's own
`.claude/settings.json`, opt-in like every other Foreman-v2 gate.

FORE-354, recorded so this isn't re-investigated as a model quirk: registration-scoped means
IDENTITY-scoped coverage does not exist. A session working in a cwd with no `.claude/settings.json`
of its own (a freshly created scratch directory, for instance) is not covered by this gate
regardless of which model is running it -- confirmed live 2026-09-05 when a Fable-model session
dispatched in-process subagents from an unregistered scratch path while two Sonnet sessions in
registered projects were denied by this same file, unmodified, with no model check anywhere in
it. The apparent "model-dependent" behavior was the opt-in boundary, not the model. A real,
scoped-permission redesign (letting an orchestrator grant in-process dispatch for a specific
reviewer role without an all-or-nothing per-project switch) is intentionally out of scope here --
named directly by the operator as later, separate work, not a fix to make against this ticket.

FAIL-CLOSED, NOT hc.run(). Same reasoning as bob_write_gate.py section 4.5: hc.run() fails open
on any internal error, and for THIS gate specifically, failing open means silently allowing the
exact thing the operator is trying to stop. That is the wrong direction for a policy gate whose
entire job is "deny this class of call," so this file uses its own top-level handler: catch
BaseException, emit hc.deny(...), record the verdict, exit 0. Named residual, same as
bob_write_gate.py: if the process is killed before it can emit anything, the harness sees no
output and allows -- there is no mechanism here to close that.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_lib  # noqa: E402
import hook_common as hc  # noqa: E402
import verdict_ledger  # noqa: E402
import agent_registry_staleness_check  # noqa: E402

RULE_ID = "AGENT-DISPATCH-GATE"

# FORE-462/CHV2-66/CHV2-67: an explicit ALLOWLIST of pure-research/fact-finding subagent types,
# exempted from the blanket deny below. Deliberately an allowlist of safe types, not a denylist
# of unsafe ones -- a new or renamed persona, or any value not in this literal set, stays denied
# by default (GOALS.json C6). Exact string match only (GOALS.json C7): no case-folding, no
# stripping, no substring test. A type qualifies only if its frontmatter `tools:` is a subset of
# {Read, Grep, Glob} -- muse-feedback is the second member, added once the CHV2-66 scope doc's
# three-condition admission rule was satisfied and the definition file landed at the harness's
# resolving path (~/.claude/agents/, C11). Any future addition still needs its own
# scope->blast-radius->cleanup pass (CHV2-66 scope doc section 3), not a bare constant edit.
# "Plan" is a closer call (it designs an approach) and stays denied under the default until
# decided in its own pass.
RESEARCH_ONLY_SUBAGENT_TYPES = frozenset({"Explore", "muse-feedback"})

# FORE-660 (Stage 3, escalated design, operator decision 2026-09-17/18, logged on that
# ticket): auto-route ONLY this narrow, evidenced set of generic-research-shaped requests to
# Explore, silently -- no deny, nothing for the agent to act on. "fork" is the ONE real case
# this project's own real incidents named as motivating evidence (this session hit the
# FORE-206 deny twice tonight requesting exactly this string; Explore worked once tried).
# Deliberately NOT a blanket "any unrecognized subagent_type routes to Explore" policy --
# the operator explicitly REJECTED the broader "for muse use Explore" substitution (that
# would be the proxy-dispatch anti-pattern the architecture record already documents,
# FOREMAN-COMPREHENSIVE-ARCHITECTURE-RECORD-20260916.md Part III section 3.2: Explore has no
# Skill/Agent tools and structurally cannot perform muse's real two-hop method). A blanket
# unrecognized-routes-to-Explore policy would also silently weaken C6's own fail-closed
# default for every future named persona that isn't added to this set yet, not just this one
# evidenced alias. Widening this set needs its own evidence, the same discipline C6 already
# requires for RESEARCH_ONLY_SUBAGENT_TYPES itself -- not a bare constant edit.
EXPLORE_SHAPED_ALIASES = frozenset({"fork"})

# CHV2-121 GAP 1 (Iris [b16c63], real P21 finding against three real disk states -- identical
# ALLOW verdict whether muse-feedback.md granted exactly {Read, Grep, Glob}, granted Bash and
# Write too, or omitted tools: entirely): CHV2-66's own three-condition admission rule (cited
# verbatim above) requires a RESEARCH_ONLY_SUBAGENT_TYPES entry's resolved persona definition to
# stay within {Read, Grep, Glob}. Before this, nothing in this gate's own decision path ever
# read that definition -- the admission rule was enforced by one same-component unit test at
# authoring time (test_muse_feedback_definition_tools_line_is_exactly_read_grep_glob) and by
# NOTHING at dispatch time. _resolved_definition_exceeds_admission() below closes that: it
# resolves the SAME way agent_registry_staleness_check.py's own _resolve_agent_md() does (real
# cwd, real candidate-directory precedence) and checks the resolved file's real tools: line.
#
# DISCLOSED, NOT A COMPLETE FIX (operator decision, PDP.md section 11.6, CHV2-121, 2026-09-19):
# Lesson 17 -- agent_registry_staleness_check.py's own entire reason for existing -- is that
# Claude Code's harness snapshots a custom subagent's definition ONCE, at session start, and may
# never re-read it mid-session. So the bytes this function reads from disk RIGHT NOW, at dispatch
# time, are not guaranteed to be the definition actually in effect for the dispatch being
# decided; a persona edited after session start could still be running under its pre-edit
# definition while this check reads the post-edit (or post-widening) file. This closes "nothing
# reads the file at dispatch time AT ALL" -- it does NOT close "the file read is guaranteed
# current", and must never be reported as though it does. Whether the deeper snapshot-staleness
# question needs its own separate design (re-deriving how the harness actually surfaces an
# in-effect subagent definition at dispatch time) is real, open, and explicitly not decided or
# closed by this function.
ADMITTED_RESEARCH_TOOLS = frozenset({"Read", "Grep", "Glob"})
TOOLS_LINE_RE = re.compile(r"^tools:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


def _resolved_definition_exceeds_admission(subagent_type, cwd):
    """Real-executed, not simulated: resolves subagent_type's currently-on-disk persona
    definition (same function, same precedence order, as agent_registry_staleness_check.py's
    own _resolve_agent_md()) and checks its real tools: line against ADMITTED_RESEARCH_TOOLS.

    Returns None when there is nothing to object to: subagent_type is a built-in with no custom
    definition file at all (claim (1) of CHV2-66's admission rule -- a built-in cannot widen via
    an edited persona file, because there is no file), or a real definition exists and its
    tools: line is a genuine subset of {Read, Grep, Glob}.

    Returns a real, human-readable reason string -- never raises on an expected condition -- for
    every other case: the definition's tools: line grants something beyond {Read, Grep, Glob}
    (CHV2-121 GAP 1's own real finding), or the definition has NO tools: key at all, which Claude
    Code's own frontmatter contract treats as granting every tool (CHV2-121 GAP 3) -- the
    maximal grant, not a trivial pass. A definition file that exists but cannot be read raises
    OSError uncaught, deliberately: this gate's own top-level fail-closed entrypoint (see module
    docstring) already converts any uncaught exception into a real deny, which is the correct
    outcome for "a persona file we expected to be readable was not" -- adding a second,
    local try/except here would only reintroduce a fail-open path this module was built
    specifically to not have."""
    resolved = agent_registry_staleness_check._resolve_agent_md(subagent_type, cwd)
    if resolved is None:
        return None
    text = resolved.read_text(encoding="utf-8")
    match = TOOLS_LINE_RE.search(text)
    if match is None:
        return (f"{resolved} has no tools: key at all, which Claude Code grants as EVERY "
                f"tool -- wider than the {{Read, Grep, Glob}} CHV2-66's admission rule allows")
    granted = {t.strip() for t in match.group(1).split(",") if t.strip()}
    if not granted <= ADMITTED_RESEARCH_TOOLS:
        return (f"{resolved}'s tools: line grants {sorted(granted)}, beyond "
                f"{{Read, Grep, Glob}} (extra: {sorted(granted - ADMITTED_RESEARCH_TOOLS)})")
    return None



# CHV2-162. DERIVED FROM TWO MEASURED CONSTRAINTS, not picked, and the upper one is the
# surprising half:
#
#   LOWER  the longest real subagent type on this machine is 14 chars (clint-eastwood),
#          measured across ~/.claude/agents plus this module's own allowlist. The cap must sit
#          comfortably above that or it truncates legitimate names.
#   UPPER  40. Above it, remedy (1) no longer survives into the verdict-ledger ROW. Measured by
#          firing the real gate at each candidate and reading the bounded row back: remedy (1)
#          survives at 40 and below, and is lost at 48 and 64.
#
# The upper bound exists because this value is COUPLED to verdict_ledger.MAX_REASON_HEAD. Every
# character the cap allows pushes remedy (1) later in the message, and once it crosses the head
# budget the ledger cuts it mid-phrase. A first attempt at 64 put remedy (1) at offset 298
# against a 300-char head -- inside the budget by two characters, which is not a margin.
#
# 32 sits between the two bounds: 2.3x the longest real name, and a full step below the point
# where the remedy starts being cut. THE COUPLING IS TESTED, NOT REMEMBERED --
# test_both_remedies_survive_into_the_LEDGER_ROW_not_just_the_message fails if this value, the
# ledger's head budget, or the message layout ever drift apart again.
#
# CHV2-165 CORRECTS THE SCOPE OF THAT CLAIM. CHV2-162 closed the caller-inflates-subagent_type
# attack (subagent_type was the only field varied against the cap), but a SECOND field competed
# for the same fixed budget and was never varied in that ticket's own derivation:
# example_task_snippet, built from the caller's own prompt/description a few lines below. Measured
# directly: with subagent_type held at an ORDINARY, real length and no attack in play at all,
# ANY real prompt (as little as 10 characters) already evicted remedy (1) from the durable
# verdict-ledger row -- CHV2-162's own coupling test never caught this because it fires with no
# prompt/description field at all, pinning the one case where the property happens to hold. See
# the comment on the deny message construction below for the fix -- this cap and its derivation
# above are UNCHANGED and still correct for what they were measured against; they were simply
# never sufficient on their own, because a second, independently-varying field was sharing the
# same fixed budget.
MAX_SUBAGENT_TYPE_IN_MESSAGE = 32


def bounded_for_message(value, limit=None):
    """An agent-supplied value, bounded for inclusion in a deny message.

    CHV2-162. The marker states the real length rather than just signalling that something was
    cut: a reader diagnosing a deny needs to know a 9000-character type was sent, and the
    difference between "odd name" and "someone pushed 9000 characters at the gate" is the whole
    diagnosis. It also means a truncated value can never be mistaken for a real type name.

    Mirrors example_task_snippet's existing cap a few lines below rather than inventing a second
    convention for the same job; that one is a display snippet, this one is a bound on a field
    the caller controls, so it names its own limit."""
    # Read at CALL time, not bound as a default argument. A default is evaluated once at
    # definition, so a test that reassigns the module constant would change nothing and would
    # silently measure the original value -- which is exactly what happened while deriving this
    # bound, and made every row of the derivation table read identically.
    limit = MAX_SUBAGENT_TYPE_IN_MESSAGE if limit is None else limit
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return f"{value[:limit]}...[{len(value)} chars, truncated]"


def main(data):
    if data.get("tool_name") != "Agent":
        return

    tool_input = data.get("tool_input") or {}
    subagent_type_value = tool_input.get("subagent_type")

    if isinstance(subagent_type_value, str) and subagent_type_value in RESEARCH_ONLY_SUBAGENT_TYPES:
        # CHV2-121 GAP 1: verify the CURRENTLY-RESOLVED persona definition still matches
        # CHV2-66's admission rule, rather than trusting the allowlist name match alone. See
        # _resolved_definition_exceeds_admission()'s own docstring for the Lesson 17 caveat --
        # real, but disclosed as partial coverage, not a complete fix.
        mismatch = _resolved_definition_exceeds_admission(subagent_type_value, data.get("cwd"))
        if mismatch:
            hc.set_rule(f"{RULE_ID}:definition-exceeds-admission")
            hc.audit("SAFETY_DENY", {"guard": "agent_dispatch_gate", "reason":
                      "resolved-definition-exceeds-admission-rule",
                      "subagent_type": subagent_type_value, "detail": mismatch},
                     data.get("session_id"), data.get("cwd"), severity="high")
            hc.deny(
                f"Blocked: {subagent_type_value}'s currently-resolved persona definition "
                f"grants more than CHV2-66's research-only admission rule allows -- {mismatch}. "
                f"This gate's own allowlist entry assumed a narrower grant; either revert the "
                f"persona definition to {{Read, Grep, Glob}} or, if the wider grant is "
                f"deliberate, route this through a fresh CHV2-66-style admission review before "
                f"re-adding {subagent_type_value} to RESEARCH_ONLY_SUBAGENT_TYPES."
            )
            return
        hc.set_rule(f"{RULE_ID}:research-only-allowed")
        hc.audit("SAFETY_ALLOW", {"guard": "agent_dispatch_gate", "reason":
                  "research-only-subagent-type-allowed", "subagent_type": subagent_type_value},
                 data.get("session_id"), data.get("cwd"), severity="info")
        return

    if isinstance(subagent_type_value, str) and subagent_type_value in EXPLORE_SHAPED_ALIASES:
        hc.set_rule(f"{RULE_ID}:explore-shaped-silent-route")
        # Standing requirement (operator decision, FORE-660): silent-to-the-agent must not
        # mean silent-to-the-record. Logged to the existing verdict/telemetry stream (decided
        # 2026-09-18 -- an analytics record, not a security guarantee needing tamper-evidence,
        # so no new dedicated ledger), naming both what was actually requested and what ran,
        # via the same hc.audit() every sibling branch in this file already uses.
        hc.audit("SAFETY_ALLOW", {"guard": "agent_dispatch_gate", "reason":
                  "explore-shaped-silent-substitution",
                  "requested_subagent_type": subagent_type_value,
                  "routed_subagent_type": "Explore"},
                 data.get("session_id"), data.get("cwd"), severity="info")
        # Full original tool_input, only subagent_type overridden -- every other field (the
        # real prompt/description) must survive unchanged, or the re-issued call loses the
        # actual task. See hook_common.rewrite()'s own docstring for why a partial dict here
        # would be wrong.
        updated_input = dict(tool_input)
        updated_input["subagent_type"] = "Explore"
        hc.rewrite(updated_input)
        return

    subagent_type = subagent_type_value if isinstance(subagent_type_value, str) else "(unspecified)"
    # CHV2-162: the value below is the caller's own, and before this it went into the deny
    # message unbounded and TWICE. Measured: a 600-char subagent_type produced a 1946-char
    # reason, ~400 chars of the stored ledger row were the caller's own string, and remedy (1)
    # was pushed out of the record entirely. The party being denied decided how much of the
    # gate's own policy text survived -- PDP.md section 16.4's shape, a gate-adjacent field
    # carrying authority nobody specified.
    #
    # CHV2-119 (head+tail truncation in verdict_ledger) does not close this and cannot: text
    # pushed out of BOTH ends of the message is gone before the ledger ever sees it. Measured
    # there across every candidate strategy, remedy (1) was lost under all of them. The bound
    # has to be here, at construction.
    #
    # ONLY THE MESSAGE IS BOUNDED. `subagent_type` itself stays whole for hc.audit() above,
    # where the full value is what a later investigation needs.
    subagent_type_shown = bounded_for_message(subagent_type)

    hc.set_rule(f"{RULE_ID}:in-process-dispatch-denied")
    hc.audit("SAFETY_DENY", {"guard": "agent_dispatch_gate", "reason":
              "in-process-dispatch-denied", "subagent_type": subagent_type},
             data.get("session_id"), data.get("cwd"), severity="high")
    # FORE-659 (Stage 2, absorbs FORE-653): the two named remedies below now each carry a
    # literal, directly re-issuable command rather than prose describing the shape of one --
    # construction over instruction, per the operator's own observation that agents ignore
    # deny-message text most of the time and per FORE-653's own documented incident (the
    # correct remedy was ALREADY named in this exact message and still wasn't acted on).
    # Genuine pre-fill (not just a literal string) is mechanically feasible for exactly the
    # Explore-substitution path: the ORIGINAL subagent_type is swapped for the one real
    # allowed value that plausibly matches "fresh-eyes research," with every other parameter
    # unchanged, so re-issuing costs nothing extra relative to retrying blind. The
    # ListAgents()-first path can't be pre-filled the same way -- SendMessage's own required
    # `to` argument doesn't exist until ListAgents' real output supplies a name -- so it gets
    # the exact next single, zero-argument, runnable call instead of a sentence about it.
    #
    # CHV2-165. CHV2-162 bounded subagent_type but missed that example_task_snippet (below) is
    # a SECOND, independently-varying field competing for the same fixed
    # verdict_ledger.MAX_REASON_HEAD/MAX_REASON_TAIL budget -- and, sitting ahead of remedy (1)
    # in the old message layout, it was the one actually evicting remedy (1) for ordinary,
    # non-adversarial dispatches (measured: any real prompt, as little as 10 characters, was
    # enough; no attack required). The fix is not a new length cap on example_task_snippet --
    # that would only re-derive CHV2-162's same coupling at a different threshold, the same
    # shape of bug this ticket is about. Instead, BOTH remedies are now fully fixed text with
    # no caller-controlled value interpolated into either (neither needs the caller's own
    # subagent_type echoed back to be actionable -- the caller already knows what it sent), and
    # the two variable-length, caller-controlled fields (subagent_type_shown,
    # example_task_snippet, plus the allowed-set listing) sit BETWEEN the two remedies --
    # never before remedy (1), never after remedy (2):
    #   - remedy (1) is protected by MAX_REASON_HEAD purely by being first: head-truncation
    #     preserves the message's own first N characters regardless of what follows, so nothing
    #     placed after remedy (1) can evict it. Measured at 245 characters, 55 under the
    #     300-character head budget -- a real margin, not "inside by two characters."
    #   - remedy (2) is protected by MAX_REASON_TAIL by being the message's own literal suffix.
    #     Nothing follows it, so as long as its own fixed text stays under the tail budget (300
    #     characters measured, 50 under MAX_REASON_TAIL=350), it cannot be squeezed out by
    #     caller-controlled content that used to follow it.
    # Verified by firing the real gate and reading the real verdict-ledger row across ordinary
    # AND adversarial subagent_type/prompt combinations, including a 999999-character
    # subagent_type paired with a 9999-character prompt simultaneously -- both remedies survive
    # in every case tested, not just the no-prompt case CHV2-162's own regression test pins.
    example_task_snippet = (tool_input.get("prompt") or tool_input.get("description") or "")
    example_task_snippet = (example_task_snippet[:60] + "...") if len(example_task_snippet) > 60 else example_task_snippet
    explore_candidate = sorted(RESEARCH_ONLY_SUBAGENT_TYPES)[0]
    remedy_1 = (
        f"Blocked: in-process Agent-tool dispatch isn't permitted here (FORE-206). Two "
        f"options: (1) genuine fresh-eyes research/fact-finding: reissue this exact call "
        f"with subagent_type={explore_candidate!r} instead of the denied type, every other "
        f"parameter unchanged."
    )
    diagnostic = (
        f" Denied subagent_type={subagent_type_shown!r} (full allowed set: "
        f"{', '.join(sorted(RESEARCH_ONLY_SUBAGENT_TYPES))})"
    )
    if example_task_snippet:
        diagnostic += f" (task: {example_task_snippet!r})"
    diagnostic += "."
    remedy_2 = (
        f" (2) For a judgment-rendering persona dispatch (a reviewer, a second opinion, "
        f"cross-file review) rather than research, the correct next call is ListAgents() to "
        f"find a real peer session, then SendMessage to that peer -- ListAgents() takes no "
        f"required arguments and is always the right first move here."
    )
    hc.deny(remedy_1 + diagnostic + remedy_2)


def _fail_closed_entrypoint():
    started = time.time()
    data = hc.read_input()
    # CHV2-120 (Iris [b16c63], BUILD4-I1's second divergence in this same re-implemented call).
    # hc.read_input() returns {} on an unparseable stdin payload -- a valid dict -- so the
    # `isinstance(data, dict)` check below never catches it: main({}) ran against an empty
    # payload, emitted nothing, and the finally block recorded that as verdict="silent", the
    # ledger's "the guard looked and correctly found nothing" cell. A machinery failure read as
    # a clean result, with session_id lost and no audit-plane record. Checked and handled here,
    # BEFORE main() ever runs, mirroring hc.run_body()'s own dedicated branch for this exact
    # input (hook_common.py ~line 470-506, ATLASSN-69) -- salvage what the raw bytes still show,
    # write the paired HOOK_ERROR, and record a real "error" row, because there is no "silent"
    # or "fire" decision to make against a payload that was never actually read.
    if hc.INPUT_UNPARSEABLE:
        elapsed = int((time.time() - started) * 1000)
        raw = hc.RAW_INPUT_ON_PARSE_FAILURE or ""
        cutoff = raw.find('"tool_input"')
        scope = raw[:cutoff] if cutoff != -1 else raw
        salvaged = {}
        for field in ("session_id", "cwd"):
            m = re.search(r'"%s"\s*:\s*"([^"]*)"' % field, scope)
            if m:
                salvaged[field] = m.group(1)
        salvage_kind = "unparseable-stdin-salvaged" if salvaged else "unparseable-stdin"
        try:
            audit_lib.audit_append(
                "HOOK_ERROR", session_id=salvaged.get("session_id"), cwd=salvaged.get("cwd"),
                severity="high", hook="agent_dispatch_gate.py",
                error="unparseable-stdin (payload was not JSON; guard ran against an empty "
                      "dict and decided nothing)")
        except Exception:
            print("[agent_dispatch_gate] could not record HOOK_ERROR to the audit plane for "
                  "an unparseable-stdin exit", file=sys.stderr)
        try:
            verdict_ledger.record(salvaged, "error", kind=salvage_kind, duration_ms=elapsed,
                                   handler_id="agent_dispatch_gate.py")
        except Exception:
            print("[agent_dispatch_gate] verdict ledger write failed", file=sys.stderr)
        sys.exit(0)
    sid = None
    cwd = None
    try:
        if not isinstance(data, dict):
            raise ValueError(f"hook payload was not a JSON object (got {type(data).__name__})")
        sid = data.get("session_id")
        cwd = data.get("cwd")
        main(data)
    except BaseException as exc:  # noqa: BLE001 -- deliberate, see module docstring
        try:
            audit_lib.audit_append("HOOK_ERROR", session_id=sid, cwd=cwd, severity="high",
                                    hook="agent_dispatch_gate.py", error=repr(exc))
        except Exception:
            print("[agent_dispatch_gate] could not record HOOK_ERROR to the audit plane",
                  file=sys.stderr)
        hc.set_rule(f"{RULE_ID}:internal-error")
        hc.deny(f"Blocked: agent_dispatch_gate.py hit an internal error "
                f"({type(exc).__name__}). Fails closed by design -- this gate does not fail "
                f"open on a bug, since failing open here means silently allowing the exact "
                f"in-process dispatch it exists to stop.")
    finally:
        elapsed = int((time.time() - started) * 1000)
        try:
            # BUILD4-I1: reason=hc.EMITTED_REASON threaded through explicitly, matching
            # hook_common.run()'s own standard wrapper (hook_common.py's "fire" branch) -- this
            # gate's own custom fail-closed entrypoint (see module docstring for why it cannot
            # use hc.run()) re-implements the ledger-write call independently, and FORE-659's
            # deny-plus-guidance reason previously never reached this call at all, only the
            # harness-visible stdout. Confirmed missing by a real subprocess test before this fix
            # (a real deny run produced a ledger row with no 'reason' key), confirmed present
            # after it with the same test.
            verdict_ledger.record(
                data,
                "fire" if hc.EMITTED_KIND else "silent",
                kind=hc.EMITTED_KIND,
                duration_ms=elapsed,
                handler_id="agent_dispatch_gate.py",
                decision=hc.EMITTED_DECISION,
                rule_id=hc.EMITTED_RULE_ID,
                reason=hc.EMITTED_REASON,
            )
        except Exception:
            print("[agent_dispatch_gate] verdict ledger write failed", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    _fail_closed_entrypoint()
