# Codex Integration

This repository contains a Zotero CLI and a thin Zotero MCP server.

## Token Configuration

For end users, configure these values in the Codex MCP UI. `.env` remains a
developer fallback only.

```text
ZOTERO_DEBUG_BRIDGE_TOKEN=<local Zotero Debug Bridge token>
```

Optional Web API fields:

```text
ZOTERO_API_KEY=<Zotero Web API key>
ZOTERO_USER_ID=<Zotero user id>
ZOTERO_GROUP_ID=<Zotero group id>
```

## Codex Config Boundary

Do not put Zotero tokens in `~/.codex/config.toml`.

Codex config should contain only the wrapper path:

```toml
[mcp_servers.zotero]
command = "/bin/bash"
args = ["/Users/<you>/projects/zotero-mcp/scripts/run_zotero_mcp.sh"]
```

Use Codex MCP settings for user-managed token/env fields when available.

## Checks

No-secret tests:

```bash
python3 tests/test_zotero_cli.py
```

Local Debug Bridge:

```bash
python3 scripts/zotero.py ping
```

The `ping` command requires Zotero running locally with Debug Bridge enabled.
