#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export TMPDIR="${ROOT}/.tmp"
export UV_CACHE_DIR="${ROOT}/.uv-cache"
mkdir -p "$TMPDIR" "$UV_CACHE_DIR"

cd "$ROOT"
exec uv run python -m zotero_mcp.server \
  --transport streamable-http \
  --host "${ZOTERO_MCP_HOST:-127.0.0.1}" \
  --port "${ZOTERO_MCP_PORT:-6817}" \
  --path "${ZOTERO_MCP_PATH:-/mcp}"
