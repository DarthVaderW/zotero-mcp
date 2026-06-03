#!/usr/bin/env python3
"""Structured Zotero operations shared by the MCP server and CLI."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from zotero_mcp.config import API_BASE, CROSSREF_EMAIL, DOI_EXCLUDED_ITEM_TYPES, PDF_SOURCES
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
from zotero_mcp.pdfs import _download_pdf as _download_pdf
from zotero_mcp.validators import (
    require_doi,
    require_isbn,
    require_item_key,
    require_item_type as require_item_type,
    validate_doi as validate_doi,
    validate_isbn as validate_isbn,
    validate_item_key as validate_item_key,
)
from zotero_mcp.web_api import api_get_json, api_request, get_api_config, paginate_all

def fmt_creators(creators):
    parts = []
    for c in creators[:3]:
        parts.append(c.get("lastName", c.get("name", "?")))
    if len(creators) > 3:
        parts.append("et al.")
    return ", ".join(parts)

def fmt_item_short(item):
    d = item["data"]
    creators = fmt_creators(d.get("creators", []))
    year = ""
    if d.get("date"):
        m = re.match(r"(\d{4})", d["date"])
        if m:
            year = m.group(1)
    title = d.get("title", "untitled")
    itype = d.get("itemType", "?")
    key = d.get("key", "?")
    return f"[{key}] {creators} ({year}) {title} [{itype}]"

def _extract_year(date_str):
    if not date_str:
        return None
    m = re.match(r"(\d{4})", str(date_str))
    return m.group(1) if m else None

def _first_author_last(item_data):
    creators = item_data.get("creators", [])
    if not creators:
        return None
    c = creators[0]
    name = c.get("lastName", c.get("name", ""))
    return name.lower().strip() if name else None

def _normalize_text(text):
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _title_similarity(a, b):
    return difflib.SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()

def _check_duplicate_by_metadata(api_key, prefix, new_item, identifier, id_type):
    creators = new_item.get("creators", [])
    title = new_item.get("title", "")

    search_terms = []
    if creators:
        last_name = creators[0].get("lastName", creators[0].get("name", ""))
        if last_name:
            search_terms.append(last_name)
    if title:
        words = [w for w in title.split() if len(w) > 4 and w.lower() not in ("about", "between", "their", "these", "those", "which", "where", "other")]
        if words:
            search_terms.append(words[0])
    if not search_terms:
        return None

    items, _ = api_get_json(f"{prefix}/items/top", api_key, params={"q": " ".join(search_terms), "limit": "25"})
    if not isinstance(items, list):
        return None

    for item in items:
        d = item.get("data", {})
        if id_type == "doi" and d.get("DOI"):
            if d["DOI"].lower().strip().rstrip("/") == identifier.lower().strip().rstrip("/"):
                return item
        if id_type == "isbn" and d.get("ISBN"):
            if identifier.replace("-", "") in d["ISBN"].replace("-", ""):
                return item
        if title and d.get("title") and title.lower().strip()[:60] == d["title"].lower().strip()[:60]:
            return item

    return None

def _doi_to_item(doi):
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    work = data.get("message", {})
    item = {
        "itemType": "journalArticle",
        "title": " ".join(work.get("title", ["Untitled"])),
        "DOI": doi,
        "url": work.get("URL", ""),
        "date": "",
        "creators": [],
        "tags": [],
        "abstractNote": work.get("abstract", ""),
    }
    issued = work.get("issued", {}).get("date-parts", [[]])
    if issued and issued[0]:
        item["date"] = "-".join(str(p) for p in issued[0])
    for author in work.get("author", []):
        item["creators"].append({"creatorType": "author", "firstName": author.get("given", ""), "lastName": author.get("family", "")})
    container = work.get("container-title", [])
    if container:
        item["publicationTitle"] = container[0]
    item["volume"] = work.get("volume", "")
    item["issue"] = work.get("issue", "")
    item["pages"] = work.get("page", "")
    return [item]

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

def _identifier_lookup_url(identifier, id_type):
    if id_type == "doi":
        require_doi(identifier)
        return f"https://doi.org/{identifier}"
    if id_type == "isbn":
        require_isbn(identifier)
        return f"https://www.worldcat.org/isbn/{identifier}"
    if id_type == "pmid":
        return f"https://pubmed.ncbi.nlm.nih.gov/{identifier}/"
    raise RuntimeError(f"Unknown identifier type: {id_type}")

def _translate_identifier(identifier, id_type):
    lookup_url = _identifier_lookup_url(identifier, id_type)
    translate_data = json.dumps({"url": lookup_url, "sessionid": "zotero-cli"}).encode("utf-8")
    translate_req = urllib.request.Request(
        "https://translate.zotero.org/web",
        data=translate_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(translate_req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if id_type == "doi":
            translated = _doi_to_item(identifier)
            if translated:
                return translated
        raise RuntimeError(f"Translation failed: {e.code} {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Translation failed: {e}") from e

def op_add_identifier(identifier, id_type="doi", collection=None, tags=None, force=False):
    api_key, prefix = get_api_config()
    translated = _translate_identifier(identifier, id_type)
    if not translated:
        raise RuntimeError("No metadata found for this identifier.")

    new_items = translated[:1] if isinstance(translated, list) else [translated]
    if not force:
        existing = _check_duplicate_by_metadata(api_key, prefix, new_items[0], identifier, id_type)
        if existing:
            data = existing.get("data", existing)
            return {
                "status": "duplicate",
                "identifier": identifier,
                "idType": id_type,
                "existing": {
                    "key": data.get("key", existing.get("key", "")),
                    "title": data.get("title", existing.get("title", "")),
                    "summary": fmt_item_short(existing),
                },
            }

    tag_values = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    for item in new_items:
        for field in ["key", "version", "dateAdded", "dateModified", "relations"]:
            item.pop(field, None)
        if collection:
            item["collections"] = [collection]
        if tag_values:
            existing_tags = item.get("tags", [])
            existing_tags.extend({"tag": tag} for tag in tag_values)
            item["tags"] = existing_tags

    body, _ = api_request(f"{prefix}/items", api_key, method="POST", data=new_items, content_type="application/json")
    response = json.loads(body) if body.strip() else {}
    successful = [
        {"key": item.get("key", ""), "title": item.get("data", {}).get("title", "untitled")}
        for item in response.get("successful", {}).values()
    ]
    failed = [
        {"message": err.get("message", "unknown error")}
        for err in response.get("failed", {}).values()
    ]
    if failed:
        return {
            "status": "failed",
            "identifier": identifier,
            "idType": id_type,
            "successful": successful,
            "failed": failed,
        }
    return {
        "status": "added",
        "identifier": identifier,
        "idType": id_type,
        "successful": successful,
        "raw": response,
    }

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

def op_batch_add(file, id_type="doi", collection=None, tags=None, force=False, sleep_seconds=1):
    get_api_config()
    with open(file, "r", encoding="utf-8") as f:
        identifiers = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    results = []
    for identifier in identifiers:
        try:
            result = op_add_identifier(
                identifier,
                id_type=id_type,
                collection=collection,
                tags=tags,
                force=force,
            )
        except RuntimeError as exc:
            result = {
                "status": "failed",
                "identifier": identifier,
                "idType": id_type,
                "error": str(exc),
            }
        results.append(result)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    return {
        "total": len(identifiers),
        "added": sum(1 for item in results if item.get("status") == "added"),
        "skipped": sum(1 for item in results if item.get("status") == "duplicate"),
        "failed": sum(1 for item in results if item.get("status") == "failed"),
        "results": results,
    }

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
        return op_attach_pdf(key, file, title=title)

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
