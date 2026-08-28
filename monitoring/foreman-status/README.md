# foreman-status — the cross-project roster dashboard

This is the dashboard this repo ships. Per-project pages (one HTML/data/server trio per
bootstrapped dogfood app) were removed from `monitoring/` so the launch repo stays a generic
template, not a bundle of one operator's own per-project dashboards.

One table across every project TESSERA has bootstrapped, sourced through ATLAS's read-only
`atlas.query.facade`, never a hand-rolled join against raw TESSERA or verdict tables.

Runs at `http://127.0.0.1:8424/` after you point the two path constants at your own checkouts.

## Files

- `foreman_status_dashboard.html` — the page itself.
- `foreman_status_dashboard_data.py` — snapshot generator. Reads project identity (codename,
  source root) straight from TESSERA's own `projects` table; ticket/gate-activity numbers come
  from ATLAS's `atlas/query/facade.py` against the live warehouse. If the warehouse's own status
  check isn't clean, the dashboard shows an honest "unavailable" banner per project — never a
  silent zero.
- `foreman_status_dashboard_server.py` — loopback-only HTTP server, no-cache-per-request.

## Setup on your own machine

1. Point `ATLAS_REPO` and `TESSERA_DB` at the top of `foreman_status_dashboard_data.py` to your
   own ATLAS and TESSERA checkouts (both are plain constants, not auto-discovered — see the
   `NOT YET PORTABLE` note in that file).
2. Run:

   ```
   /path/to/venv/bin/python3 /path/to/foreman_status_dashboard_server.py 8424
   ```

3. Open `http://127.0.0.1:8424/`.

Requires a populated ATLAS warehouse (see `atlas/warehouse/`) and a TESSERA `projects` table with
at least one real bootstrapped project — an empty/fresh warehouse renders the "unavailable" state
described above rather than failing.
