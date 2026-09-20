"""Feature extractor for `bash_command_shape` (ATLASSN-61, ARCHITECTURE.md section 24).

A token scanner over `shlex`-split text plus a handful of regexes -- not a parser and not a
sandboxed interpreter. It records that a shape is present, never what it means: nothing here
decodes a base64 payload or executes anything. See ARCHITECTURE.md section 24.2 for what this
deliberately does not attempt.

EXTRACTOR_VERSION is bumped whenever the detection logic changes, so a future recompute can tell
which rows were produced by which version rather than silently overwriting the evidence of what
an older version actually flagged.
"""

import re
import shlex

EXTRACTOR_VERSION = "2"

_CHR_PAREN_RE = re.compile(r"\bchr\(")
_EVAL_TOKEN_RE = re.compile(r"^eval$")
_GETATTR_DUNDER_IMPORT_RE = re.compile(r"getattr\(\s*__import__")
_STRING_CONCAT_IMPORT_RE = re.compile(r"__import__\(([^()]*)\)")
_DOLLAR_VAR_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")
_HEREDOC_OP_RE = re.compile(r"<<-?\s*['\"]?\w+['\"]?")
_INTERPRETER_WORD_RE = re.compile(r"\b(python3?|bash|sh|zsh|perl|ruby|node)\b")
_PYTHON_BIN_RE = re.compile(r"(^|/)python3?(\.\d+)?$")
_BASE64_DECODE_FLAG_RE = re.compile(r"^(-d|--decode)$")

# ATLASSN-95 / v2 features. Originally two structural additions (pattern 1: has_quote_splice,
# pattern 5: has_cmd_pos_var_indirection) closing the two respell patterns v1 leaks entirely, per
# BASH-SHAPE-EVASION-COVERAGE-MEASURED-20260904.md's structure-over-enumeration thesis. Only
# pattern 5's fix landed -- see below for why pattern 1's did not.
#
# PATTERN 1 (has_quote_splice) PULLED, per this ticket's own frozen falsification condition
# (ATLASSN-95-PREREGISTRATION-20260904.md: "Feature B's real-corpus FP rate comes in >= 0.5% ...
# Feature B is pulled from the v2 commit"). Measured on a 1,500-row raw-text sample re-extracted
# from the real warehouse (not the synthetic 22-24 case negative battery Feature B was originally
# calibrated against): 5.13% fired, ~14x the 0.5% threshold, and neither of its two signals clears
# the bar alone (empty-quote-pair 1.40%, backslash-alnum-splice ~4.1%). Root cause the small
# battery never exercised: `[A-Za-z0-9_-]\\[A-Za-z0-9]` (an alnum immediately followed by a
# backslash-escaped alnum) matches an ordinary regex escape sequence -- `\b`, `\s`, `\n`, `\d` --
# embedded in any grep/rg/python -c invocation, which is extremely common real developer traffic,
# not a quote-splicing attempt. The empty-quote-pair signal separately fires on real heredoc-
# embedded multi-line Python/Go source (adjacent quote characters across string literals/
# docstrings). Both are real, disclosed misses of the "calibrate against a small synthetic
# battery, then hit the real corpus" trap -- exactly what ATLASSN-95's own falsification
# discipline exists to catch before a claim is published. `bash_command_shape_v2.has_quote_splice`
# stays in the schema (already reviewed and live) as a placeholder column, always 0 -- not
# repopulated with a value nothing currently trusts. A real fix needs a detector calibrated
# against THIS corpus's actual quoting/escaping shapes, not the original curated battery; left
# for a follow-on ticket, not shipped with a known false-positive surface.

# Pattern 5 -- variable in COMMAND position after any leading NAME=VALUE assignments, checked
# per STATEMENT rather than only at the head of the whole command line. The Bash tool in this
# harness does not persist shell state between calls, so a realistic assign-then-invoke attempt
# has to be a single, statement-joined command string (`X=rm; $X -rf ...`) -- and the dangerous
# invocation can sit after an earlier, unrelated statement (`cd /tmp && X=rm && $X -rf ./build`),
# not only at position 0. Splitting on `;`, `&&`, `||`, `|`, and newlines FIRST, then applying the
# assignment-skip independently within EACH segment, measured 11/11 on the union of both this
# ticket's synthetic battery and a second, independent session's realistic-attempt battery, versus
# 8/11 for checking only the whole command's leading run -- at the same 1/24 false-positive rate
# (the accepted `$EDITOR file` twin) either way. Reproduced at
# bash-shape-combined-candidate/measure_combined_pattern5.py. A regex split, not a real shell
# parser, consistent with this module's own stated approach -- it does not respect quoting, so a
# segment like `echo "a; b"` can be mis-split at the embedded `;`; each half then either fails to
# parse (caught by the per-segment try/except below) or is checked as its own, harmless segment.
# Not observed to affect any measured case, disclosed as a known limitation rather than hidden.
_STATEMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")
_ASSIGNMENT_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_CMD_POS_VAR_RE = re.compile(r"(^\$)|(\$\{)")


def _has_string_concat_import(command):
    for match in _STRING_CONCAT_IMPORT_RE.finditer(command):
        if "+" in match.group(1):
            return True
    return False


def _has_python_c_os_subprocess(tokens):
    for i, tok in enumerate(tokens):
        if tok == "-c" and i > 0 and _PYTHON_BIN_RE.search(tokens[i - 1]):
            if i + 1 < len(tokens):
                code = tokens[i + 1]
                if "os.system(" in code or "subprocess." in code:
                    return True
    return False


def _has_base64_decode(tokens):
    if "base64" not in tokens:
        return False
    return any(_BASE64_DECODE_FLAG_RE.match(tok) for tok in tokens)


def _has_dollar_var_command_position(tokens):
    if not tokens:
        return False
    return bool(_DOLLAR_VAR_RE.match(tokens[0]))


def _has_eval_word(tokens, command):
    if any(_EVAL_TOKEN_RE.match(tok) for tok in tokens):
        return True
    return False


def _has_cmd_pos_var_indirection(command):
    for segment in _STATEMENT_SPLIT_RE.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            toks = shlex.split(segment, posix=True)
        except ValueError:
            continue
        idx = 0
        while idx < len(toks) and _ASSIGNMENT_TOKEN_RE.match(toks[idx]):
            idx += 1
        if idx >= len(toks):
            continue
        if _CMD_POS_VAR_RE.search(toks[idx]):
            return True
    return False


def extract_shape(command):
    """Returns the fixed feature-vector dict for one Bash command string. Never raises --
    a `shlex.split` failure is itself the `parse_failure` signal, and every flag falls back to
    a raw-text-only check (documented per-flag below) rather than silently zeroing out just
    because tokenization failed."""
    features = {
        "token_count": None,
        "parse_failure": 0,
        "has_chr_paren": 0,
        "has_base64_decode": 0,
        "has_eval_word": 0,
        "has_getattr_dunder_import": 0,
        "has_string_concat_import": 0,
        "has_dollar_var_command_position": 0,
        "has_heredoc_into_interpreter": 0,
        "has_python_c_os_subprocess": 0,
        "has_quote_splice": 0,
        "has_cmd_pos_var_indirection": 0,
    }

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = None
        features["parse_failure"] = 1

    if tokens is not None:
        features["token_count"] = len(tokens)
        features["has_dollar_var_command_position"] = int(
            _has_dollar_var_command_position(tokens)
        )
        features["has_python_c_os_subprocess"] = int(_has_python_c_os_subprocess(tokens))
        features["has_base64_decode"] = int(_has_base64_decode(tokens))
        features["has_eval_word"] = int(_has_eval_word(tokens, command))
    else:
        # Parse failed (e.g. an unbalanced quote) -- token-position checks (command-position
        # $VAR, python -c's next argument) are meaningless without tokens and stay 0. The two
        # word-boundary checks below still run against the raw text: an unbalanced quote does
        # not change whether the literal substrings "base64 -d" or "eval" are present.
        features["has_base64_decode"] = int(
            bool(re.search(r"\bbase64\b", command)) and bool(re.search(r"(-d|--decode)\b", command))
        )
        features["has_eval_word"] = int(bool(re.search(r"\beval\b", command)))

    # Regex checks below run over the raw string regardless of tokenization outcome -- these are
    # substring/shape patterns, not token-position patterns, so a parse failure does not change
    # whether they can be evaluated.
    features["has_chr_paren"] = int(bool(_CHR_PAREN_RE.search(command)))
    features["has_getattr_dunder_import"] = int(bool(_GETATTR_DUNDER_IMPORT_RE.search(command)))
    features["has_string_concat_import"] = int(_has_string_concat_import(command))
    # has_quote_splice stays 0 -- Feature B was pulled after failing its own frozen real-corpus
    # false-positive gate (C4). See the module-level comment above _has_cmd_pos_var_indirection's
    # neighboring block for the measured numbers and root cause.
    features["has_cmd_pos_var_indirection"] = int(_has_cmd_pos_var_indirection(command))
    features["has_heredoc_into_interpreter"] = int(
        bool(_HEREDOC_OP_RE.search(command)) and bool(_INTERPRETER_WORD_RE.search(command))
    )

    return features
