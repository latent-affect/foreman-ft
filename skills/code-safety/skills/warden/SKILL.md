---
name: warden
description: Runs a code review in its own process, outside the session that wrote the code. Use when the user asks for a review before pushing or merging, wants a security pass on a diff, or wants a reviewer that never saw the reasoning which produced the code.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[warden · loaded · 1AE75B22]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary elsewhere in this practice — a clean result is only
> trustworthy if the canary came back.


# Warden, the review that runs outside this context

`@sentry/warden` is an LLM code reviewer that runs as its own process against a working tree.
Its value is not that it is better than reviewing in-session. Its value is that it never saw the
argument that produced the code, so it cannot recognise its own reasoning and mistake that for
checking. That is the standing rule in `CLAUDE.md`: one context implements, a separate context
reviews. Warden is the strongest available form of that, because a subprocess shares no session
state at all rather than merely starting fresh inside one.

License FSL-1.1-ALv2. Source `getsentry/warden`. Verified against **v0.43.0 on 2026-08-11**;
re-check the flags below if the version has moved, because several published claims about this
tool were already wrong at that version.

## The one thing that will silently ruin a run

**With no target, Warden analyses `origin/master..HEAD` and nothing else.** On a clean tree, a
fresh clone, or a newly created worktree, that diff is empty:

```
Analyzing changes from origin/master to HEAD...
WARN: No changes found from origin/master to HEAD
```

It then exits reporting no findings. That result is indistinguishable from a clean review and it
means the opposite: nothing was read. Any conclusion drawn from it is void. If the intent is to
review a whole codebase rather than a diff, **always pass an explicit target**.

```bash
npx @sentry/warden@0.43.0 "**/*.go" --runtime claude    # whole tree by glob
npx @sentry/warden@0.43.0 server.go jobs.go --runtime claude
npx @sentry/warden@0.43.0 HEAD~3..HEAD --runtime claude  # a git range
npx @sentry/warden@0.43.0 --staged --runtime claude      # staged changes only
```

Before any expensive run, confirm what it is about to read. `--runtime pi` fails authentication in
under 10ms when no API key is set, but it prints the resolved target set first, which makes it a
free dry run:

```bash
npx @sentry/warden@0.43.0 "**/*.go" --runtime pi 2>&1 | head -8
# Found N files ... then: ERROR Authentication failed
```

Read the file count. If it says 0, or a number you did not expect, fix the target before spending
anything.

## Credentials on this machine

`warden.toml` ships `runtime = "pi"`, which requires `WARDEN_MODEL` plus a matching
`WARDEN_<PROVIDER>_API_KEY`. **No such key is set here**, so the default runtime always fails.

**Pass `--runtime claude`.** That path uses the Claude Agent SDK and authenticates through the
Claude Code subscription with no key required. Two consequences worth holding onto:

- Warden prints `$0.00` for every run under this path. That is not a measurement of what the run
  cost. It means no metered API key was billed. Report token counts from the `Usage:` line instead,
  never the dollar figure.
- The GitHub Actions workflow that `init` writes cannot work here at all. Actions has no
  subscription to fall back on and reads `WARDEN_ANTHROPIC_API_KEY` from repo secrets. Without an
  API key, Warden is a local pre-push CLI and nothing else, and `.github/workflows/warden.yml` is
  dead weight in the tree.

## Never pass `--fix`

The flag exists. Do not use it. A reviewer that also proposes patches anchors on its own
suggestions on any subsequent pass, and review quality drifts once that happens. Sentry's own skill
says to run Warden once rather than loop it over the same changes. Keep the reviewer pure and treat
applying a fix as a separate decision made somewhere else.

## Setup, per repository

Warden is configured per repo, not at user scope.

```bash
cd <repo>
npx @sentry/warden@0.43.0 init
npx @sentry/warden@0.43.0 add security-review
npx @sentry/warden@0.43.0 add code-review
```

`init` writes `warden.toml`, `.github/workflows/warden.yml`, and appends `.warden/` to
`.gitignore`. Config keys are camelCase under `[defaults]`: `failOn` and `reportOn`, taking
`critical`, `high`, `medium`, `low`, `info`, or `off`. `failOn = "high"` and
`reportOn = "medium"` are the defaults already.

**Two `init` defects to clean up afterwards.** It creates `.claude/skills -> ../.agents/skills`
without creating the target, leaving a dangling symlink, and it reports that step as "Skipped
(already installed)" while doing it. Delete the broken link. It also writes the Actions workflow
unconditionally; drop `.github/` unless the repo genuinely has an API key in its secrets.

A worktree does not inherit an uncommitted `warden.toml` from the main checkout. Copy it in.

## What it costs

Measured on 2026-08-11, subscription runtime, security-review plus code-review:

| Target | Wall clock | Input tokens |
|---|---|---|
| one 2.6 KB file, cold | 2m 32s | 1.9M |
| per file across a 19-file Go repo | 3s to 6m 25s | falls sharply after the first file |

The first number is the one to plan around, and the reason is worth understanding: an
uncontaminated reviewer has to read the surrounding repository to judge one file, which is exactly
the property being paid for. Caching makes subsequent files much cheaper within a run. A small
target does not imply a small run.

## Reading the output honestly

- `--fail-on <severity>` sets the exit code. `--report-on <severity>` sets what is displayed. They
  are independent, so a run can exit 0 while still printing findings below the fail threshold.
- `--min-confidence` filters on the model's own confidence, which is self-reported and not
  calibrated. Treat it as a display convenience, not evidence.
- Findings arrive with a `verification` block citing specific files and line ranges. Check those
  citations. They are the difference between a finding and a plausible sentence.
- `-o <path>` writes structured JSONL alongside the human output. Use it when a count matters,
  rather than counting lines of terminal text.

## Where it does not help

It reviews code, so it says nothing about a repository's configuration, its CI, its secrets
hygiene, or its dependencies. It is per-file and per-hunk, so a defect that only exists in the
interaction between two distant files is one it can miss. And it is an LLM reviewer, which means a
confident, well-cited, wrong finding is a normal output and not an anomaly. Verify before acting,
particularly before changing code in response.

For vetting a dependency rather than reviewing code, use `library-scout`. For a threat model or an
adoption decision, use `risk-assessment`. For a review inside the current context, with the
self-attribution caveat that implies, use `adversarial-code-review`.
