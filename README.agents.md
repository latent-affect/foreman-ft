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
4. `git config core.hooksPath .githooks` has been run for this clone, so tessguard `commit-msg`/`pre-commit`/`pre-push` run -- run the `Verify` section below yourself rather than trusting this line. DEVH-52 (2026-09-02): this exact claim was stated here as already-true fact while dev-harness's real hooks had never actually been wired, and every push in its real history went out with zero gate enforcement as a result. `scripts/install-dev-harness.sh` currently only *prints* the command as an instruction (line ~142); it does not run it (DEVH-55, open).
5. `PYTHONPATH` includes this clone so `python3 -m tessera.api.cli` and `atlas.*` import.

## Git-hooks gate: what it enforces, and what it doesn't (DEVH-52/DEVH-54)

`.githooks/{pre-commit,pre-push,commit-msg}` are thin wrappers around
`tessera.tessguard.gitgate` -- read that module's own docstring for what it actually checks
(recent TESSERA ticket activity, and that a commit message names a real ticket). Three
prerequisites, not one, have to hold before it enforces anything:

1. **`core.hooksPath` must be set** (see point 4 above). `git config --get core.hooksPath` is
   shared across every worktree of one repository -- setting it once from any worktree wires
   all of them, confirmed empirically 2026-09-02 across `dev-harness`, `dev-harness-run2`, and
   `dev-harness-qa` sharing one `.git/config`.
2. **`TESSGUARD_DB_PATH` must be exported in the operator's shell.** Without it, every hook
   invocation fails closed immediately on `no TESSERA db at /path/to/ticket-system/data/tessera.db`
   -- that path is a literal, unresolved template placeholder
   (`tessera/tessguard/config.py`'s `DEFAULT_DB_PATH_INTERNAL`), not a real fallback. Nothing
   in `scripts/install-dev-harness.sh` sets this automatically.
3. **The repo path you're committing from must match a registered TESSERA project's
   `source_root` exactly.** `gitgate.py` resolves the repo root via `git rev-parse
   --show-toplevel`, which for a git *worktree* is that worktree's own distinct path, not the
   main checkout's. A worktree whose path isn't itself a registered `source_root` resolves to
   zero registered projects, and every hook fails closed regardless of ticket citation or real
   activity -- confirmed live for `dev-harness-run2` against the `DEVH` project (registered
   `source_root=/Users/m5/dev/dev-harness`). **DEVH-54, open, not yet fixed.** If you're working
   from a worktree other than the one your project was registered against, do not assume the
   hooks are covering you just because `core.hooksPath` prints correctly.

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
