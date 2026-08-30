# Codex integration

Zotero MCP is a local STDIO server for Zotero 10+'s official Local API.

## Add the server

```powershell
codex mcp add zotero -- uvx --from git+https://github.com/DarthVaderW/zotero-mcp.git@v0.4.0 zotero-mcp
```

Equivalent `config.toml`:

```toml
[mcp_servers.zotero]
command = "uvx"
args = [
  "--from",
  "git+https://github.com/DarthVaderW/zotero-mcp.git@v0.4.0",
  "zotero-mcp",
]
default_tools_approval_mode = "writes"
startup_timeout_sec = 30
tool_timeout_sec = 180
```

No Zotero credential is needed in Codex configuration. Zotero displays its own
authorization dialog on the first write and the MCP remembers an approved key
locally, partitioned by Zotero server ID.

Optional endpoint overrides:

```toml
[mcp_servers.zotero.env]
ZOTERO_LOCAL_API_URL = "http://127.0.0.1:23119/api"
ZOTERO_LOCAL_LIBRARY_PREFIX = "/users/0"
```

## Verification

```powershell
codex mcp list
```

Within Codex, `zotero_ping` should report `backend: local_api` and the running
Zotero version.

## Security

- Keep port 23119 local; do not expose it through Tailscale, a reverse proxy,
  or the public internet.
- A local write key can modify every editable library in the current Zotero
  profile. Revoke it from Zotero Settings -> Advanced when needed.
- `zotero_delete_items` moves items to trash; permanent deletion is not exposed
  through MCP.
- HTML capture stores one downloaded response, not a recursive browser archive.
