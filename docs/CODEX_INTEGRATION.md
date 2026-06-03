# Client Integration Notes

This repository contains a packaged Zotero MCP server plus a helper CLI. The MCP
server and CLI both call `zotero_mcp/operations.py`; the CLI keeps
human-readable text output as presentation only. Debug Bridge, Web API, and
validation helpers live in separate modules. This component page keeps the
current client-specific setup self-contained.

## Prerequisite

Make sure `uv` and `uvx` are available to desktop apps:

```bash
uv --version
uvx --version
```

If either command is missing, install `uv` and then restart Codex or Claude
Code:

```bash
brew install uv
```

or:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Codex

Use a custom STDIO MCP entry in Codex.

```text
Name: zotero
Command: uvx
Args:
  --from
  git+https://github.com/DarthVaderW/zotero-mcp.git@stable
  zotero-mcp
```

Configure these values in the same MCP entry:

```text
ZOTERO_DEBUG_BRIDGE_TOKEN=<local Zotero Debug Bridge token>
ZOTERO_DEBUG_BRIDGE_URL=http://127.0.0.1:23119/debug-bridge/execute
ZOTERO_LIBRARY_ID=1
ZOTERO_API_KEY=<optional Zotero Web API key>
ZOTERO_USER_ID=<optional Zotero user id>
ZOTERO_GROUP_ID=<optional Zotero group id>
CROSSREF_EMAIL=<real contact email for CrossRef/Unpaywall>
```

Set `CROSSREF_EMAIL` to a real contact email when using PDF discovery:
Unpaywall requires it, and CrossRef uses it for polite requests.

Do not use Codex plugin install as the ordinary path for this MCP right now.
Codex plugin-provided MCP rows are read-only and do not currently expose an
editable token/config form. The Codex plugin shell remains in the repository for
packaging, marketplace testing, and possible future Codex plugin improvements.

To upgrade after `stable` moves:

```bash
uvx --refresh --from git+https://github.com/DarthVaderW/zotero-mcp.git@stable zotero-mcp --help >/dev/null
```

Then fully restart Codex. Existing threads can see refreshed MCP tools after
restart; if they do not, open a new thread.

## Claude Code

Use the GUI Personal plugins path when available:

```text
Customize -> Personal plugins -> Add
DarthVaderW/zotero-mcp
```

CLI install is also valid:

```bash
claude plugin marketplace add DarthVaderW/zotero-mcp
claude plugin install zotero-mcp@darthvaderw-zotero-mcp
```

Claude Code prompts for the same local values through `userConfig`. For current
Claude Code compatibility, Debug Bridge token and Web API key are stored with
the other plugin options instead of using Claude's `sensitive` userConfig mode.
This is local to the user's machine, but it is not keychain-backed.

To upgrade:

```bash
claude plugin marketplace update darthvaderw-zotero-mcp
claude plugin update zotero-mcp@darthvaderw-zotero-mcp
```

Restart Claude Code after updating.

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
```

Local Debug Bridge:

```bash
python3 -m zotero_mcp.cli ping
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
