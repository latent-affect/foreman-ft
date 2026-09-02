#!/usr/bin/env python3
"""Classifies a Bash command that programmatically constructs a destructive-operation token
(character-code concatenation, hex-byte escapes) WITHOUT ever executing or evaluating the
command it is classifying -- bollard/GOALS.json C12/C13, PRD.md R15 (DEVH-16).

Resolution is STATIC ONLY. chr(N) calls and \\xHH escapes are decoded by extracting the numeric
codes with regex and mapping each one through Python's builtin chr(), which converts an integer
codepoint to the character it names and executes nothing else -- it never runs the command's own
chr(...) expression, only regex-extracts the literal digits inside the parentheses. The decoded
text is then matched against guard_destructive.py's own DESTRUCTIVE_PATTERNS (reused, not
duplicated, so retiring a pattern there does not leave a stale copy here) exactly as if it were
the literal command.

No eval, exec, compile, __import__, os.system, os.popen or any subprocess member appears
anywhere in this module's resolution path, and no call reaches through getattr, __builtins__,
globals() or locals() to reintroduce one indirectly: a resolver that runs attacker-controlled
input to see what it produces is a worse hole than the evasion it closes (GOALS.json F12).
find_forbidden_calls() below is the scan that proves this AST-statically -- by name AND by
tracing dynamic-resolution primitives -- and test_guard_semantic_resolution.py proves the scan
itself fires, on two fixtures, rather than trusting this docstring.

DENY-or-nothing, hook_common.py's convention: acts only when the decoded text forms a
destructive shape, stays silent otherwise, fails open on its own internal error (hc.run()).
"""

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
from guard_destructive import DESTRUCTIVE_PATTERNS, _normalize  # noqa: E402

RULE_ID = "GUARD-SEMANTIC-RESOLUTION"

_CHR_CALL = re.compile(r"chr\(\s*(\d{1,7})\s*\)")
_HEX_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})")

_MAX_CODEPOINT = 0x10FFFF


def _decode_chr_concat(command: str) -> str:
    """Reconstructs the text formed by every chr(N) call in the command, in the order they
    appear. Purely a regex extract of the literal digits followed by chr(int(...)) -- the
    command's own chr(...) expression is never invoked, only read as text."""
    pieces = []
    for match in _CHR_CALL.finditer(command):
        codepoint = int(match.group(1))
        if 0 <= codepoint <= _MAX_CODEPOINT:
            pieces.append(chr(codepoint))
    return "".join(pieces)


def _decode_hex_escapes(command: str) -> str:
    """Reconstructs the text formed by every \\xHH escape in the command, in order (bash
    ANSI-C quoting / printf hex bytes) -- the second programmatic construction C12 requires,
    decoded the same static extract-then-chr() way as _decode_chr_concat."""
    pieces = []
    for match in _HEX_ESCAPE.finditer(command):
        pieces.append(chr(int(match.group(1), 16)))
    return "".join(pieces)


def _resolved_match(command: str):
    """Returns the name of the first DESTRUCTIVE_PATTERNS entry the decoded text matches, or
    None. Checks each construction's decode independently: a command may use only one."""
    for decoded in (_decode_chr_concat(command), _decode_hex_escapes(command)):
        if not decoded:
            continue
        normalized = _normalize(decoded)
        for name, pattern in DESTRUCTIVE_PATTERNS:
            if pattern.search(normalized):
                return name
    return None


def main(data):
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command:
        return

    matched = _resolved_match(command)
    if matched is None:
        hc.set_rule(f"{RULE_ID}:no-match")
        return

    hc.set_rule(f"{RULE_ID}:{matched}")
    hc.deny(
        f"guard_semantic_resolution: this command programmatically constructs a "
        f"destructive-operation shape ({matched}) via character-code construction and is "
        f"denied. If this is genuinely intended, run it yourself in a terminal."
    )


# ---- C13's self-proof: an AST scan for execution paths, never invoked against the Bash
# command text above (that text is only ever regex-scanned by _decode_* and _resolved_match).
# Exported so test_guard_semantic_resolution.py drives the SAME function against this module's
# own source and against the two fixtures, rather than re-implementing the walk in the test.

_NAMED_FORBIDDEN = {"eval", "exec", "compile", "__import__", "os.system", "os.popen"}
_DYNAMIC_RESOLUTION_NAMES = {"getattr", "globals", "locals", "__builtins__"}


def _callee_dotted_name(func_node):
    """Returns a dotted string like 'os.system' or 'eval' for a Name/Attribute callee chain
    (e.g. `eval(...)` or `os.system(...)`), or None when the callee is not a simple static
    name/attribute chain -- for instance when it is itself a Call, as in
    `getattr(__builtins__, 'eval')(...)`, which _contains_dynamic_resolution catches instead."""
    parts = []
    node = func_node
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _contains_dynamic_resolution(call_node) -> bool:
    """True if anywhere under this Call node -- its callee OR any argument -- a name resolves
    to getattr, globals, locals or __builtins__. Catches getattr(__builtins__, 'ev'+'al')(tok)
    and the same shape through globals()/locals(), which a name-only scan for 'eval' misses."""
    for sub in ast.walk(call_node):
        if isinstance(sub, ast.Name) and sub.id in _DYNAMIC_RESOLUTION_NAMES:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "__builtins__":
            return True
    return False


def find_forbidden_calls(source: str) -> list:
    """Parses `source` and returns a list of forbidden-call findings: 'eval', 'os.system',
    'subprocess.run', 'dynamic-resolution', etc. Empty list means the scan found no execution
    path by either detection mode. This function does not execute `source` in any way -- it
    only calls ast.parse and ast.walk, neither of which runs the parsed code."""
    tree = ast.parse(source)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _callee_dotted_name(node.func)
        if dotted in _NAMED_FORBIDDEN:
            findings.append(dotted)
            continue
        if dotted is not None and dotted.split(".")[0] == "subprocess":
            findings.append(dotted)
            continue
        if _contains_dynamic_resolution(node):
            findings.append("dynamic-resolution")
    return findings


if __name__ == "__main__":
    hc.run(main)
