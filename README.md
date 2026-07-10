# Zotero MCP

General-purpose Zotero MCP server and helper CLI.

## Layers

- `zotero_mcp/cli.py`: CLI-only argument parsing and human/JSON presentation.
- `zotero_mcp/server.py`: MCP tool surface that calls structured operations directly.
- `zotero_mcp/operations.py`: structured operation entrypoints shared by the MCP server and CLI.
- `zotero_mcp/local_ops.py`: local Debug Bridge create/attach operations.
- `zotero_mcp/arxiv.py`: arXiv metadata import workflow, PDF attachment, and arXiv HTML snapshot discovery.
- `zotero_mcp/pdfs.py`: shared PDF download helpers.
- `zotero_mcp/identifiers.py`: DOI/ISBN/PMID translation and add/batch-add operations.
- `zotero_mcp/pdf_discovery.py`: remote PDF discovery and Zotero Web API attachment upload/linking.
- `zotero_mcp/doi_ops.py`: CrossRef citation checks and missing-DOI discovery.
- `zotero_mcp/web_items.py`: Web API item update, export, and PDF coverage reports.
- `zotero_mcp/metadata.py`: shared metadata formatting and matching helpers.
- `zotero_mcp/debug_bridge.py`: local Zotero Debug Bridge transport and data helpers.
- `zotero_mcp/web_api.py`: Zotero Web API requests, pagination, and retry handling.
- `zotero_mcp/validators.py`: identifier and payload validation helpers.

For MCP tools that accept file paths, absolute paths are preferred. Relative
paths are resolved from the repository root to preserve the earlier CLI-wrapper
behavior.

## Install

This repository ships one stdio MCP server. Codex and Claude Code use the same
server, but the ordinary client setup differs.

Prerequisite:

```bash
uv --version
```

If `uv` is not found, install it first. Official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Homebrew is also fine on macOS:

```bash
brew install uv
```

After installing, restart Codex or Claude Code so the app can see the updated
PATH.

Codex recommended path: add a custom STDIO MCP server in the Codex MCP Servers
settings.

```text
Name: zotero
Command: uvx
Args:
  --from
  git+https://github.com/DarthVaderW/zotero-mcp.git@stable
  zotero-mcp
```

Claude Code recommended path: use the GUI Personal plugins flow, or the
equivalent CLI plugin commands.

```text
Customize -> Personal plugins -> Add
DarthVaderW/zotero-mcp
```

## Configure

Required local values:

```text
ZOTERO_DEBUG_BRIDGE_TOKEN=<local Zotero Debug Bridge token>
ZOTERO_DEBUG_BRIDGE_URL=http://127.0.0.1:23119/debug-bridge/execute
ZOTERO_LIBRARY_ID=1
```

Optional Web API values:

```text
ZOTERO_API_KEY=<Zotero Web API key>
ZOTERO_USER_ID=<Zotero user id>
ZOTERO_GROUP_ID=<Zotero group id>
CROSSREF_EMAIL=<real contact email for CrossRef/Unpaywall>
```

Set `CROSSREF_EMAIL` to a real contact email when using PDF discovery:
Unpaywall requires it, and CrossRef uses it for polite requests.

Codex users enter these in the custom STDIO MCP configuration. Claude Code users
enter them through the plugin's `userConfig` prompt. For current Claude Code
compatibility, tokens are stored with the other plugin options instead of using
Claude's `sensitive` userConfig mode. Do not commit `.env`, PDFs, or local
Zotero data.

Codex plugin manifests are still kept in this repository for packaging,
marketplace testing, and possible future Codex plugin improvements. They are not
the ordinary Codex install path right now because plugin-provided MCP rows are
read-only in Codex and do not expose an editable token/config form.

## MCP Tools

The MCP server registers 25 tools. They are grouped below by which backend
each one talks to. Local tools go through the local Zotero Debug Bridge
(`ZOTERO_DEBUG_BRIDGE_TOKEN`; Zotero must be running) and never touch the
Zotero Web API. Remote tools go through the Zotero Web API
(`ZOTERO_API_KEY` + `ZOTERO_USER_ID`/`ZOTERO_GROUP_ID`) and work without
Zotero running locally. The two arXiv tools talk to arxiv.org directly and
only write to the local library once an arXiv ID is confirmed.

### Local read-only (Debug Bridge)

| Tool | Description |
| --- | --- |
| `zotero_ping` | Check local Zotero Debug Bridge connectivity. |
| `zotero_search_items` | Search local Zotero items through the Debug Bridge. |
| `zotero_get_item` | Get a local Zotero item and its children by item key. |
| `zotero_list_items` | List local Zotero items, optionally scoped to a collection key. |
| `zotero_list_collections` | List local Zotero collections (key and name). |
| `zotero_list_tags` | List local Zotero tags. |
| `zotero_get_children` | List child items (attachments and notes) of a local parent item. |
| `zotero_get_attachment_text` | Read local attachment text, preferring Zotero's full-text cache when available. |

### Local write/import (Debug Bridge)

| Tool | Description |
| --- | --- |
| `zotero_import_arxiv` | Import or reuse an arXiv item locally, attach the PDF, and try arXiv HTML. |
| `zotero_import_by_identifier` | Import or reuse an item by DOI/ISBN/PMID through the local Debug Bridge; does not require `ZOTERO_API_KEY`. |
| `zotero_attach_arxiv_sidecars` | Attach missing arXiv PDF/HTML sidecars to a known local item. |
| `zotero_create_item` | Create a local Zotero item from metadata JSON. |
| `zotero_attach_pdf` | Attach a local PDF file to a Zotero parent item. |
| `zotero_attach_snapshot` | Attach a web page snapshot from a URL to a Zotero parent item. |
| `zotero_delete_items` | Move local items to the trash by item key (permanent delete is CLI-only). |

### Remote (Zotero Web API)

| Tool | Description |
| --- | --- |
| `zotero_fetch_pdf` | Fetch OA PDFs remotely by DOI, or attach a local PDF when `key` and `file` are both given. |
| `zotero_check_pdfs` | Report which library items have or are missing PDF attachments. |
| `zotero_add_by_identifier` | Add an item to the library by DOI/ISBN/PMID via the Web API. |
| `zotero_update_item` | Update title/date/DOI/url/tags/collection on a library item. |
| `zotero_export` | Export library items as bibtex, ris, or csljson. |
| `zotero_batch_add` | Batch-add identifiers (one per line in a file) via the Web API. |
| `zotero_find_dois` | Find missing DOIs via CrossRef; read-only unless `apply=True` writes them. |
| `zotero_crossref` | Cross-reference "Author (Year)" citations in a file against the library. |

### arXiv / dual-mode

| Tool | Description |
| --- | --- |
| `zotero_search_arxiv` | Search arXiv candidates by ID/URL or paper title. Read-only; talks to arxiv.org directly. |
| `zotero_capture_arxiv` | Capture an arXiv paper once an ID/URL or confirmed candidate is available; title-only input stays read-only until confirmed. |

## Upgrade

Codex users refresh the local `uvx @stable` cache, then fully restart Codex.
Existing threads can see refreshed MCP tools after restart; if they do not,
open a new thread:

```bash
uvx --refresh --from git+https://github.com/DarthVaderW/zotero-mcp.git@stable zotero-mcp --help >/dev/null
```

Claude Code users update the marketplace/plugin, then restart Claude Code:

```bash
claude plugin marketplace update darthvaderw-zotero-mcp
claude plugin update zotero-mcp@darthvaderw-zotero-mcp
```

## Developer Command Mode

For source development, point Codex or Claude Code at the local checkout:

```toml
[mcp_servers.zotero]
command = "/bin/bash"
args = ["/Users/<you>/projects/zotero-mcp/scripts/run_zotero_mcp.sh"]
```

## Verify

```bash
python3 tests/test_zotero_cli.py
uv run python tests/smoke_test_mcp.py --expect-tool zotero_ping
python3 -m zotero_mcp.cli ping
```

The final command requires Zotero running locally with Debug Bridge enabled.

Useful local commands:

```bash
python3 -m zotero_mcp.cli search-arxiv "Retargeting Matters"
python3 -m zotero_mcp.cli capture-arxiv "Retargeting Matters" --confirmed-arxiv-id 2510.02252 --collection "Humanoid Retargeting"
python3 -m zotero_mcp.cli arxiv 2603.11480 --collection "Humanoid Retargeting"
python3 -m zotero_mcp.cli import-doi 10.1145/3610548.3618247 --collection "Humanoid Retargeting"
python3 -m zotero_mcp.cli attach-arxiv-sidecars --key ABC12345 --arxiv 2310.03930
python3 -m zotero_mcp.cli attach-snapshot --key ABC12345 --url https://arxiv.org/html/2603.11480v1 --title "arXiv HTML Snapshot"
```

`search-arxiv` is read-only. `capture-arxiv` writes only when the input is an
arXiv ID/URL or `--confirmed-arxiv-id` is supplied; title-only input returns
candidates and does not write to Zotero. arXiv imports check for existing local
items by arXiv ID/DOI/URL before creating a new item. Existing items are reused
and missing sidecars are topped up: PDF is attached when absent, and arXiv HTML
snapshot is attached when available unless `--no-html` is set. Use `--force`
only when a duplicate parent item is intentional.

`import-doi`, `import-isbn`, and `import-pmid` resolve identifier metadata but
write through the local Zotero Debug Bridge, not the Zotero Web API. They reuse
local duplicates when possible, create the parent item locally, add it to a
collection locally, and try to attach an open-access PDF locally. If no open PDF
is found, the created item key is still returned with `pdfStatus:
needs_user_file`; attach a user-provided PDF later with `attach-pdf`.
`attach-arxiv-sidecars` is for the common final-publication case: keep one
canonical parent item and attach missing arXiv PDF/HTML sidecars to that item.

For model reading workflows, use the MCP tool `zotero_get_attachment_text` with
an attachment key. It asks local Zotero for the attachment's real file path,
then prefers Zotero's `.zotero-ft-cache` when present. Store Zotero attachment
keys in upstream notes or tables instead of hard-coding local
`Zotero/storage/...` paths. The matching CLI command is:

```bash
python3 -m zotero_mcp.cli attachment-text <attachment-key> --max-chars 20000
```

## Troubleshooting

If Claude Code reports that the MCP failed to start, check `uv` before
re-entering tokens:

```bash
command -v uv
uv --version
```

`uv: command not found` means the MCP process never started. Install `uv`,
restart Claude Code, then retry the plugin. A missing `uv` can look like a
token/config problem, but the token is not used until the MCP server actually
starts.

If `uv` works but `zotero_ping` fails, then check:

```text
Zotero is running
Zotero Debug Bridge is enabled
ZOTERO_DEBUG_BRIDGE_URL is http://127.0.0.1:23119/debug-bridge/execute unless changed
ZOTERO_DEBUG_BRIDGE_TOKEN matches the local Debug Bridge token
```
