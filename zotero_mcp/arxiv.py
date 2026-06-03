"""arXiv import workflow for local Zotero libraries."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from zotero_mcp.debug_bridge import db_add_item_to_collection, db_add_snapshot
from zotero_mcp.local_ops import attach_pdf_from_file, create_item
from zotero_mcp.pdfs import _download_pdf


def _mcp_user_agent() -> str:
    return "ZoteroMCP/1.0 (+https://github.com/DarthVaderW/zotero-mcp)"


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
