"""Remote PDF discovery, download, and Web API attachment operations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from zotero_mcp.config import API_BASE, BACKEND, CROSSREF_EMAIL, PDF_SOURCES
from zotero_mcp.local_api import ensure_local_api, get_local_client
from zotero_mcp.local_ops import attach_pdf_from_file
from zotero_mcp.metadata import _extract_year, _first_author_last
from zotero_mcp.pdfs import _download_pdf
from zotero_mcp.validators import require_item_key
from zotero_mcp.web_api import api_request, get_api_config, paginate_all


def _try_unpaywall(doi):
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='')}"
    if CROSSREF_EMAIL:
        url += "?" + urllib.parse.urlencode({"email": CROSSREF_EMAIL})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        oa = data.get("best_oa_location") or {}
        pdf_url = oa.get("url_for_pdf")
        if pdf_url:
            return (pdf_url, pdf_url)
        return None
    except Exception:
        return None


def _try_semantic_scholar(doi):
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi, safe='')}?fields=openAccessPdf"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        oa = data.get("openAccessPdf") or {}
        pdf_url = oa.get("url")
        if pdf_url:
            return (pdf_url, pdf_url)
        return None
    except Exception:
        return None


def _try_doi_content_negotiation(doi):
    url = f"https://doi.org/{urllib.parse.quote(doi, safe='/:')}"
    req = urllib.request.Request(url, headers={"Accept": "application/pdf"}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if "application/pdf" in resp.headers.get("Content-Type", ""):
                return (resp.url, url)
        return None
    except Exception:
        return None


def _find_pdf_source(doi, sources):
    source_funcs = {
        "unpaywall": (_try_unpaywall, 1),
        "semanticscholar": (_try_semantic_scholar, 1),
        "doi": (_try_doi_content_negotiation, 2),
    }
    for src in sources:
        if src not in source_funcs:
            continue
        func, delay = source_funcs[src]
        result = func(doi)
        if result:
            return (result[0], result[1], src)
        time.sleep(delay)
    return None


def _create_linked_url_attachment(api_key, prefix, parent_key, title, url):
    attachment = [{
        "itemType": "attachment",
        "parentItem": parent_key,
        "linkMode": "linked_url",
        "title": title,
        "url": url,
        "contentType": "application/pdf",
        "tags": [],
        "relations": {},
    }]
    body, _ = api_request(f"{prefix}/items", api_key, method="POST", data=attachment, content_type="application/json")
    result = json.loads(body) if body.strip() else {}
    return bool(result.get("successful"))


def _upload_pdf_to_zotero(api_key, prefix, parent_key, filepath, filename):
    if BACKEND == "local":
        path = os.path.abspath(filepath)
        try:
            with open(path, "rb") as handle:
                file_bytes = handle.read()
            get_local_client().create_attachment(
                parent_key,
                filename=filename,
                content_type="application/pdf",
                title=filename,
                data=file_bytes,
                link_mode="imported_file",
                mtime_ms=int(os.path.getmtime(path) * 1000),
            )
            return True
        except Exception:
            return False

    attachment = [{
        "itemType": "attachment",
        "parentItem": parent_key,
        "linkMode": "imported_file",
        "title": filename,
        "filename": filename,
        "contentType": "application/pdf",
        "tags": [],
        "relations": {},
    }]
    body, _ = api_request(f"{prefix}/items", api_key, method="POST", data=attachment, content_type="application/json")
    result = json.loads(body) if body.strip() else {}
    success = result.get("successful", {})
    if not success:
        return False

    attach_key = list(success.values())[0]["key"]
    with open(filepath, "rb") as f:
        file_bytes = f.read()
    file_md5 = hashlib.md5(file_bytes).hexdigest()

    auth_params = urllib.parse.urlencode({
        "md5": file_md5,
        "filename": filename,
        "filesize": str(len(file_bytes)),
        "mtime": str(int(os.path.getmtime(filepath) * 1000)),
    })

    auth_url = f"{API_BASE}{prefix}/items/{attach_key}/file"
    auth_headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
        "Content-Type": "application/x-www-form-urlencoded",
        "If-None-Match": "*",
    }
    auth_req = urllib.request.Request(auth_url, data=auth_params.encode("utf-8"), headers=auth_headers, method="POST")
    try:
        with urllib.request.urlopen(auth_req, timeout=30) as resp:
            auth_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return False

    if auth_data.get("exists"):
        return True

    upload_req = urllib.request.Request(
        auth_data.get("url"),
        data=auth_data.get("prefix", "").encode("utf-8") + file_bytes + auth_data.get("suffix", "").encode("utf-8"),
        headers={"Content-Type": auth_data.get("contentType", "application/x-www-form-urlencoded")},
        method="POST",
    )
    try:
        with urllib.request.urlopen(upload_req, timeout=120):
            pass
    except urllib.error.HTTPError:
        return False

    reg_params = urllib.parse.urlencode({"upload": auth_data.get("uploadKey", "")})
    reg_req = urllib.request.Request(auth_url, data=reg_params.encode("utf-8"), headers=auth_headers, method="POST")
    try:
        with urllib.request.urlopen(reg_req, timeout=30):
            pass
        return True
    except urllib.error.HTTPError:
        return False


def _make_pdf_filename(item_data, item_key):
    first_author = _first_author_last(item_data) or "Unknown"
    year = _extract_year(item_data.get("date", "")) or "NoDate"
    safe_author = re.sub(r"[^\w]", "", first_author.capitalize())
    return f"{safe_author}{year}_{item_key}.pdf"


def _bulk_find_pdf_parents(api_key, prefix, collection_key=None):
    all_items = paginate_all(f"{prefix}/collections/{collection_key}/items" if collection_key else f"{prefix}/items", api_key)
    pdf_parents = set()
    parents = {}
    for item in all_items:
        d = item["data"]
        itype = d.get("itemType", "")
        if itype == "attachment":
            ct = d.get("contentType", "")
            title = d.get("title", "") + d.get("filename", "")
            if "pdf" in ct.lower() or title.lower().endswith(".pdf"):
                parent_key = d.get("parentItem", "")
                if parent_key:
                    pdf_parents.add(parent_key)
        elif itype != "note":
            parents[d["key"]] = item
    return parents, pdf_parents


def _attach_local_pdf(key, file, title="Full Text PDF"):
    ensure_local_api()
    require_item_key(key)
    return {"attachment_key": attach_pdf_from_file(key, file, title=title)}


def op_fetch_pdfs(
    key=None,
    file=None,
    title="Full Text PDF",
    collection=None,
    limit=None,
    force=False,
    sources=None,
    download_dir="pdfs",
    dry_run=False,
    download_only=False,
    link_only=False,
):
    if key or file:
        if not (key and file):
            raise RuntimeError("Error: local mode requires both --key and --file")
        return _attach_local_pdf(key, file, title=title)

    api_key, prefix = get_api_config()
    source_names = [s.strip().lower() for s in (sources or ",".join(PDF_SOURCES)).split(",") if s.strip()]
    path = f"{prefix}/collections/{collection}/items/top" if collection else f"{prefix}/items/top"
    items = paginate_all(path, api_key)
    parents, pdf_parents = _bulk_find_pdf_parents(api_key, prefix, collection_key=collection)

    candidates = []
    for item in items:
        data = item.get("data", {})
        item_key = data.get("key")
        if not item_key or item_key not in parents:
            continue
        if not data.get("DOI", "").strip():
            continue
        if item_key in pdf_parents and not force:
            continue
        candidates.append(item)

    if limit:
        candidates = candidates[:limit]

    if not candidates:
        return {
            "processed": 0,
            "downloaded": 0,
            "attached": 0,
            "linked": 0,
            "failed": 0,
            "results": [],
        }

    os.makedirs(download_dir, exist_ok=True)
    summary = {"processed": 0, "downloaded": 0, "attached": 0, "linked": 0, "failed": 0}
    results = []
    for item in candidates:
        summary["processed"] += 1
        data = item["data"]
        item_key = data["key"]
        doi = data.get("DOI", "").strip()
        entry = {"key": item_key, "title": data.get("title", "untitled"), "doi": doi}
        source_info = _find_pdf_source(doi, source_names)
        if not source_info:
            summary["failed"] += 1
            entry["status"] = "no_source"
            results.append(entry)
            continue

        pdf_url, source_url, source_name = source_info
        filename = _make_pdf_filename(data, item_key)
        local_path = os.path.join(download_dir, filename)
        entry.update({"source": source_name, "pdfUrl": pdf_url, "sourceUrl": source_url, "localPath": local_path})

        if dry_run:
            entry["status"] = "dry_run"
            results.append(entry)
            continue

        if not _download_pdf(pdf_url, local_path):
            summary["failed"] += 1
            entry["status"] = "download_failed"
            results.append(entry)
            continue
        summary["downloaded"] += 1

        if download_only:
            entry["status"] = "downloaded"
            results.append(entry)
            continue

        if link_only:
            ok = _create_linked_url_attachment(api_key, prefix, item_key, title, source_url)
            if ok:
                summary["linked"] += 1
                entry["status"] = "linked"
            else:
                summary["failed"] += 1
                entry["status"] = "link_failed"
            results.append(entry)
            continue

        ok = _upload_pdf_to_zotero(api_key, prefix, item_key, local_path, filename)
        if ok:
            summary["attached"] += 1
            entry["status"] = "attached"
        else:
            summary["failed"] += 1
            entry["status"] = "upload_failed"
        results.append(entry)

    summary["results"] = results
    return summary
