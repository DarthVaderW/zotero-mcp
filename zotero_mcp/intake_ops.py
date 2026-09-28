"""Identifier and arXiv intake workflows shared by MCP and CLI."""

from __future__ import annotations

import os
import tempfile

from zotero_mcp.arxiv import find_existing_pdf_child, attach_arxiv_sidecars, import_arxiv
from zotero_mcp.arxiv_metadata import extract_arxiv_id, search_arxiv
from zotero_mcp.config import PDF_SOURCES
from zotero_mcp.identifiers import translate_identifier, clean_translated_item_for_local
from zotero_mcp.local_api import ensure_local_api
from zotero_mcp.local_library import (
    attach_pdf_from_file, create_item, db_add_item_to_collection,
    db_find_arxiv_item, db_find_item_by_identifier, db_get_children, db_get_item,
)
from zotero_mcp.pdf_discovery import find_pdf_source
from zotero_mcp.pdfs import download_pdf
from zotero_mcp.validators import require_item_key, validate_id_type


def _identifier_result_item(item_key, created=False, existing=None):
    return {
        "created": created,
        "item_key": item_key,
        "zoteroItemKey": item_key,
        "zoteroSelectUri": f"zotero://select/library/items/{item_key}"
        if item_key
        else "",
        "existing": existing,
    }


def _local_pdf_result(
    item_key, doi, attach_pdf=True, title="Full Text PDF", children=None
):
    if not attach_pdf:
        return {"pdfStatus": "skipped", "pdfSourceAttempts": []}

    current_children = list(
        children if children is not None else (db_get_children(item_key) or [])
    )
    pdf_child = find_existing_pdf_child(current_children)
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

    source_info = find_pdf_source(doi, source_names)
    if not source_info:
        return {
            "pdfStatus": "needs_user_file",
            "pdfSourceAttempts": source_names,
            "warnings": [
                "No open PDF source was found; attach a user-provided local file later."
            ],
        }

    pdf_url, source_url, source_name = source_info
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not download_pdf(pdf_url, tmp_path):
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
    id_type = validate_id_type(id_type)
    ensure_local_api()
    translated = translate_identifier(identifier, id_type)
    if not translated:
        raise RuntimeError("No metadata found for this identifier.")

    item = translated[0] if isinstance(translated, list) else translated
    payload = clean_translated_item_for_local(item, tags=tags)
    title = payload.get("title", "")
    warnings = []
    collection_result = None

    if not force:
        existing = (
            db_find_item_by_identifier(identifier, id_type=id_type, title=title) or []
        )
        if existing:
            item_key = existing[0].get("key")
            if item_key and collection:
                try:
                    collection_result = db_add_item_to_collection(item_key, collection)
                except Exception as exc:
                    warnings.append(f"collection update failed: {exc}")
            pdf_result = (
                _local_pdf_result(
                    item_key,
                    _identifier_pdf_doi(identifier, id_type, payload),
                    attach_pdf=attach_pdf,
                    children=db_get_children(item_key) if item_key else [],
                )
                if item_key
                else {"pdfStatus": "skipped", "pdfSourceAttempts": []}
            )
            warnings.extend(pdf_result.pop("warnings", []))
            return {
                "status": "existing",
                "identifier": identifier,
                "idType": id_type,
                **_identifier_result_item(
                    item_key, created=False, existing=existing[0]
                ),
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
    ensure_local_api()
    require_item_key(key)
    try:
        arxiv_id = extract_arxiv_id(arxiv)
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
            sidecar_result = attach_arxiv_sidecars(
                item_key, arxiv_id, attach_html=attach_html
            )
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
    ensure_local_api()
    try:
        arxiv_id = extract_arxiv_id(arxiv)
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
    result = import_arxiv(
        arxiv_id, collection_name_or_key=collection_name_or_key, attach_html=attach_html
    )
    result.setdefault("status", "added")
    return result


def op_search_arxiv(query, limit=5):
    return search_arxiv(query, limit=limit)


def op_capture_arxiv(
    paper, confirmed_arxiv_id=None, collection=None, attach_html=True, force=False
):
    paper = (paper or "").strip()
    if not paper:
        raise ValueError("paper is required")
    if confirmed_arxiv_id:
        return op_arxiv(
            confirmed_arxiv_id,
            collection_name_or_key=collection,
            attach_html=attach_html,
            force=force,
        )
    try:
        arxiv_id = extract_arxiv_id(paper)
    except ValueError:
        arxiv_id = None
    if arxiv_id:
        return op_arxiv(
            arxiv_id,
            collection_name_or_key=collection,
            attach_html=attach_html,
            force=force,
        )
    search = op_search_arxiv(paper, limit=5)
    return {
        "status": "needs_selection",
        "query": paper,
        "message": "Title searches are read-only. Pass confirmed_arxiv_id to capture one candidate.",
        "candidates": search["candidates"],
    }
