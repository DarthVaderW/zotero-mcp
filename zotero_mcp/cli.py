#!/usr/bin/env python3
"""Zotero CLI — local debug-bridge first, Web API for remote/lookup/export workflows.

Local debug-bridge commands (require ZOTERO_DEBUG_BRIDGE_TOKEN):
  ping, items, search, get, collections, tags, children,
  create-item, attach-pdf, attach-snapshot, search-arxiv, capture-arxiv,
  arxiv, import-doi, import-isbn, import-pmid, attach-arxiv-sidecars,
  attachment-text, delete, fetch-pdfs --key --file

Web API commands (require ZOTERO_API_KEY + ZOTERO_USER_ID|ZOTERO_GROUP_ID):
  add-doi, add-isbn, add-pmid, update, export, batch-add,
  check-pdfs, crossref, find-dois, fetch-pdfs (remote mode)
"""

from __future__ import annotations

import argparse
import json
import sys

from zotero_mcp.operations import (
    PDF_SOURCES,
    db_get_item,
    ensure_debug_bridge,
    op_add_identifier,
    op_arxiv,
    op_attach_arxiv_sidecars,
    op_attach_pdf,
    op_attach_snapshot,
    op_attachment_text,
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

_json_mode = False


def _enable_json_mode() -> None:
    _set_json_mode(True)


def _set_json_mode(enabled: bool) -> None:
    global _json_mode
    _json_mode = enabled


def _json_print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _json_error(message: str, code: int = 0) -> None:
    print(json.dumps({"error": message, "code": code}), file=sys.stderr)


def require_debug_bridge() -> None:
    try:
        ensure_debug_bridge()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

def cmd_ping(_args):
    result = op_ping()
    if _json_mode:
        _json_print(result)
    else:
        print(result["zotero_version"])


def cmd_items(args):
    result = op_items(limit=args.limit, collection_key=args.collection)
    items = result["items"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Showing {len(items)} item(s)\n")
    for item in items:
        print(f"[{item.get('key','')}] {item.get('creators','')} ({item.get('dateAdded','')[:4]}) {item.get('title','Untitled')[:80]}")


def cmd_search(args):
    result = op_search(args.query, limit=args.limit)
    items = result["items"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Found {len(items)} result(s)\n")
    for item in items:
        print(f"[{item.get('key','')}] {item.get('creators','')} {item.get('title','Untitled')[:80]}")


def cmd_get(args):
    result = op_get(args.key)
    item = result["item"]
    children = result["children"]
    if _json_mode:
        _json_print(result)
        return
    if not item:
        print(f"Item {args.key} not found", file=sys.stderr)
        sys.exit(1)
    print(f"Title: {item.get('title', 'Untitled')}")
    print(f"Key: {item.get('key')}")
    print(f"Creators: {item.get('creators')}")
    print(f"Date Added: {item.get('dateAdded')}")
    print(f"Date Modified: {item.get('dateModified')}")
    if item.get("DOI"):
        print(f"DOI: {item.get('DOI')}")
    if item.get("url"):
        print(f"URL: {item.get('url')}")
    if children:
        print(f"\nChildren ({len(children)}):")
        for c in children:
            if c.get("itemType") == "attachment":
                print(f"  [ATT] [{c['key']}] {c.get('title', 'Attachment')} [{c.get('contentType', '?')}]")
            else:
                print(f"  [NOTE] [{c['key']}] {c.get('title', 'Note')}")


def cmd_collections(_args):
    result = op_collections()
    cols = result["collections"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Collections ({len(cols)}):\n")
    for c in cols:
        print(f"[{c['key']}] {c['name']}")


def cmd_tags(_args):
    result = op_tags()
    tags = result["tags"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Tags ({len(tags)}):\n")
    for t in tags:
        print(t["name"])


def cmd_children(args):
    result = op_children(args.key)
    children = result["children"]
    if _json_mode:
        _json_print(result)
        return
    if not children:
        print("No children found.")
        return
    for c in children:
        if c.get("itemType") == "attachment":
            print(f"[ATT] [{c['key']}] {c.get('title', 'Attachment')} [{c.get('contentType', '?')}]")
        else:
            print(f"[NOTE] [{c['key']}] {c.get('title', 'Note')}")


def cmd_attachment_text(args):
    result = op_attachment_text(args.key, max_chars=args.max_chars, prefer_cache=not args.no_cache)
    if _json_mode:
        _json_print(result)
        return
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}", file=sys.stderr)
    if result.get("text"):
        print(result["text"])
    else:
        print("No readable attachment text found.", file=sys.stderr)
        sys.exit(1)


def cmd_create_item(args):
    meta = json.loads(args.meta_json) if args.meta_json else {}
    result = op_create_item(meta)
    if _json_mode:
        _json_print(result)
    else:
        print(result["item_key"])


def cmd_attach_pdf(args):
    result = op_attach_pdf(args.key, args.file)
    if _json_mode:
        _json_print(result)
    else:
        print(result["attachment_key"])


def cmd_attach_snapshot(args):
    result = op_attach_snapshot(args.key, args.url, title=args.title)
    if _json_mode:
        _json_print(result)
    else:
        print(result["snapshot_key"])


def cmd_arxiv(args):
    result = op_arxiv(
        args.arxiv,
        collection_name_or_key=args.collection,
        attach_html=not args.no_html,
        force=args.force,
    )
    if _json_mode:
        _json_print(result)
    else:
        print(json.dumps(result, ensure_ascii=False))


def cmd_search_arxiv(args):
    result = op_search_arxiv(args.query, limit=args.limit)
    if _json_mode:
        _json_print(result)
        return
    if not result["candidates"]:
        print("No arXiv candidates found.")
        return
    for index, candidate in enumerate(result["candidates"], 1):
        authors = ", ".join(candidate.get("authors", [])[:3])
        score = candidate.get("score", 0)
        print(f"[{index}] {candidate['arxiv_id']} score={score} {candidate['title']}")
        if authors:
            print(f"    {authors}")


def cmd_capture_arxiv(args):
    result = op_capture_arxiv(
        args.paper,
        confirmed_arxiv_id=args.confirmed_arxiv_id,
        collection=args.collection,
        attach_html=not args.no_html,
        force=args.force,
    )
    if _json_mode:
        _json_print(result)
        return
    print(json.dumps(result, ensure_ascii=False))


def cmd_delete(args):
    if args.permanent and args.trash:
        print("Error: --permanent and --trash are mutually exclusive", file=sys.stderr)
        sys.exit(1)
    permanent = bool(args.permanent)
    keys = []
    if not args.yes:
        require_debug_bridge()
        mode = "permanently delete" if permanent else "move to trash"
        for key in args.keys:
            item = db_get_item(key)
            title = item.get("title", "Untitled") if item else "Missing item"
            if not args.yes:
                print(f"[{key}] {title}")
                confirm = input(f"{mode.capitalize()}? [y/N] ").strip().lower()
                if confirm != "y":
                    print("Skipped.")
                    continue
            keys.append(key)
    else:
        keys = args.keys

    result = op_delete_items(keys, permanent=permanent)
    if _json_mode:
        _json_print(result)
        return
    for item in result["invalid"]:
        print(f"Invalid item key: '{item['key']}'. Must be 8 alphanumeric characters.", file=sys.stderr)
    for item in result["missing"]:
        print(f"Item {item['key']} not found", file=sys.stderr)
    for item in result["deleted"]:
        print(f"OK: {item['title']} [{item['key']}] ({item.get('mode', 'unknown')})")
    for item in result["failed"]:
        print(f"Failed: {item.get('error', 'Unknown error')}", file=sys.stderr)


def cmd_add_identifier(args):
    result = op_add_identifier(
        args.identifier,
        id_type=args.id_type,
        collection=args.collection,
        tags=args.tags,
        force=getattr(args, "force", False),
    )
    if _json_mode:
        _json_print(result)
        return result["status"]
    if result["status"] == "duplicate":
        print(f"Already in library: {result['existing']['summary']}")
        print("Use --force to add anyway.")
    elif result["status"] == "added":
        for item in result["successful"]:
            print(f"Added: {item.get('title', 'untitled')} [{item.get('key', '')}]")
    elif result["status"] == "failed":
        for item in result.get("failed", []):
            print(f"Failed: {item.get('message', 'unknown error')}", file=sys.stderr)
    return result["status"]

def cmd_import_identifier(args):
    result = op_import_identifier(
        args.identifier,
        id_type=args.id_type,
        collection=args.collection,
        tags=args.tags,
        force=getattr(args, "force", False),
        attach_pdf=not getattr(args, "no_pdf", False),
    )
    if _json_mode:
        _json_print(result)
        return result["status"]
    title = result.get("title") or result.get("existing", {}).get("title") or "untitled"
    if result["status"] == "existing":
        print(f"Already in local library: {title} [{result.get('item_key', '')}]")
    else:
        print(f"Imported locally: {title} [{result.get('item_key', '')}]")
    pdf_status = result.get("pdfStatus", "unknown")
    if pdf_status in {"attached", "existing"}:
        print(f"PDF: {pdf_status} [{result.get('pdfAttachmentKey', '')}]")
    elif pdf_status == "needs_user_file":
        print("PDF: needs user-provided file")
    elif pdf_status != "skipped":
        print(f"PDF: {pdf_status}")
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}", file=sys.stderr)
    return result["status"]

def cmd_attach_arxiv_sidecars(args):
    result = op_attach_arxiv_sidecars(args.key, args.arxiv, attach_html=not args.no_html)
    if _json_mode:
        _json_print(result)
        return
    print(f"Updated arXiv sidecars for [{args.key}] ({result['arxiv_id']})")
    sidecars = result.get("sidecars", {})
    for name, info in sidecars.items():
        print(f"{name}: {info.get('status', 'unknown')} {info.get('key', '')}".rstrip())
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}", file=sys.stderr)


def cmd_update(args):
    result = op_update_item(
        args.key,
        title=args.title,
        date=args.date,
        doi=args.doi,
        url=args.url,
        add_tags=args.add_tags,
        remove_tags=args.remove_tags,
        add_collection=args.add_collection,
    )
    if _json_mode:
        _json_print(result)
        return
    if result["status"] == "no_changes":
        print("No changes specified.")
        return
    print("Updated successfully.")


def cmd_export(args):
    result = op_export(format=args.format, collection=args.collection, output=args.output)
    if _json_mode:
        output = dict(result)
        if "text" in output:
            output["text"] = output["text"]
        _json_print(output)
        return
    if args.output:
        print(f"Exported to {args.output} ({result['bytes']} bytes)")
    else:
        print(result["text"])


def cmd_batch_add(args):
    result = op_batch_add(
        args.file,
        id_type=args.type,
        collection=args.collection,
        tags=args.tags,
        force=args.force,
    )
    if _json_mode:
        _json_print(result)
        return
    if not result["total"]:
        print("No identifiers found in file.")
        return
    for index, item in enumerate(result["results"], 1):
        print(f"[{index}/{result['total']}] {item.get('identifier', '')}")
    print(f"Added: {result['added']}")
    print(f"Skipped: {result['skipped']}")
    print(f"Failed: {result['failed']}")


def cmd_check_pdfs(_args):
    result = op_check_pdfs()
    if _json_mode:
        _json_print(result)
        return
    print("PDF Attachment Report")
    print(f"Total items: {result['total']}")
    print(f"With PDF:    {result['with_pdf']}")
    print(f"Without PDF: {result['without_pdf']}")
    if result["missing"]:
        print("\nItems missing PDFs:")
        for item in result["missing"]:
            print(f"  [{item['key']}] {item['title']}")


def cmd_crossref(args):
    result = op_crossref(args.file)
    if _json_mode:
        _json_print(result)
        return
    if not result["total"]:
        print("No citations found in file. Expected format: Author (Year)")
        return
    print(f"Citations in file: {result['total']}")
    print(f"Found in library:  {len(result['found'])}")
    print(f"Missing:           {len(result['missing'])}")


def cmd_find_dois(args):
    result = op_find_dois(apply=args.apply, limit=args.limit, collection=args.collection)
    if _json_mode:
        _json_print(result)
        return
    print(f"Found {result['processed']} items missing DOIs")
    for index, item in enumerate(result["results"], 1):
        print(f"[{index}/{result['processed']}] [{item['key']}] {item.get('title', '')[:80]}")
        if item["status"] == "matched":
            print(f"  Match: {item['doi']} (title similarity: {item['match']['similarity']}%)")
            if item.get("written"):
                print("  DOI written")
            elif item.get("writeError"):
                print(f"  Failed to write DOI: {item['writeError']}", file=sys.stderr)
    print(f"Processed: {result['processed']}")
    print(f"Matched: {result['matched']}")
    print(f"Unmatched: {result['unmatched']}")
    print(f"Already had DOI: {result['alreadyHadDoi']}")
    print(f"Wrong item type: {result['wrongItemType']}")
    if result["matched"] and not args.apply:
        print("Dry run mode. Use --apply to write DOIs.")


def cmd_fetch_pdfs(args):
    """Two modes:
    1) Local attach mode (debug bridge): --key + --file
    2) Remote OA fetch mode (Web API): scans items by DOI and attaches PDFs
    """
    result = op_fetch_pdfs(
        key=args.key,
        file=args.file,
        title=args.title,
        collection=args.collection,
        limit=args.limit,
        force=args.force,
        sources=args.sources,
        download_dir=args.download_dir,
        dry_run=args.dry_run,
        download_only=args.download_only,
        link_only=args.link_only,
    )
    if _json_mode:
        _json_print(result)
        return
    if args.key or args.file:
        if result.get("attachment_key"):
            print(f"Attached: {args.file} -> [{args.key}]")
        else:
            print("Attach failed", file=sys.stderr)
        return
    if not result["processed"]:
        print("No candidate items to fetch PDFs for.")
        return
    for index, item in enumerate(result["results"], 1):
        print(f"[{index}/{result['processed']}] [{item['key']}] {item.get('title', 'untitled')[:70]}")
        if item["status"] == "no_source":
            print("  No OA PDF source found")
        elif item["status"] == "dry_run":
            print(f"  DRY-RUN {item['source']}: {item['pdfUrl']}")
        elif item["status"] == "download_failed":
            print(f"  Download failed: {item['pdfUrl']}")
        elif item["status"] == "downloaded":
            print(f"  Saved: {item['localPath']}")
        elif item["status"] == "linked":
            print(f"  Linked URL attachment ({item['source']})")
        elif item["status"] == "link_failed":
            print("  Failed to create linked URL attachment")
        elif item["status"] == "attached":
            print(f"  Uploaded PDF ({item['source']})")
        elif item["status"] == "upload_failed":
            print("  Upload failed")
    print("\nfetch-pdfs summary")
    print(f"Processed: {result['processed']}")
    print(f"Downloaded: {result['downloaded']}")
    print(f"Attached(uploaded): {result['attached']}")
    print(f"Linked URL: {result['linked']}")
    print(f"Failed: {result['failed']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Zotero CLI — debug-bridge local workflows + Web API remote workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable text")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    p = subparsers.add_parser("ping", help="Check local debug-bridge connection/version")

    p = subparsers.add_parser("items", help="List local Zotero items via debug-bridge")
    p.add_argument("--limit", type=int, default=25, help="Max items to return")
    p.add_argument("--collection", help="Collection key")

    p = subparsers.add_parser("search", help="Search local items via debug-bridge")
    p.add_argument("query", help="Search query")
    p.add_argument("--limit", type=int, default=25, help="Max results")

    p = subparsers.add_parser("get", help="Get full local item details")
    p.add_argument("key", help="Item key")

    subparsers.add_parser("collections", help="List local collections")
    subparsers.add_parser("tags", help="List local tags")

    p = subparsers.add_parser("children", help="List local child items")
    p.add_argument("key", help="Parent item key")

    p = subparsers.add_parser("attachment-text", help="Read local attachment text/cache by attachment key")
    p.add_argument("key", help="Attachment item key")
    p.add_argument("--max-chars", type=int, default=20000, help="Maximum characters to return")
    p.add_argument("--no-cache", action="store_true", help="Read attachment file before Zotero full-text cache")

    p = subparsers.add_parser("create-item", help="Create local item via debug-bridge")
    p.add_argument("--meta-json", default="{}", help="Item metadata JSON string")

    p = subparsers.add_parser("attach-pdf", help="Attach local PDF via debug-bridge")
    p.add_argument("--key", required=True, help="Parent item key")
    p.add_argument("--file", required=True, help="Local PDF path")

    p = subparsers.add_parser("attach-snapshot", help="Attach web page snapshot via debug-bridge")
    p.add_argument("--key", required=True, help="Parent item key")
    p.add_argument("--url", required=True, help="Web page URL")
    p.add_argument("--title", default="Web Page Snapshot", help="Attachment title")

    p = subparsers.add_parser("arxiv", help="Import arXiv item + local PDF + HTML snapshot")
    p.add_argument("arxiv", help="arXiv ID or URL")
    p.add_argument("--collection", help="Collection name or key")
    p.add_argument("--no-html", action="store_true", help="Do not try to attach arXiv HTML snapshot")
    p.add_argument("--force", action="store_true", help="Create even if a matching arXiv item exists")

    p = subparsers.add_parser("search-arxiv", help="Search arXiv by ID, URL, or title")
    p.add_argument("query", help="arXiv ID/URL or paper title")
    p.add_argument("--limit", type=int, default=5, help="Max candidates")

    p = subparsers.add_parser("capture-arxiv", help="Capture arXiv by ID/URL or confirmed candidate")
    p.add_argument("paper", help="arXiv ID/URL or title")
    p.add_argument("--confirmed-arxiv-id", help="Candidate arXiv ID selected from search-arxiv")
    p.add_argument("--collection", help="Collection name or key")
    p.add_argument("--no-html", action="store_true", help="Do not try to attach arXiv HTML snapshot")
    p.add_argument("--force", action="store_true", help="Create even if a matching arXiv item exists")

    p = subparsers.add_parser("import-doi", help="Import item by DOI via local debug-bridge")
    p.add_argument("identifier", help="DOI")
    p.add_argument("--collection", help="Collection name or key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Create even if duplicate detected")
    p.add_argument("--no-pdf", action="store_true", help="Do not try to attach an OA PDF")
    p.set_defaults(id_type="doi")

    p = subparsers.add_parser("import-isbn", help="Import item by ISBN via local debug-bridge")
    p.add_argument("identifier", help="ISBN")
    p.add_argument("--collection", help="Collection name or key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Create even if duplicate detected")
    p.add_argument("--no-pdf", action="store_true", help="Do not try to attach an OA PDF")
    p.set_defaults(id_type="isbn")

    p = subparsers.add_parser("import-pmid", help="Import item by PMID via local debug-bridge")
    p.add_argument("identifier", help="PMID")
    p.add_argument("--collection", help="Collection name or key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Create even if duplicate detected")
    p.add_argument("--no-pdf", action="store_true", help="Do not try to attach an OA PDF")
    p.set_defaults(id_type="pmid")

    p = subparsers.add_parser("attach-arxiv-sidecars", help="Attach arXiv PDF/HTML sidecars to an existing local item")
    p.add_argument("--key", required=True, help="Parent item key")
    p.add_argument("--arxiv", required=True, help="arXiv ID or URL")
    p.add_argument("--no-html", action="store_true", help="Do not try to attach arXiv HTML snapshot")

    p = subparsers.add_parser("delete", help="Delete local items (default: trash)")
    p.add_argument("keys", nargs="+", help="Item key(s)")
    p.add_argument("--yes", action="store_true", help="Skip confirmation")
    p.add_argument("--permanent", action="store_true", help="Permanently delete")
    p.add_argument("--trash", action="store_true", help="Explicitly move to trash (default)")

    p = subparsers.add_parser("add-doi", help="Add item by DOI (Web API)")
    p.add_argument("identifier", help="DOI")
    p.add_argument("--collection", help="Add to collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Add even if duplicate detected")
    p.set_defaults(id_type="doi")

    p = subparsers.add_parser("add-isbn", help="Add item by ISBN (Web API)")
    p.add_argument("identifier", help="ISBN")
    p.add_argument("--collection", help="Add to collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Add even if duplicate detected")
    p.set_defaults(id_type="isbn")

    p = subparsers.add_parser("add-pmid", help="Add item by PMID (Web API)")
    p.add_argument("identifier", help="PMID")
    p.add_argument("--collection", help="Add to collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Add even if duplicate detected")
    p.set_defaults(id_type="pmid")

    p = subparsers.add_parser("update", help="Update metadata via Web API")
    p.add_argument("key", help="Item key")
    p.add_argument("--title", help="New title")
    p.add_argument("--date", help="New date")
    p.add_argument("--doi", help="Set DOI")
    p.add_argument("--url", help="Set URL")
    p.add_argument("--add-tags", help="Comma-separated tags to add")
    p.add_argument("--remove-tags", help="Comma-separated tags to remove")
    p.add_argument("--add-collection", help="Add to collection key")

    p = subparsers.add_parser("export", help="Export via Web API")
    p.add_argument("--format", default="bibtex", choices=["bibtex", "ris", "csljson"], help="Export format")
    p.add_argument("--collection", help="Collection key")
    p.add_argument("--output", help="Output file path")

    p = subparsers.add_parser("batch-add", help="Batch add identifiers via Web API")
    p.add_argument("file", help="File with one identifier per line")
    p.add_argument("--type", default="doi", choices=["doi", "isbn", "pmid"], help="Identifier type")
    p.add_argument("--collection", help="Collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Skip duplicate detection")

    subparsers.add_parser("check-pdfs", help="Report PDF attachment status via Web API")

    p = subparsers.add_parser("crossref", help="Cross-reference citations via Web API")
    p.add_argument("file", help="Citation text/markdown file")

    p = subparsers.add_parser("find-dois", help="Find missing DOIs via CrossRef and Web API")
    p.add_argument("--apply", action="store_true", help="Write matched DOIs")
    p.add_argument("--limit", type=int, default=None, help="Max items to process")
    p.add_argument("--collection", help="Collection key")

    p = subparsers.add_parser("fetch-pdfs", help="Fetch OA PDFs (Web API) or attach local file (--key --file)")
    p.add_argument("--key", help="Local attach mode: parent item key")
    p.add_argument("--file", help="Local attach mode: local PDF file path")
    p.add_argument("--title", default="Full Text PDF", help="Attachment title")
    p.add_argument("--collection", help="Remote mode: collection key scope")
    p.add_argument("--limit", type=int, default=None, help="Remote mode: max items")
    p.add_argument("--force", action="store_true", help="Remote mode: include items that already have PDFs")
    p.add_argument("--sources", default=",".join(PDF_SOURCES), help="Remote mode: comma-separated PDF sources")
    p.add_argument("--download-dir", default="pdfs", help="Remote mode: local download directory")
    p.add_argument("--dry-run", action="store_true", help="Remote mode: do not download/write")
    p.add_argument("--download-only", action="store_true", help="Remote mode: download locally, do not attach")
    p.add_argument("--link-only", action="store_true", help="Remote mode: create linked_url attachment instead of upload")

    return parser


def dispatch(args) -> None:
    if args.command == "ping":
        cmd_ping(args)
    elif args.command == "items":
        cmd_items(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "get":
        cmd_get(args)
    elif args.command == "collections":
        cmd_collections(args)
    elif args.command == "tags":
        cmd_tags(args)
    elif args.command == "children":
        cmd_children(args)
    elif args.command == "attachment-text":
        cmd_attachment_text(args)
    elif args.command == "create-item":
        cmd_create_item(args)
    elif args.command == "attach-pdf":
        cmd_attach_pdf(args)
    elif args.command == "attach-snapshot":
        cmd_attach_snapshot(args)
    elif args.command == "arxiv":
        cmd_arxiv(args)
    elif args.command == "search-arxiv":
        cmd_search_arxiv(args)
    elif args.command == "capture-arxiv":
        cmd_capture_arxiv(args)
    elif args.command in ("import-doi", "import-isbn", "import-pmid"):
        result = cmd_import_identifier(args)
        if result not in {"added", "existing"}:
            sys.exit(1)
    elif args.command == "attach-arxiv-sidecars":
        cmd_attach_arxiv_sidecars(args)
    elif args.command == "delete":
        cmd_delete(args)
    elif args.command in ("add-doi", "add-isbn", "add-pmid"):
        result = cmd_add_identifier(args)
        if result == "failed":
            sys.exit(1)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "batch-add":
        cmd_batch_add(args)
    elif args.command == "check-pdfs":
        cmd_check_pdfs(args)
    elif args.command == "crossref":
        cmd_crossref(args)
    elif args.command == "find-dois":
        cmd_find_dois(args)
    elif args.command == "fetch-pdfs":
        cmd_fetch_pdfs(args)


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.json:
        _enable_json_mode()

    try:
        dispatch(args)
    except RuntimeError as e:
        if _json_mode:
            _json_error(str(e), getattr(e, "code", 0))
        else:
            print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
