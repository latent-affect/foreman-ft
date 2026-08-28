#!/usr/bin/env python3
"""
adversarial-code-review triage scanner.

Heuristic, language-limited helper for the MECHANICAL half of checks 1-3 in
SKILL.md. This narrows where to look; it does not replace the reasoning pass,
and it does not grade severity. Every hit is a candidate for a human/model
judgment call, not a finding on its own. Treat "no hits" as "no hits from
this heuristic" -- not as a clean bill of health for the whole check. Say so
in the report (see SKILL.md's "Not verified" section).

Usage:
    python3 scan.py proxy-markers <path>        # check 3 candidates, any text file
    python3 scan.py silent-failures <path>      # check 2 candidates (.py AST + generic regex)
    python3 scan.py undefined-calls <file.py>   # check 1 candidates, Python only, AST-based
    python3 scan.py all <path>                  # run everything applicable to what's found

Known limitations (read before trusting a clean scan):
  - undefined-calls only catches calls to bare names that are never defined,
    imported, or built in (e.g. calling a helper that doesn't exist). It CANNOT
    catch a wrong method on a properly-imported object (e.g. `df.wrong_method()`
    on a real DataFrame) -- that needs real type info. Run the code or a type
    checker for that half of check 1. It is Python-only, full stop -- for Go,
    run `go build`/`go vet`; for JS/TS, run the actual bundler/tsc. A 0-hit
    scan on a non-.py file means "not checked," not "clean." (Confirmed the
    hard way: a real run against a Go+JS codebase reported 0 hits from this
    mode on every non-Python file -- correctly, since it never claimed to
    check them, but easy to misread as a clean bill of health if you don't
    already know that.)
  - proxy-markers is a keyword scan. It will not catch a substitution that uses
    no marker word at all -- e.g. code that reads the wrong-but-present field
    and returns a confident, wrong answer with no flag anywhere. That class of
    bug is exactly why check 3 also requires tracing external data dependencies
    by hand, not just running this script. Hits inside recognized test paths
    (`_test.go`, `.test.js`, `.spec.ts`, `/test(s)/`, `/__tests__/`) are shown
    in a separate low-priority bucket -- a real run found 72 raw marker hits
    with effectively zero real issues, nearly all of them the normal, correct
    use of words like "mock" and "fixture" inside test files.
  - silent-failures' AST pass is Python-only. Go gets a regex heuristic for the
    two idioms that stand in for try/catch there (a bare `_ = call()` discard,
    and an empty or no-op `if err != nil {}` block) -- regex, not a real Go
    parser, so it's lower-precision than the Python AST pass and will miss
    idioms it doesn't recognize. If `errcheck` (https://github.com/kisielk/errcheck)
    is installed, prefer it for Go -- it's a real, purpose-built linter for
    exactly this bug class and will outperform this heuristic. Other non-Python,
    non-Go files fall back to the empty-catch-block regex, now including .html
    files so inline `<script>` blocks get scanned too.
"""

import ast
import re
import sys
import builtins
from pathlib import Path

PROXY_MARKERS = [
    "mock", "stub", "dummy", "placeholder", "fixture", "hardcoded", "hard-coded",
    "fake", "fallback", "todo", "fixme", "xxx", "should not happen",
    "shouldn't happen", "not implemented", "notimplemented", "test_data",
    "example_data", "sample_data", "canned", "synthetic", "temp_value",
    "for now", "stopgap", "as a workaround", "hack:",
]

# The subset of PROXY_MARKERS whose natural home is a comment. A hit on one of
# these inside a comment is the check doing its job, so it stays in the main
# stream; only the IMPLEMENTATION markers get split out as comment prose.
DEFERRAL_MARKERS = [
    "todo", "fixme", "xxx", "hack:", "for now", "stopgap", "as a workaround",
    "not implemented", "notimplemented", "should not happen", "shouldn't happen",
]

# A line that is ONLY a comment -- no code before the comment opener. Deliberately
# conservative: `x = get_fallback()  # mock` keeps code on the line and so stays a
# real hit. Covers //, #, and the * continuation of a /* */ block.
COMMENT_ONLY_RE = re.compile(r"^\s*(//+|#+|/\*+|\*+/?|<!--)")


def _python_docstring_lines(path: Path, lines):
    """Line numbers (1-based) covered by docstrings in a .py file.

    Returns an empty set for non-Python files and for Python that will not parse.
    Failing to an empty set is the SAFE direction here: a docstring line that is not
    recognised stays in the main candidate stream, so the worst case is the noisy
    behaviour this exists to reduce, never a suppressed real hit.
    """
    if path.suffix != ".py":
        return set()
    try:
        tree = ast.parse("\n".join(lines), filename=str(path))
    except (SyntaxError, ValueError):
        return set()
    covered = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        if not isinstance(first.value.value, str):
            continue
        end = getattr(first, "end_lineno", first.lineno)
        covered.update(range(first.lineno, (end or first.lineno) + 1))
    return covered


def marker_pattern(markers):
    """Compile markers with a LEADING word boundary, no trailing one.

    Plain substring matching made "canned" match inside the word "SCANNED", so this
    file's own '[NOT SCANNED -- could not read]' disclosure strings were reported as
    proxy markers. A leading \\b fixes that: in "SCANNED" the letters "canned" are
    preceded by "S", a word character, so there is no boundary and no match.

    The trailing boundary is deliberately OMITTED. Requiring one would stop "todo"
    matching "TODOs" and "hardcoded" matching "hardcodedValue", trading a false
    positive for a false negative, which is the worse direction for a merge gate.

    A leading \\b is only meaningful before a word character, so markers that start
    with punctuation are matched literally.
    """
    parts = []
    for marker in markers:
        escaped = re.escape(marker)
        parts.append(rf"\b{escaped}" if marker[:1].isalnum() else escaped)
    return re.compile("|".join(parts), re.IGNORECASE)

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".java", ".c", ".cpp",
    ".h", ".hpp", ".rs", ".swift", ".sh", ".sql", ".html", ".htm", ".vue", ".svelte",
}

TEST_PATH_RE = re.compile(
    r"(_test\.go$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|(^|/)(tests?|__tests__)(/|$))",
    re.IGNORECASE,
)


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    for p in path.rglob("*"):
        if p.is_file() and p.suffix.lower() in CODE_EXTENSIONS:
            if any(part in {".git", "node_modules", "__pycache__", ".venv", "venv"} for part in p.parts):
                continue
            yield p


def proxy_markers(path: Path):
    """Returns (hits, test_file_hits, comment_prose_hits).

    test_file_hits are marker matches inside recognized test paths -- usually the
    normal, correct use of words like "mock" or "fixture" in a test, not a real
    check-3 candidate.

    comment_prose_hits are matches where an IMPLEMENTATION marker ("mock",
    "stub", "fake", "hardcoded", "fallback", ...) appears only inside a comment,
    with no code on the line. Prose that explains a design routinely contains
    those words while the code does the opposite: measured on the hyphy arm of
    the rigor pilot, 14 of 119 unique flagged items were this, including a
    comment reading "-- no mocks" flagged for "mock" and one reading "hardcoded
    path would silently get wrong" flagged for "hardcoded".

    DEFERRAL markers ("todo", "fixme", "xxx", "hack:", "for now", "stopgap",
    "not implemented") are deliberately NOT split out. Living in a comment is
    the whole point of those words, so a hit there is the check working.

    All three streams are returned rather than filtered, for the same reason
    unreadable files stay in the main stream: a suppressed hit that nobody can
    see is indistinguishable from a clean scan."""
    pattern = marker_pattern(PROXY_MARKERS)
    deferral = marker_pattern(DEFERRAL_MARKERS)
    hits, test_hits, prose_hits, unreadable = [], [], [], []
    for f in iter_files(path):
        try:
            lines = f.read_text(errors="ignore").splitlines()
        except Exception as e:
            unreadable.append(f"{f}: [NOT SCANNED -- could not read: {e}]")
            continue
        is_test = bool(TEST_PATH_RE.search(str(f)))
        # Python docstrings are string literals, not comments, so COMMENT_ONLY_RE cannot
        # see them and docstring prose explaining a design was landing in the main
        # stream. 9 of the 25 acceptance records written for this file's own source were
        # that class. Resolved with the parser rather than another line regex, because a
        # line-based rule cannot tell "inside a triple-quoted string" from ordinary code.
        docstring_lines = _python_docstring_lines(f, lines)
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                entry = f"{f}:{i}: {line.strip()[:160]}"
                is_prose = COMMENT_ONLY_RE.match(line) or i in docstring_lines
                if is_test:
                    test_hits.append(entry)
                elif is_prose and not deferral.search(line):
                    prose_hits.append(entry)
                else:
                    hits.append(entry)
    # Unreadable files go into the main hits stream, not a swallowed skip: a file
    # that wasn't scanned is a gap in coverage, and reporting "0 candidates" while
    # having silently skipped files is precisely the confident-but-incomplete
    # output this whole skill exists to catch.
    return unreadable + hits, test_hits, prose_hits


SIGNAL_TOKENS = {
    "log", "logger", "logging", "warn", "warning", "error", "errors", "critical",
    "print", "exit", "sys", "stderr", "flag", "fail", "failed", "failure",
    "raise", "report", "alert", "notify", "debug", "exception", "traceback",
    "abort", "panic",
    # "fatal" and its Go spellings. Without these, `if err != nil {
    # t.Fatalf("ConfigWrite: %v", err) }` and `if err != nil { fatal(...) }` are
    # both reported as "body does not log or propagate": the body neither returns
    # err nor contains a listed token, because whole-part matching turns "Fatalf"
    # into {"fatalf"} and "fail" therefore never matches it. Measured on the hyphy
    # arm of the rigor pilot, 6 of 119 unique flagged items were this one gap.
    # t.Fatalf both logs the error and aborts, which is the strongest handling a
    # test can give it, so flagging it inverts the check.
    "fatal", "fatalf", "fatalln",
}


def _identifier_parts(name: str):
    """Split an identifier into lowercase word parts, handling snake_case and
    camelCase. Matching whole parts rather than substrings matters: a plain
    `in` test treats logout() as logging (contains "log"), flagship_reset() as
    flagging (contains "flag"), and systemd_restart() as signaling (contains
    "sys"). Measured against a planted set, substring matching missed 3 of 4
    genuinely-silent handlers -- a false negative in the false-negative
    detector, which is the worst place to have one."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    return {p for p in spaced.lower().split("_") if p}


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """A handler counts as silent if its body never raises, logs, prints,
    exits, or otherwise signals failure to anything outside itself."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return False
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
                # also check the base, e.g. logger.error(...) or self.logger.warning(...)
                base = func.value
                if isinstance(base, ast.Name) and _identifier_parts(base.id) & SIGNAL_TOKENS:
                    return False
                if isinstance(base, ast.Attribute) and _identifier_parts(base.attr) & SIGNAL_TOKENS:
                    return False
            if name and _identifier_parts(name) & SIGNAL_TOKENS:
                return False
    return True


GO_DISCARD_RE = re.compile(r"^\s*_\s*(,\s*_\s*)*(:)?=\s*\S")
GO_ERR_CHECK_RE = re.compile(r"if\s+err\s*!=\s*nil\s*\{")


def _go_err_check_body(text: str, open_brace_idx: int):
    """Return the body of an `if err != nil {` block by brace matching."""
    depth, i = 0, open_brace_idx
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_idx + 1 : i]
        i += 1
    return text[open_brace_idx + 1 :]


def _go_silent_failures(text: str, f: Path):
    """Regex + brace-matching heuristic, not a real Go parser -- see module
    docstring. Flags the two idioms that stand in for try/catch in Go: a
    blank-identifier discard of a call's return value, and an err-check whose
    body neither signals nor propagates the error. Prefer `errcheck` over this
    if it's installed; this exists for when it isn't."""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if GO_DISCARD_RE.match(line) and "(" in line:
            hits.append(f"{f}:{i}: blank-identifier discard -- '{line.strip()[:120]}' "
                        f"-- check whether the real pass/fail signal is determined through a "
                        f"different channel nearby (e.g. a second return value already checked) "
                        f"before treating this as a finding")
    for m in GO_ERR_CHECK_RE.finditer(text):
        body = _go_err_check_body(text, m.end() - 1)
        # Strip comments first. Comment prose routinely contains words like "err",
        # "log", or "fail" while the code beneath does none of those things -- and
        # reading a comment as if it were behavior is how a silent failure gets
        # scored as handled. (Caught by the seeded corpus: a seed whose own
        # explanatory comment said "err check" was read as propagating the error.)
        code = re.sub(r"//[^\n]*", "", body)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        # Strip STRING LITERAL CONTENTS for the same reason comments are stripped, and
        # it is the same bug one layer in: the message text is prose, not behaviour.
        # Without this, `if err != nil { note("this failed") }` counts as handled
        # because "failed" is in SIGNAL_TOKENS, so a genuinely silent handler whose
        # message happens to describe a failure scores as signalling. Caught while
        # calibrating corpus control C05b, which passed against the unpatched scanner
        # for exactly this reason -- a control that measured nothing. The quotes are
        # kept so the call itself still tokenises; only the contents go.
        code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', code)
        code = re.sub(r"`[^`]*`", "``", code)
        # Propagating the error is not silent: `return err`, `return nil, err`,
        # `return fmt.Errorf("...: %w", err)` all hand the failure upward.
        #
        # This used to be [^\n]*, which stops at the first newline. A
        # `return fmt.Errorf(...)` call that gofmt wraps across multiple lines -- routine
        # once the format string plus args don't fit one line -- put `return` and `err` on
        # different lines and the check failed to find propagation that was genuinely
        # there. Measured on a real corpus: 7 of 82 events, the highest-frequency false-positive
        # class found in that pass. `code` is already bounded to this err-check's own body
        # by brace matching above, so searching the whole body with DOTALL can't leak past
        # the block's closing brace into unrelated code.
        propagates = re.search(r"\breturn\b.*\berr\b", code, re.DOTALL) is not None
        signals = bool(
            {p for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code)
             for p in _identifier_parts(tok)} & SIGNAL_TOKENS
        )
        if not propagates and not signals:
            line_no = text[: m.start()].count("\n") + 1
            preview = " ".join(code.split())[:80]
            desc = "empty" if not code.strip() else f"body does not log or propagate: {{{preview}}}"
            hits.append(f"{f}:{line_no}: 'if err != nil' -- {desc}")
    return hits


def silent_failures(path: Path):
    hits = []
    for f in iter_files(path):
        if f.suffix == ".py":
            try:
                tree = ast.parse(f.read_text(errors="ignore"), filename=str(f))
            except SyntaxError:
                hits.append(f"{f}: [could not parse -- syntax error, check by hand]")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Try):
                    for handler in node.handlers:
                        if _handler_is_silent(handler):
                            exc_desc = "bare except" if handler.type is None else ast.dump(handler.type)[:60]
                            hits.append(f"{f}:{handler.lineno}: silent handler ({exc_desc}) -- no raise/log/flag found in body")
        elif f.suffix == ".go":
            try:
                text = f.read_text(errors="ignore")
            except Exception as e:
                hits.append(f"{f}: [NOT SCANNED -- could not read: {e}]")
                continue
            hits.extend(_go_silent_failures(text, f))
        else:
            try:
                text = f.read_text(errors="ignore")
            except Exception as e:
                hits.append(f"{f}: [NOT SCANNED -- could not read: {e}]")
                continue
            for m in re.finditer(r"catch\s*\([^)]*\)\s*\{\s*\}", text):
                line_no = text[: m.start()].count("\n") + 1
                hits.append(f"{f}:{line_no}: empty catch block")
    return hits


def undefined_calls(path: Path):
    if path.is_dir():
        hits = []
        for f in path.rglob("*.py"):
            if any(part in {".git", "node_modules", "__pycache__", ".venv", "venv"} for part in f.parts):
                continue
            hits.extend(undefined_calls(f))
        return hits
    if path.suffix != ".py":
        return [f"[skip] {path}: undefined-calls is Python-only"]
    try:
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
    except SyntaxError as e:
        return [f"{path}: [could not parse -- {e}]"]

    known = set(dir(builtins))
    known.update({"self", "cls", "__class__"})
    has_star_import = [False]

    class DefCollector(ast.NodeVisitor):
        def visit_Import(self, node):
            for alias in node.names:
                known.add((alias.asname or alias.name).split(".")[0])
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            for alias in node.names:
                if alias.name == "*":
                    has_star_import[0] = True
                else:
                    known.add(alias.asname or alias.name)
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            known.add(node.name)
            for arg in node.args.args + node.args.kwonlyargs:
                known.add(arg.arg)
            if node.args.vararg:
                known.add(node.args.vararg.arg)
            if node.args.kwarg:
                known.add(node.args.kwarg.arg)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            known.add(node.name)
            self.generic_visit(node)

        def visit_Assign(self, node):
            for target in node.targets:
                for name_node in ast.walk(target):
                    if isinstance(name_node, ast.Name):
                        known.add(name_node.id)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if isinstance(node.target, ast.Name):
                known.add(node.target.id)
            self.generic_visit(node)

        def visit_For(self, node):
            for name_node in ast.walk(node.target):
                if isinstance(name_node, ast.Name):
                    known.add(name_node.id)
            self.generic_visit(node)

        def visit_With(self, node):
            for item in node.items:
                if item.optional_vars:
                    for name_node in ast.walk(item.optional_vars):
                        if isinstance(name_node, ast.Name):
                            known.add(name_node.id)
            self.generic_visit(node)

        def visit_comprehension(self, node):
            for name_node in ast.walk(node.target):
                if isinstance(name_node, ast.Name):
                    known.add(name_node.id)
            self.generic_visit(node)

        def visit_Lambda(self, node):
            for arg in node.args.args:
                known.add(arg.arg)
            self.generic_visit(node)

        def visit_Global(self, node):
            known.update(node.names)

        def visit_Nonlocal(self, node):
            known.update(node.names)

    DefCollector().visit(tree)

    hits = []
    if has_star_import[0]:
        hits.append(f"{path}: [low confidence] this file has a 'from X import *' -- undefined-calls "
                     f"cannot tell a real star-imported name from a typo here, so every hit below "
                     f"may be a false positive. Resolve the star import or check by hand instead.")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in known:
                hits.append(f"{path}:{node.lineno}: call to undefined name '{node.func.id}(...)' -- not imported, "
                             f"not built in, not defined anywhere in this file")
    return hits


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    mode, target = sys.argv[1], Path(sys.argv[2])
    if not target.exists():
        print(f"error: {target} does not exist")
        sys.exit(1)

    def run_proxy_markers():
        hits, test_hits, prose_hits = proxy_markers(target)
        print(f"({len(hits)} candidate{'s' if len(hits) != 1 else ''} -- each needs a judgment call, this is not a grade)")
        for h in hits:
            print(h)
        if test_hits:
            print(f"\n  -- {len(test_hits)} more marker hit(s) inside recognized test paths, shown separately "
                  f"since these are usually the normal, correct use of the word, not a finding:")
            for h in test_hits:
                print(f"  {h}")
        if prose_hits:
            print(f"\n  -- {len(prose_hits)} more implementation-marker hit(s) appearing only inside a "
                  f"comment, shown separately since prose that explains a design routinely uses those "
                  f"words while the code does the opposite. Deferral markers (todo/fixme/for now) are "
                  f"NOT diverted here and stay in the list above:")
            for h in prose_hits:
                print(f"  {h}")

    def run_other(fn):
        hits = fn()
        print(f"({len(hits)} candidate{'s' if len(hits) != 1 else ''} -- each needs a judgment call, this is not a grade)")
        for h in hits:
            print(h)

    runs = {
        "proxy-markers": run_proxy_markers,
        "silent-failures": lambda: run_other(lambda: silent_failures(target)),
        "undefined-calls": lambda: run_other(lambda: undefined_calls(target)),
    }

    if mode == "all":
        for name, fn in runs.items():
            print(f"\n=== {name} ===")
            fn()
    elif mode in runs:
        runs[mode]()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
