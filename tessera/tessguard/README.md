# tessguard

TESSERA-logging enforcement, two layers. Design is in `/path/to/ticket-system/ARCHITECTURE.md`
(the `tessguard` section) and `ARCHITECTURE-REVIEW.md`. This file is a map, not a spec.

- **`audit.py`** (layer 1, non-blocking): binary coverage predicate over an explicitly-named,
  presumed-complete session transcript. No wall-clock/cron auto-trigger — invoke by hand:
  `python3 -m tessera.tessguard.audit <transcript_path> [repo_root]`. Appends every run
  (including its own hard-gate install self-check) to `.foreman/tessguard-audit-log.jsonl`.
- **`gitgate.py`** (layer 2, blocking): the actual enforcement point. Runs as `git`'s own
  pre-commit/pre-push/commit-msg subprocess via `.githooks/` + `core.hooksPath`. Exit code is
  the whole mechanism. commit-msg is T-7 (the commit message must name the ticket).
- **`project_resolve.py`**, **`event_activity.py`**, **`transcript.py`**, **`gitutil.py`**,
  **`config.py`**: shared building blocks both layers call into. See each module's own docstring.

Zero changes to `tessera/store`, `tessera/api`, `tessera/gitops`, `tessera/reviewui`,
`tessera/common` — every call here is read-only against `store`'s existing public methods
(`list_projects`, `get_events_since`) and `common.utc_now_iso()`. (`get_project` was named
in early design drafts but the shipped implementation never calls it — `list_projects()`
alone is sufficient for `project_resolve.py`'s matching; see ARCHITECTURE.md's `tessguard`
section.)

Install (one-time, per clone): `git config core.hooksPath .githooks`.
Uninstall: `git config --unset core.hooksPath` (reverts to git's own default `.git/hooks/`,
which stays empty/`.sample`-only unless something else populates it).

Backtest/review evidence (why these two specific mechanisms, not the four that were tried and
rejected first): `/path/to/ticket-system/docs/tessguard-backtest-evidence/`.
