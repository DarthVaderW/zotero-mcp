# Zotero MCP

Zotero MCP runtime and helper CLI for the research system.

## Layers

- `scripts/zotero.py`: existing CLI, debug-bridge-first for local workflows and Web API for remote workflows.
- `zotero_mcp/server.py`: thin MCP wrapper around the CLI.

Cross-system paper-library rules live in `research-paper-skill`, not here.

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
CROSSREF_EMAIL=<email for CrossRef/Unpaywall>
```

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

Codex users refresh the local `uvx @stable` cache, then fully restart Codex and
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
uv run python tests/test_header_config.py
uv run python tests/smoke_test_mcp.py --expect-tool zotero_ping
python3 scripts/zotero.py ping
```

The final command requires Zotero running locally with Debug Bridge enabled.

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
