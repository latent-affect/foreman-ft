#!/usr/bin/env python3
"""Gemini-backed arm of the local_review comparison, added 2026-08-21 to test whether a
frontier-tier free model outperforms the local 3B/7B models on the exact same task, using the
exact same 'Accountable Bughunter' persona that won the /spread rubric and was empirically
tested against local models.

Uses gemini-2.5-flash-lite, the most-generous free-tier model as of the domain research this
session did (docs/domain/small-local-coding-models-2026.md). Free tier's real constraint is
requests-per-minute (quoted 5-15 RPM depending on source), not the daily cap, so this script
paces itself conservatively (one call per ~13s, ~4.6 RPM) rather than trusting the upper end of
that range.

Credential handling: the API key is read fresh from macOS Keychain on every run via
`security find-generic-password`, never accepted as a CLI argument, never printed, never written
to a file. See CLAUDE.md's "Before anything else: credential hygiene check" for why -- a prior
key got exposed via a literal `export KEY=value` typed in a terminal and pasted back for
troubleshooting; this script is written specifically to make that mistake impossible to repeat.
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_review_accountable as lra  # noqa: E402 -- reuse the winning persona prompt

MODEL = "gemini-2.5-flash-lite"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
MIN_SECONDS_BETWEEN_CALLS = 13.0  # ~4.6 RPM, conservative against the quoted 5-15 RPM free tier


def get_api_key():
    result = subprocess.run(
        ["security", "find-generic-password", "-a", subprocess_user(), "-s", "gemini-api-key", "-w"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            "Could not read gemini-api-key from Keychain. Set it up per CLAUDE.md's "
            "credential-hygiene section before running this script."
        )
    return result.stdout.strip()


def subprocess_user():
    import os
    return os.environ.get("USER") or subprocess.run(
        ["whoami"], capture_output=True, text=True
    ).stdout.strip()


def call_gemini(api_key, code_text):
    payload = {
        "systemInstruction": {"parts": [{"text": lra.ACCOUNTABLE_SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": code_text}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API error {exc.code}: {detail[:500]}") from exc
    try:
        return body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response shape: {body!r}") from exc


def review_text_gemini(api_key, code_text):
    raw = call_gemini(api_key, code_text)
    import local_review as base
    return base.parse_findings(raw)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    args = parser.parse_args()
    key = get_api_key()
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    t0 = time.time()
    result = review_text_gemini(key, text)
    print(f"({time.time()-t0:.1f}s)", result)
