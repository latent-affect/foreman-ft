# Agent install

`README.md` is orientation. This file is the install playbook for Claude Code, Grok Build, or anything else attaching this clone.

Do not copy `~/.claude/CLAUDE.md`. That file is operator-private.

## Install

From this clone:

```
sh scripts/install-dev-harness.sh
git config core.hooksPath .githooks
```

The script fail-closes if a required hook or `GOALS.template.json` is missing. It never partial-installs. It never copies `~/.claude/CLAUDE.md`.

Source tokens are `/path/to/dev-harness`, `/path/to/ticket-system`, `/path/to/home`, `/path/to/venv`. The script rewrites those in the copied hooks and skills. It does not rewrite this clone's source tree.

New Foreman project, before that project's first session:

```
python3 skills/foreman/scripts/foreman_init.py /path/to/new/project CODENAME PREFIX ["purpose"]
```

`foreman_init.py` reads `skills/foreman/config/settings.json.template`, which points hook commands at `~/.claude/hooks/...`. Those files must already exist. Init fail-closes if they do not.

A Foreman project needs `ARCHITECTURE.md` at its own root. This clone does not ship one for itself.

## Installed means

1. Bollard PreToolUse hooks are files under `~/.claude/hooks/`.
2. Foreman, code-safety, and research-lifecycle skills are under `~/.claude/skills/`.
3. Named reviewers: source `agents/`, project copy `.claude/agents/`, install dest `/path/to/home/.claude/agents/`. Same seven files. Copy, not a single location.
4. This clone has `git config core.hooksPath .githooks` so tessguard `commit-msg` runs.
5. `PYTHONPATH` includes this clone so `python3 -m tessera.api.cli` and `atlas.*` import.

## Environment

| Variable | Purpose |
|---|---|
| `HOOKS_DST` | Install dest for Bollard (default `$HOME/.claude/hooks`) |
| `SKILLS_DST` | Install dest for skills (default `$HOME/.claude/skills`) |
| `AGENTS_DST` | Install dest for named reviewers (default `$HOME/.claude/agents`) |
| `PYTHONPATH` | This clone root, so `tessera` and `atlas` import |
| `TESSERA_DB` / tessera `--db` | Path to `tessera.db` |
| `TESSERA_CWD` | TESSERA checkout the CLI runs from |
| `TESSERA_ROOT` | Install-time replacement for `/path/to/ticket-system` (default `$HOME/dev/ticket-system`) |
| `TESSGUARD_DB_PATH` | Same db, used by gitgate |
| `ATLAS_REPO` | Defaults to this clone (dashboard + MCP) |
| `TESSERA_ENABLE_SQL` | Must be `1` to enable HTTP `POST /sql` (off by default) |

Actor on every TESSERA write: the harness name, for example `grok-4.6` or the Claude session identity. Never a personal name.

## Verify

```
test -f ~/.claude/hooks/architecture_gate.py
test -x .githooks/commit-msg
PYTHONPATH=. python3 -c "from tessera.store.schema import init_schema; import sqlite3; c=sqlite3.connect(':memory:'); init_schema(c); print(c.execute(\"SELECT sql FROM sqlite_master WHERE name='v_flat'\").fetchone()[0][:40])"
git config --get core.hooksPath
```

The last line must print `.githooks` in this clone.
