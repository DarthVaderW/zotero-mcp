from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "zotero.py"

mcp = FastMCP("zotero-mcp")


def run_zotero(args: list[str], expect_json: bool = True) -> dict[str, Any]:
    command = [sys.executable, str(CLI), "--json", *args]
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
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
    mcp.run()


if __name__ == "__main__":
    main()
