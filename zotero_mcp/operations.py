#!/usr/bin/env python3
"""Structured Zotero operations shared by the MCP server and CLI."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from zotero_mcp.config import API_BASE, CROSSREF_EMAIL, DOI_EXCLUDED_ITEM_TYPES, PDF_SOURCES as PDF_SOURCES
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
from zotero_mcp.web_api import api_get_json, api_request, get_api_config, paginate_all

def _crossref_search(title, first_author):
    params = {"query.bibliographic": title, "rows": "3"}
    if CROSSREF_EMAIL:
        params["mailto"] = CROSSREF_EMAIL
    if first_author:
        params["query.author"] = first_author
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("message", {}).get("items", [])
    except Exception:
        return []

def _match_crossref_result(work, zotero_title, zotero_year, zotero_first_author):
    cr_title = " ".join(work.get("title", [""]))
    sim = _title_similarity(zotero_title, cr_title)
    if sim < 0.85:
        return None

    issued = work.get("issued", {}).get("date-parts", [[]])
    cr_year = str(issued[0][0]) if issued and issued[0] else None
    if zotero_year and cr_year and zotero_year != cr_year:
        return None
    if zotero_year and not cr_year:
        return None

    if zotero_first_author:
        author_found = False
        for a in work.get("author", []):
            family = a.get("family", "").lower()
            if family and (zotero_first_author in family or family in zotero_first_author):
                author_found = True
                break
        if not author_found:
            return None

    doi = work.get("DOI", "")
    if not doi:
        return None

    return (doi, {"similarity": round(sim * 100, 1), "cr_title": cr_title, "cr_year": cr_year})

def _patch_item_field(api_key, prefix, item_key, field, value, version):
    url = f"{API_BASE}{prefix}/items/{item_key}"
    headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
        "If-Unmodified-Since-Version": str(version),
    }
    body = json.dumps({field: value}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status

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

def op_update_item(
    key,
    title=None,
    date=None,
    doi=None,
    url=None,
    add_tags=None,
    remove_tags=None,
    add_collection=None,
):
    api_key, prefix = get_api_config()
    require_item_key(key)

    item, headers = api_get_json(f"{prefix}/items/{key}", api_key)
    version = headers.get("Last-Modified-Version", "0")
    data = item.get("data", {})

    changes = {}
    if title:
        changes["title"] = title
    if date:
        changes["date"] = date
    if doi is not None:
        changes["DOI"] = doi
    if url is not None:
        changes["url"] = url

    current_tags = [tag["tag"] for tag in data.get("tags", [])]
    tags_changed = False
    if add_tags:
        for tag in add_tags.split(","):
            tag = tag.strip()
            if tag and tag not in current_tags:
                current_tags.append(tag)
                tags_changed = True
    if remove_tags:
        for tag in remove_tags.split(","):
            tag = tag.strip()
            if tag in current_tags:
                current_tags.remove(tag)
                tags_changed = True
    if tags_changed:
        changes["tags"] = [{"tag": tag} for tag in current_tags]

    if add_collection:
        current_cols = list(data.get("collections", []))
        if add_collection not in current_cols:
            current_cols.append(add_collection)
            changes["collections"] = current_cols

    if not changes:
        return {"status": "no_changes", "key": key, "changes": {}}

    req_headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
        "If-Unmodified-Since-Version": str(version),
    }
    req = urllib.request.Request(
        f"{API_BASE}{prefix}/items/{key}",
        data=json.dumps(changes).encode("utf-8"),
        headers=req_headers,
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else ""
        detail = f"Update failed: {e.code} {e.reason}"
        if err_body:
            detail += f"\n{err_body[:500]}"
        raise RuntimeError(detail) from e

    return {"status": "updated", "key": key, "changes": changes}

def op_export(format="bibtex", collection=None, output=None):
    api_key, prefix = get_api_config()
    path = f"{prefix}/collections/{collection}/items" if collection else f"{prefix}/items/top"
    params = {"format": format, "limit": "100"}
    chunks = []
    start = 0
    while True:
        params["start"] = str(start)
        body, headers = api_request(path, api_key, params=params)
        if body.strip():
            chunks.append(body)
        total = int(headers.get("Total-Results", "0"))
        start += 100
        if start >= total:
            break

    text = "\n".join(chunks)
    result = {"format": format, "collection": collection, "bytes": len(text)}
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        result["output"] = output
    else:
        result["text"] = text
    return result

def op_check_pdfs():
    api_key, prefix = get_api_config()
    all_items = paginate_all(f"{prefix}/items", api_key)

    parents = {}
    pdf_parents = set()
    for item in all_items:
        data = item["data"]
        item_type = data.get("itemType", "")
        if item_type == "attachment":
            if data.get("contentType", "").startswith("application/pdf") and data.get("parentItem"):
                pdf_parents.add(data["parentItem"])
        elif item_type != "note":
            parents[data["key"]] = item

    with_pdf = [parents[key] for key in parents if key in pdf_parents]
    without_pdf = [parents[key] for key in parents if key not in pdf_parents]
    return {
        "total": len(with_pdf) + len(without_pdf),
        "with_pdf": len(with_pdf),
        "without_pdf": len(without_pdf),
        "missing": [
            {"key": item["data"].get("key", ""), "title": item["data"].get("title", "")}
            for item in without_pdf
        ],
    }

def _extract_citations(text):
    patterns = [
        r"([A-Z][a-zé]+(?:\s+(?:et\s+al\.|&\s+[A-Z][a-zé]+))?)\s*\((\d{4})\)",
        r"([A-Z][a-zé]+(?:\s+(?:et\s+al\.|,?\s+(?:and|&)\s+[A-Z][a-zé]+))?),?\s+(\d{4})",
    ]
    citations = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            citations.add((match.group(1).strip().rstrip(","), match.group(2)))
    return sorted(citations)

def op_crossref(file):
    api_key, prefix = get_api_config()
    with open(file, "r", encoding="utf-8") as f:
        text = f.read()

    citations = _extract_citations(text)
    if not citations:
        return {"total": 0, "found": [], "missing": []}

    items = paginate_all(f"{prefix}/items/top", api_key)
    items = [item for item in items if item["data"].get("itemType") not in ("attachment", "note")]
    lib_index = {}
    for item in items:
        data = item["data"]
        year = _extract_year(data.get("date", "")) or ""
        for creator in data.get("creators", []):
            last = creator.get("lastName", creator.get("name", ""))
            if last and year:
                lib_index.setdefault((last.lower(), year), []).append(item)

    found = []
    missing = []
    for author, year in citations:
        key = (author.split()[0].lower().rstrip(","), year)
        match_item = None
        if key in lib_index:
            match_item = lib_index[key][0]
        else:
            for (lib_author, lib_year), lib_items in lib_index.items():
                if lib_year == year and (lib_author.startswith(key[0][:4]) or key[0].startswith(lib_author[:4])):
                    match_item = lib_items[0]
                    break
        if match_item:
            data = match_item["data"]
            found.append(
                {
                    "author": author,
                    "year": year,
                    "key": data.get("key", ""),
                    "title": data.get("title", ""),
                }
            )
        else:
            missing.append({"author": author, "year": year})

    return {"total": len(citations), "found": found, "missing": missing}

def op_find_dois(apply=False, limit=None, collection=None, sleep_seconds=1):
    api_key, prefix = get_api_config()
    path = f"{prefix}/collections/{collection}/items/top" if collection else f"{prefix}/items/top"
    items = paginate_all(path, api_key)

    candidates = []
    skipped_has_doi = skipped_wrong_type = 0
    for item in items:
        data = item["data"]
        item_type = data.get("itemType", "")
        if item_type in DOI_EXCLUDED_ITEM_TYPES:
            skipped_wrong_type += 1
            continue
        if data.get("DOI", "").strip():
            skipped_has_doi += 1
            continue
        candidates.append(item)

    if limit:
        candidates = candidates[:limit]

    results = []
    matched = unmatched = written = write_failed = 0
    for item in candidates:
        data = item["data"]
        title = data.get("title", "")
        year = _extract_year(data.get("date", ""))
        first_author = _first_author_last(data)
        key = data.get("key", "?")
        entry = {"key": key, "title": title, "year": year, "firstAuthor": first_author}
        if not title:
            unmatched += 1
            entry["status"] = "unmatched"
            results.append(entry)
            continue

        works = _crossref_search(title, first_author or "")
        if sleep_seconds:
            time.sleep(sleep_seconds)
        best = None
        for work in works:
            match = _match_crossref_result(work, title, year, first_author)
            if match:
                best = match
                break

        if not best:
            unmatched += 1
            entry["status"] = "unmatched"
            results.append(entry)
            continue

        doi, info = best
        matched += 1
        entry.update({"status": "matched", "doi": doi, "match": info})
        if apply:
            try:
                version = item.get("version", item.get("data", {}).get("version", 0))
                _patch_item_field(api_key, prefix, key, "DOI", doi, version)
                written += 1
                entry["written"] = True
            except Exception as e:
                write_failed += 1
                entry["written"] = False
                entry["writeError"] = str(e)
        results.append(entry)

    return {
        "processed": len(candidates),
        "matched": matched,
        "unmatched": unmatched,
        "alreadyHadDoi": skipped_has_doi,
        "wrongItemType": skipped_wrong_type,
        "apply": apply,
        "written": written,
        "writeFailed": write_failed,
        "results": results,
    }
