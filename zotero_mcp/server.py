from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "zotero.py"
MCP_HOST = os.getenv("ZOTERO_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("ZOTERO_MCP_PORT", "6817"))
MCP_PATH = os.getenv("ZOTERO_MCP_PATH", "/mcp")

mcp = FastMCP("zotero-mcp", host=MCP_HOST, port=MCP_PORT, streamable_http_path=MCP_PATH)


def request_headers() -> Any:
    try:
        request = mcp.get_context().request_context.request
    except Exception:
        return {}
    return getattr(request, "headers", {}) or {}


def header_value(*names: str) -> str | None:
    headers = request_headers()
    for name in names:
        try:
            value = headers.get(name)
        except AttributeError:
            value = None
        if value:
            return str(value).strip()
    return None


def bearer_token() -> str | None:
    authorization = header_value("authorization")
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def env_from_headers() -> dict[str, str]:
    values: dict[str, str | None] = {
        "ZOTERO_DEBUG_BRIDGE_TOKEN": (
            header_value("x-zotero-debug-bridge-token", "zotero-debug-bridge-token")
            or bearer_token()
        ),
        "ZOTERO_DEBUG_BRIDGE_URL": header_value(
            "x-zotero-debug-bridge-url",
            "zotero-debug-bridge-url",
        ),
        "ZOTERO_LIBRARY_ID": header_value("x-zotero-library-id", "zotero-library-id"),
        "ZOTERO_API_KEY": header_value("x-zotero-api-key", "zotero-api-key"),
        "ZOTERO_USER_ID": header_value("x-zotero-user-id", "zotero-user-id"),
        "ZOTERO_GROUP_ID": header_value("x-zotero-group-id", "zotero-group-id"),
        "CROSSREF_EMAIL": header_value("x-crossref-email", "crossref-email"),
    }
    return {key: value for key, value in values.items() if value}


def run_zotero(args: list[str], expect_json: bool = True) -> dict[str, Any]:
    command = [sys.executable, str(CLI), "--json", *args]
    env = os.environ.copy()
    env.update(env_from_headers())
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        raise RuntimeError(stderr or stdout or f"zotero.py exited with {proc.returncode}")
    if expect_json:
        try:
            return json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            return {"stdout": stdout}
    return {"stdout": stdout, "stderr": stderr}


@mcp.tool()
def zotero_ping() -> dict[str, Any]:
    """Check local Zotero Debug Bridge connectivity."""
    return run_zotero(["ping"])


@mcp.tool()
def zotero_search_items(query: str, limit: int = 25) -> dict[str, Any]:
    """Search local Zotero items through the Debug Bridge."""
    return run_zotero(["search", query, "--limit", str(limit)])


@mcp.tool()
def zotero_get_item(key: str) -> dict[str, Any]:
    """Get a local Zotero item and its children by item key."""
    return run_zotero(["get", key])


@mcp.tool()
def zotero_import_arxiv(arxiv: str, collection: str | None = None) -> dict[str, Any]:
    """Import or reuse an arXiv paper in local Zotero and attach the PDF."""
    args = ["arxiv", arxiv]
    if collection:
        args.extend(["--collection", collection])
    return run_zotero(args)


@mcp.tool()
def zotero_create_item(meta: dict[str, Any]) -> dict[str, Any]:
    """Create a local Zotero item from metadata JSON."""
    return run_zotero(["create-item", "--meta-json", json.dumps(meta, ensure_ascii=False)])


@mcp.tool()
def zotero_attach_pdf(key: str, file: str) -> dict[str, Any]:
    """Attach a local PDF file to a Zotero parent item."""
    return run_zotero(["attach-pdf", "--key", key, "--file", file])


@mcp.tool()
def zotero_fetch_pdf(
    key: str | None = None,
    file: str | None = None,
    title: str = "Full Text PDF",
    collection: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    download_only: bool = False,
) -> dict[str, Any]:
    """Fetch OA PDFs remotely or attach a local PDF when key and file are provided."""
    args = ["fetch-pdfs", "--title", title]
    if key:
        args.extend(["--key", key])
    if file:
        args.extend(["--file", file])
    if collection:
        args.extend(["--collection", collection])
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if dry_run:
        args.append("--dry-run")
    if download_only:
        args.append("--download-only")
    return run_zotero(args, expect_json=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Zotero MCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.getenv("ZOTERO_MCP_TRANSPORT", "stdio"),
        help="MCP transport. Use streamable-http for Codex UI URL mode.",
    )
    parser.add_argument("--host", default=MCP_HOST, help="HTTP host for streamable-http.")
    parser.add_argument("--port", type=int, default=MCP_PORT, help="HTTP port for streamable-http.")
    parser.add_argument("--path", default=MCP_PATH, help="HTTP MCP path for streamable-http.")
    args = parser.parse_args()

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.streamable_http_path = args.path
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
