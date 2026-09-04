#!/usr/bin/env python3
"""FORE-314. Redirects HOME for any bollard test process that invokes a real hook -- in-process
via hook_common.run()/run_body(), or via a real subprocess spawning a guard script as __main__ --
so verdict_ledger.py's module-level VERDICT_LOG (computed once, at import time, from
Path.home()) never resolves to the operator's real ~/.claude/telemetry/verdicts.jsonl during a
test run.

THE DEFECT THIS CLOSES (FORE-314, measured on the live warehouse post-promotion, ingest run 49):
126 rows in hook_verdict carry handler_id values like 'python3 -m unittest' and '-c' -- not a
hook, the guard test harness itself, caught because Python's own `-m unittest` and `-c` entry
points rewrite sys.argv[0] to those literal strings, and verdict_ledger.record()'s default
handler_id is os.path.basename(sys.argv[0]). Fifteen of the 126 arrived in the most recent
ingest run, under an hour before a promotion window -- this is live, not historical residue.

WHY HOME AND NOT A DIRECT VERDICT_LOG OVERRIDE. verdict_ledger.VERDICT_LOG is a plain module
constant, not a function of an environment variable this module could point somewhere else after
import -- reassigning the imported module's own attribute from a test would only affect
IN-PROCESS calls, and does nothing for a spawned subprocess that computes its own VERDICT_LOG
fresh from ITS OWN environment. Redirecting HOME is the one lever that covers both call shapes
with one mechanism, because subprocess.run() inherits the parent's os.environ by default unless a
caller explicitly overrides it -- the same isolation approach FORE-273's negative control used for
dev-harness-run2-5e, which produced clean attribution where timing-based attribution had produced
a false accusation.

ORDERING REQUIREMENT, THE ONE WAY TO USE THIS WRONG. HOME must be redirected BEFORE hook_common
(and therefore verdict_ledger) is imported ANYWHERE in the process -- both modules compute their
paths once, at import time, and re-importing an already-cached module is a no-op. Every test file
that needs this MUST import this module as its FIRST local import, before `import hook_common` /
`import guard_x`. A later import is silently too late and this module cannot detect that from
here -- it can only ever protect what happens after it runs; see the guard clause below, which at
least makes a too-late import loud instead of silently ineffective.

SCOPE, STATED DELIBERATELY RATHER THAN LEFT IMPLICIT. bollard exists as (at least) eleven
independently-diverged copies across this machine (dev-harness, dev-harness-qa, dev-harness-run2
and five sibling worktrees, dev-harness-v2, atlas-sonnet-qa, foreman-v2-qa -- verified by direct
filesystem check, each with a different bollard/*.py file count, so these are not simple mirrors
of one another). ~/.claude/telemetry/verdicts.jsonl is ONE file, shared by the whole machine
account, so any of the eleven copies' test suites can reproduce this exact outage regardless of
this fix. This commit closes it in dev-harness-run2 only -- see the FORE-314 ticket comment this
commit cites for the explicit propagation decision and why the other ten were not touched here.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_fake_home = None


def _ensure_fake_home():
    global _fake_home
    if _fake_home is not None:
        return _fake_home
    _fake_home = tempfile.mkdtemp(prefix="bollard-test-home-")
    atexit.register(shutil.rmtree, _fake_home, True)
    return _fake_home


def isolated_verdict_log_path():
    """The fake ~/.claude/telemetry/verdicts.jsonl this process's tests actually write to, for a
    test that wants to assert against it directly rather than merely against its absence from the
    real one."""
    return Path(_ensure_fake_home()) / ".claude" / "telemetry" / "verdicts.jsonl"


if "hook_common" in sys.modules or "verdict_ledger" in sys.modules:
    # Too late for THIS process -- see the module docstring's ordering requirement. Not fatal (a
    # test file that already relied on the real ledger before this landed keeps working exactly
    # as it did), but loud: a silent no-op here is exactly the kind of gap FORE-314 exists to
    # close, and this is the one place that can still say so.
    print(f"[isolate_verdict_ledger] hook_common or verdict_ledger was already imported before "
          f"this module in {sys.argv[0]!r} -- HOME redirection will NOT take effect for "
          f"verdict_ledger in this process. Import isolate_verdict_ledger before hook_common.",
          file=sys.stderr)
else:
    os.environ["HOME"] = _ensure_fake_home()
