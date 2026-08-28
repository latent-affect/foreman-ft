# atlas MCP server

A local stdio MCP server exposing `atlas.query.facade.QueryFacade` (read-only, view-gated,
trust-gate-refusing) as three MCP tools: `atlas_status`, `atlas_list_views`, `atlas_query_view`.
See the design notes for the rationale -- this file adds no safety logic beyond what
`atlas/query/facade.py` already enforces, it only translates that facade's calls into MCP
tool responses.

## Setup

`mcp` requires Python >=3.10. System `python3` on this machine is 3.9.6, so this package gets
its own venv rather than sharing the repo's (nonexistent) main one:

```
/opt/homebrew/bin/python3.11 -m venv /path/to/dev-harness/.venv-mcp
/path/to/dev-harness/.venv-mcp/bin/pip install -r /path/to/dev-harness/atlas/mcp/requirements.txt
```

`.venv-mcp/` is gitignored; `requirements.txt` is what's committed.

## Registering with Claude Code

```
claude mcp add --scope user atlas -- /path/to/dev-harness/.venv-mcp/bin/python3 /path/to/dev-harness/atlas/mcp/server.py
```

User scope, not project scope, so any session on this machine can reach it regardless of
which project it's rooted in -- verify with `claude mcp get atlas` (expect `Status: Connected`).
A session that was already running when this was registered needs a restart to see the new
tools; a fresh session picks them up immediately.

## Overriding the warehouse path

`ATLAS_DB_PATH` env var, if the warehouse ever moves again (it already has once). Defaults to
`atlas/warehouse/atlas.db` relative to this repo's root.

## Verifying it after any change

Don't trust a code read alone -- self-test as a real MCP client:

```
/path/to/dev-harness/.venv-mcp/bin/python3 -m mcp.client /path/to/dev-harness/.venv-mcp/bin/python3 /path/to/dev-harness/atlas/mcp/server.py
```

confirms the initialize handshake only. For real tool-call coverage (list_tools, a valid
atlas_query_view call, a refused raw-table name, an unknown tool), see the test pattern in
this session's scratchpad -- `ClientSession`/`stdio_client` from `mcp.client`, not a hand-rolled
JSON-RPC client. Re-run something equivalent after any change to server.py or facade.py before
trusting either.
