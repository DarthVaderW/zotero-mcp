"""arXiv import workflow for local Zotero libraries."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from zotero_mcp.debug_bridge import db_add_item_to_collection, db_add_snapshot, db_get_children
from zotero_mcp.local_ops import attach_pdf_from_file, create_item
from zotero_mcp.pdfs import _download_pdf


def _mcp_user_agent() -> str:
    return "ZoteroMCP/1.0 (+https://github.com/DarthVaderW/zotero-mcp)"


def _read_url(req, timeout=30, retries=1):
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt + 1 < retries:
                time.sleep(3 * (attempt + 1))
                continue
            if exc.code == 429:
                raise RuntimeError("arXiv API rate limited this request (HTTP 429); retry later.") from exc
            raise RuntimeError(f"arXiv HTTP {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"arXiv network error: {last_error}") from last_error


def _extract_arxiv_id(arxiv_id_or_url):
    s = arxiv_id_or_url.strip()
    m = re.search(r"arxiv\.org/(abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}(v\d+)?)", s, re.I)
    if m:
        return m.group(2)
    m = re.match(r"^([0-9]{4}\.[0-9]{4,5}(v\d+)?)$", s, re.I)
    if m:
        return m.group(1)
    raise ValueError(f"Invalid arXiv ID or URL: {arxiv_id_or_url}")


def _clean_text(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def _creator_display_names(creators):
    names = []
    for creator in creators:
        if creator.get("name"):
            names.append(creator["name"])
            continue
        name = " ".join(part for part in [creator.get("firstName", ""), creator.get("lastName", "")] if part).strip()
        if name:
            names.append(name)
    return names


def _title_score(query, title):
    try:
        from zotero_mcp.metadata import _title_similarity

        return round(float(_title_similarity(query, title)), 4)
    except Exception:
        return 0.0


def _escape_arxiv_query_value(value):
    return (value or "").replace("\\", "\\\\").replace('"', r"\"")


def _arxiv_id_from_url(url):
    match = re.search(r"arxiv\.org/abs/([^?#]+)", url or "", re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _metadata_to_candidate(meta, query=None):
    arxiv_id = meta.get("extra_fields", {}).get("archiveLocation") or _arxiv_id_from_url(meta.get("url", ""))
    abs_url = meta.get("url") or f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = meta.get("__pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}"
    title = _clean_text(meta.get("title", ""))
    candidate = {
        "arxiv_id": arxiv_id,
        "arxivId": arxiv_id,
        "title": title,
        "authors": _creator_display_names(meta.get("creators", [])),
        "published": meta.get("date", ""),
        "abstract": _clean_text(meta.get("abstract", "")),
        "abs_url": abs_url,
        "pdf_url": pdf_url,
        "arxivAbsUrl": abs_url,
        "arxivPdfUrl": pdf_url,
        "doi": meta.get("extra_fields", {}).get("DOI", ""),
    }
    if query:
        candidate["score"] = _title_score(query, title)
    return candidate


def _entry_to_candidate(entry, ns, query):
    entry_url = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
    arxiv_id = _arxiv_id_from_url(entry_url)
    title = _clean_text(entry.findtext("atom:title", default="", namespaces=ns) or "")
    authors = [
        _clean_text(author.findtext("atom:name", default="", namespaces=ns) or "")
        for author in entry.findall("atom:author", ns)
    ]
    authors = [author for author in authors if author]
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""
    for link in entry.findall("atom:link", ns):
        if (link.attrib.get("title") or "").lower() == "pdf" and link.attrib.get("href"):
            pdf_url = link.attrib["href"]
            break
    doi = (entry.findtext("arxiv:doi", default="", namespaces=ns) or "").strip()
    if not doi and arxiv_id:
        base_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
        doi = f"10.48550/arXiv.{base_id}"
    return {
        "arxiv_id": arxiv_id,
        "arxivId": arxiv_id,
        "title": title,
        "authors": authors,
        "published": (entry.findtext("atom:published", default="", namespaces=ns) or "")[:10],
        "updated": (entry.findtext("atom:updated", default="", namespaces=ns) or "")[:10],
        "abstract": _clean_text(entry.findtext("atom:summary", default="", namespaces=ns) or ""),
        "abs_url": entry_url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
        "pdf_url": pdf_url,
        "arxivAbsUrl": entry_url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
        "arxivPdfUrl": pdf_url,
        "doi": doi,
        "score": _title_score(query, title),
    }


def search_arxiv(query, limit=5):
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(limit or 5), 25))

    try:
        arxiv_id = _extract_arxiv_id(query)
    except ValueError:
        arxiv_id = None

    if arxiv_id:
        return {
            "query": query,
            "total": 1,
            "candidates": [_metadata_to_candidate(_fetch_arxiv_metadata(arxiv_id), query=query)],
        }

    params = {
        "search_query": f'ti:"{_escape_arxiv_query_value(query)}"',
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/atom+xml", "User-Agent": _mcp_user_agent()})
    xml_text = _read_url(req, timeout=30, retries=2)

    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RuntimeError("arXiv API returned invalid XML") from exc
    candidates = [_entry_to_candidate(entry, ns, query) for entry in root.findall("atom:entry", ns)]
    candidates = [candidate for candidate in candidates if candidate.get("arxiv_id")]
    return {"query": query, "total": len(candidates), "candidates": candidates[:limit]}


def _fetch_arxiv_metadata_from_abs_page(arxiv_id):
    url = f"https://arxiv.org/abs/{arxiv_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _mcp_user_agent()})
    html_text = _read_url(req, timeout=30, retries=2).decode("utf-8", errors="replace")

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
        xml_text = _read_url(req, timeout=30, retries=2)
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


def _find_arxiv_html_url(arxiv_id):
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    req = urllib.request.Request(abs_url, headers={"User-Agent": _mcp_user_agent()})
    html_text = _read_url(req, timeout=30, retries=2).decode("utf-8", errors="replace")

    for match in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL):
        href = html.unescape(match.group(1)).strip()
        label = re.sub(r"<.*?>", "", match.group(2)).strip()
        if not href:
            continue
        if "HTML" in label or "/html/" in href:
            return urllib.parse.urljoin(abs_url, href)
    return None


def _child_text(child, key):
    return str((child or {}).get(key) or "").strip()


def _find_existing_pdf_child(children):
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
    arxiv_id = _extract_arxiv_id(arxiv_id)
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    warnings = []
    sidecars = {}
    current_children = list(children if children is not None else (db_get_children(item_key) or []))

    pdf_child = _find_existing_pdf_child(current_children)
    attachment_key = pdf_child.get("key") if pdf_child else None
    if pdf_child:
        sidecars["pdf"] = {"status": "existing", "key": attachment_key}
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            if not _download_pdf(pdf_url, tmp_path):
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
                html_url = _find_arxiv_html_url(arxiv_id)
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
    arxiv_id = _extract_arxiv_id(arxiv_id_or_url)
    source = "translator"
    warnings = []
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
            html_url = _find_arxiv_html_url(arxiv_id)
            if html_url:
                html_snapshot_key = db_add_snapshot(item_key, html_url, title="arXiv HTML Snapshot")
        except Exception as exc:
            warnings.append(f"html snapshot failed: {exc}")

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
