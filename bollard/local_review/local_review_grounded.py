#!/usr/bin/env python3
"""Experimental variant of local_review.py: same model, same task, but the system prompt is
saturated with a few worked examples instead of relying on the base model's own judgment to
apply an abstract rule.

Built 2026-08-21 specifically to test one diagnosed failure: the 384-trial baseline run on
local_review.py found the model repeatedly flagged `safe_divide` for "silently returning None
on b==0" even though its own docstring documents that as the designed behavior -- despite the
base prompt explicitly instructing it not to flag documented intentional behavior. The rule was
stated; it wasn't reliably followed. This variant replaces the bare instruction with concrete
examples of the exact judgment call, on the theory (the operator's own experience grounding a model
against a RAG corpus) that a small model does better matching a worked pattern than reasoning
from an abstract rule.

Not a replacement for local_review.py -- a controlled A/B arm. Same test harness
(plateau_test_384.py's methodology), same two test files, same metrics, so the before/after
comparison is real rather than a vibe.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_review as base  # noqa: E402

FEW_SHOT_EXAMPLES = """
Three worked examples of the exact judgment calls that matter most -- study the PATTERN, not \
just the verdict:

EXAMPLE 1 -- do NOT flag (documented intentional behavior):
def safe_divide(a, b):
    \"\"\"Return a / b, or None if b is zero.\"\"\"
    if b == 0:
        return None
    return a / b
Correct output: no finding here. The docstring explicitly documents returning None for b==0 as \
the designed behavior. Returning None is not a silent failure when the docstring says that's \
what happens -- ALWAYS read the docstring/comment directly above a function before judging its \
body, and if it already describes the exact behavior you're about to flag, do not flag it.

EXAMPLE 2 -- DO flag (genuine silent failure, no documentation excuses it):
def parse_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        pass
Correct output: silent-failure, confidence high. The except block swallows every exception with \
no re-raise, no log, no returned error value, no flag -- and nothing above the function \
documents this as intentional. This is the real thing to catch.

EXAMPLE 3 -- do NOT flag (comment matches code exactly):
def add(a, b):
    \"\"\"Return the sum of a and b.\"\"\"
    return a + b
Correct output: no finding. The docstring and the body agree exactly. Do not manufacture a \
mismatch where none exists.

Apply this same discipline to the code you are given: read any comment/docstring directly above \
each function FIRST, and check whether it already documents the behavior you're about to flag \
before flagging it.
"""

GROUNDED_SYSTEM_PROMPT = base.SYSTEM_PROMPT + "\n\n" + FEW_SHOT_EXAMPLES


def call_ollama_grounded(code_text):
    import json
    import urllib.request

    payload = {
        "model": base.MODEL,
        "system": GROUNDED_SYSTEM_PROMPT,
        "prompt": code_text,
        "stream": False,
        "format": "json",
    }
    req = urllib.request.Request(
        base.OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", "")


def review_text_grounded(code_text):
    raw = call_ollama_grounded(code_text)
    return base.parse_findings(raw)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    args = parser.parse_args()
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    result = review_text_grounded(text)
    print(result)
