# Zotero MCP

Zotero MCP runtime and helper CLI for the research system.

## Layers

- `scripts/zotero.py`: existing CLI, debug-bridge-first for local workflows and Web API for remote workflows.
- `zotero_mcp/server.py`: thin MCP wrapper around the CLI.

Cross-system paper-library rules live in `research-paper-skill`, not here.

## Configure

For end users, configure credentials in the Codex MCP UI. `.env` remains a
developer fallback only.

Required for local Zotero:

```text
ZOTERO_DEBUG_BRIDGE_TOKEN=<local Zotero Debug Bridge token>
```

Optional Web API:

```text
ZOTERO_API_KEY=<Zotero Web API key>
ZOTERO_USER_ID=<Zotero user id>
ZOTERO_GROUP_ID=<Zotero group id>
```

Do not commit `.env`, PDFs, or local Zotero data.

## Codex MCP Config

Developer stdio mode:

```toml
[mcp_servers.zotero]
command = "/bin/bash"
args = ["/Users/<you>/projects/zotero-mcp/scripts/run_zotero_mcp.sh"]
```

Local HTTP runtime mode:

```bash
scripts/run_zotero_mcp_http.sh
```

Then add this URL in Codex MCP UI:

```text
http://127.0.0.1:6817/mcp
```

## Verify

```bash
python3 tests/test_zotero_cli.py
uv run python tests/smoke_test_mcp.py --expect-tool zotero_ping
uv run python tests/smoke_test_http_mcp.py --expect-tool zotero_ping
python3 scripts/zotero.py ping
```

The final command requires Zotero running locally with Debug Bridge enabled.
