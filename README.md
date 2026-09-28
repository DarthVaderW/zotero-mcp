# Zotero MCP

Local-only MCP server and helper CLI for Zotero 10+.

The server reads and writes through Zotero's official Local API at
`http://127.0.0.1:23119/api`. It does not connect to a remote Zotero library
backend and does not require Zotero account credentials in Codex.

Library access uses the official Local API exclusively; there is no Zotero
Web API or Debug Bridge fallback. DOI/Crossref/arXiv lookups and PDF or HTML
downloads still contact their external sources, then save through the Local API.

## Capabilities

- Search and inspect items, collections, tags, children, attachments, and
  Zotero full-text caches.
- Import DOI, ISBN, PMID, and arXiv records with duplicate checks.
- Discover open-access PDFs during identifier import and attach local files.
- Create and update items with object-version preconditions.
- Collection changes use the version of the item that supplied the original
  collection list. Concurrent changes return HTTP 412; re-read before retrying.
- Export BibTeX, RIS, and CSL JSON; report missing PDFs; find missing DOIs.
- Store one downloaded HTML document as an attachment.
- Move items to Zotero trash. Permanent deletion is not exposed through MCP.

## Requirements

1. Zotero 10 or newer.
2. Enable `Settings -> Advanced -> Allow other applications on this computer
   to communicate with Zotero`.
3. Install [uv](https://docs.astral.sh/uv/).

The Local API is intended for programs on the same computer as Zotero. Do not
forward or publicly expose port 23119.

## Install in Codex

```powershell
codex mcp add zotero -- uvx --from git+https://github.com/DarthVaderW/zotero-mcp.git@stable zotero-mcp
```

No Zotero credential belongs in the Codex MCP entry. On the first write,
Zotero displays an authorization dialog. Choose **Always Allow** to remember
the permission.

The remembered local key is stored per Zotero server ID:

- Windows: `%LOCALAPPDATA%\zotero-mcp\credentials.json`
- macOS: `~/Library/Application Support/zotero-mcp/credentials.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/zotero-mcp/credentials.json`

The key is never printed. Revoke remembered permissions with **Clear Write
Authorizations** in Zotero Settings -> Advanced.

## Configuration

Ordinary installations need no environment variables. Optional overrides are:

```text
ZOTERO_LOCAL_API_URL=http://127.0.0.1:23119/api
ZOTERO_LOCAL_LIBRARY_PREFIX=/users/0
ZOTERO_LOCAL_API_APP_NAME=Zotero MCP
ZOTERO_MCP_CREDENTIALS_FILE=<custom credential file>
ZOTERO_LOCAL_API_KEY=<pre-authorized local key>
CROSSREF_EMAIL=<contact email for Crossref and Unpaywall>
```

## Primary MCP workflows

- Read: `zotero_ping`, `zotero_search_items`, `zotero_get_item`,
  `zotero_list_items`, `zotero_list_collections`, `zotero_list_tags`,
  `zotero_get_children`, `zotero_get_attachment_text`, `zotero_check_pdfs`.
- Intake: `zotero_import_by_identifier`, `zotero_search_arxiv`,
  `zotero_capture_arxiv`, `zotero_attach_arxiv_sidecars`.
- Write: `zotero_create_item`, `zotero_update_item`, `zotero_attach_pdf`,
  `zotero_attach_snapshot`, `zotero_find_dois`, `zotero_delete_items`.
- Output and checks: `zotero_export`, `zotero_crossref`.

`zotero_capture_arxiv` accepts an arXiv ID or URL directly. Title-only input
returns candidates and writes only after `confirmed_arxiv_id` is supplied.

## CLI examples

```powershell
uv run python -m zotero_mcp.cli ping
uv run python -m zotero_mcp.cli search "retargeting"
uv run python -m zotero_mcp.cli import-doi 10.1145/3610548.3618247
uv run python -m zotero_mcp.cli capture-arxiv 2510.02252
uv run python -m zotero_mcp.cli check-pdfs
```

## Development and verification

```powershell
uv sync
uv run python -m unittest discover -s tests -v
uv run python tests/smoke_test_mcp.py --expect-tool zotero_ping
uv run python tests/live_local_api_smoke.py
```

The unit and MCP smoke tests do not need Zotero credentials. The live smoke
test creates, reads, updates, and then moves its temporary item to Zotero trash.

## Source layout

- `zotero_mcp/local_api.py`: official Local API HTTP client, per-instance
  authorization, versioned writes, and upload protocol.
- `zotero_mcp/local_library.py`: item, collection, attachment and trash
  helpers over that client, including payload normalization.
- `zotero_mcp/library_ops.py`: update, export, DOI patch, and PDF coverage
  workflows.
- `zotero_mcp/basic_ops.py`: direct library, attachment-text, and delete operations shared by MCP and CLI.
- `zotero_mcp/operations.py`: identifier and arXiv intake, including local PDF discovery and duplicate handling.
- `zotero_mcp/arxiv_metadata.py`: arXiv search and external metadata retrieval; `arxiv.py` imports papers and attaches PDF/HTML sidecars through the Local API.
- `zotero_mcp/server.py`: MCP tools and annotations.
- `zotero_mcp/identifiers.py`, `pdf_discovery.py`: identifier metadata and open-access discovery.
