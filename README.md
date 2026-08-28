# Foreman

Scaffolding for an autonomous coding agent you can leave running overnight.

The first ask was a development skill: stop a session from rotting its own context, and stop it from shipping code that was not what was asked for. It grew past that. Bollard, Tessera, and ATLAS showed up because the skill alone was not enough. What you have here is more than the original skill. The overnight problem is still the point.

This tree does not have a Foreman `ARCHITECTURE.md`. It started as a skill, not a system, and never grew one for itself. New work in this repo still needs one, at the project root, same as any other Foreman project. The missing file is origin, not a template.

## What is in here

`bollard/` PreToolUse hooks that can deny a write. `skills/foreman/` stage skills, concept through ship-readiness. `agents/` named reviewers the stage skills dispatch. The same files are copied at `.claude/agents/` so a session in this clone can load them, and the install script copies them to `/path/to/home/.claude/agents/`. `tessera/` local tickets; a closed claim is checked against the real git diff. `atlas/` warehouse joining hook verdicts to tickets. `QUALITY-BAR.md` how a claim is scored.

`docs/atlas-architecture.md` is ATLAS, not Foreman. Fourteen hundred lines of measured design. That file is what the process produces. Read it if you want the scale. The rest of this repo is the scaffolding that made it possible, not a diary of how.

GifSmith, wat, and hyphy are separate repos. They are not in this tree.

https://github.com/latent-affect/gifsmith
https://github.com/latent-affect/wat
https://github.com/latent-affect/hyphy

## Install

Humans stay here. Agents follow `README.agents.md`.

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
