# Codex And Claude Code Integration

This repository contains a Zotero CLI and a thin Zotero MCP server.

## Codex Plugin Install

Make sure `uv` is available before installing the plugin:

```bash
uv --version
```

If it is missing, install `uv` from Astral:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then restart Codex or Claude Code so the updated PATH is picked up.

For the Codex GUI custom MCP flow, use:

```text
Name: zotero
Command: uv
Args:
  tool
  run
  --from
  git+https://github.com/DarthVaderW/zotero-mcp.git@v0.1.10
  zotero-mcp
```

Install the public marketplace and plugin:

```bash
codex plugin marketplace add DarthVaderW/zotero-mcp --ref stable \
  --sparse .agents/plugins \
  --sparse plugins/zotero-mcp
codex plugin add zotero-mcp@zotero-mcp
```

Then configure these values in Codex Settings -> MCP:

```text
ZOTERO_DEBUG_BRIDGE_TOKEN=<local Zotero Debug Bridge token>
ZOTERO_DEBUG_BRIDGE_URL=http://127.0.0.1:23119/debug-bridge/execute
ZOTERO_LIBRARY_ID=1
ZOTERO_API_KEY=<optional Zotero Web API key>
ZOTERO_USER_ID=<optional Zotero user id>
ZOTERO_GROUP_ID=<optional Zotero group id>
CROSSREF_EMAIL=<email for CrossRef/Unpaywall>
```

The plugin starts the stdio MCP with `uv tool run` from a fixed release tag; no local
HTTP service is required, and normal MCP startup does not auto-refresh from
GitHub.

To upgrade the plugin install:

```bash
codex plugin marketplace upgrade zotero-mcp
```

For GUI custom MCP installs, change the tag in the args to the new release tag
and restart Codex.

## Claude Code Plugin Install

Inside Claude Code:

```text
/plugin marketplace add DarthVaderW/zotero-mcp
/plugin install zotero-mcp@darthvaderw-zotero-mcp
```

Claude Code prompts for the same local values through `userConfig`. For current
Claude Code compatibility, Debug Bridge token and Web API key are stored with
the other plugin options instead of using Claude's `sensitive` userConfig mode.
This is local to the user's machine, but it is not keychain-backed.

## Developer Command Mode

For development, clients can start the MCP with a local command:

```toml
[mcp_servers.zotero]
command = "/bin/bash"
args = ["/Users/<you>/projects/zotero-mcp/scripts/run_zotero_mcp.sh"]
```

Do not commit `.env`, PDFs, local Zotero data, or real tokens.

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

## Claude Code Startup Failure

If Claude Code suggests the token or `userConfig` may be wrong, first verify
that `uv` exists:

```bash
command -v uv
uv --version
```

When `uv` is missing, Claude Code cannot start the MCP server at all. Install
`uv`, restart Claude Code, and retry before changing the Zotero token.
