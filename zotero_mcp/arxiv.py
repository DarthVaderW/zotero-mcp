"""arXiv item import and PDF/HTML sidecar workflows."""

from __future__ import annotations

import os
import re
import tempfile

from zotero_mcp import arxiv_metadata as metadata
from zotero_mcp.local_library import (
    attach_pdf_from_file, create_item, db_add_item_to_collection, db_add_snapshot,
    db_get_children,
)
from zotero_mcp.pdfs import download_pdf


def _child_text(child, key):
    return str((child or {}).get(key) or "").strip()


def find_existing_pdf_child(children):
    for child in children or []:
        if child.get("itemType") != "attachment":
            continue
        content_type = _child_text(child, "contentType").lower()
        title = _child_text(child, "title").lower()
        url = _child_text(child, "url").lower()
        if "pdf" in content_type or title.endswith(".pdf") or " pdf" in title or "/pdf/" in url:
            return child
    return None


def _find_existing_arxiv_html_child(children, arxiv_id, html_url=None):
    base_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE).lower()
    exact_html_url = (html_url or "").lower().rstrip("/")
    html_fragments = [f"/html/{arxiv_id.lower()}", f"/html/{base_id}"]
    for child in children or []:
        if child.get("itemType") != "attachment":
            continue
        content_type = _child_text(child, "contentType").lower()
        title = _child_text(child, "title").lower()
        url = _child_text(child, "url").lower().rstrip("/")
        if exact_html_url and url == exact_html_url:
            return child
        if any(fragment in url for fragment in html_fragments):
            return child
        if "arxiv html" in title and ("html" in content_type or not content_type):
            return child
    return None


def attach_arxiv_sidecars(item_key, arxiv_id, attach_html=True, children=None):
    """Attach missing arXiv PDF/HTML sidecars to an existing Zotero item."""
    arxiv_id = metadata.extract_arxiv_id(arxiv_id)
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    warnings = []
    sidecars = {}
    current_children = list(children if children is not None else (db_get_children(item_key) or []))

    pdf_child = find_existing_pdf_child(current_children)
    attachment_key = pdf_child.get("key") if pdf_child else None
    if pdf_child:
        sidecars["pdf"] = {"status": "existing", "key": attachment_key}
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            if not download_pdf(pdf_url, tmp_path):
                raise RuntimeError(f"Failed to download arXiv PDF: {pdf_url}")
            attachment_key = attach_pdf_from_file(item_key, tmp_path, title="Preprint PDF")
            sidecars["pdf"] = {"status": "added", "key": attachment_key}
        except Exception as exc:
            warnings.append(f"pdf attachment failed: {exc}")
            sidecars["pdf"] = {"status": "failed", "error": str(exc)}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    html_url = None
    html_snapshot_key = None
    if attach_html:
        html_child = _find_existing_arxiv_html_child(current_children, arxiv_id)
        if html_child:
            html_snapshot_key = html_child.get("key")
            html_url = html_child.get("url") or None
            sidecars["html"] = {"status": "existing", "key": html_snapshot_key}
        else:
            try:
                html_url = metadata.find_arxiv_html_url(arxiv_id)
                if html_url:
                    html_snapshot_key = db_add_snapshot(item_key, html_url, title="arXiv HTML Snapshot")
                    sidecars["html"] = {"status": "added", "key": html_snapshot_key}
                else:
                    sidecars["html"] = {"status": "unavailable"}
            except Exception as exc:
                warnings.append(f"html snapshot failed: {exc}")
                sidecars["html"] = {"status": "failed", "error": str(exc)}
    else:
        sidecars["html"] = {"status": "skipped"}

    return {
        "attachment_key": attachment_key,
        "snapshot_key": None,
        "abstract_snapshot_key": None,
        "html_snapshot_key": html_snapshot_key,
        "arxiv_abs_url": abs_url,
        "arxiv_pdf_url": pdf_url,
        "arxiv_html_url": html_url,
        "warnings": warnings,
        "sidecars": sidecars,
        "pdfAttachmentKey": attachment_key,
        "abstractSnapshotKey": None,
        "htmlSnapshotKey": html_snapshot_key,
        "arxivAbsUrl": abs_url,
        "arxivPdfUrl": pdf_url,
        "arxivHtmlUrl": html_url,
    }


def import_arxiv(arxiv_id_or_url, collection_name_or_key=None, attach_html=True):
    arxiv_id = metadata.extract_arxiv_id(arxiv_id_or_url)
    source = "translator"
    warnings = []
    try:
        meta = metadata.fetch_arxiv_metadata_via_translator(arxiv_id)
    except Exception:
        source = "manual"
        meta = metadata.fetch_arxiv_metadata(arxiv_id)

    try:
        page_meta = metadata.fetch_arxiv_metadata_from_abs_page(arxiv_id)
        if not (meta.get("extra_fields", {}).get("DOI", "") or "").strip():
            meta.setdefault("extra_fields", {})["DOI"] = page_meta.get("extra_fields", {}).get("DOI", "")
        if not meta.get("__pdf_url"):
            meta["__pdf_url"] = page_meta.get("__pdf_url")
    except Exception:
        pass

    if not (meta.get("extra_fields", {}).get("DOI", "") or "").strip():
        meta.setdefault("extra_fields", {})["DOI"] = f"10.48550/arXiv.{arxiv_id.split('v')[0]}"

    pdf_url = meta.pop("__pdf_url", f"https://arxiv.org/pdf/{arxiv_id}")
    abs_url = meta.get("url", f"https://arxiv.org/abs/{arxiv_id}")

    item_key = create_item(meta)

    snapshot_key = None
    try:
        snapshot_key = db_add_snapshot(item_key, abs_url)
    except Exception as exc:
        warnings.append(f"abstract snapshot failed: {exc}")

    html_url = None
    html_snapshot_key = None
    if attach_html:
        try:
            html_url = metadata.find_arxiv_html_url(arxiv_id)
            if html_url:
                html_snapshot_key = db_add_snapshot(item_key, html_url, title="arXiv HTML Snapshot")
        except Exception as exc:
            warnings.append(f"html snapshot failed: {exc}")

    attachment_key = None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not download_pdf(pdf_url, tmp_path):
            raise RuntimeError(f"Failed to download arXiv PDF: {pdf_url}")
        attachment_key = attach_pdf_from_file(item_key, tmp_path, title="Preprint PDF")
    except Exception as exc:
        warnings.append(f"pdf attachment failed: {exc}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    collection = None
    if collection_name_or_key:
        try:
            collection = db_add_item_to_collection(item_key, collection_name_or_key)
        except Exception:
            pass

    return {
        "source": source,
        "arxiv_id": arxiv_id,
        "item_key": item_key,
        "attachment_key": attachment_key,
        "snapshot_key": snapshot_key,
        "abstract_snapshot_key": snapshot_key,
        "html_snapshot_key": html_snapshot_key,
        "arxiv_abs_url": abs_url,
        "arxiv_pdf_url": pdf_url,
        "arxiv_html_url": html_url,
        "collection": collection,
        "warnings": warnings,
        "zoteroItemKey": item_key,
        "pdfAttachmentKey": attachment_key,
        "abstractSnapshotKey": snapshot_key,
        "htmlSnapshotKey": html_snapshot_key,
        "arxivId": arxiv_id,
        "arxivAbsUrl": abs_url,
        "arxivPdfUrl": pdf_url,
        "arxivHtmlUrl": html_url,
    }
