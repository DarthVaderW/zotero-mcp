#!/usr/bin/env python3
"""Structured Zotero operations shared by the MCP server and CLI."""

from __future__ import annotations

import re

from zotero_mcp.config import PDF_SOURCES as PDF_SOURCES
from zotero_mcp.debug_bridge import (
    db_delete_item,
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
from zotero_mcp.arxiv import import_arxiv as import_arxiv
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

def op_create_item(meta):
    ensure_debug_bridge()
    return {"item_key": create_item(meta)}

def op_attach_pdf(key, file, title="Full Text PDF"):
    ensure_debug_bridge()
    require_item_key(key)
    return {"attachment_key": attach_pdf_from_file(key, file, title=title)}

def op_arxiv(arxiv, collection_name_or_key=None):
    ensure_debug_bridge()
    return import_arxiv(arxiv, collection_name_or_key=collection_name_or_key)

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
