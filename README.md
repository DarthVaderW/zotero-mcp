# Zotero MCP

Zotero MCP runtime and helper CLI for the research system.

## Layers

- `scripts/zotero.py`: existing CLI, debug-bridge-first for local workflows and Web API for remote workflows.
- `zotero_mcp/server.py`: thin MCP wrapper around the CLI.

Cross-system paper-library rules live in `research-paper-skill`, not here.

## Install

This repository ships the same stdio MCP server for Codex and Claude Code. The
MCP implementation is shared; only the plugin shell differs by client.

Prerequisite:

```bash
uvx --version
```

If `uvx` is not found, install `uv` first. Official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Homebrew is also fine on macOS:

```bash
brew install uv
```

After installing, restart Codex or Claude Code so the app can see the updated
PATH.

Codex:

```bash
codex plugin marketplace add DarthVaderW/zotero-mcp --ref stable \
  --sparse .agents/plugins \
  --sparse plugins/zotero-mcp
codex plugin add zotero-mcp@zotero-mcp
```

Claude Code:

```text
/plugin marketplace add DarthVaderW/zotero-mcp
/plugin install zotero-mcp@darthvaderw-zotero-mcp
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

Codex users enter these in the Codex MCP configuration UI. Claude Code users
enter them through the plugin's `userConfig` prompt. Do not commit `.env`,
PDFs, or local Zotero data.

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

If Claude Code reports that the MCP failed to start, check `uvx` before
re-entering tokens:

```bash
command -v uvx
uvx --version
```

`uvx: command not found` means the MCP process never started. Install `uv`,
restart Claude Code, then retry the plugin. A missing `uvx` can look like a
token or sensitive-storage problem, but the token is not used until the MCP
server actually starts.

If `uvx` works but `zotero_ping` fails, then check:

```text
Zotero is running
Zotero Debug Bridge is enabled
ZOTERO_DEBUG_BRIDGE_URL is http://127.0.0.1:23119/debug-bridge/execute unless changed
ZOTERO_DEBUG_BRIDGE_TOKEN matches the local Debug Bridge token
```
