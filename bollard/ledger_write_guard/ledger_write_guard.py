#!/usr/bin/env python3
"""ledger_write_guard.py -- FORE-485. PreToolUse hard-deny on any direct Edit/Write/
NotebookEdit/MultiEdit or Bash write targeting `.foreman/ledger.jsonl`, PDP.md's append-only
evidence ledger (section 6).

PDP.md section 15: "The ledger's write protection is unresolved. Until it lands, an agent
with Bash can append to the ledger directly, and the whole evidence model rests on nobody
choosing to." PDP-RATIONALE.md section 7 (revision 3 rewrite) names this an EXTENSION of the
Alice/Bob least-privilege pattern this project already designed, "not a greenfield choice,"
and this file is modeled directly on the one place that shape is already shipped and
enforcing in this exact repo: review_events_ledger_guard.py (REQ-12), applying the identical
"hard-deny the direct write path, name the one legitimate writer" shape to its own sibling
ledger, `.foreman/review-events.jsonl`.

MECHANISM CHOICE, narrowed against PDP-RATIONALE.md section 7's own two-candidate framing.
That section names two options: (a) this repo's existing PreToolUse hard-deny-hook pattern,
already shipped as review_events_ledger_guard.py, sharing the agent's own UID; or (b) a
stronger, heavier mechanism -- a sandbox-exec profile (measured 5/5 payloads blocked, ~6ms)
or a loopback daemon under a second uid. This file builds (a). Reasons, stated rather than
assumed: FORE-485 is declared Tier 2 ("a well-specified, single-mechanism build, not a fresh
architecture" -- FORE-484's own sibling-ticket framing, same parent FORE-483); (a) is the
pattern PDP-RATIONALE.md section 7 itself says "now reads as an extension of an existing
in-house pattern rather than a fresh choice," already has one shipped, tested instance in
this repo, and needs no new infrastructure. (b) remains real future-hardening work, not
silently dropped -- PDP-RATIONALE.md section 7 is the pointer for whoever picks it up next.

DISCLOSED SEQUENCING DEPENDENCY, the same shape FORE-484's stage_order_gate.py discloses:
PDP.md's own "the stamper" -- the code that would legitimately compute and append a
stage_close/stage_skip row -- does not exist yet. FORE-351 is the open ticket for the
`foreman close/verdict/skip/ablate` CLI; nothing under this repo or foreman-v2 implements it
(re-checked at proposal time: no `stage_close.py`, no `foreman` entrypoint anywhere).
Registering this guard therefore denies EVERY write to `.foreman/ledger.jsonl`, with no
carve-out, until that CLI exists -- the correct fail-closed reading of PDP.md's R1 ("evidence
is authored by something other than the party judged") rather than a defect: nothing should
be appending to this ledger by hand today regardless of this guard's existence, and this
guard is what makes that mechanically true instead of resting on nobody choosing to violate
it. This proposal does NOT include any settings.json registration, for the same reason
FORE-484's proposal doesn't -- that decision needs FORE-351 to exist first, and for this
guard specifically, registering it today would make the ledger permanently unwritable, which
may well be correct but isn't this proposal's call to force.

COMPONENT PLACEMENT, decided and stated rather than left to Bob to guess (per his own
question): its own subdirectory, `hooks/ledger_write_guard/**`, declared as its OWN component
in ARCHITECTURE.md -- not an extension of `review_events_ledger_guard`, which is itself an
undeclared bare file at `hooks/` (confirmed: absent from ARCHITECTURE.md's own components
block). Extending an undeclared thing would compound the exact "seven components collide on
one hooks/ root" defect that document already names as unresolved, not fix anything. New
gates in this repo get their own subdirectory (tessera_resolver, tier_triage_gate,
freeze_reentry_gate, ticket_status_gate, dependency_provenance_gate all already do) -- that is
the established remediation pattern this file follows, not the legacy flat-file pattern.

RESIDUAL, inherited from (a) and disclosed rather than overclaimed, matching PDP-RATIONALE.md
section 7's own honesty ceiling ("a gate verifying that a file says someone is authorized is
not verifying that anyone with authority said so") and this project's own measured A2 finding
(ALICE-BOB-LEAST-PRIVILEGE-DESIGN.md section 3.2: an attribute-built or string-concatenated
equivalent of a denied literal defeats a literal-text matcher -- same operation, same target,
invisible to it). This guard's Bash-path coverage is exactly review_events_ledger_guard.py's
own: it denies a Bash command whose OWN visible text names ledger.jsonl alongside a
write-shaped operator, and a direct Edit/Write/NotebookEdit/MultiEdit naming the ledger as
`file_path`. It cannot and does not see inside a script the agent invokes via Bash -- a script
that opens and appends to the ledger internally, with its target path built at runtime rather
than named literally on the Bash command line, is invisible to a PreToolUse hook watching only
that command's own text, exactly as it already is for review_events_ledger_guard.py today.
Same shared-UID accounting PDP-RATIONALE.md section 7 names for the stronger mechanisms too:
"promoting a mechanism from audit to enforcement does not change the shared-UID model, it
changes what a successful forgery is worth." An agent that can edit this hook file or
`.claude/settings.json`, or set `disableAllHooks`, defeats this guard entirely -- the same
residual PDP-RATIONALE.md section 7 already names for both candidate mechanisms, not a new
one introduced here. test_ledger_write_guard.py's own last test exercises this residual
directly rather than only describing it in prose.

AMENDED 2026-09-11 (FORE-567, frozen ledger_write_guard/GOALS.json scope_version 2, hash
sha256:6738aea3e3cdb768688055caa435531f8d521064c495887ba922bfa890904f42): adds a narrow,
fail-closed writer-identity exemption (C8-C14) for foreman_close_skip.py's own legitimate
`.foreman/ledger.jsonl` writes on the Bash path. C1-C5's existing deny paths above are
UNCHANGED -- this is one new allow path inside the `tool_name == "Bash"` branch, gated by
three layers that must ALL pass (Layer 1: positive charset + grammar match against a pinned
invocation shape, no blacklist, no whitespace-shorthand class member, absolute pinned
interpreter, backslash-A/backslash-Z anchored, never re.MULTILINE; Layer 2: resolved script
path + content-hash pin; Layer 3:
effective-cwd project-root check + argument sanity), with fall-through to the unchanged deny
logic on any layer failing. Full design history, including two real bypasses reviewer-peer
found and closed by execution (a raw-string interpreter-pin defeated by a writable symlink at
`~/.venv/bin/python3`; a whitespace-shorthand-in-charset trap that silently readmits
newline-chaining):
`/Users/m5/dev/claude-hooks-v2/BOB-DESIGN-FORE567-LEDGER-WRITER-IDENTITY-EXEMPTION-20260911.md`.
Open question OQ1 (sidecar re-pin has no separation of duty from whoever can edit
foreman_close_skip.py itself) is accepted as a named residual per the design-scope freeze,
not solved here -- see that file's own `open_questions` array and DECISION-FORE567-DESIGN-
SCOPE-FREEZE-20260911.md.
"""

import hashlib
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import component_coupling as cc  # noqa: E402
import hook_common as hc  # noqa: E402

RULE_ID = "FOREMAN-LEDGER-WRITE-GUARD"
LEDGER_NAME = "ledger.jsonl"
FOREMAN_DIR = ".foreman"

# Matches the exact path (with or without a leading .foreman/) so both a project-relative
# reference and a bare filename reference are caught -- same convention
# review_events_ledger_guard.py's own LEDGER_PATH_RE uses for its sibling ledger.
LEDGER_PATH_RE = re.compile(r"(?:\.foreman[/\\])?ledger\.jsonl", re.IGNORECASE)

# Same write-operator set review_events_ledger_guard.py's own text-match backstop uses --
# a real, established pattern in this suite, not invented fresh here.
BASH_WRITE_OPERATOR_RE = re.compile(
    r">>?(?!\s*&)|\btee\b|\bcp\b|\bmv\b|\bsed\s+-i\b|\bdd\b[^\n]*\bof="
)

# ---- FORE-567: writer-identity exemption -----------------------------------
# C12: a single hardcoded constant, never discovered via search/glob -- the guard does not
# search for or honor a second writer_allowlist.json anywhere else on the machine.
SIDECAR_PATH = Path(__file__).resolve().parent / "writer_allowlist.json"

# Layer 1a charset gate -- C13: the whitespace member is a LITERAL space, never `\s` (which
# also matches tab/newline/CR/FF/VT and would silently reopen newline-chaining even with
# \A...\Z anchoring below and re.MULTILINE never passed to re.compile -- F7/F9). \A...\Z
# anchors true start/end of string only, unlike ^...$, which also matches at every line
# boundary under re.MULTILINE, and unlike bare $, which matches just before a trailing
# newline even without it (reviewer-peer measured this differential directly, R3 design doc
# section 4). This pattern is a positive allowlist, not an extended blacklist -- F4's seven
# chaining forms (;, &&, ||, |, backtick, $(), bare &, newline) are excluded categorically by
# what is NOT in the class, not by enumeration.
WRITER_CHARSET_RE = re.compile(r"\A[A-Za-z0-9 ./_-]*\Z")  # never re.MULTILINE -- F7

CLOSE_FLAGS = {"--stage", "--ticket", "--component", "--cwd"}
CLOSE_REQUIRED = {"--stage", "--ticket"}
SKIP_FLAGS = {"--stage", "--reason", "--ticket", "--acceptor", "--cwd"}
SKIP_REQUIRED = {"--stage", "--reason", "--ticket", "--acceptor"}

# Layer 3 argument sanity -- real stage/ticket id shapes, per pipeline.json's own stage `id`
# fields and TESSERA's own ticket-id convention.
#
# CHV2-124: TICKET_ID_RE was `[A-Z]+-\d+`, whose prefix class cannot match a digit. Five of the
# 42 project prefixes live in TESSERA today contain one -- CHV2, DEVHR2, F9FR, FOREV2, TESSV2 --
# including this repo's own (CHV2, project CLAUDE-HOOKS-V2). A legitimate, correctly-pinned
# closer invocation for any ticket in those projects failed this check, fell through to the
# guard's ordinary hard deny, and could not close its own ledger entry. Found by Iris Support
# attacking the predicate with a 2x4 grid rather than reading it, with every condition ahead of
# the ticket check verified individually so the regex was isolated as the sole cause.
#
# The prefix must still START with a letter, so an all-digit prefix is still rejected; only the
# interior admits digits. That mirrors STAGE_ID_RE's own letter-then-alphanumeric shape directly
# above rather than inventing a second convention for the same kind of identifier.
#
# This widens an EXEMPTION from a hard deny, so it is worth stating why it does not widen the
# attack surface: `--ticket`'s value is only ever grammar-checked, never used as a path or
# re-executed, and every layer ahead of it still applies -- pinned interpreter, pinned script,
# matching sha256, allowed/required flags, a valid stage id, and an effective root that resolves
# inside a real Foreman project. Accepting `CHV2-124` alongside `FORE-351` changes which
# legitimate ticket ids can close a ledger entry, and nothing else.
STAGE_ID_RE = re.compile(r"\A[a-z][a-z0-9-]*\Z")
TICKET_ID_RE = re.compile(r"\A[A-Z][A-Z0-9]*-\d+\Z")


def resolve_write_target(path_str, cwd):
    """The absolute, symlink-resolved path a tool_input names, or None.

    A relative path is resolved against the hook PAYLOAD's own `cwd` -- the agent's logical
    working directory at the time of the tool call -- never against this hook subprocess's
    own os.getcwd(). That is stage_order_gate.py's QA-Bob round-1 finding B2 applied here
    before it can bite: the two need not match, and resolving against the wrong base makes a
    relative path miss. Same `base = Path(cwd) if cwd else Path.cwd()` shape
    component_coupling.extract_bash_write_targets() already uses, reused rather than
    reinvented.
    """
    if not path_str:
        return None
    base = Path(cwd) if cwd else Path.cwd()
    candidate = Path(path_str)
    absolute = candidate if candidate.is_absolute() else (base / candidate)
    try:
        return absolute.resolve()
    except OSError:
        return None


def is_ledger_path(path_str, cwd=None):
    """True only for a real Foreman evidence ledger -- `<anything>/.foreman/ledger.jsonl`.

    F3 (reviewer-peer, 2026-09-10). This predicate used to be
    `Path(path_str).name == LEDGER_NAME`: any file named ledger.jsonl, anywhere. An eight-case
    battery against the landed file scored 3 wrong, and only ONE of the three was the
    over-match everyone had been discussing:

      FALSE POSITIVE  an unrelated file merely sharing the basename was denied. There are ~145
                      of these live on this machine under
                      /Users/m5/dev/observer-effect-doe/runs/**/ledger.jsonl, dated 2026-09-09
                      -- created AFTER agent-remediation-cc cleared this over-match on
                      2026-09-07 on the grounds that no such collision existed. That clearance
                      was scoped to one repo and has expired.

      BYPASS          a symlink named anything else, pointing AT the real ledger, was ALLOWED.
                      The old predicate read the name it was handed and never resolved it, so
                      renaming the door defeated the guard on an evidence-integrity check.

      BYPASS          `.foreman/Ledger.JSONL` was ALLOWED. macOS is case-insensitive and
                      case-preserving, so that is the same file under a different spelling --
                      FORE-128's finding, live here.

    So this change is not simply a narrowing. It narrows on DIRECTORY (the false positive) and
    widens on CASE and SYMLINK (the two bypasses), and the net is strictly more fail-closed on
    the real ledger than what it replaces. Anchoring on the parent directory rather than
    walking up for a project root is deliberate: `.foreman/` IS the Foreman marker
    component_coupling.find_project_root() looks for, so a file sitting directly inside one is
    a Foreman ledger by construction, with no walk required.

    DELIBERATELY NOT CHANGED, and this is a real trade rather than an oversight: the Bash
    raw-text backstop's LEDGER_PATH_RE stays unanchored, so a command whose visible text names
    a foreign `ledger.jsonl` beside a write operator is still denied. Anchoring that regex to
    `.foreman/` would open a genuine hole -- `cd .foreman && echo x >> ledger.jsonl` carries a
    payload cwd of the project root, not of .foreman/, so neither the anchored regex nor
    extract_bash_write_targets() would resolve it onto the ledger. The backstop is a heuristic
    of last resort whose false positive costs one command the operator can run in a terminal,
    which its own deny message already says; the structured path check is where the observed
    false positive actually came from, and is what this fixes.
    """
    resolved = resolve_write_target(path_str, cwd)
    if resolved is None:
        return False
    return (resolved.name.lower() == LEDGER_NAME
            and resolved.parent.name.lower() == FOREMAN_DIR)


def bash_text_mentions_ledger_write(command):
    return bool(LEDGER_PATH_RE.search(command)) and bool(BASH_WRITE_OPERATOR_RE.search(command))


def load_writer_allowlist():
    """The pinned writer-identity exemption entries, or [] on any read/parse failure --
    fail-closed: an unreadable or corrupt sidecar exempts nothing, it never opens the gate.
    C12: SIDECAR_PATH is the single hardcoded constant above, never searched for."""
    try:
        data = json.loads(SIDECAR_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def _parse_closer_flags(tokens, allowed_flags):
    """tokens[3:] -> {flag: value}, or None if the shape is anything but a clean sequence of
    recognized `--flag value` pairs, each flag at most once. An unrecognized flag, a flag with
    no following value token (or whose value itself looks like a flag), a stray positional
    token, or a repeated flag all fail the match -- this is Layer 1b's grammar check; semantic
    validity of the values themselves (real stage/ticket shape, a real project root) is
    Layer 3's job below, kept separate."""
    if len(tokens) % 2 != 0:
        return None
    flags = {}
    for i in range(0, len(tokens), 2):
        flag, value = tokens[i], tokens[i + 1]
        if flag not in allowed_flags or flag in flags or value.startswith("--"):
            return None
        flags[flag] = value
    return flags


def is_exempt_writer_invocation(command, cwd):
    """True only for a Bash command that is, byte-for-byte, one clean invocation of a pinned,
    hash-verified closer script -- FORE-567, C8-C14. `command` must independently pass all
    three layers (charset+grammar, resolved-path+hash, effective-root+argument sanity) against
    at least one sidecar entry. Any failure at any layer returns False, which is NOT a decision
    on its own: the caller (main()) falls through to the guard's existing, UNCHANGED C1-C5 deny
    logic below. This function only ever widens what is silently allowed relative to that
    existing logic; it never narrows it.
    """
    if not WRITER_CHARSET_RE.fullmatch(command):
        return False  # Layer 1a -- C13, F4, F9

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if len(tokens) < 3:
        return False

    resolved_interpreter = resolve_write_target(tokens[0], cwd)
    resolved_script = resolve_write_target(tokens[1], cwd)
    subcommand = tokens[2]
    if resolved_interpreter is None or resolved_script is None or subcommand not in ("close", "skip"):
        return False

    for entry in load_writer_allowlist():
        if subcommand not in (entry.get("subcommands_allowed") or []):
            continue
        if str(resolved_interpreter) != entry.get("interpreter_pinned_resolved"):
            continue  # Layer 1b interpreter identity, resolved not raw -- C14, F6, F8
        if str(resolved_script) != entry.get("resolved_path"):
            continue  # Layer 1b script identity, resolved not raw -- F5
        try:
            script_hash = hashlib.sha256(resolved_script.read_bytes()).hexdigest()
        except OSError:
            continue
        if script_hash != entry.get("sha256_pinned"):
            continue  # Layer 2 -- C9

        allowed_flags = CLOSE_FLAGS if subcommand == "close" else SKIP_FLAGS
        required_flags = CLOSE_REQUIRED if subcommand == "close" else SKIP_REQUIRED
        flags = _parse_closer_flags(tokens[3:], allowed_flags)
        if flags is None or not required_flags.issubset(flags):
            continue  # Layer 1b grammar

        if not STAGE_ID_RE.fullmatch(flags.get("--stage", "")):
            continue  # Layer 3 -- C10
        if not TICKET_ID_RE.fullmatch(flags.get("--ticket", "")):
            continue  # Layer 3 -- C10

        # Effective root: the --cwd token's value when given, else the payload's own cwd --
        # the omitted-`--cwd` case is the CLI's ordinary shape (`cwd = args.cwd or
        # os.getcwd()`), and it must be validated too, not skipped (fix 5).
        effective_cwd = flags.get("--cwd") or cwd
        resolved_root = resolve_write_target(effective_cwd, cwd) if effective_cwd else None
        if resolved_root is None or cc.find_project_root(str(resolved_root)) is None:
            continue  # Layer 3 -- C10, fix 4: `is not None`, never an equality check

        return True

    return False


def main(data):
    tool_name = data.get("tool_name")
    sid, cwd = data.get("session_id"), data.get("cwd")

    if tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        fp = hc.target_path(data.get("tool_input"))
        if fp and is_ledger_path(fp, cwd):
            hc.audit("SAFETY_DENY", {"guard": "ledger_write_guard", "reason":
                      "direct-write-to-ledger", "file_path": fp}, sid, cwd, severity="high")
            hc.set_rule(f"{RULE_ID}:direct-edit-denied")
            hc.deny(
                f"Blocked direct write to {Path(fp).name}: .foreman/ledger.jsonl is an "
                f"append-only evidence ledger (PDP.md section 6), written only by the stage "
                f"closer, never edited directly. If you're trying to close a stage, run "
                f"`foreman close --stage <id>` (PDP.md section 6) -- if that command doesn't "
                f"exist yet in this repo, that's FORE-351, not a reason to write this file "
                f"by hand."
            )
            return
        return

    if tool_name == "Bash":
        command = (data.get("tool_input") or {}).get("command", "") or ""
        if not command:
            return
        if is_exempt_writer_invocation(command, cwd):
            # FORE-567, C8: a new allow path, audited even though silence is this guard's
            # ordinary convention (C4) -- this specific allow is a narrow carve-out into an
            # otherwise hard-deny gate and should be independently observable, unlike an
            # ordinary unrelated command that never touched the ledger at all.
            hc.audit("WRITER_IDENTITY_EXEMPT", {"guard": "ledger_write_guard", "reason":
                      "closer-invocation-verified", "command": command[:500]}, sid, cwd,
                     severity="info")
            hc.set_rule(f"{RULE_ID}:writer-identity-exempt")
            return
        for target in cc.extract_bash_write_targets(command, cwd):
            if is_ledger_path(str(target), cwd):
                hc.audit("SAFETY_DENY", {"guard": "ledger_write_guard", "reason":
                          "direct-bash-write-to-ledger", "command": command[:500]},
                         sid, cwd, severity="high")
                hc.set_rule(f"{RULE_ID}:bash-write-denied")
                hc.deny(
                    "Blocked Bash write to ledger.jsonl: this append-only evidence ledger "
                    "(PDP.md section 6) is written only by the stage closer, never edited "
                    "directly."
                )
                return
        if bash_text_mentions_ledger_write(command):
            hc.audit("SAFETY_DENY", {"guard": "ledger_write_guard", "reason":
                      "bash-text-match-ledger", "command": command[:500]}, sid, cwd,
                     severity="high")
            hc.set_rule(f"{RULE_ID}:bash-text-match-denied")
            hc.deny(
                "Blocked Bash command: it names ledger.jsonl alongside a write-shaped "
                "operator. This append-only evidence ledger (PDP.md section 6) is written "
                "only by the stage closer. If this command doesn't actually write to that "
                "path, run it yourself in a terminal."
            )
            return


def pin_writer(argv):
    """FORE-567, C11: CLI-only, human-invoked (`ledger_write_guard.py --pin-writer ...`),
    never automatic and never reachable from main()'s normal PreToolUse handling -- this
    function is called ONLY from the `__name__ == "__main__"` branch below, guarded by the
    literal `--pin-writer` flag, the same shape settings_integrity_check.py's own `--pin`
    already establishes for the sibling settings-integrity mechanism. Writes/updates one entry
    in the single-canonical sidecar (SIDECAR_PATH, C12).

    OQ1, stated again at the point it would be easy to forget: re-pinning is CLI-only and
    human-invoked, but nothing here stops the same session that just edited the pinned script
    from also being the one that runs this command afterward. That gap is accepted as a named
    residual per the design-scope freeze (ledger_write_guard/GOALS.json open_questions[0]), not
    solved by this function.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="ledger_write_guard.py --pin-writer")
    ap.add_argument("--id", default="foreman-close-skip")
    ap.add_argument("--interpreter", default="/Users/m5/.venv/bin/python3")
    ap.add_argument("--script", default=str(
        Path(__file__).resolve().parent.parent / "foreman_cli" / "foreman_close_skip.py"))
    ap.add_argument("--subcommands", nargs="+", default=["close", "skip"])
    args = ap.parse_args(argv)

    interpreter_resolved = Path(args.interpreter).expanduser().resolve()
    script_resolved = Path(args.script).expanduser().resolve()
    try:
        script_hash = hashlib.sha256(script_resolved.read_bytes()).hexdigest()
    except OSError as exc:
        print(f"[ledger_write_guard --pin-writer] cannot read script at {script_resolved}: {exc}",
              file=sys.stderr)
        return 1

    sidecar = {"schema": "ledger-writer-allowlist.v1", "entries": []}
    if SIDECAR_PATH.exists():
        try:
            sidecar = json.loads(SIDECAR_PATH.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[ledger_write_guard --pin-writer] existing sidecar unreadable, refusing to "
                  f"overwrite blind ({exc}); fix or remove {SIDECAR_PATH} by hand first.",
                  file=sys.stderr)
            return 1

    entries = [e for e in sidecar.get("entries", []) if e.get("id") != args.id]
    entries.append({
        "id": args.id,
        "interpreter_pinned_resolved": str(interpreter_resolved),
        "resolved_path": str(script_resolved),
        "sha256_pinned": script_hash,
        "pinned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pinned_by": "manual CLI invocation (--pin-writer) -- see the invoking session's own "
                     "record for who and why",
        "subcommands_allowed": list(args.subcommands),
    })
    sidecar["schema"] = "ledger-writer-allowlist.v1"
    sidecar["entries"] = entries
    SIDECAR_PATH.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n")
    print(f"[ledger_write_guard --pin-writer] pinned {args.id!r}: script={script_resolved} "
          f"sha256={script_hash[:16]}... interpreter={interpreter_resolved} "
          f"subcommands={list(args.subcommands)}")
    return 0


if __name__ == "__main__":
    if "--pin-writer" in sys.argv:
        _idx = sys.argv.index("--pin-writer")
        sys.exit(pin_writer(sys.argv[_idx + 1:]))
    hc.run(main)
