"""Read, attachment and direct library operations shared by MCP and CLI."""

from __future__ import annotations

import re
from pathlib import Path

from zotero_mcp.local_api import ensure_local_api
from zotero_mcp.local_library import (
    attach_pdf_from_file, create_item, db_add_snapshot, db_delete_item,
    db_get_attachment_file, db_get_children, db_get_collections, db_get_item,
    db_get_items, db_get_tags, db_search,
)
from zotero_mcp.validators import require_item_key


def op_ping():
    details = ensure_local_api()
    return details


def op_items(limit=25, collection_key=None):
    ensure_local_api()
    items = db_get_items(limit=limit, collection_key=collection_key) or []
    return {"total": len(items), "items": items}


def op_search(query, limit=25):
    ensure_local_api()
    items = db_search(query, limit=limit) or []
    return {"total": len(items), "items": items}


def op_get(key):
    ensure_local_api()
    require_item_key(key)
    item = db_get_item(key)
    children = db_get_children(key)
    return {"item": item, "children": children}


def op_collections():
    ensure_local_api()
    cols = db_get_collections() or []
    return {"total": len(cols), "collections": cols}


def op_tags():
    ensure_local_api()
    tags = db_get_tags() or []
    return {"total": len(tags), "tags": tags}


def op_children(key):
    ensure_local_api()
    require_item_key(key)
    children = db_get_children(key) or []
    return {"total": len(children), "children": children}


TEXT_ATTACHMENT_SUFFIXES = {
    ".bib",
    ".csv",
    ".htm",
    ".html",
    ".json",
    ".md",
    ".ris",
    ".tex",
    ".txt",
    ".xhtml",
    ".xml",
}

TEXT_ATTACHMENT_CONTENT_TYPES = {
    "application/bibtex",
    "application/json",
    "application/ris",
    "application/xhtml+xml",
    "application/xml",
}


def _read_attachment_text(path: Path, max_chars: int) -> tuple[str, bool]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        text = handle.read(max_chars + 1)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return text, truncated


def _is_text_attachment(path: Path, content_type: str) -> bool:
    normalized_type = content_type.split(";", 1)[0].strip().lower()
    return (
        normalized_type.startswith("text/")
        or normalized_type in TEXT_ATTACHMENT_CONTENT_TYPES
        or path.suffix.lower() in TEXT_ATTACHMENT_SUFFIXES
    )


def op_attachment_text(key, max_chars=20000, prefer_cache=True):
    if max_chars < 1 or max_chars > 200000:
        raise RuntimeError("max_chars must be between 1 and 200000")
    ensure_local_api()
    require_item_key(key)
    info = db_get_attachment_file(key)
    if not info:
        raise RuntimeError(f"Attachment not found: {key}")
    if not isinstance(info, dict):
        raise RuntimeError(
            "Zotero Local API returned an unexpected attachment response."
        )

    file_path = Path(info.get("filePath") or "") if info.get("filePath") else None
    storage_dir = (
        Path(info.get("storageDirectory") or "")
        if info.get("storageDirectory")
        else None
    )
    cache_path = None
    if storage_dir:
        cache_path = storage_dir / ".zotero-ft-cache"
    elif file_path:
        cache_path = file_path.parent / ".zotero-ft-cache"

    warnings = []
    selected_path = None
    source = None
    if prefer_cache and cache_path and cache_path.exists():
        selected_path = cache_path
        source = "zotero-ft-cache"
    elif file_path and file_path.exists():
        selected_path = file_path
        source = "attachment-file"
    elif cache_path and cache_path.exists():
        selected_path = cache_path
        source = "zotero-ft-cache"

    text = ""
    truncated = False
    content_type = str(info.get("contentType") or "")
    if selected_path:
        if source == "attachment-file" and not _is_text_attachment(
            selected_path, content_type
        ):
            if cache_path and cache_path.exists():
                selected_path = cache_path
                source = "zotero-ft-cache"
            else:
                warnings.append(
                    "Attachment file is not text-readable and has no Zotero full-text cache; "
                    "use a format-specific parser instead."
                )
                source = None
        if source:
            text, truncated = _read_attachment_text(selected_path, max_chars)
    else:
        warnings.append(
            "No readable local attachment file or Zotero full-text cache was found."
        )

    return {
        "key": key,
        "attachment": info,
        "filePath": str(file_path) if file_path else "",
        "storageDirectory": str(storage_dir) if storage_dir else "",
        "cachePath": str(cache_path) if cache_path else "",
        "cacheExists": bool(cache_path and cache_path.exists()),
        "source": source,
        "text": text,
        "characters": len(text),
        "truncated": truncated,
        "maxChars": max_chars,
        "warnings": warnings,
    }


def op_create_item(meta):
    ensure_local_api()
    return {"item_key": create_item(meta)}


def op_attach_pdf(key, file, title="Full Text PDF"):
    ensure_local_api()
    require_item_key(key)
    return {"attachment_key": attach_pdf_from_file(key, file, title=title)}


def op_attach_snapshot(key, url, title="Web Page Snapshot"):
    ensure_local_api()
    require_item_key(key)
    snapshot_key = db_add_snapshot(key, url, title=title)
    return {"snapshot_key": snapshot_key, "url": url, "title": title}


def op_delete_items(keys, permanent=False):
    ensure_local_api()
    result = {
        "mode": "permanent" if permanent else "trash",
        "deleted": [],
        "missing": [],
        "invalid": [],
        "failed": [],
    }
    for key in keys:
        if not re.match(r"^[A-Za-z0-9]{8}$", key):
            result["invalid"].append({"key": key, "error": "Invalid item key"})
            continue
        item = db_get_item(key)
        if not item:
            result["missing"].append({"key": key})
            continue
        deleted = db_delete_item(key, permanent=permanent)
        entry = {
            "key": key,
            "title": item.get("title", "Untitled"),
            "mode": deleted.get("mode", result["mode"]),
        }
        if deleted.get("success"):
            result["deleted"].append(entry)
        else:
            entry["error"] = deleted.get("error", "Unknown error")
            result["failed"].append(entry)
    result["total"] = len(keys)
    return result
