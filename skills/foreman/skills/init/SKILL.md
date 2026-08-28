---
name: init
description: Explains and points to foreman-init.sh, the deterministic pre-session bootstrap for a new Foreman project. Use when starting a brand-new Foreman-managed project, or when a project's hard hooks seem inert despite a correct-looking .claude/settings.json. Cannot itself fix a project already mid-session — the fix has to run before the session that will build in that directory starts.
---

# foreman:init

Foreman's two hard hooks are registered project-locally
(`<project>/.claude/settings.json`), and that registration is loaded when a Claude Code
session **starts**, not dynamically mid-session. A real build (TESSERA, 2026-08-15)
confirmed this the hard way: `.claude/settings.json` was created *during* the session that
needed it, and `architecture_gate.py`/`goals_freeze_gate.py` fired **zero** times for the
rest of that session — confirmed empirically against `~/.claude/telemetry/verdicts.jsonl`,
not assumed from the config existing on disk. The discipline was followed manually for that
entire build, so nothing was actually bypassed, but the hooks themselves were inert the
whole time.

**This skill cannot fix that for the session it's invoked from.** A skill runs inside an
already-started session; by the time `/foreman:init` could run, that session's hook
registration has already been decided. What this skill *can* do is tell you to run the real
fix — a plain script, from a plain shell, before `claude` is ever invoked in the new
project's directory:

```
/path/to/home/.claude/skills/foreman/scripts/foreman-init.sh /path/to/new/project CODENAME PREFIX ["one-paragraph purpose"]
```

This is not optional ceremony — the script's own self-verification actually invokes both
hook scripts with a synthetic pre-architecture `Write` payload and confirms
`architecture_gate.py` denies it and `goals_freeze_gate.py` stays silent, before printing
`READY`. If it doesn't print `READY`, the project's hooks are not live and starting a
session there will repeat the exact failure that motivated this script.

## What it writes

- `.foreman/` — the marker every Foreman hook checks for before applying at all.
- `.foreman/GOALS.template.json` — copied from this skill's `config/GOALS.template.json`
  (vendored in the launch tree; never from a home-directory path).
- `.claude/settings.json` — from this skill's own `config/settings.json.template`, one
  canonical source, not hand-copied between projects. Registers architecture, concept,
  goals-freeze, preflight, dependency-provenance, and ship-readiness. (Does not yet
  register a tier-triage or freeze-reentry hook — those are designed in
  `foreman-design.html` §04/§05 but not yet built.)
- `CLAUDE.md` — from `config/CLAUDE.md.template`, codename/prefix/purpose substituted.
  Never overwrites an existing `CLAUDE.md` — that's real content, not scaffold.

## What it proves now (was a real gap before)

Checks 1-4 of the self-verification run the hook scripts directly as subprocesses — real
proof the *hook logic* is correct against a fresh project, but no proof the harness's
session-start hook-loading behavior actually picks up the file just written. That second
half used to require a manual, one-off check (spawning a fresh subagent by hand, as the
TESSERA build's Phase-1 report first did). Check 5 now runs it automatically, every time:
a real, independent `claude -p` session is spawned with `cwd=project_root`, and its own
`permission_denials` field (the harness's own account, not a self-report) confirms a probe
`Write` was actually denied. `foreman_init.py READY` now means the harness was proven to
enforce this config for a real session, not just that the config exists and the bare script
agrees with itself.

**Scope, tested directly, not assumed:** `-p`/`--resume` invocations are fresh OS
processes each time, so they always re-read current disk state -- directly confirmed by
resuming a session that started in an EMPTY directory, writing config into it afterward, then
attempting a gated Write in that same resumed session: correctly denied. So check 5 cannot,
and does not claim to, catch a long-running INTERACTIVE session that loaded its config once
at launch and stays alive while disk state changes underneath it -- no automated check can
probe an already-running process's internal state without disrupting it. That half of the
fix stays procedural: run this script before ANY session, interactive included, starts here.

## Trigger when

- Starting a brand-new Foreman-managed project, before the first `claude` session in it.
- A project's `.claude/settings.json` looks correct but the hard hooks never seem to fire —
  check `~/.claude/telemetry/verdicts.jsonl` for `FOREMAN-ARCH-GATE`/
  `FOREMAN-GOALS-FREEZE-GATE` entries with that project's `cwd` before assuming it's fine;
  zero entries despite real edits is the same signature the original bug had.
