# local_review — a cheap, local, no-stakes review tier

Built 2026-08-21. Runs `qwen2.5-coder:3b` locally via Ollama
(`brew install ollama`, `ollama pull qwen2.5-coder:3b`, `brew services start ollama`) with a
system prompt whose only job is review — it never writes code, so it has no self-attribution
bias toward agreeing with whoever did.

## What this is NOT

Not a replacement for independent review (`adversarial-code-review` or a real separate-context
pass). A concurrency bug caught by independent review had already been missed by nine-plus
in-session passes. A 3B model has a real capability ceiling below that. This tool exists to
catch mechanically-findable issues cheaply, before the expensive review runs, not instead of it.

## Validated before trusting (measure-then-decide, not by hunch)

Two synthetic test files, run 2026-08-21:
- A file with a misleading function name (docstring/name promises validation, body silently
  swallows the error and always returns `True`) plus an unclosed file handle. **Result: caught
  the misleading-name issue with a correct, specific summary. Missed the resource leak entirely.**
  One real catch, one real miss — exactly the capability-ceiling caveat this tool's own design
  predicted, not a surprise.
- A genuinely clean file (two small, correct functions). **Result: zero findings, no false
  positives.**

n=2, both synthetic, not the project's own real historical incidents (didn't have quick access to
the actual code from the concurrency-bug case for a true replication). Confidence in this tool as
a genuine value-add: **medium** — internally consistent, real signal, but not yet validated
against a real historical incident this project has record of, and n=2 is thin. The one thing that
would raise it: running it against the actual diff from a past confirmed finding and checking it
catches what it should.

## Usage

```
python3 /path/to/home/.claude/hooks/local_review/local_review.py <file_path>
cat some_diff.patch | python3 /path/to/home/.claude/hooks/local_review/local_review.py -
```

Fails loudly (raises, non-zero exit) if Ollama isn't reachable or the model returns malformed
JSON — a silent "clean" result from a tool that couldn't actually run would be exactly the
failure class this whole project exists to catch.

## Not wired into any hook path

Deliberately not registered in any `settings.json` PreToolUse/PostToolUse chain yet. An LLM call
takes seconds, not the sub-200ms budget the existing hard gates run in — wiring this into the hot
path needs its own latency/blocking-vs-advisory design pass, not a default. Callable on demand for
now (by a skill, or by hand) until that design happens.
