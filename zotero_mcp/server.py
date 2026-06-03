from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from zotero_mcp.runtime import run_zotero

ROOT = Path(__file__).resolve().parents[1]

mcp = FastMCP("zotero-mcp")


def root_relative_path(path: str) -> str:
    local_path = Path(path)
    if local_path.is_absolute():
        return str(local_path)
    return str(ROOT / local_path)


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
    """Import or reuse an arXiv item in local Zotero and attach the PDF."""
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
    return run_zotero(["attach-pdf", "--key", key, "--file", root_relative_path(file)])


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
    if bool(key) != bool(file):
        raise ValueError(
            "Local PDF attach mode requires both 'key' and 'file'. "
            "Provide both to attach a local PDF, or omit both for remote Web API fetch mode."
        )
    args = ["fetch-pdfs", "--title", title]
    if key:
        args.extend(["--key", key])
    if file:
        args.extend(["--file", root_relative_path(file)])
    if collection:
        args.extend(["--collection", collection])
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if dry_run:
        args.append("--dry-run")
    if download_only:
        args.append("--download-only")
    if not key:
        args.extend(["--download-dir", str(ROOT / "pdfs")])
    return run_zotero(args, expect_json=False)


@mcp.tool()
def zotero_list_items(limit: int = 25, collection: str | None = None) -> dict[str, Any]:
    """List local Zotero items via the Debug Bridge, optionally scoped to a collection key."""
    args = ["items", "--limit", str(limit)]
    if collection:
        args.extend(["--collection", collection])
    return run_zotero(args)


@mcp.tool()
def zotero_list_collections() -> dict[str, Any]:
    """List local Zotero collections (key and name) via the Debug Bridge."""
    return run_zotero(["collections"])


@mcp.tool()
def zotero_list_tags() -> dict[str, Any]:
    """List local Zotero tags via the Debug Bridge."""
    return run_zotero(["tags"])


@mcp.tool()
def zotero_get_children(key: str) -> dict[str, Any]:
    """List child items (attachments and notes) of a local Zotero parent item."""
    return run_zotero(["children", key])


@mcp.tool()
def zotero_check_pdfs() -> dict[str, Any]:
    """Report which library items have or are missing PDF attachments (Web API)."""
    return run_zotero(["check-pdfs"])


@mcp.tool()
def zotero_add_by_identifier(
    identifier: str,
    id_type: str = "doi",
    collection: str | None = None,
    tags: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Add an item to the library by identifier via the Zotero Web API.

    id_type is one of 'doi', 'isbn', 'pmid'. tags is a comma-separated string.
    Set force=True to add even when a duplicate is detected.
    """
    if id_type not in {"doi", "isbn", "pmid"}:
        raise ValueError("id_type must be one of: doi, isbn, pmid")
    args = [f"add-{id_type}", identifier]
    if collection:
        args.extend(["--collection", collection])
    if tags:
        args.extend(["--tags", tags])
    if force:
        args.append("--force")
    return run_zotero(args, expect_json=False)


@mcp.tool()
def zotero_update_item(
    key: str,
    title: str | None = None,
    date: str | None = None,
    doi: str | None = None,
    url: str | None = None,
    add_tags: str | None = None,
    remove_tags: str | None = None,
    add_collection: str | None = None,
) -> dict[str, Any]:
    """Update metadata on a library item via the Zotero Web API.

    add_tags/remove_tags are comma-separated strings. Only provided fields change.
    """
    args = ["update", key]
    if title is not None:
        args.extend(["--title", title])
    if date is not None:
        args.extend(["--date", date])
    if doi is not None:
        args.extend(["--doi", doi])
    if url is not None:
        args.extend(["--url", url])
    if add_tags:
        args.extend(["--add-tags", add_tags])
    if remove_tags:
        args.extend(["--remove-tags", remove_tags])
    if add_collection:
        args.extend(["--add-collection", add_collection])
    return run_zotero(args, expect_json=False)


@mcp.tool()
def zotero_export(
    format: str = "bibtex",
    collection: str | None = None,
    output: str | None = None,
) -> dict[str, Any]:
    """Export library items via the Web API. format is 'bibtex', 'ris', or 'csljson'.

    When output is given, writes to that path; otherwise returns the export text.
    """
    if format not in {"bibtex", "ris", "csljson"}:
        raise ValueError("format must be one of: bibtex, ris, csljson")
    args = ["export", "--format", format]
    if collection:
        args.extend(["--collection", collection])
    if output:
        args.extend(["--output", root_relative_path(output)])
    return run_zotero(args, expect_json=False)


@mcp.tool()
def zotero_batch_add(
    file: str,
    id_type: str = "doi",
    collection: str | None = None,
    tags: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Batch-add identifiers (one per line in `file`) via the Web API.

    id_type is one of 'doi', 'isbn', 'pmid'. tags is a comma-separated string.
    """
    if id_type not in {"doi", "isbn", "pmid"}:
        raise ValueError("id_type must be one of: doi, isbn, pmid")
    args = ["batch-add", root_relative_path(file), "--type", id_type]
    if collection:
        args.extend(["--collection", collection])
    if tags:
        args.extend(["--tags", tags])
    if force:
        args.append("--force")
    return run_zotero(args, expect_json=False)


@mcp.tool()
def zotero_find_dois(
    apply: bool = False,
    limit: int | None = None,
    collection: str | None = None,
) -> dict[str, Any]:
    """Find missing DOIs for items via CrossRef. Read-only unless apply=True writes them."""
    args = ["find-dois"]
    if apply:
        args.append("--apply")
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if collection:
        args.extend(["--collection", collection])
    return run_zotero(args, expect_json=False)


@mcp.tool()
def zotero_crossref(file: str) -> dict[str, Any]:
    """Cross-reference 'Author (Year)' citations in a text/markdown file against the library."""
    return run_zotero(["crossref", root_relative_path(file)], expect_json=False)


@mcp.tool()
def zotero_delete_items(keys: list[str]) -> dict[str, Any]:
    """Move local Zotero items to the trash by item key (recoverable from Zotero's trash).

    Permanent deletion is intentionally NOT exposed over MCP; use the CLI for that.
    """
    if not keys:
        raise ValueError("Provide at least one item key to delete.")
    return run_zotero(["delete", *keys, "--yes", "--trash"], expect_json=False)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
