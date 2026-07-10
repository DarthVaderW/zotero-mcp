from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from zotero_mcp.operations import (
    op_add_identifier,
    op_arxiv,
    op_attach_arxiv_sidecars,
    op_attachment_text,
    op_attach_pdf,
    op_attach_snapshot,
    op_batch_add,
    op_capture_arxiv,
    op_check_pdfs,
    op_children,
    op_collections,
    op_create_item,
    op_crossref,
    op_delete_items,
    op_export,
    op_fetch_pdfs,
    op_find_dois,
    op_get,
    op_import_identifier,
    op_items,
    op_ping,
    op_search,
    op_search_arxiv,
    op_tags,
    op_update_item,
)
from zotero_mcp.validators import validate_id_type

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
    return op_ping()


@mcp.tool()
def zotero_search_items(query: str, limit: int = 25) -> dict[str, Any]:
    """Search local Zotero items through the Debug Bridge."""
    return op_search(query, limit=limit)


@mcp.tool()
def zotero_get_item(key: str) -> dict[str, Any]:
    """Get a local Zotero item and its children by item key."""
    return op_get(key)


@mcp.tool()
def zotero_import_arxiv(
    arxiv: str,
    collection: str | None = None,
    attach_html: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Import or reuse an arXiv item in local Zotero, attach the PDF, and try arXiv HTML."""
    return op_arxiv(arxiv, collection_name_or_key=collection, attach_html=attach_html, force=force)


@mcp.tool()
def zotero_search_arxiv(query: str, limit: int = 5) -> dict[str, Any]:
    """Search arXiv candidates by arXiv ID/URL or paper title. This is read-only."""
    return op_search_arxiv(query, limit=limit)


@mcp.tool()
def zotero_capture_arxiv(
    paper: str,
    confirmed_arxiv_id: str | None = None,
    collection: str | None = None,
    attach_html: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Capture an arXiv paper after an ID/URL or confirmed candidate is available.

    Title-only input returns candidates and does not write until confirmed_arxiv_id is provided.
    """
    return op_capture_arxiv(
        paper,
        confirmed_arxiv_id=confirmed_arxiv_id,
        collection=collection,
        attach_html=attach_html,
        force=force,
    )


@mcp.tool()
def zotero_import_by_identifier(
    identifier: str,
    id_type: str = "doi",
    collection: str | None = None,
    tags: str | None = None,
    force: bool = False,
    attach_pdf: bool = True,
) -> dict[str, Any]:
    """Import or reuse an item by DOI/ISBN/PMID through local Zotero Debug Bridge.

    Metadata lookup may use public identifier services, but duplicate checks,
    item creation, collection updates, and PDF attachment are local Debug Bridge
    operations. This tool does not require ZOTERO_API_KEY.
    """
    id_type = validate_id_type(id_type)
    return op_import_identifier(
        identifier,
        id_type=id_type,
        collection=collection,
        tags=tags,
        force=force,
        attach_pdf=attach_pdf,
    )


@mcp.tool()
def zotero_attach_arxiv_sidecars(
    key: str,
    arxiv: str,
    attach_html: bool = True,
) -> dict[str, Any]:
    """Attach missing arXiv PDF/HTML sidecars to a known local Zotero item."""
    return op_attach_arxiv_sidecars(key, arxiv, attach_html=attach_html)


@mcp.tool()
def zotero_create_item(meta: dict[str, Any]) -> dict[str, Any]:
    """Create a local Zotero item from metadata JSON."""
    return op_create_item(meta)


@mcp.tool()
def zotero_attach_pdf(key: str, file: str) -> dict[str, Any]:
    """Attach a local PDF file to a Zotero parent item."""
    return op_attach_pdf(key, root_relative_path(file))


@mcp.tool()
def zotero_attach_snapshot(key: str, url: str, title: str = "Web Page Snapshot") -> dict[str, Any]:
    """Attach a web page snapshot from a URL to a Zotero parent item."""
    return op_attach_snapshot(key, url, title=title)


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
    return op_fetch_pdfs(
        key=key,
        file=root_relative_path(file) if file else None,
        title=title,
        collection=collection,
        limit=limit,
        dry_run=dry_run,
        download_only=download_only,
        download_dir=str(ROOT / "pdfs"),
    )


@mcp.tool()
def zotero_list_items(limit: int = 25, collection: str | None = None) -> dict[str, Any]:
    """List local Zotero items via the Debug Bridge, optionally scoped to a collection key."""
    return op_items(limit=limit, collection_key=collection)


@mcp.tool()
def zotero_list_collections() -> dict[str, Any]:
    """List local Zotero collections (key and name) via the Debug Bridge."""
    return op_collections()


@mcp.tool()
def zotero_list_tags() -> dict[str, Any]:
    """List local Zotero tags via the Debug Bridge."""
    return op_tags()


@mcp.tool()
def zotero_get_children(key: str) -> dict[str, Any]:
    """List child items (attachments and notes) of a local Zotero parent item."""
    return op_children(key)


@mcp.tool()
def zotero_get_attachment_text(
    key: str,
    max_chars: int = 20000,
    prefer_cache: bool = True,
) -> dict[str, Any]:
    """Read local attachment text, preferring Zotero's full-text cache when available."""
    return op_attachment_text(key, max_chars=max_chars, prefer_cache=prefer_cache)


@mcp.tool()
def zotero_check_pdfs() -> dict[str, Any]:
    """Report which library items have or are missing PDF attachments (Web API)."""
    return op_check_pdfs()


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
    id_type = validate_id_type(id_type)
    return op_add_identifier(
        identifier,
        id_type=id_type,
        collection=collection,
        tags=tags,
        force=force,
    )


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
    return op_update_item(
        key,
        title=title,
        date=date,
        doi=doi,
        url=url,
        add_tags=add_tags,
        remove_tags=remove_tags,
        add_collection=add_collection,
    )


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
    return op_export(
        format=format,
        collection=collection,
        output=root_relative_path(output) if output else None,
    )


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
    id_type = validate_id_type(id_type)
    return op_batch_add(
        root_relative_path(file),
        id_type=id_type,
        collection=collection,
        tags=tags,
        force=force,
    )


@mcp.tool()
def zotero_find_dois(
    apply: bool = False,
    limit: int | None = None,
    collection: str | None = None,
) -> dict[str, Any]:
    """Find missing DOIs for items via CrossRef. Read-only unless apply=True writes them."""
    return op_find_dois(apply=apply, limit=limit, collection=collection)


@mcp.tool()
def zotero_crossref(file: str) -> dict[str, Any]:
    """Cross-reference 'Author (Year)' citations in a text/markdown file against the library."""
    return op_crossref(root_relative_path(file))


@mcp.tool()
def zotero_delete_items(keys: list[str]) -> dict[str, Any]:
    """Move local Zotero items to the trash by item key (recoverable from Zotero's trash).

    Permanent deletion is intentionally NOT exposed over MCP; use the CLI for that.
    """
    if not keys:
        raise ValueError("Provide at least one item key to delete.")
    return op_delete_items(keys, permanent=False)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
