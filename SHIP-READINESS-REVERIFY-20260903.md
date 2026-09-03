# Ship-readiness re-verification — R14/R15 guard-chain workstream, dogfood scope, 2026-09-03

**Verdict: GO** for dogfood scope across all six live trees (`dev-harness-run2`, `-e1`, `-e2`,
`-qa-security`, `-qa-general`, `-qa-evasion`), with one disclosed, bounded scope boundary named in
§3 and two non-blocking housekeeping items in §4. Not a clean Go with nothing to say — a Go that
names what it is accepting.

Gate lens: Marcus Webb, embodied in session `dev-harness-run2-qa-security-7d`. Scope: REQ-57 bounded
re-verification of the two items held open by the first full pass (`SHIP-READINESS-GATE-20260903.md`,
dev-harness-run2 commit `9aeb9b2`, verdict RECYCLE), plus the blast radius of what landed since. Not
a re-derivation of that pass's full scope.

## 0. Independence

Zero authorship stake in any commit under review here (`4545369`, `e7f3272`, `3513903`, `9bf19d3`,
`e9315f6`). This session's only prior output in this worktree is three handoff docs (`HANDOFF-
qa-security-{b8,94,7d}-20260903.md`), none of which touch guard code, `.claude/settings.json`, or
either held item. Everything below is a fresh execution against live state, not a re-read of the
dispatch message or of `SHIP-READINESS-GATE-20260903.md`'s own claims.

## 1. Held item 1 — DEVH-95 (e1/e2 zero Foreman hooks)

**Re-verified independently, holds.** Did not take the dispatch's 19-hooks-per-tree number on
trust — recounted from the live file:

- `/Users/m5/dev/dev-harness-run2-e1/.claude/settings.json` and `.../e2/...`: both exist, mtime
  2026-09-03 12:59 (matches the claimed `register_e1_e2.py` run time). 13 entries on
  `Edit|Write|Bash`, 1 each on `WebFetch`/`Agent`/`AskUserQuestion`, 1 each on PostToolUse
  `ScheduleWakeup`/`Write`, 1 on `Stop` = 19, both trees.
- All 19 command targets in `e1`'s file resolve to real files on disk (checked every path, 19/19).
  `e2`'s 6 own-bollard targets independently checked, 6/6 exist.
- `diff`'d `e1` vs `e2` settings.json: the only deltas are the 6 `bollard`-path commands, each
  correctly retargeted to its own tree (`.../dev-harness-run2-e1/bollard/...` vs
  `.../dev-harness-run2-e2/bollard/...`) — the exact failure mode (leaking to a shared or wrong
  tree) that would be silent if copied carelessly.
- **Fired real payloads**, not read the code: a `rm -rf` recursive-delete against `e1`'s own
  `guard_destructive.py` was denied (`recursive_force_delete`); a benign `ls` against the same
  guard passed clean (exit 0, no output). Both ways, both real subprocess results.

No divergence from the dispatch's numbers. This item is closed on independently-produced evidence.

## 2. Held item 2 — DEVH-66 (run-2 disposition record)

**Re-verified independently, holds.** The claim was a commit message (`e7f3272`); I read the
artifact it says it produced, not the message:

- `/Users/m5/dev/dev-harness-run2/DEVH-66-DISPOSITION-RECORD.md` exists, 229 lines, real content —
  a sourcing rule (ticket status / real citing commit / ticket comment only, no session report or
  handoff treated as evidence, including its own author's), a disposition table, and named residual
  findings (`CLOSED-UNTRACEABLE` = 0; 14 tickets filed overnight with zero activity since).
- This was the second blocker in the original RECYCLE gate ("no record artifact exists anywhere in
  the worktree") — that condition is now false, checked directly, not inferred from the commit log.

## 3. Blast radius of what landed since — and the call that's actually mine

**DEVH-92 Class C** (`3513903`, `9bf19d3`, dev-harness-run2): enumerates `doas`/`pkexec`/`sudoedit`/
`run0` into `guard_allowlist.py` and `find <dir> -delete` into `guard_destructive.py`, plus a
`KNOWN_NOT_ENUMERATED` boundary declaration pinned by `test_class_c_coverage_boundary.py`. Both
commits real, both diffs read directly, not summarized.

**FORE-291** (`e9315f6`, claude-hooks-v2): `git merge-base --is-ancestor e9315f6 master` returns
0 — confirmed on master, not just committed to a branch nobody's on.

**The disclosure, checked, not taken on trust**: `bollard/` is a separate working copy in all six
trees (confirmed by listing each). Grepping each tree's `guard_allowlist.py` for the new `doas`
enumeration: `dev-harness-run2`, `-e1`, `-e2` all carry it; `-qa-security`, `-qa-general`,
`-qa-evasion` do not. Then the live behavioral proof, fired at two real trees rather than inferred
from the grep: a `doas -u root id` payload against **this worktree's own** `guard_allowlist.py`
(`qa-security`, pre-fix) produces no denial and exits clean — the evasion passes silently, right now,
in the tree this review is running from. The identical payload against `e1`'s `guard_allowlist.py`
(post-fix) is denied with `privilege_escalation_doas`. The split is exactly as disclosed, and I
verified it from the losing side of the gap, not the winning one.

**My call**: acceptable for dogfood, not a blocker, for three reasons specific to blast radius, not
to convenience —

1. The original RECYCLE gate already accepted "5 of 10 in-scope evasion-taxonomy patterns are clean
   misses" as non-blocking dogfood risk (§3 of `SHIP-READINESS-GATE-20260903.md`). Tonight's fix
   closes 2 of those 10 patterns in 3 of 6 trees and leaves the other 3 trees exactly at the
   already-accepted baseline — it does not make the QA forks worse than the state Go was already
   granted against once.
2. Propagating the fix into three worktrees with sessions actively working in them mid-run would
   drag ~30 unrelated commits into those trees' history, a real and immediate disruption cost, for
   a gap that is bounded, disclosed, and already tracked (DEVH-92, open, correctly so).
3. The boundary is enforced mechanically (`KNOWN_NOT_ENUMERATED` + a pinned test), not by a comment
   that can rot — the strongest available form of "stated honestly" this codebase has, even without
   a prose coverage summary anywhere (checked: no `bollard/*.md` exists, no top-level doc states the
   5/10 figure in prose — code-level declaration only).

This would not be my call if the gap were novel or the scope were production. It is neither.

## 4. Non-blocking, do not let this get silently dropped

- **DEVH-66 and DEVH-95 are both still `open` in the live TESSERA DB** (`ticket-system/data/
  tessera.db`, checked directly) despite both having real, independently-verified fixes landed.
  DEVH-68 (the third item from the original gate's §7) is correctly `closed` as of 18:30 today — so
  this isn't a systemic gap, just these two. Ticket status lagging real state is the exact shape of
  problem this whole review exists to catch; close both to match reality.
- **Corrected in-line, not carried forward wrong**: this section originally claimed no TESSERA
  project resolves for `qa-security` at all. Attempting the real commit disproved that — the `projects`
  table indeed has no dedicated row for `qa-security`/`qa-general`/`qa-evasion`, but `tessguard`'s
  commit-msg gate resolved this repo to a real registered project (`DEVH`) via some fallback and
  blocked only because my first commit message cited no ticket. Re-committing with `DEVH-95, DEVH-66:
  ...` in the message passed clean, no override used. So DEVH-95 and DEVH-66's fixes are now recorded
  in git for real (`a722ee3`), and the actual, narrower gap is: **`register_qa_worktrees.py` (DEVH-90)
  wired `.claude/settings.json` in the three QA forks but did not give any of them their own TESSERA
  project row** — `qa-security-b8`'s two staged, unrelated test files (`bollard/test_guard_
  shared_pattern_near_miss.py`, `bollard/test_guard_session_level_sequences.py`) remain uncommitted;
  I did not commit them myself since they aren't this pass's work and b8's handoff should stay the
  record of what they are. Worth a follow-up ticket, not a Go/Hold blocker: this fallback resolution
  is undocumented and its exact mechanism (which project, by what rule) wasn't chased further here —
  bounded pass.

## Files referenced above (full paths)

- `/Users/m5/dev/dev-harness-run2/SHIP-READINESS-GATE-20260903.md` (first pass, RECYCLE)
- `/Users/m5/dev/dev-harness-run2/DEVH-66-DISPOSITION-RECORD.md`
- `/Users/m5/dev/dev-harness-run2-e1/.claude/settings.json`
- `/Users/m5/dev/dev-harness-run2-e2/.claude/settings.json`
- `/Users/m5/dev/dev-harness-run2-e1/bollard/guard_destructive.py`, `guard_allowlist.py`
- `/Users/m5/dev/dev-harness-run2-qa-security/bollard/guard_allowlist.py`
- `/Users/m5/dev/ticket-system/data/tessera.db`
- `/Users/m5/dev/foreman-v2/docs/PRD.md` (REQ-57, line 2570)
