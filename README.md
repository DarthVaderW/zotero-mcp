# Zotero MCP

Local-first Zotero MCP server and helper CLI.

Version 0.3 uses Zotero 10+'s official Local API for local reads and writes.
The Debug Bridge plugin and token are no longer required.

## What it does

- Search, list, and inspect local Zotero items, collections, tags, children,
  attachments, and Zotero full-text caches.
- Create and update items with Zotero object-version preconditions.
- Import DOI, ISBN, PMID, and arXiv metadata with duplicate checks.
- Upload local PDFs and other stored files through Zotero's official
  three-phase file-upload flow.
- Store a downloaded HTML document as an `imported_url` attachment.
- Move items to Zotero trash. Permanent deletion is intentionally unavailable
  through MCP.
- Optionally use the zotero.org Web API by setting `ZOTERO_BACKEND=web`.

## Requirements

1. Zotero 10 or newer.
2. In Zotero, enable:
   `Settings -> Advanced -> Allow other applications on this computer to communicate with Zotero`.
3. Install [uv](https://docs.astral.sh/uv/).

The Local API is intended for programs on the same computer as Zotero. Do not
forward or publicly expose port 23119.

## Install in Codex

Add a local STDIO MCP server:

```powershell
codex mcp add zotero -- uvx --from git+https://github.com/DarthVaderW/zotero-mcp.git@v0.3.0 zotero-mcp
```

No Zotero token is needed in Codex configuration. On the first write, Zotero
opens its own authorization dialog. Choose **Always Allow** if you want future
writes to run without another dialog.

The remembered local key is stored per `Zotero-Server-ID` outside the package:

- Windows: `%LOCALAPPDATA%\zotero-mcp\credentials.json`
- macOS: `~/Library/Application Support/zotero-mcp/credentials.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/zotero-mcp/credentials.json`

The key is never printed by the CLI or MCP. Zotero can revoke remembered keys
with **Clear Write Authorizations** in Settings -> Advanced.

Codex, its desktop app, and IDE extension share the same MCP configuration on a
Codex host. Restart the MCP server after installation or upgrade.

## Configuration

The default requires no environment variables:

```text
ZOTERO_BACKEND=local
ZOTERO_LOCAL_API_URL=http://127.0.0.1:23119/api
ZOTERO_LOCAL_LIBRARY_PREFIX=/users/0
```

Optional values:

```text
ZOTERO_LOCAL_API_APP_NAME=Zotero MCP
ZOTERO_MCP_CREDENTIALS_FILE=<custom credential file>
CROSSREF_EMAIL=<contact email for CrossRef and Unpaywall>
```

`ZOTERO_LOCAL_API_KEY` can override automatic authorization, but ordinary
local installs should let Zotero grant and store the key.

To use the zotero.org API instead:

```text
ZOTERO_BACKEND=web
ZOTERO_API_KEY=<Web API key>
ZOTERO_USER_ID=<user id>
# Or use ZOTERO_GROUP_ID instead of ZOTERO_USER_ID.
```

## Snapshot behavior

`zotero_attach_snapshot` downloads one HTML response and uploads it as an
`imported_url` attachment. It does not recursively archive images, scripts,
stylesheets, or browser state. This deliberate limitation removes the last
Debug Bridge dependency. Use the Zotero Connector when a browser-complete
snapshot is required.

## Main MCP workflows

- `zotero_search_items`, `zotero_get_item`,
  `zotero_get_attachment_text`
- `zotero_import_by_identifier`
- `zotero_search_arxiv`, `zotero_capture_arxiv`,
  `zotero_attach_arxiv_sidecars`
- `zotero_create_item`, `zotero_update_item`
- `zotero_attach_pdf`, `zotero_attach_snapshot`
- `zotero_delete_items`

Existing 0.2 tool names remain available for compatibility. Codex tool
annotations distinguish read-only, write, network, and destructive operations.

## CLI examples

```powershell
uv run python -m zotero_mcp.cli ping
uv run python -m zotero_mcp.cli search "retargeting"
uv run python -m zotero_mcp.cli import-doi 10.1145/3610548.3618247
uv run python -m zotero_mcp.cli capture-arxiv 2510.02252
```

Title-only arXiv capture is read-only and returns candidates. It writes only
after an arXiv ID/URL or `--confirmed-arxiv-id` is supplied.

## Development and verification

```powershell
uv sync
uv run python -m unittest discover -s tests -v
uv run python tests/smoke_test_mcp.py --expect-tool zotero_ping
uv run python tests/live_local_api_smoke.py
```

The first two checks use no Zotero secrets. The live smoke test requires Zotero
and performs create, note, attachment, update, readback, and trash operations.

## Upgrade

```powershell
codex mcp remove zotero
codex mcp add zotero -- uvx --from git+https://github.com/DarthVaderW/zotero-mcp.git@v0.3.0 zotero-mcp
```

Replace `v0.3.0` with the new release tag, then restart the Zotero MCP server
or Codex. Pinning a release tag avoids ambiguity from a cached mutable branch.

## Source layout

- `zotero_mcp/local_api.py`: Local API authorization, credentials, reads,
  versioned writes, file uploads, attachment paths, and trash semantics.
- `zotero_mcp/web_api.py`: unified Local/Web API request surface.
- `zotero_mcp/operations.py`: structured workflows shared by MCP and CLI.
- `zotero_mcp/server.py`: MCP tools, annotations, and server instructions.
- `zotero_mcp/arxiv.py`, `identifiers.py`, `pdf_discovery.py`: research
  import and attachment workflows.

Official references:

- [Zotero Local API](https://www.zotero.org/support/dev/web_api/v3/local_api)
- [Zotero write requests](https://www.zotero.org/support/dev/web_api/v3/write_requests)
- [Zotero file uploads](https://www.zotero.org/support/dev/web_api/v3/file_upload)
