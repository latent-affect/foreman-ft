# tessera

The TESSERA ticket-tracking package. This directory is an umbrella housing six declared
components (see `ARCHITECTURE.md`'s `components` block) rather than being a component itself —
each is independently scoped and documented at its own path:

- `store/` — the real, transactional ticket store (SQLite-backed).
- `api/` — CLI and HTTP API surfaces over the store.
- `gitops/` — git-integrated stage promotion/rollback.
- `reviewui/` — review-facing UI surface.
- `common/` — shared utilities used across the other components.
- `tessguard/` — the commit-msg gate enforcing real, checkable ticket references.

Added per `FORE-93`/REQ-28's contamination-sweep check: a top-level directory with no
declared component glob covering it as a whole (none of the six above claim `tessera/**`
itself) is correctly flagged for lacking its own provenance README — this file closes that
finding, real content, not contamination.
