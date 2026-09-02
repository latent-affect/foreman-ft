#!/usr/bin/env python3
"""foreman-init: deterministic Foreman project bootstrap.

Run this BEFORE starting a Claude Code session in a new project directory. This is the
direct structural fix for a real bug found 2026-08-15 building TESSERA: a project's
.claude/settings.json was created mid-session (by the agent, after the architecture stage
began), and the two hard hooks it registered (architecture_gate.py, goals_freeze_gate.py)
never fired -- zero times, confirmed against ~/.claude/telemetry/verdicts.jsonl -- for the
rest of that session, because project-local hook registration loads at session start, not
dynamically. The fix is structural, not procedural: make the config exist on disk before
`claude` is ever invoked in that directory, by running this script from a plain shell first.

Usage:
    python3 foreman_init.py /path/to/project CODENAME [PREFIX] ["purpose, one paragraph"]

PREFIX is optional: when omitted, it defaults to the first four alphanumeric
characters of the directory name, uppercased -- override by passing it explicitly.
Purpose can only be given once PREFIX is given explicitly, so there's no positional
ambiguity about what a given argument means.

Writes, all idempotent (safe to re-run):
    .foreman/                      -- the opt-in marker every Foreman hook checks for
    .foreman/GOALS.template.json   -- a local copy of config/GOALS.template.json, so
                                       foreman:design-and-scope never has to search for it
    .claude/settings.json          -- from config/settings.json.template, ONE canonical
                                       source (this file), not hand-copied per project
    CLAUDE.md                      -- from config/CLAUDE.md.template, codename/prefix/
                                       purpose substituted; only if it doesn't already exist,
                                       since an existing project's own CLAUDE.md is real
                                       content, never silently overwritten
    A TESSERA project registration -- same codename/prefix as above, source_root
                                       = this directory. Means the project never needs
                                       tessera_resolver's .foreman/tessera-prefix override
                                       to resolve unambiguously; that override stays
                                       reserved for pre-existing ambiguous cases.

Then self-verifies -- does NOT declare the project ready on config existing alone:
  1. settings.json parses as JSON and has the exact PreToolUse/Edit|Write|Bash shape.
  2. Every hook command it references resolves to a real file on disk.
  3. Both hook scripts, invoked directly with synthetic PreToolUse payloads (not through a
     live Claude Code session -- this script isn't one), produce the exact verdicts the
     bootstrap scenario requires: architecture_gate.py denies a Write to a nested path with
     no ARCHITECTURE.md yet; goals_freeze_gate.py stays silent on that same pre-architecture
     write (a written contract -- see "What this script's self-verification does NOT
     prove", below).
  4. architecture_gate.py also denies the same write attempted via Bash (`printf ... >
     path`) -- a regression check: a hook matching only Edit|Write never even sees
     the tool call an agent falls back to after being denied once.
  4b. preflight_blocking_gate.py, now wired into the template alongside the other
      two, stays silent against the freshly-registered project -- proves both that TESSERA
      registration actually took AND that a project with zero tickets isn't blocked by its
      own bootstrap.
  5. A REAL, independent `claude -p` session (not the bare hook script via
     subprocess) is spawned with cwd=project_root, and its own `permission_denials` field
     -- the harness's account of what it blocked, not a model's self-report -- confirms the
     probe Write was actually denied. Checks 1-4 above prove the hook SCRIPT decides
     correctly given a payload; this is the only check that proves the HARNESS actually
     loads and enforces this config for a real session, which is what the original bug
     (mid-session settings.json creation never taking effect) was about in the first place.

What this does NOT do, stated plainly: write a real, frozen, per-component
GOALS.json. That can't be pre-populated before a component's actual design exists -- it's
foreman:design-and-scope's job, after ARCHITECTURE.md exists and is reviewed. What this
script provides is .foreman/GOALS.template.json, ready for that step when it happens.

Exit code 0 only if every check passes. Non-zero and a clear reason otherwise.
"""
import json
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_TEMPLATE = SKILL_ROOT / "config" / "settings.json.template"
CLAUDE_MD_TEMPLATE = SKILL_ROOT / "config" / "CLAUDE.md.template"
GOALS_TEMPLATE_SOURCE = SKILL_ROOT / "config" / "GOALS.template.json"
HOOKS_DIR = Path("/path/to/home/.claude/hooks")

sys.path.insert(0, str(HOOKS_DIR / "tessera_resolver"))
import tessera_resolver as tr  # noqa: E402


class InitError(Exception):
    pass


def write_scaffold(project_root, codename, prefix, purpose):
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".foreman").mkdir(exist_ok=True)
    (project_root / ".claude").mkdir(exist_ok=True)

    if not GOALS_TEMPLATE_SOURCE.is_file():
        raise InitError(
            f"expected GOALS template at {GOALS_TEMPLATE_SOURCE} -- confirmed exact path "
            f"per the corrected finding in the TESSERA build report; if this no longer "
            f"exists there, this script needs updating, not a fallback search."
        )
    (project_root / ".foreman" / "GOALS.template.json").write_text(
        GOALS_TEMPLATE_SOURCE.read_text()
    )

    settings_content = SETTINGS_TEMPLATE.read_text()
    (project_root / ".claude" / "settings.json").write_text(settings_content)

    claude_md_path = project_root / "CLAUDE.md"
    template = CLAUDE_MD_TEMPLATE.read_text()
    rendered = (
        template.replace("{{CODENAME}}", codename)
        .replace("{{PREFIX}}", prefix)
        .replace("{{PURPOSE}}", purpose or f"{codename} — purpose not yet filled in.")
    )
    if not claude_md_path.exists():
        claude_md_path.write_text(rendered)
    else:
        existing = claude_md_path.read_text()
        if "Foreman is active here" in existing:
            print(f"  CLAUDE.md already exists at {claude_md_path} and already has the Foreman block, left untouched")
        else:
            claude_md_path.write_text(existing.rstrip() + "\n\n" + rendered)
            print(f"  CLAUDE.md already existed at {claude_md_path}; appended Foreman block (never overwrites, never copies ~/.claude/CLAUDE.md)")


def register_tessera_project(project_root, codename, prefix):
    """Register the new project in TESSERA using the same codename/prefix already
    given as CLI args -- no second input, no second naming decision. Idempotent by checking
    list-projects first, same discipline as write_scaffold's own idempotency.

    Registering here, not leaving it to whoever writes the ticket, means every NEW project
    gets a unique prefix against its own real source_root from the start -- it never needs
    tessera_resolver's .foreman/tessera-prefix override to resolve unambiguously.
    That override stays reserved for pre-existing ambiguous cases (agent-remediation), not
    something every new project has to carry.
    """
    ok, data, err = tr.run_cli(["list-projects"])
    if not ok:
        raise InitError(f"could not reach TESSERA to register this project ({err}) -- "
                         f"is data/tessera.db present at {tr.DB_PATH}?")
    root_str = str(project_root.resolve())
    projects = data.get("projects", [])

    existing_for_root = [p for p in projects if p.get("source_root") == root_str]
    if existing_for_root:
        return existing_for_root[0]["prefix"]  # already registered, nothing to do

    prefix_taken_by_other = [p for p in projects
                              if p["prefix"] == prefix and p.get("source_root") != root_str]
    if prefix_taken_by_other:
        raise InitError(
            f"TESSERA prefix {prefix!r} is already registered for a different project "
            f"({prefix_taken_by_other[0]['source_root']!r}) -- choose a different PREFIX."
        )

    ok, out, err = tr.run_cli([
        "register-project", "--new-codename", codename, "--new-prefix", prefix,
        "--source-root", root_str,
    ])
    if not ok:
        raise InitError(f"TESSERA register-project failed: {err}")
    return prefix


def verify_settings_shape(project_root):
    path = project_root / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise InitError(f"{path} is not valid JSON: {exc}")

    pre_tool_use = data.get("hooks", {}).get("PreToolUse", [])
    if not pre_tool_use:
        raise InitError(f"{path} has no hooks.PreToolUse entries")

    # Bash-coverage invariant, checked independently of command collection below (DEVH-29): some
    # entry's matcher must name Bash as one of its pipe-separated alternatives, or a denied
    # Edit/Write is trivially routed around with a shell redirect, tee, cp/mv, or sed -i -- the
    # reason this function already gave for requiring it. Splitting the matcher string on "|"
    # and comparing tokens exactly, not a substring or prefix test on the raw matcher text: R24's
    # guard_destructive.py is registered under the exact matcher "Bash", and the original
    # six-gate entry stays "Edit|Write|Bash" -- both must satisfy this, and a substring test
    # would also silently accept an unrelated future matcher like "BashDisabled".
    found_bash_covering_matcher = any(
        "Bash" in (entry.get("matcher") or "").split("|") for entry in pre_tool_use
    )
    if not found_bash_covering_matcher:
        raise InitError(f"{path} has no PreToolUse entry whose matcher covers Bash")

    # Command collection (DEVH-29 fix): every entry's commands are validated unconditionally,
    # not gathered by matching on matcher text at all. The prior version only ever collected
    # commands from the entry whose matcher was EXACTLY "Edit|Write|Bash" -- invisible to every
    # other entry, including R24's three guards registered under "Bash", "Edit|Write" and the
    # outbound-fetch surface. A broken or missing guard passed this check cleanly before this
    # fix; nothing about that class of bug is caught by relaxing the old exact match to a
    # substring test instead, which is why this collects from every entry rather than doing that.
    commands = []
    for entry in pre_tool_use:
        for h in entry.get("hooks", []):
            commands.append(h.get("command", ""))

    missing = []
    for cmd in commands:
        # commands are "python3 /abs/path/to/hook.py"
        parts = cmd.split()
        script_path = Path(parts[-1]) if parts else None
        if not script_path or not script_path.is_file():
            missing.append(cmd)
    if missing:
        raise InitError(f"{path} references hook command(s) that don't resolve to a real file: {missing}")

    return commands


def run_hook(hook_path, payload):
    proc = subprocess.run(
        ["python3", str(hook_path)],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )
    stdout = proc.stdout.strip()
    if not stdout:
        return None  # silent -- hook ran, decided nothing (pass-through)
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise InitError(f"{hook_path} produced non-JSON stdout: {stdout!r}")


def verify_hooks_live_mid_build(project_root):
    """verify_hooks_live()'s ORIGINAL probe assumes a brand-new, pre-architecture
    project -- expects architecture_gate.py to DENY. That assumption breaks for a project
    foreman-init is run against AFTER it already has a real, reviewed ARCHITECTURE.md (a
    playground/worktree copy of an in-progress project, or a re-run of this script against
    an existing one) -- found live, running this against a real pre-existing project,
    which already has 6 real declared components. architecture_gate.py correctly returning
    "gate-open" there isn't a failure, it's the correct verdict for that real state; the
    original check would have raised InitError on exactly the right behavior.

    This verifies the SAME thing (the harness enforces what the current real project state
    says it should), but against goals_freeze_gate.py and the real component map instead,
    since architecture_gate.py has nothing left to prove once ARCHITECTURE.md is real and
    reviewed. Picks the first real declared component, checks whether ITS GOALS.json is
    actually frozen on disk, and asserts the gate's verdict matches that real state --
    not a hardcoded expectation, a live comparison against the ground truth.
    """
    import sys as _sys
    _sys.path.insert(0, str(HOOKS_DIR))
    import component_coupling as cc  # noqa: E402

    arch_hook = HOOKS_DIR / "architecture_gate.py"
    goals_hook = HOOKS_DIR / "goals_freeze_gate.py"
    probe_path = str(project_root / "src" / "probe_module" / "example.py")

    arch_payload = {"tool_name": "Write", "tool_input": {"file_path": probe_path},
                     "cwd": str(project_root)}
    arch_verdict = run_hook(arch_hook, arch_payload)
    arch_decision = (arch_verdict or {}).get("hookSpecificOutput", {}).get("permissionDecision")

    # Found live (2026-08-16), rolling Foreman out to Chrome Investigation: ARCHITECTURE.md
    # existing on disk does NOT mean "gate-open is the only correct verdict" -- architecture_
    # gate.py has a second real state, ARCHITECTURE.md present but ARCHITECTURE-REVIEW.md
    # absent/empty, where DENY is still correct (same rule the fresh-bootstrap check enforces,
    # different reason). The original version of this function only knew about "no doc yet"
    # vs "doc + reviewed", so it raised InitError on a gate that was actually right.
    review_path = project_root / "ARCHITECTURE-REVIEW.md"
    review_nonempty = review_path.is_file() and review_path.stat().st_size > 0
    if not review_nonempty:
        if arch_decision != "deny":
            raise InitError(
                f"architecture_gate.py should deny -- ARCHITECTURE.md exists at "
                f"{project_root} but ARCHITECTURE-REVIEW.md doesn't (or is empty) -- got "
                f"{arch_verdict!r} instead"
            )
        return {"architecture_gate": "deny (correct, ARCHITECTURE.md exists but not yet reviewed)",
                "goals_freeze_gate": "not checked -- architecture still gates every write",
                "real_session_probe_path": probe_path,
                "real_session_expect_denied": True}

    if arch_decision == "deny":
        raise InitError(
            f"architecture_gate.py denied a Write even though ARCHITECTURE.md exists AND "
            f"is reviewed at {project_root} -- real architecture state and the gate's "
            f"verdict disagree, got {arch_verdict!r}"
        )

    component_map = cc.parse_component_map(project_root)
    if not component_map:
        print("  (mid-build project, but ARCHITECTURE.md declares no components -- "
              "goals_freeze_gate.py has nothing to check against yet, skipping that half)")
        return {"architecture_gate": "gate-open (correct, architecture already exists)",
                "goals_freeze_gate": "not checked -- no declared components",
                "real_session_probe_path": probe_path,
                "real_session_expect_denied": False}

    component_name = next(iter(component_map))
    comp_root = cc.component_root(component_name, component_map)
    goals_path = project_root / comp_root / "GOALS.json"
    really_frozen = False
    if goals_path.is_file():
        try:
            goals_data = json.loads(goals_path.read_text())
            really_frozen = bool(goals_data.get("criteria_frozen_at")) and bool(
                (goals_data.get("integrity") or {}).get("criteria_hash_at_freeze")
            )
        except json.JSONDecodeError:
            really_frozen = False  # unreadable GOALS.json fails toward "not frozen" (deny expected)

    # Found live: a leading-underscore filename here made a real `claude -p`
    # session correctly substitute a DIFFERENT filename per the operator's own global
    # CLAUDE.md rule 1 ("never prefix a filename with underscore") -- the gate denied
    # the (renamed) write correctly, but verify_real_session's exact-path match against
    # the ORIGINAL name then falsely reported "not denied". Self-inflicted: this
    # script's own generated paths have to follow the same global naming rule every
    # other write in this codebase already does.
    comp_probe_path = str(project_root / comp_root / "foremanInitProbeTemp.py")
    goals_payload = {"tool_name": "Write", "tool_input": {"file_path": comp_probe_path},
                      "cwd": str(project_root)}
    goals_verdict = run_hook(goals_hook, goals_payload)
    goals_decision = (goals_verdict or {}).get("hookSpecificOutput", {}).get("permissionDecision")
    denied = goals_decision == "deny"

    if denied == really_frozen:
        raise InitError(
            f"goals_freeze_gate.py's verdict disagrees with the real state of "
            f"{goals_path}: criteria_frozen_at present={really_frozen}, but the gate "
            f"{'denied' if denied else 'allowed'} a write into component "
            f"{component_name!r} (got {goals_verdict!r})"
        )

    # Same Bash-coverage proof established for architecture_gate, now confirmed for
    # goals_freeze_gate against a REAL component too, not just a synthetic path.
    bash_payload = {"tool_name": "Bash",
                     "tool_input": {"command": f'printf \'x = 1\\n\' > "{comp_probe_path}"'},
                     "cwd": str(project_root)}
    bash_verdict = run_hook(goals_hook, bash_payload)
    bash_decision = (bash_verdict or {}).get("hookSpecificOutput", {}).get("permissionDecision")
    bash_denied = bash_decision == "deny"
    if bash_denied != denied:
        raise InitError(
            f"goals_freeze_gate.py's Write and Bash branches disagree for the same "
            f"component ({component_name!r}): Write verdict deny={denied}, Bash verdict "
            f"deny={bash_denied} -- the Bash-bypass class of bug reproduced on "
            f"the second hard gate."
        )

    preflight_hook = HOOKS_DIR / "preflight_blocking_gate.py"
    preflight_verdict = run_hook(preflight_hook, arch_payload)
    if preflight_verdict is not None:
        preflight_decision = (preflight_verdict or {}).get("hookSpecificOutput", {}).get("permissionDecision")
        # A mid-build project can legitimately have real open blocking tickets right now
        # (that's the gate doing its job) -- only fail this check on something OTHER than
        # deny/ask, which would mean the gate itself is broken, not that the queue is real.
        if preflight_decision not in ("deny", "ask"):
            raise InitError(
                f"preflight_blocking_gate.py produced an unexpected verdict "
                f"{preflight_verdict!r} -- neither silent, ask, nor deny"
            )
        preflight_status = f"{preflight_decision} (real: check the named ticket(s))"
    else:
        preflight_status = "silent (no open blocking tickets right now)"

    return {
        "architecture_gate": "gate-open (correct, architecture already exists)",
        "goals_freeze_gate": f"{'deny' if denied else 'gate-open'} for component "
                              f"{component_name!r} (matches real frozen={really_frozen} state, "
                              f"Write and Bash branches agree)",
        "preflight_blocking_gate": preflight_status,
        "real_session_probe_path": comp_probe_path,
        "real_session_expect_denied": denied,
    }


def verify_hooks_live(project_root):
    """The actual self-verification the brief requires: prove the hooks fire and decide
    correctly, using the SAME check that originally caught the mid-session-creation bug --
    real invocation against the real script, not trust that the settings file exists.

    What this proves: architecture_gate.py + goals_freeze_gate.py, invoked directly, apply
    correct logic against THIS project's fresh .foreman/ marker.
    What this does NOT prove: that Claude Code's own session harness will actually load
    THIS settings.json at the start of a session run against this directory -- that
    requires a real session, which this script (a plain Python process) cannot start. See
    the report section "what self-verification can and can't prove" for the real subagent
    test that closes that gap.

    This whole function assumes a brand-new, PRE-architecture project (expects
    architecture_gate.py to deny). If ARCHITECTURE.md already exists -- a playground/
    worktree copy of an in-progress project, or a re-run against an existing one -- that
    assumption is simply wrong, and verify_hooks_live_mid_build() runs instead.
    """
    if (project_root / "ARCHITECTURE.md").is_file():
        return verify_hooks_live_mid_build(project_root)

    probe_path = str(project_root / "src" / "probe_module" / "example.py")
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": probe_path},
        "cwd": str(project_root),
    }

    arch_hook = HOOKS_DIR / "architecture_gate.py"
    goals_hook = HOOKS_DIR / "goals_freeze_gate.py"

    arch_verdict = run_hook(arch_hook, payload)
    arch_decision = (arch_verdict or {}).get("hookSpecificOutput", {}).get("permissionDecision")
    if arch_decision != "deny":
        raise InitError(
            f"architecture_gate.py did not deny a pre-architecture Write as expected "
            f"(got {arch_verdict!r}) -- the hook is not behaving correctly against this "
            f"project's fresh state, config existing on disk is not enough"
        )

    goals_verdict = run_hook(goals_hook, payload)
    if goals_verdict is not None:
        raise InitError(
            f"goals_freeze_gate.py should stay silent on a pre-architecture path (nothing "
            f"to freeze-check yet), got {goals_verdict!r} instead"
        )

    # Regression check: the exact bypass that was found live -- a denied Write routed
    # around via `printf ... > path`, zero Foreman hook involvement, file lands anyway. Proves
    # the Bash branch of architecture_gate.py, not just the Edit|Write branch already checked
    # above.
    bash_payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f'printf \'x = 1\\n\' > "{probe_path}"'},
        "cwd": str(project_root),
    }
    bash_verdict = run_hook(arch_hook, bash_payload)
    bash_decision = (bash_verdict or {}).get("hookSpecificOutput", {}).get("permissionDecision")
    if bash_decision != "deny":
        raise InitError(
            f"architecture_gate.py did not deny a Bash redirect into a pre-architecture path "
            f"(got {bash_verdict!r}) -- this is the exact known bypass class; a hook that only "
            f"catches Edit/Write is not enough"
        )

    # preflight_blocking_gate.py is now wired into the template alongside the other
    # two -- a genuinely fresh, freshly-registered-in-TESSERA project has zero tickets, so
    # it must stay silent. If it doesn't, either registration didn't actually take (checked
    # separately, above, but this is the live behavioral proof) or the gate itself is wrong.
    preflight_hook = HOOKS_DIR / "preflight_blocking_gate.py"
    preflight_verdict = run_hook(preflight_hook, payload)
    if preflight_verdict is not None:
        raise InitError(
            f"preflight_blocking_gate.py should stay silent for a fresh project with zero "
            f"tickets, got {preflight_verdict!r} instead"
        )

    return {"architecture_gate": "deny (correct, no ARCHITECTURE.md yet)",
            "goals_freeze_gate": "silent (correct)",
            "architecture_gate_bash_bypass_check": "deny (correct)",
            "preflight_blocking_gate": "silent (correct)",
            "real_session_probe_path": probe_path,
            "real_session_expect_denied": True}


def verify_real_session(project_root, probe_path, expect_denied=True):
    """Closes the gap named in this module's own docstring and in verify_hooks_live's
    own "what this does NOT prove" note. run_hook() proves the hook SCRIPT decides correctly
    given a payload -- it never starts a real Claude Code session, so it proves nothing about
    whether the harness actually LOADS a settings.json written moments ago. That's exactly the
    original bug (TESSERA's own build: a project's settings.json created mid-session never
    fired for the rest of that session, confirmed against the verdict ledger, not assumed).

    This spawns a genuinely independent `claude -p` process, cwd=project_root, and inspects
    its OWN permission_denials field -- the harness's own account of what it blocked, not a
    self-report from the model that ran inside that session. A model can be persuaded to lie
    about what happened; permission_denials is emitted by the harness itself.

    SCOPE, tested and disclosed rather than assumed: this proves a FRESH `-p`
    process, started after config exists, enforces it -- print-mode invocations are new OS
    processes each time (even `--resume` on the same session id), so they always re-read
    current disk state. Directly tested against the mid-session scenario itself: started a
    `-p` session in an empty directory, ran this script to write config into it AFTER that
    session existed, then `--resume`d the SAME session id and attempted a gated Write -- it
    was correctly denied. So `-p`/`--resume` sessions do not reproduce the original bug at
    all. What this check still CANNOT prove, and no automated check run from outside can: a
    long-running INTERACTIVE session that loaded its config once at actual process launch
    and stays alive for hours while files on disk change underneath it. There is no way to
    probe an already-running interactive process's internal config state without disrupting
    it, so that half of the original bug's fix remains procedural (run this script before
    ANY session -- interactive or not -- starts in the directory), not something this
    check, or any check like it, can verify after the fact.
    """
    prompt = (
        f"Use the Write tool to create a file at {probe_path} with content: x = 1. "
        f"Report the raw tool result verbatim."
    )
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--output-format", "json"],
            cwd=str(project_root), capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise InitError(f"could not spawn a real claude -p session to verify against: {exc!r}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise InitError(
            f"real-session verification produced non-JSON output ({exc}): {proc.stdout[:500]!r}"
        )

    denials = result.get("permission_denials") or []
    write_denied = any(
        d.get("tool_name") == "Write"
        and (d.get("tool_input") or {}).get("file_path") == probe_path
        for d in denials
    )
    # expect_denied=False for a mid-build project where the real, current state
    # (already-frozen GOALS.json, or no declared component at that path) means the write
    # SHOULD go through -- verify_hooks_live_mid_build() already determined which case
    # applies from the real component map, this isn't a second, independent guess.
    if expect_denied and not write_denied:
        raise InitError(
            f"a REAL Claude Code session at {project_root} did not deny the probe Write "
            f"(permission_denials={denials!r}) -- config exists on disk and the bare hook "
            f"script decides correctly in isolation, but the harness itself is not enforcing "
            f"it for an actual session. This is the exact class of bug this script exists to "
            f"catch; config-exists is not enough."
        )
    if not expect_denied and write_denied:
        raise InitError(
            f"a REAL Claude Code session at {project_root} denied the probe Write, but the "
            f"real project state (checked directly against the component's actual "
            f"GOALS.json) says this exact write should have been allowed -- the harness's "
            f"live enforcement disagrees with the bare hook script's own verdict."
        )
    if expect_denied and Path(probe_path).exists():
        raise InitError(
            f"the real session's permission_denials reported the Write as denied, but "
            f"{probe_path} exists on disk anyway -- the known bypass class, reproduced by "
            f"this exact check."
        )
    return {"real_session_write_denied": write_denied, "probe_path": probe_path}


def derive_default_prefix(project_root):
    """Per the operator's direct call: default TESSERA prefix is the first four
    alphanumeric characters of the repo directory name, uppercased -- overridable by passing
    PREFIX explicitly. Non-alphanumeric characters (dashes, underscores) are stripped before
    taking the first four, so "ticket-system" -> "TICK", not "TICK" vs "TICK-" ambiguity.
    A name shorter than 4 alphanumeric characters uses whatever it has, not padded or errored.
    """
    alnum = "".join(ch for ch in project_root.name if ch.isalnum())
    if not alnum:
        raise InitError(
            f"cannot derive a default TESSERA prefix from directory name {project_root.name!r} "
            f"(no alphanumeric characters) -- pass PREFIX explicitly."
        )
    return alnum[:4].upper()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)

    project_root = Path(sys.argv[1]).resolve()
    codename = sys.argv[2]
    # PREFIX is positional-optional: 3 args -> derive it; 4+ args -> sys.argv[3] is PREFIX
    # explicitly. Purpose only appears once PREFIX is given explicitly (5th arg), so there's
    # no ambiguity about what a given argv position means -- no guessing which one was meant.
    if len(sys.argv) == 3:
        prefix = derive_default_prefix(project_root)
        prefix_source = "derived from directory name"
        purpose = ""
    else:
        prefix = sys.argv[3]
        prefix_source = "explicit"
        purpose = sys.argv[4] if len(sys.argv) > 4 else ""

    print(f"foreman-init: {project_root} (codename={codename}, prefix={prefix} [{prefix_source}])")

    try:
        write_scaffold(project_root, codename, prefix, purpose)
        print("  scaffold written: .foreman/, .claude/settings.json, CLAUDE.md, .foreman/GOALS.template.json")

        registered_prefix = register_tessera_project(project_root, codename, prefix)
        print(f"  TESSERA project registered: prefix={registered_prefix} "
              f"(idempotent -- no-op if already registered)")

        commands = verify_settings_shape(project_root)
        print(f"  settings.json shape OK -- {len(commands)} hook command(s) resolve to real files")

        verdicts = verify_hooks_live(project_root)
        # real_session_probe_path/expect_denied are bookkeeping for the next
        # step, not part of the printed summary -- pop them out first. verdicts' shape
        # now depends on which path ran (fresh-bootstrap vs mid-build); print what was
        # ACTUALLY verified rather than a fixed string that would misreport a mid-build
        # project's real (and correct) gate-open verdicts as if they were fresh-project
        # deny verdicts.
        real_session_probe_path = verdicts.pop("real_session_probe_path")
        real_session_expect_denied = verdicts.pop("real_session_expect_denied")
        print("  hooks verified live: " + ", ".join(f"{k} -> {v}" for k, v in verdicts.items()))

        verify_real_session(project_root, real_session_probe_path,
                             expect_denied=real_session_expect_denied)
        if real_session_expect_denied:
            print(f"  REAL claude -p session confirmed the harness enforces this config "
                  f"(not just the bare hook script) -- probe Write denied, file not created")
        else:
            print(f"  REAL claude -p session confirmed the harness's live enforcement "
                  f"matches the real project state -- probe Write correctly allowed, "
                  f"not incorrectly blocked")
            # An ALLOWED probe write is a REAL file the real session actually
            # created inside the project -- clean it up, this was a self-verification
            # probe, not real project content.
            probe_file = Path(real_session_probe_path)
            if probe_file.exists():
                probe_file.unlink()
                print(f"  probe file cleaned up: {probe_file}")

    except InitError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        print("Project is NOT ready. Config was written but did not verify -- fix the "
              "cause above before starting a Claude Code session here.", file=sys.stderr)
        sys.exit(1)

    print(f"READY: {project_root} — config is on disk and verified live, before any "
          f"session starts.")
    sys.exit(0)


if __name__ == "__main__":
    main()
