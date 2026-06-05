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
python3 -m zotero_mcp.cli attach-snapshot --key ABC12345 --url https://arxiv.org/html/2603.11480v1 --title "arXiv HTML Snapshot"
```

`search-arxiv` is read-only. `capture-arxiv` writes only when the input is an
arXiv ID/URL or `--confirmed-arxiv-id` is supplied; title-only input returns
candidates and does not write to Zotero. arXiv imports check for existing local
items by arXiv ID/DOI/URL before creating a new item; use `--force` only when a
duplicate is intentional.

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
