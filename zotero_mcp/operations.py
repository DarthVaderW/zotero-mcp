#!/usr/bin/env python3
"""Structured Zotero operations shared by the MCP server and CLI."""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from zotero_mcp.config import API_BASE, CROSSREF_EMAIL, DOI_EXCLUDED_ITEM_TYPES, PDF_SOURCES
from zotero_mcp.debug_bridge import (
    db_add_attachment,
    db_add_item_to_collection,
    db_add_snapshot,
    db_create_item,
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
from zotero_mcp.validators import (
    require_doi,
    require_isbn,
    require_item_key,
    require_item_type,
    validate_doi as validate_doi,
    validate_isbn as validate_isbn,
    validate_item_key as validate_item_key,
)
from zotero_mcp.web_api import api_get_json, api_request, get_api_config, paginate_all

def _pdf_user_agent() -> str:
    contact = f"; mailto:{CROSSREF_EMAIL}" if CROSSREF_EMAIL else ""
    return f"Mozilla/5.0 (compatible; ZoteroCLI/1.0{contact})"

def _mcp_user_agent() -> str:
    return "ZoteroMCP/1.0 (+https://github.com/DarthVaderW/zotero-mcp)"

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

def _download_pdf(url, dest_path):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _pdf_user_agent(),
            "Accept": "application/pdf,*/*",
        },
    )
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                with open(dest_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            with open(dest_path, "rb") as f:
                if f.read(5) != b"%PDF-":
                    os.unlink(dest_path)
                    return False
            return True
        except Exception:
            if os.path.exists(dest_path):
                os.unlink(dest_path)
            time.sleep(1)
    return False

def create_item(meta):
    payload = dict(meta or {})
    if "abstractNote" not in payload and "abstract" in payload:
        payload["abstractNote"] = payload["abstract"]
    payload.pop("abstract", None)
    extra_fields = payload.pop("extra_fields", {})
    if isinstance(extra_fields, dict):
        payload.update(extra_fields)
    require_item_type(payload)
    payload.setdefault("title", "")
    result = db_create_item(payload)
    if isinstance(result, dict) and result.get("success"):
        return result.get("key")
    raise RuntimeError(f"Create item failed: {result}")

def attach_pdf_from_file(parent_item_key, pdf_path, title="Full Text PDF"):
    result = db_add_attachment(parent_item_key, pdf_path, title=title)
    if result.get("success"):
        return result.get("attachment_key")
    raise RuntimeError(result.get("error", "Unknown attachment error"))

def _extract_arxiv_id(arxiv_id_or_url):
    s = arxiv_id_or_url.strip()
    m = re.search(r"arxiv\.org/(abs|pdf)/([0-9]{4}\.[0-9]{4,5}(v\d+)?)", s, re.I)
    if m:
        return m.group(2)
    m = re.match(r"^([0-9]{4}\.[0-9]{4,5}(v\d+)?)$", s, re.I)
    if m:
        return m.group(1)
    raise ValueError(f"Invalid arXiv ID or URL: {arxiv_id_or_url}")

def _fetch_arxiv_metadata_from_abs_page(arxiv_id):
    url = f"https://arxiv.org/abs/{arxiv_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _mcp_user_agent()})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html_text = resp.read().decode("utf-8", errors="replace")

    def _meta(name):
        m = re.search(rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"\s*/?>', html_text, re.IGNORECASE)
        return html.unescape(m.group(1)).strip() if m else ""

    authors = re.findall(r'<meta\s+name="citation_author"\s+content="([^"]*)"\s*/?>', html_text, flags=re.IGNORECASE)
    creators = []
    for name in authors:
        n = html.unescape(name).strip()
        if not n:
            continue
        parts = n.split()
        if len(parts) == 1:
            creators.append({"creatorType": "author", "name": n})
        else:
            creators.append({"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]})

    doi = _meta("citation_doi")
    if not doi:
        m = re.search(r"https?://doi\.org/(10\.48550/arXiv\.[0-9]{4}\.[0-9]{4,5})", html_text, re.I)
        if m:
            doi = m.group(1)
    if not doi:
        doi = f"10.48550/arXiv.{arxiv_id.split('v')[0]}"

    return {
        "itemType": "preprint",
        "title": _meta("citation_title"),
        "abstract": _meta("citation_abstract"),
        "url": _meta("citation_abstract_html_url") or url,
        "date": (_meta("citation_date") or "")[:10],
        "creators": creators,
        "extra_fields": {
            "archive": "arXiv",
            "archiveLocation": arxiv_id,
            "DOI": doi,
        },
        "__pdf_url": _meta("citation_pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}",
    }

def _fetch_arxiv_metadata(arxiv_id):
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id})
    req = urllib.request.Request(url, headers={"Accept": "application/atom+xml", "User-Agent": _mcp_user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_text = resp.read()
    except Exception:
        return _fetch_arxiv_metadata_from_abs_page(arxiv_id)

    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml_text)
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise RuntimeError(f"arXiv metadata not found for {arxiv_id}")

    creators = []
    for author in entry.findall("atom:author", ns):
        name = (author.findtext("atom:name", default="", namespaces=ns) or "").strip()
        if not name:
            continue
        parts = name.split()
        if len(parts) == 1:
            creators.append({"creatorType": "author", "name": name})
        else:
            creators.append({"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]})

    doi = (entry.findtext("arxiv:doi", default="", namespaces=ns) or "").strip()
    if not doi:
        doi = f"10.48550/arXiv.{arxiv_id.split('v')[0]}"

    return {
        "itemType": "preprint",
        "title": (entry.findtext("atom:title", default="", namespaces=ns) or "").strip(),
        "abstract": (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip(),
        "url": (entry.findtext("atom:id", default="", namespaces=ns) or "").strip() or f"https://arxiv.org/abs/{arxiv_id}",
        "date": (entry.findtext("atom:published", default="", namespaces=ns) or "")[:10],
        "creators": creators,
        "extra_fields": {"archive": "arXiv", "archiveLocation": arxiv_id, "DOI": doi},
        "__pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
    }

def _fetch_arxiv_metadata_via_translator(arxiv_id):
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    payload = json.dumps({"url": abs_url, "sessionid": "zotero-cli"}).encode("utf-8")
    req = urllib.request.Request("https://translate.zotero.org/web", data=payload, headers={"Content-Type": "application/json"}, method="POST")

    translated = None
    last_err = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                translated = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as e:
            last_err = e
            time.sleep(1)

    if translated is None:
        raise RuntimeError(f"Translator service unavailable: {last_err}")

    item = translated[0] if isinstance(translated, list) else translated
    pdf_url = ""
    for att in item.get("attachments", []) or []:
        mime = (att.get("mimeType", "") or "").lower()
        if "pdf" in mime and att.get("url"):
            pdf_url = att["url"]
            break

    return {
        "itemType": item.get("itemType", "preprint"),
        "title": item.get("title", ""),
        "abstract": item.get("abstractNote", ""),
        "url": item.get("url", abs_url),
        "date": item.get("date", ""),
        "creators": item.get("creators", []) or [],
        "extra_fields": {
            "archive": "arXiv",
            "archiveLocation": arxiv_id,
            "DOI": (item.get("DOI", "") or "").strip(),
        },
        "__pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
    }

def import_arxiv(arxiv_id_or_url, collection_name_or_key=None):
    arxiv_id = _extract_arxiv_id(arxiv_id_or_url)
    source = "translator"
    try:
        meta = _fetch_arxiv_metadata_via_translator(arxiv_id)
    except Exception:
        source = "manual"
        meta = _fetch_arxiv_metadata(arxiv_id)

    try:
        page_meta = _fetch_arxiv_metadata_from_abs_page(arxiv_id)
        if not (meta.get("extra_fields", {}).get("DOI", "") or "").strip():
            meta.setdefault("extra_fields", {})["DOI"] = page_meta.get("extra_fields", {}).get("DOI", "")
        if not meta.get("__pdf_url"):
            meta["__pdf_url"] = page_meta.get("__pdf_url")
    except Exception:
        pass

    if not (meta.get("extra_fields", {}).get("DOI", "") or "").strip():
        meta.setdefault("extra_fields", {})["DOI"] = f"10.48550/arXiv.{arxiv_id.split('v')[0]}"

    pdf_url = meta.pop("__pdf_url", f"https://arxiv.org/pdf/{arxiv_id}")

    item_key = create_item(meta)

    snapshot_key = None
    try:
        snapshot_key = db_add_snapshot(item_key, meta.get("url", f"https://arxiv.org/abs/{arxiv_id}"))
    except Exception:
        pass

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not _download_pdf(pdf_url, tmp_path):
            raise RuntimeError(f"Failed to download arXiv PDF: {pdf_url}")
        attachment_key = attach_pdf_from_file(item_key, tmp_path, title="Preprint PDF")
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
        "collection": collection,
    }

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
