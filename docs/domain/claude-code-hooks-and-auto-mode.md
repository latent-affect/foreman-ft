# Domain briefing — Claude Code's hook/permission/auto-mode stack (compiled 2026-09-02, for task: DEVH-64/R14a OSSandboxGuard C16 verification)

**Confidence & ceiling:** Grounded in Anthropic's own primary docs (code.claude.com/docs/en/hooks,
anthropic.com/engineering/claude-code-auto-mode) plus one GitHub issue and direct empirical
observation in this session (both the outer session's own denials and a live 3-run C16 test).
Does NOT cover: whether `--dangerously-skip-permissions` truly disables the Tier-3 transcript
classifier (the primary source frames them as alternative modes but never states this explicitly;
this session's own C16 run is inconclusive on it — see Open gaps). Recheck if a future Claude Code
version changes auto-mode's tiering or the exit-code behavior in headless mode.

## Vocabulary — terms of art
- **PreToolUse hook** — a project-registered script that runs before a tool call and returns
  `hookSpecificOutput.permissionDecision` (`allow`/`deny`/`ask`/`defer`), optionally with
  `updatedInput` to rewrite the tool's arguments. [code.claude.com/docs/en/hooks]
- **Tier 1 / Tier 2 / Tier 3 (auto mode)** — auto mode's own three-stage classification: Tier 1 is
  a built-in safe-tool allowlist, Tier 2 is in-project file operations, Tier 3 is the "transcript
  classifier" that sees shell commands, web fetches, external tool integrations, subagent spawns,
  and filesystem operations outside the project directory. [anthropic.com/engineering/claude-code-auto-mode]
- **Transcript classifier** — Tier 3's actual mechanism. Deliberately strips assistant text and
  tool output before judging, "so the agent can't talk the classifier into making a bad call."
  [anthropic.com/engineering/claude-code-auto-mode]
- **Headless / non-interactive mode (`claude -p`)** — no UI exists to escalate an ambiguous case
  to a human; a refusal that would otherwise prompt a human instead just terminates the process.

## Canon — the load-bearing sources
- `code.claude.com/docs/en/hooks` — the hook output schema (`permissionDecision`, `updatedInput`).
  Primary, official.
- `anthropic.com/engineering/claude-code-auto-mode` — Anthropic's own engineering writeup of the
  three-tier auto-mode classifier and its headless-mode termination behavior. Primary, official,
  and the one that explains what this session witnessed directly.
- GitHub `anthropics/claude-code` issue #52506 — "`--dangerously-skip-permissions` still shows
  confirmation dialog; `--permission-mode dontAsk` is the headless flag, but nothing says so."
  Community-reported, corroborating (not independently re-verified by fetching the issue itself
  in this pass — flagged, not confirmed to canon standard).

## Pitfalls — what practitioners get wrong
- **Assuming exit code 0 means the tool call ran.** In headless mode, a denied/refused action does
  NOT fail the run — `claude -p` can return exit 0, `is_error: false`, with the denial visible only
  in a `permission_denials` field of `--output-format json` output that a plain-text-mode caller
  never sees. A test harness that only checks the process exit code cannot tell "ran successfully"
  apart from "silently refused." This is exactly what produced Run 3's anomalous "no guards, but
  marker absent" result in this session's first C16 attempt.
- **Treating `--dangerously-skip-permissions` as disabling every safety layer.** It bypasses the
  interactive/allowlist permission system, but auto mode's Tier-3 transcript classifier is
  presented as a *separate operational mode*, not a sub-feature of the permission system it
  bypasses — so a command that reads as genuinely destructive-shaped (e.g. containing literal
  `rm -rf`) can still be intercepted by a layer neither the target guard nor `--dangerously-
  skip-permissions` was designed to talk about.
- **Conflating a hook's PreToolUse decision with the harness's own built-in classifier.** They are
  architecturally distinct: hooks are user/project-registered scripts; the transcript classifier
  is core Claude Code behavior that runs regardless of what hooks a project registers. A test
  measuring "does my guard deny this" must be able to distinguish the two, or a classifier refusal
  gets misread as guard evidence (or, just as bad, guard evidence gets misread as classifier noise).

## Frontier — current state (installed version 2.1.258, checked 2026-09-02)
- `--output-format json` for `claude -p` returns a single JSON object including `subtype`,
  `is_error`, `permission_denials`, and `result` (the model's final text) — confirmed via
  `claude -p --help` on this machine, not just docs.
- `--include-hook-events` (stream-json only) can show hook lifecycle events directly, which would
  be the more precise diagnostic than `--output-format json` alone if this gap recurs.

## Open gaps — what this pass couldn't establish
- Whether `--dangerously-skip-permissions` actually suppresses the Tier-3 transcript classifier,
  or whether it is layered underneath it regardless. Recommended way to close: re-run the C16
  three-case test with `--output-format json` (v2 script) and read `permission_denials` /
  `result` directly — if Run 3 (no bollard guards) still shows a refusal in that output rather
  than the marker simply not being written by choice, that's live evidence the classifier applies
  even under skip-permissions.
- Whether the classifier's behavior is deterministic for a fixed command (this session and a
  peer session, agent-remediation-aa, both observed the *same* literal command blocked in one
  session/run and clean in another — logged by the peer as DEVH-74, a suspected harness defect,
  not yet root-caused).
