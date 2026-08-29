# Codex integration

Zotero MCP runs as a local STDIO server. The default backend is Zotero 10+'s
official Local API at `http://127.0.0.1:23119/api`.

## Add the server

```powershell
codex mcp add zotero -- uvx --from git+https://github.com/DarthVaderW/zotero-mcp.git@v0.3.0 zotero-mcp
```

Equivalent `config.toml`:

```toml
[mcp_servers.zotero]
command = "uvx"
args = [
  "--from",
  "git+https://github.com/DarthVaderW/zotero-mcp.git@v0.3.0",
  "zotero-mcp",
]
default_tools_approval_mode = "writes"
startup_timeout_sec = 30
tool_timeout_sec = 180
```

No Zotero key belongs in Codex configuration for the default local backend.
Zotero displays its own authorization dialog on the first write. A remembered
key is stored locally by the MCP, partitioned by Zotero Server ID.

## Optional environment

```toml
[mcp_servers.zotero.env]
ZOTERO_BACKEND = "local"
ZOTERO_LOCAL_API_URL = "http://127.0.0.1:23119/api"
ZOTERO_LOCAL_LIBRARY_PREFIX = "/users/0"
```

For the remote zotero.org API, set `ZOTERO_BACKEND=web` and provide
`ZOTERO_API_KEY` plus either `ZOTERO_USER_ID` or `ZOTERO_GROUP_ID`.

## Verification

```powershell
codex mcp list
```

Within Codex, `zotero_ping` should report `backend: local_api` and the
running Zotero version. The MCP declares read/write/destructive hints, so
`default_tools_approval_mode = "writes"` prompts for mutating tools while
allowing ordinary search and reads.

## Security

- Keep Local API port 23119 bound to the local computer; do not expose it over
  Tailscale, a reverse proxy, or the public internet.
- The local write key grants changes to every editable library in the current
  Zotero profile. Revoke it from Zotero Settings -> Advanced when needed.
- `zotero_delete_items` moves items to Zotero trash. Permanent deletion is not
  exposed through MCP.
- HTML attachment capture stores one downloaded response, not a recursive
  browser snapshot.
