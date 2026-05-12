# Codex Integration

This repository contains a Zotero CLI and a thin Zotero MCP server.

## Token Configuration

For end users, run the local HTTP runtime and configure these values in the
Codex MCP UI as request headers. `.env` remains a developer fallback only.

```text
URL: http://127.0.0.1:6817/mcp
Authorization: Bearer <local Zotero Debug Bridge token>
```

You can use `X-Zotero-Debug-Bridge-Token: <token>` instead of the Authorization
header. Optional fields:

```text
X-Zotero-Debug-Bridge-Url: http://127.0.0.1:23119/debug-bridge/execute
X-Zotero-Library-Id: 1
X-Zotero-API-Key: <Zotero Web API key>
X-Zotero-User-Id: <Zotero user id>
X-Zotero-Group-Id: <Zotero group id>
X-Crossref-Email: <email for CrossRef/Unpaywall>
```

## Codex Config Boundary

Do not put Zotero tokens in `~/.codex/config.toml`.

Codex config should contain only the wrapper path:

```toml
[mcp_servers.zotero]
command = "/bin/bash"
args = ["/Users/<you>/projects/zotero-mcp/scripts/run_zotero_mcp.sh"]
```

Use Codex MCP settings for user-managed token/header fields when available.
Developer command-mode installs can use shell environment variables or an
untracked `.env`.

## Checks

No-secret tests:

```bash
python3 tests/test_zotero_cli.py
uv run python tests/test_header_config.py
```

Local Debug Bridge:

```bash
python3 scripts/zotero.py ping
```

The `ping` command requires Zotero running locally with Debug Bridge enabled.
