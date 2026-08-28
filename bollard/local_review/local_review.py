#!/usr/bin/env python3
"""A cheap, fast, local-only review tier -- NOT a substitute for independent review.

Runs a small (3B) coding model, locally via Ollama, with a system prompt that has no stake in
the code looking good: it never writes code, only this one job. Its purpose is narrow on
purpose -- catch the mechanically-findable stuff (stale comments, silent error handlers,
misleading names, stub markers, obvious unreleased resources) BEFORE the expensive real
independent review runs (adversarial-code-review / Warden / a real side-by-side comparison process),
not instead of it.

Positioned per an earlier discussion, 2026-08-21: independence of context is necessary but not
sufficient -- a 3B model has a real capability ceiling, and this tool is deliberately built to
say "uncertain" or "not reviewed" rather than manufacture false confidence. The concurrency bug
that nine-plus in-session review passes missed on this project's own real builds needed a full
independent process to catch, not a cheap first-pass tier -- this tool is not claimed to catch
that class of defect, and its own prompt says so.

Usage:
    python3 local_review.py <file_path>
    cat some_diff.patch | python3 local_review.py -

Requires: an Ollama server running locally (http://localhost:11434) with the model pulled
(`ollama pull qwen2.5-coder:3b`). Fails loudly, not silently, if either is missing -- this is a
review tool; a silent no-op that reports "clean" because it couldn't actually run would be
exactly the failure class this whole project studies.
"""

import json
import sys
import urllib.error
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5-coder:3b"

SYSTEM_PROMPT = """You are a code review tool with exactly one job: read the code you're given \
and report concrete problems you can actually see in it -- nothing more. You did not write this \
code. You have no stake in whether it looks good; your only job is to be accurate about what's \
actually there.

You are NOT a substitute for a full independent security/correctness review. You are a cheap, \
fast, first-pass check that runs BEFORE that real review, to catch what's easy to catch. Do not \
claim you've done a deep analysis you haven't done. If something might be a problem but you are \
not sure, say so as low confidence rather than asserting confidently in either direction.

Check specifically for:
1. comment-mismatch -- a comment or docstring says one thing while the code below it does \
something different or contradictory.
2. silent-failure -- an error is caught (except/catch/rescue) but the handler does nothing \
observable: no re-raise, no log, no returned error value, no flag set.
3. misleading-name -- a function or variable name promises something (e.g. validate_X, safe_Y, \
verified_Z) that the actual body does not do.
4. stub-marker -- a TODO, FIXME, stub, mock, or placeholder comment describing something that \
looks like it was meant to be replaced before shipping.
5. resource-leak -- a resource is opened/acquired with no visible corresponding close/release on \
every path YOU CAN SEE. Only flag what's actually visible in the given text; do not guess about \
code outside it.

Do NOT: invent problems to look thorough, comment on style or formatting preferences, flag \
something an adjacent comment already documents as an intentional, explained tradeoff, or claim \
you checked for security/concurrency/correctness issues beyond what your literal reading covers \
-- those need a real reviewer, not you.

Output ONLY valid JSON, no prose outside it, in exactly this shape:
{"findings": [{"line": <int or null>, "category": "<comment-mismatch|silent-failure|\
misleading-name|stub-marker|resource-leak>", "summary": "<one sentence>", \
"confidence": "<low|medium|high>"}], "not_reviewed": "<one sentence naming what you could NOT \
meaningfully check given your limits, e.g. cross-file behavior, concurrency, anything outside \
this text>"}
If you find nothing, return {"findings": [], "not_reviewed": "..."} -- an honest empty result is \
correct, not a failure to try harder."""


def call_ollama(code_text):
    payload = {
        "model": MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": code_text,
        "stream": False,
        "format": "json",
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_URL} -- is it running? "
            f"(`brew services start ollama`). Underlying error: {exc}"
        ) from exc
    return body.get("response", "")


def parse_findings(raw_response):
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Model did not return valid JSON -- treating as a review FAILURE, not a clean "
            f"result. Raw output: {raw_response[:500]!r}"
        ) from exc
    if "findings" not in parsed or "not_reviewed" not in parsed:
        raise RuntimeError(
            f"Model returned JSON missing required keys (findings/not_reviewed) -- treating as "
            f"a review FAILURE, not a clean result. Raw: {parsed!r}"
        )
    return parsed


def review_text(code_text):
    raw = call_ollama(code_text)
    return parse_findings(raw)


def main():
    if len(sys.argv) != 2:
        print("usage: local_review.py <file_path | ->", file=sys.stderr)
        return 2

    source = sys.argv[1]
    if source == "-":
        code_text = sys.stdin.read()
        label = "<stdin>"
    else:
        with open(source, "r", encoding="utf-8") as fh:
            code_text = fh.read()
        label = source

    result = review_text(code_text)

    print(f"local_review ({MODEL}) -- {label}")
    if not result["findings"]:
        print("  no findings")
    for f in result["findings"]:
        line = f.get("line")
        loc = f"L{line}" if line is not None else "L?"
        print(f"  [{f.get('confidence', '?')}] {loc} {f.get('category', '?')}: {f.get('summary', '')}")
    print(f"  not reviewed: {result.get('not_reviewed', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
