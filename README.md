# Zotero MCP

Zotero MCP runtime and helper CLI for the research system.

## Layers

- `scripts/zotero.py`: existing CLI, debug-bridge-first for local workflows and Web API for remote workflows.
- `zotero_mcp/server.py`: thin MCP wrapper around the CLI.

Cross-system paper-library rules live in `research-paper-skill`, not here.

## Configure

For end users, run the local HTTP runtime and configure credentials in the Codex
MCP UI as request headers. `.env` remains a developer fallback only.

Required header for local Zotero:

```text
Authorization: Bearer <local Zotero Debug Bridge token>
```

Optional headers:

```text
X-Zotero-Debug-Bridge-Token: <local Zotero Debug Bridge token>
X-Zotero-Debug-Bridge-Url: http://127.0.0.1:23119/debug-bridge/execute
X-Zotero-Library-Id: 1
X-Zotero-API-Key: <Zotero Web API key>
X-Zotero-User-Id: <Zotero user id>
X-Zotero-Group-Id: <Zotero group id>
X-Crossref-Email: <email for CrossRef/Unpaywall>
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

Add `Authorization: Bearer <local Zotero Debug Bridge token>` or
`X-Zotero-Debug-Bridge-Token: <local Zotero Debug Bridge token>` in Codex UI.
Developer command-mode installs may still use local environment variables or an
untracked `.env`.

## Verify

```bash
python3 tests/test_zotero_cli.py
uv run python tests/test_header_config.py
uv run python tests/smoke_test_mcp.py --expect-tool zotero_ping
uv run python tests/smoke_test_http_mcp.py --expect-tool zotero_ping
python3 scripts/zotero.py ping
```

The final command requires Zotero running locally with Debug Bridge enabled.
