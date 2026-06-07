#!/usr/bin/env python3
"""Structured Zotero operations shared by the MCP server and CLI."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from zotero_mcp.config import PDF_SOURCES as PDF_SOURCES
from zotero_mcp.debug_bridge import (
    db_add_item_to_collection,
    db_add_snapshot,
    db_delete_item,
    db_find_arxiv_item,
    db_find_item_by_identifier,
    db_get_attachment_file,
    db_get_children,
    db_get_collections,
    db_get_item,
    db_get_items,
    db_get_tags,
    db_ping,
    db_search,
    ensure_debug_bridge,
)
from zotero_mcp.errors import CommandError as CommandError
from zotero_mcp.arxiv import _extract_arxiv_id as _extract_arxiv_id
from zotero_mcp.arxiv import _find_existing_pdf_child as _find_existing_pdf_child
from zotero_mcp.arxiv import attach_arxiv_sidecars as attach_arxiv_sidecars
from zotero_mcp.arxiv import import_arxiv as import_arxiv
from zotero_mcp.arxiv import search_arxiv as search_arxiv
from zotero_mcp.doi_ops import (
    _crossref_search as _crossref_search,
    _extract_citations as _extract_citations,
    _match_crossref_result as _match_crossref_result,
    op_crossref as op_crossref,
    op_find_dois as op_find_dois,
)
from zotero_mcp.local_ops import attach_pdf_from_file as attach_pdf_from_file
from zotero_mcp.local_ops import create_item as create_item
from zotero_mcp.metadata import (
    _extract_year as _extract_year,
    _first_author_last as _first_author_last,
    _normalize_text as _normalize_text,
    _title_similarity as _title_similarity,
    fmt_creators as fmt_creators,
    fmt_item_short as fmt_item_short,
)
from zotero_mcp.pdfs import _download_pdf as _download_pdf
from zotero_mcp.identifiers import (
    _check_duplicate_by_metadata as _check_duplicate_by_metadata,
    _doi_to_item as _doi_to_item,
    _identifier_lookup_url as _identifier_lookup_url,
    _translate_identifier as _translate_identifier,
    clean_translated_item_for_local as clean_translated_item_for_local,
    op_add_identifier as op_add_identifier,
    op_batch_add as op_batch_add,
)
from zotero_mcp.pdf_discovery import (
    _bulk_find_pdf_parents as _bulk_find_pdf_parents,
    _create_linked_url_attachment as _create_linked_url_attachment,
    _find_pdf_source as _find_pdf_source,
    _make_pdf_filename as _make_pdf_filename,
    _try_doi_content_negotiation as _try_doi_content_negotiation,
    _try_semantic_scholar as _try_semantic_scholar,
    _try_unpaywall as _try_unpaywall,
    _upload_pdf_to_zotero as _upload_pdf_to_zotero,
    op_fetch_pdfs as op_fetch_pdfs,
)
from zotero_mcp.validators import (
    require_item_key,
    require_item_type as require_item_type,
    validate_doi as validate_doi,
    validate_isbn as validate_isbn,
    validate_item_key as validate_item_key,
)
from zotero_mcp.web_items import (
    _patch_item_field as _patch_item_field,
    op_check_pdfs as op_check_pdfs,
    op_export as op_export,
    op_update_item as op_update_item,
)
from zotero_mcp.web_api import (
    api_get_json as api_get_json,
    api_request as api_request,
    get_api_config as get_api_config,
    paginate_all as paginate_all,
)

def op_ping():
    ensure_debug_bridge()
    return {"zotero_version": db_ping()}

def op_items(limit=25, collection_key=None):
    ensure_debug_bridge()
    items = db_get_items(limit=limit, collection_key=collection_key) or []
    return {"total": len(items), "items": items}

def op_search(query, limit=25):
    ensure_debug_bridge()
    items = db_search(query, limit=limit) or []
    return {"total": len(items), "items": items}

def op_get(key):
    ensure_debug_bridge()
    require_item_key(key)
    item = db_get_item(key)
    children = db_get_children(key)
    return {"item": item, "children": children}

def op_collections():
    ensure_debug_bridge()
    cols = db_get_collections() or []
    return {"total": len(cols), "collections": cols}

def op_tags():
    ensure_debug_bridge()
    tags = db_get_tags() or []
    return {"total": len(tags), "tags": tags}

def op_children(key):
    ensure_debug_bridge()
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
    ensure_debug_bridge()
    require_item_key(key)
    info = db_get_attachment_file(key)
    if not info:
        raise RuntimeError(f"Attachment not found: {key}")
    if not isinstance(info, dict):
        raise RuntimeError("Debug Bridge returned an unexpected attachment response.")

    file_path = Path(info.get("filePath") or "") if info.get("filePath") else None
    storage_dir = Path(info.get("storageDirectory") or "") if info.get("storageDirectory") else None
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
        if source == "attachment-file" and not _is_text_attachment(selected_path, content_type):
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
        warnings.append("No readable local attachment file or Zotero full-text cache was found.")

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
    ensure_debug_bridge()
    return {"item_key": create_item(meta)}

def op_attach_pdf(key, file, title="Full Text PDF"):
    ensure_debug_bridge()
    require_item_key(key)
    return {"attachment_key": attach_pdf_from_file(key, file, title=title)}

def _identifier_result_item(item_key, created=False, existing=None):
    return {
        "created": created,
        "item_key": item_key,
        "zoteroItemKey": item_key,
        "zoteroSelectUri": f"zotero://select/library/items/{item_key}" if item_key else "",
        "existing": existing,
    }

def _local_pdf_result(item_key, doi, attach_pdf=True, title="Full Text PDF", children=None):
    if not attach_pdf:
        return {"pdfStatus": "skipped", "pdfSourceAttempts": []}

    current_children = list(children if children is not None else (db_get_children(item_key) or []))
    pdf_child = _find_existing_pdf_child(current_children)
    if pdf_child:
        key = pdf_child.get("key")
        return {
            "pdfStatus": "existing",
            "attachment_key": key,
            "pdfAttachmentKey": key,
            "pdfSourceAttempts": [],
        }

    doi = (doi or "").strip()
    source_names = list(PDF_SOURCES)
    if not doi:
        return {
            "pdfStatus": "needs_user_file",
            "pdfSourceAttempts": [],
            "warnings": ["No DOI was available for OA PDF discovery."],
        }

    source_info = _find_pdf_source(doi, source_names)
    if not source_info:
        return {
            "pdfStatus": "needs_user_file",
            "pdfSourceAttempts": source_names,
            "warnings": ["No open PDF source was found; attach a user-provided local file later."],
        }

    pdf_url, source_url, source_name = source_info
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not _download_pdf(pdf_url, tmp_path):
            return {
                "pdfStatus": "download_failed",
                "pdfSource": source_name,
                "pdfUrl": pdf_url,
                "sourceUrl": source_url,
                "pdfSourceAttempts": source_names,
            }
        try:
            attachment_key = attach_pdf_from_file(item_key, tmp_path, title=title)
        except Exception as exc:
            return {
                "pdfStatus": "attach_failed",
                "pdfSource": source_name,
                "pdfUrl": pdf_url,
                "sourceUrl": source_url,
                "pdfSourceAttempts": source_names,
                "warnings": [f"PDF downloaded but local attach failed: {exc}"],
            }
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return {
        "pdfStatus": "attached",
        "pdfSource": source_name,
        "pdfUrl": pdf_url,
        "sourceUrl": source_url,
        "attachment_key": attachment_key,
        "pdfAttachmentKey": attachment_key,
        "pdfSourceAttempts": source_names,
    }

def _identifier_pdf_doi(identifier, id_type, payload):
    if id_type == "doi":
        return identifier
    return (payload or {}).get("DOI", "")

def op_import_identifier(
    identifier,
    id_type="doi",
    collection=None,
    tags=None,
    force=False,
    attach_pdf=True,
):
    if id_type not in {"doi", "isbn", "pmid"}:
        raise RuntimeError("id_type must be one of: doi, isbn, pmid")
    ensure_debug_bridge()
    translated = _translate_identifier(identifier, id_type)
    if not translated:
        raise RuntimeError("No metadata found for this identifier.")

    item = translated[0] if isinstance(translated, list) else translated
    payload = clean_translated_item_for_local(item, tags=tags)
    title = payload.get("title", "")
    warnings = []
    collection_result = None

    if not force:
        existing = db_find_item_by_identifier(identifier, id_type=id_type, title=title) or []
        if existing:
            item_key = existing[0].get("key")
            if item_key and collection:
                try:
                    collection_result = db_add_item_to_collection(item_key, collection)
                except Exception as exc:
                    warnings.append(f"collection update failed: {exc}")
            pdf_result = _local_pdf_result(
                item_key,
                _identifier_pdf_doi(identifier, id_type, payload),
                attach_pdf=attach_pdf,
                children=db_get_children(item_key) if item_key else [],
            ) if item_key else {"pdfStatus": "skipped", "pdfSourceAttempts": []}
            warnings.extend(pdf_result.pop("warnings", []))
            return {
                "status": "existing",
                "identifier": identifier,
                "idType": id_type,
                **_identifier_result_item(item_key, created=False, existing=existing[0]),
                "matches": existing,
                "collection": collection_result,
                "warnings": warnings,
                **pdf_result,
            }

    item_key = create_item(payload)
    if collection:
        try:
            collection_result = db_add_item_to_collection(item_key, collection)
        except Exception as exc:
            warnings.append(f"collection update failed: {exc}")

    pdf_result = _local_pdf_result(
        item_key,
        _identifier_pdf_doi(identifier, id_type, payload),
        attach_pdf=attach_pdf,
    )
    warnings.extend(pdf_result.pop("warnings", []))
    return {
        "status": "added",
        "identifier": identifier,
        "idType": id_type,
        "title": title,
        **_identifier_result_item(item_key, created=True),
        "collection": collection_result,
        "warnings": warnings,
        **pdf_result,
    }

def op_attach_arxiv_sidecars(key, arxiv, attach_html=True):
    ensure_debug_bridge()
    require_item_key(key)
    try:
        arxiv_id = _extract_arxiv_id(arxiv)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    item = db_get_item(key)
    if not item:
        raise RuntimeError(f"Item not found: {key}")
    sidecar_result = attach_arxiv_sidecars(
        key,
        arxiv_id,
        attach_html=attach_html,
        children=db_get_children(key) or [],
    )
    return {
        "status": "updated",
        "item_key": key,
        "zoteroItemKey": key,
        "arxiv_id": arxiv_id,
        "arxivId": arxiv_id,
        **sidecar_result,
    }

def _existing_arxiv_result(arxiv_id, existing, collection=None, attach_html=True):
    item = existing[0] if existing else {}
    item_key = item.get("key")
    collection_result = None
    warnings = []
    if item_key and collection:
        try:
            collection_result = db_add_item_to_collection(item_key, collection)
        except Exception as exc:
            warnings.append(f"collection update failed: {exc}")
    sidecar_result = {}
    if item_key:
        try:
            sidecar_result = attach_arxiv_sidecars(item_key, arxiv_id, attach_html=attach_html)
            warnings.extend(sidecar_result.get("warnings", []))
        except Exception as exc:
            warnings.append(f"sidecar top-up failed: {exc}")
    return {
        "status": "existing",
        "arxiv_id": arxiv_id,
        "arxivId": arxiv_id,
        "item_key": item_key,
        "zoteroItemKey": item_key,
        "attachment_key": sidecar_result.get("attachment_key"),
        "snapshot_key": sidecar_result.get("snapshot_key"),
        "abstract_snapshot_key": sidecar_result.get("abstract_snapshot_key"),
        "html_snapshot_key": sidecar_result.get("html_snapshot_key"),
        "arxiv_abs_url": sidecar_result.get("arxiv_abs_url"),
        "arxiv_pdf_url": sidecar_result.get("arxiv_pdf_url"),
        "arxiv_html_url": sidecar_result.get("arxiv_html_url"),
        "pdfAttachmentKey": sidecar_result.get("pdfAttachmentKey"),
        "abstractSnapshotKey": sidecar_result.get("abstractSnapshotKey"),
        "htmlSnapshotKey": sidecar_result.get("htmlSnapshotKey"),
        "arxivAbsUrl": sidecar_result.get("arxivAbsUrl"),
        "arxivPdfUrl": sidecar_result.get("arxivPdfUrl"),
        "arxivHtmlUrl": sidecar_result.get("arxivHtmlUrl"),
        "sidecars": sidecar_result.get("sidecars", {}),
        "existing": item,
        "matches": existing,
        "collection": collection_result,
        "warnings": warnings,
    }

def op_arxiv(arxiv, collection_name_or_key=None, attach_html=True, force=False):
    ensure_debug_bridge()
    try:
        arxiv_id = _extract_arxiv_id(arxiv)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if not force:
        existing = db_find_arxiv_item(arxiv_id) or []
        if existing:
            return _existing_arxiv_result(
                arxiv_id,
                existing,
                collection=collection_name_or_key,
                attach_html=attach_html,
            )
    result = import_arxiv(arxiv_id, collection_name_or_key=collection_name_or_key, attach_html=attach_html)
    result.setdefault("status", "added")
    return result

def op_search_arxiv(query, limit=5):
    return search_arxiv(query, limit=limit)

def op_capture_arxiv(paper, confirmed_arxiv_id=None, collection=None, attach_html=True, force=False):
    paper = (paper or "").strip()
    if not paper:
        raise ValueError("paper is required")
    if confirmed_arxiv_id:
        return op_arxiv(confirmed_arxiv_id, collection_name_or_key=collection, attach_html=attach_html, force=force)
    try:
        arxiv_id = _extract_arxiv_id(paper)
    except ValueError:
        arxiv_id = None
    if arxiv_id:
        return op_arxiv(arxiv_id, collection_name_or_key=collection, attach_html=attach_html, force=force)
    search = op_search_arxiv(paper, limit=5)
    return {
        "status": "needs_selection",
        "query": paper,
        "message": "Title searches are read-only. Pass confirmed_arxiv_id to capture one candidate.",
        "candidates": search["candidates"],
    }

def op_attach_snapshot(key, url, title="Web Page Snapshot"):
    ensure_debug_bridge()
    require_item_key(key)
    snapshot_key = db_add_snapshot(key, url, title=title)
    return {"snapshot_key": snapshot_key, "url": url, "title": title}

def op_delete_items(keys, permanent=False):
    ensure_debug_bridge()
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
