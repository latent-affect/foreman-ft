# Foreman

Scaffolding for an autonomous coding agent you can leave running overnight, plus the machinery that checks its work while nobody's watching it do that.

The first ask was small: stop a session from rotting its own context, and stop it from shipping code that wasn't what got asked for. That grew into three real subsystems. Bollard denies a bad write before it lands, not after. Tessera keeps a ticket ledger where a closed claim gets checked against the real git diff, not taken on the session's word. ATLAS joins hook verdicts to tickets across every project on the machine, and its own 1,400-line design document is not a description written after the fact; it's the record of building the thing, including the parts that broke. A contract floor got set above its own achievable ceiling and failed on every run from the first one. A row-count check was off by one on its own first pass. Both are in the document, by name, with the fix.

None of this is theoretical. Sessions have run for hours unattended against these gates and shipped real, working code at the end of it.

## What's in here

`bollard/` is the enforcement layer, PreToolUse hooks that can deny a write outright instead of flagging it after the damage is done. `architecture_gate.py`, `goals_freeze_gate.py`, `preflight_blocking_gate.py`, and `ship_readiness_gate.py` are the four that matter most day to day; `component_coupling.py` is what lets `architecture_gate.py` tell a real component boundary from an unenforced guess.

`tessera/` is the ticket ledger itself. `tessguard` checks a ticket's claimed files-touched against the actual commit before the close counts for anything.

`atlas/` is the cross-project query facade. It reads hook verdicts and the ticket ledger together, across every registered project, and answers questions neither system can answer alone. `docs/atlas-architecture.md` is worth reading on its own if you want to see what "measured against live files, not sketched from memory" looks like at real scale.

`skills/foreman/` is the stage-gate skill set, concept through ship-readiness, each stage gated by the hooks in `bollard/`. `agents/` are the seven named reviewers those skills dispatch. The same files live at `.claude/agents/` so a session working in this clone can load them, and the install script copies them to `/path/to/home/.claude/agents/` too.

`QUALITY-BAR.md` is the real bar, including the gaps.

## The honest number

This project's own PRD names 47 requirements. Re-run live against real ticket evidence: 13 are closed with a real, checkable ticket behind them. The rest sit at every stage between working-but-not-yet-independently-reviewed and blocked on a named precondition, and `docs/PRD.md` says which is which, requirement by requirement, not in aggregate.

That's not a number to apologize for. A system built to enforce rules on other projects has to exist before those rules can be turned on itself, the same way a compiler has to compile something before it can compile itself. Field-trial is the honest label for where that leaves this release. It's also why the repo is called `foreman-ft` and not something that promises more than 13 of 47 backs up. Contributions closing the gap are genuinely wanted here, not just tolerated.

This tree didn't have its own `ARCHITECTURE.md` until 2026-08-27. It started as a skill, not a system, and outgrew that shape before it had documentation to match; the gap is closed now, but it's closed, not erased. New work in this repo still needs one, at the project root, same as any other Foreman project.


## Install

Devs, stay here. Agents follow `README.agents.md`.

From this clone:

```
sh scripts/install-dev-harness.sh
git config core.hooksPath .githooks
```

The script fail-closes if a required hook is missing. Runtime paths ship as `/path/to/...` tokens and get rewritten on install.

New Foreman project, before that project's first session:

```
python3 skills/foreman/scripts/foreman_init.py /path/to/new/project CODENAME PREFIX ["purpose"]
```

Init fail-closes if the hooks are not already on disk. It writes `ARCHITECTURE.md` expectations into the new project; that is where a Foreman architecture belongs.

## Also here

`ACKNOWLEDGEMENTS.md` names the tools that wrote this. `QUALITY-BAR.md` is the bar, including the gaps.
