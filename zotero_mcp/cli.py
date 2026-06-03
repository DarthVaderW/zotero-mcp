#!/usr/bin/env python3
"""Zotero CLI — local debug-bridge first, Web API for remote/lookup/export workflows.

Local debug-bridge commands (require ZOTERO_DEBUG_BRIDGE_TOKEN):
  ping, items, search, get, collections, tags, children,
  create-item, attach-pdf, arxiv, delete, fetch-pdfs --key --file

Web API commands (require ZOTERO_API_KEY + ZOTERO_USER_ID|ZOTERO_GROUP_ID):
  add-doi, add-isbn, add-pmid, update, export, batch-add,
  check-pdfs, crossref, find-dois, fetch-pdfs (remote mode)
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

API_BASE = "https://api.zotero.org"

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


load_dotenv(ROOT_DIR / ".env")

DEBUG_BRIDGE_URL = os.environ.get(
    "ZOTERO_DEBUG_BRIDGE_URL",
    "http://127.0.0.1:23119/debug-bridge/execute",
)
DEBUG_BRIDGE_TOKEN = os.environ.get("ZOTERO_DEBUG_BRIDGE_TOKEN")
DEBUG_BRIDGE_LIBRARY_ID = int(os.environ.get("ZOTERO_LIBRARY_ID", "1"))

CROSSREF_EMAIL = os.environ.get("CROSSREF_EMAIL", "").strip()
DOI_EXCLUDED_ITEM_TYPES = {"attachment", "note"}
PDF_SOURCES = ["unpaywall", "semanticscholar", "doi"]

_MAX_RETRIES = 2
_RETRY_CODES = {429, 503}
_json_mode = False


class CommandError(RuntimeError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def _enable_json_mode() -> None:
    _set_json_mode(True)


def _set_json_mode(enabled: bool) -> None:
    global _json_mode
    _json_mode = enabled


def _json_print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _json_error(message: str, code: int = 0) -> None:
    print(json.dumps({"error": message, "code": code}), file=sys.stderr)


def _pdf_user_agent() -> str:
    contact = f"; mailto:{CROSSREF_EMAIL}" if CROSSREF_EMAIL else ""
    return f"Mozilla/5.0 (compatible; ZoteroCLI/1.0{contact})"


def _mcp_user_agent() -> str:
    return "ZoteroMCP/1.0 (+https://github.com/DarthVaderW/zotero-mcp)"


def ensure_debug_bridge() -> None:
    if not DEBUG_BRIDGE_TOKEN:
        raise RuntimeError(
            "Error: ZOTERO_DEBUG_BRIDGE_TOKEN is required for this command\n"
            "Set it from your local Zotero debug-bridge plugin."
        )


def require_debug_bridge() -> None:
    try:
        ensure_debug_bridge()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


def get_api_config() -> tuple[str, str]:
    api_key = os.environ.get("ZOTERO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Error: ZOTERO_API_KEY environment variable not set\n"
            "Create a key at https://www.zotero.org/settings/keys/new"
        )

    user_id = os.environ.get("ZOTERO_USER_ID")
    group_id = os.environ.get("ZOTERO_GROUP_ID")
    if not user_id and not group_id:
        raise RuntimeError("Error: Set ZOTERO_USER_ID or ZOTERO_GROUP_ID")

    prefix = f"/users/{user_id}" if user_id else f"/groups/{group_id}"
    return api_key, prefix


def debug_bridge(js_code: str):
    req = urllib.request.Request(
        DEBUG_BRIDGE_URL,
        data=js_code.encode("utf-8"),
        headers={
            "Content-Type": "text/plain",
            "Authorization": f"Bearer {DEBUG_BRIDGE_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"Debug-bridge HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Debug-bridge network error: {e.reason}") from e

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def api_request(path, api_key, method="GET", data=None, content_type=None, params=None):
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
    }
    if content_type:
        headers["Content-Type"] = content_type

    body = None
    if data is not None:
        if isinstance(data, str):
            body = data.encode("utf-8")
        elif isinstance(data, bytes):
            body = data
        else:
            body = json.dumps(data).encode("utf-8")
            if not content_type:
                headers["Content-Type"] = "application/json"

    for attempt in range(_MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_CODES and attempt < _MAX_RETRIES:
                time.sleep((attempt + 1) * 2)
                continue
            err_body = e.read().decode("utf-8") if e.fp else ""
            msg = f"API Error {e.code}: {e.reason}"
            if err_body:
                msg += f"\n{err_body[:500]}"
            raise CommandError(msg, e.code) from e
        except urllib.error.URLError as e:
            if attempt < _MAX_RETRIES:
                time.sleep((attempt + 1) * 2)
                continue
            msg = f"Network error: {e.reason}"
            raise CommandError(msg, 0) from e

    raise CommandError(f"Request failed after {_MAX_RETRIES + 1} attempts", 0)


def api_get_json(path, api_key, params=None):
    body, headers = api_request(path, api_key, params=params)
    return (json.loads(body) if body.strip() else {}), headers


def paginate_all(path, api_key, params=None):
    params = dict(params or {})
    params.setdefault("limit", "100")
    all_items = []
    start = 0
    while True:
        params["start"] = str(start)
        items, headers = api_get_json(path, api_key, params=params)
        if not isinstance(items, list):
            return [items]
        all_items.extend(items)
        total = int(headers.get("Total-Results", len(all_items)))
        if len(all_items) >= total:
            break
        start = len(all_items)
    return all_items


def validate_doi(s):
    if not re.match(r"^10\.\d{4,}/\S+$", s):
        print(f"Invalid DOI format: '{s}'. Expected pattern: 10.xxxx/...", file=sys.stderr)
        return False
    return True


def require_doi(s):
    if not re.match(r"^10\.\d{4,}/\S+$", s):
        raise RuntimeError(f"Invalid DOI format: '{s}'. Expected pattern: 10.xxxx/...")
    return s


def validate_item_key(s):
    if not re.match(r"^[A-Za-z0-9]{8}$", s):
        print(f"Invalid item key: '{s}'. Must be 8 alphanumeric characters.", file=sys.stderr)
        return False
    return True


def require_item_key(s):
    if not re.match(r"^[A-Za-z0-9]{8}$", s):
        raise RuntimeError(f"Invalid item key: '{s}'. Must be 8 alphanumeric characters.")
    return s


def validate_isbn(s):
    cleaned = s.replace("-", "").replace(" ", "")
    if not re.match(r"^\d{10}(\d{3})?$", cleaned):
        print(f"Invalid ISBN: '{s}'. Must be 10 or 13 digits.", file=sys.stderr)
        return False
    return True


def require_isbn(s):
    cleaned = s.replace("-", "").replace(" ", "")
    if not re.match(r"^\d{10}(\d{3})?$", cleaned):
        raise RuntimeError(f"Invalid ISBN: '{s}'. Must be 10 or 13 digits.")
    return s


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


# --- Local debug-bridge operations ---

def db_ping():
    return debug_bridge("""
await Zotero.Schema.schemaUpdatePromise;
return Zotero.version;
""")


def db_get_items(limit=100, collection_key=None):
    if collection_key:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const collection = Zotero.Collections.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(collection_key)});
if (!collection) throw new Error("Collection not found");
const items = await collection.getChildItems(false, false);
return items.slice(0, {int(limit)}).map(i => ({{
  key: i.key,
  itemType: Zotero.ItemTypes.getName(i.itemTypeID),
  title: i.getDisplayTitle(),
  creators: i.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
  dateAdded: i.dateAdded,
  dateModified: i.dateModified
}}));
"""
    else:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const items = await Zotero.Items.getAll({DEBUG_BRIDGE_LIBRARY_ID}, false, ["itemType", "title", "creators", "dateAdded", "dateModified"]);
return items.slice(0, {int(limit)}).map(i => ({{
  key: i.key,
  itemType: Zotero.ItemTypes.getName(i.itemTypeID),
  title: i.getDisplayTitle(),
  creators: i.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
  dateAdded: i.dateAdded,
  dateModified: i.dateModified
}}));
"""
    return debug_bridge(js)


def db_search(query, limit=50):
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const search = new Zotero.Search();
search.libraryID = {DEBUG_BRIDGE_LIBRARY_ID};
search.addCondition("quicksearch", "contains", {json.dumps(query)});
const itemIDs = await search.search();
const items = [];
for (const id of itemIDs.slice(0, {int(limit)})) {{
  const item = Zotero.Items.get(id);
  if (!item) continue;
  items.push({{
    key: item.key,
    itemType: Zotero.ItemTypes.getName(item.itemTypeID),
    title: item.getDisplayTitle(),
    creators: item.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
    dateAdded: item.dateAdded
  }});
}}
return items;
"""
    return debug_bridge(js)


def db_get_item(key):
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return null;
return {{
  key: item.key,
  itemType: Zotero.ItemTypes.getName(item.itemTypeID),
  title: item.getDisplayTitle(),
  creators: item.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
  dateAdded: item.dateAdded,
  dateModified: item.dateModified,
  DOI: item.getField("DOI"),
  url: item.getField("url"),
  abstractNote: item.getField("abstractNote")
}};
"""
    return debug_bridge(js)


def db_get_children(key):
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return [];
const result = [];
for (const id of item.getAttachments()) {{
  const att = Zotero.Items.get(id);
  if (!att) continue;
  result.push({{ key: att.key, itemType: "attachment", title: att.getDisplayTitle() || "Attachment", contentType: att.getField("contentType") }});
}}
for (const id of item.getNotes()) {{
  const note = Zotero.Items.get(id);
  if (!note) continue;
  result.push({{ key: note.key, itemType: "note", title: note.getDisplayTitle() || "Note" }});
}}
return result;
"""
    return debug_bridge(js)


def db_get_collections():
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const collections = Zotero.Collections.getByLibrary({DEBUG_BRIDGE_LIBRARY_ID});
return collections.slice(0, 200).map(c => ({{ key: c.key, name: c.getDisplayTitle(), dateAdded: c.dateAdded }}));
""")


def db_get_tags():
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const tags = await Zotero.Tags.getAll({DEBUG_BRIDGE_LIBRARY_ID});
return tags.slice(0, 200).map(t => ({{ name: t.tag, type: t.type }}));
""")


def require_item_type(payload):
    item_type = str(payload.get("itemType") or "").strip()
    if not item_type:
        raise RuntimeError("itemType is required. Pass an explicit Zotero item type.")
    payload["itemType"] = item_type
    return item_type


def db_create_item(item_data):
    payload = dict(item_data)
    require_item_type(payload)
    payload.setdefault("title", "")
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = new Zotero.Item({json.dumps(payload.get("itemType"))});
item.libraryID = {DEBUG_BRIDGE_LIBRARY_ID};
const data = {json.dumps(payload)};
for (const [k, v] of Object.entries(data)) {{
  if (k === "itemType" || k.startsWith("__") || v === null || v === undefined) continue;
  if (k === "creators" && Array.isArray(v)) {{ item.setCreators(v); continue; }}
  item.setField(k, v);
}}
await item.saveTx();
return {{ key: item.key, success: true }};
"""
    return debug_bridge(js)


def db_add_attachment(parent_key, file_path, title="Full Text PDF"):
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return {"success": False, "error": f"File not found: {abs_path}"}

    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const parent = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(parent_key)});
if (!parent) throw new Error("Parent item not found");
const file = Zotero.File.pathToFile({json.dumps(abs_path)});
if (!file.exists()) throw new Error("File not found");
const att = await Zotero.Attachments.importFromFile({{ file, parentItemID: parent.id }});
if (att && {json.dumps(title)}) {{
  att.setField("title", {json.dumps(title)});
  await att.saveTx();
}}
return att ? att.key : null;
"""
    result = debug_bridge(js)
    return {"success": bool(result), "attachment_key": result} if result else {"success": False, "error": "Failed to import file"}


def db_add_snapshot(parent_key, page_url, title="Web Page Snapshot"):
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const parent = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(parent_key)});
if (!parent) throw new Error("Parent item not found");
const att = await Zotero.Attachments.importFromURL({{
  libraryID: {DEBUG_BRIDGE_LIBRARY_ID},
  url: {json.dumps(page_url)},
  parentItemID: parent.id,
  title: {json.dumps(title)},
  contentType: "text/html"
}});
return att ? att.key : null;
""")


def db_add_item_to_collection(item_key, collection_name_or_key):
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const lib = {DEBUG_BRIDGE_LIBRARY_ID};
const item = Zotero.Items.getByLibraryAndKey(lib, {json.dumps(item_key)});
if (!item) throw new Error("Item not found");
const target = {json.dumps(collection_name_or_key)};
let col = /^[A-Za-z0-9]{{8}}$/.test(target) ? Zotero.Collections.getByLibraryAndKey(lib, target) : null;
if (!col) col = Zotero.Collections.getByLibrary(lib).find(c => c.name === target) || null;
if (!col) {{
  col = new Zotero.Collection();
  col.libraryID = lib;
  col.name = target;
  await col.saveTx();
}}
item.addToCollection(col.key);
await item.saveTx();
return {{ itemKey: item.key, collectionKey: col.key, collectionName: col.name }};
""")


def db_delete_item(key, permanent=False):
    if permanent:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return {{ success: false, error: "Item not found" }};
await item.eraseTx();
return {{ success: true, mode: "permanent" }};
"""
    else:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return {{ success: false, error: "Item not found" }};
item.deleted = true;
await item.saveTx();
return {{ success: true, mode: "trash" }};
"""
    return debug_bridge(js)


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


# --- arXiv workflow ---

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


# --- Web API helpers/commands ---

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
    except Exception as e:
        print(f"CrossRef lookup failed: {e}", file=sys.stderr)
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
    except Exception as e:
        print(f"    CrossRef request failed: {e}", file=sys.stderr)
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


# --- command handlers ---

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


def cmd_ping(_args):
    result = op_ping()
    if _json_mode:
        _json_print(result)
    else:
        print(result["zotero_version"])


def cmd_items(args):
    result = op_items(limit=args.limit, collection_key=args.collection)
    items = result["items"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Showing {len(items)} item(s)\n")
    for item in items:
        print(f"[{item.get('key','')}] {item.get('creators','')} ({item.get('dateAdded','')[:4]}) {item.get('title','Untitled')[:80]}")


def cmd_search(args):
    result = op_search(args.query, limit=args.limit)
    items = result["items"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Found {len(items)} result(s)\n")
    for item in items:
        print(f"[{item.get('key','')}] {item.get('creators','')} {item.get('title','Untitled')[:80]}")


def cmd_get(args):
    result = op_get(args.key)
    item = result["item"]
    children = result["children"]
    if _json_mode:
        _json_print(result)
        return
    if not item:
        print(f"Item {args.key} not found", file=sys.stderr)
        sys.exit(1)
    print(f"Title: {item.get('title', 'Untitled')}")
    print(f"Key: {item.get('key')}")
    print(f"Creators: {item.get('creators')}")
    print(f"Date Added: {item.get('dateAdded')}")
    print(f"Date Modified: {item.get('dateModified')}")
    if item.get("DOI"):
        print(f"DOI: {item.get('DOI')}")
    if item.get("url"):
        print(f"URL: {item.get('url')}")
    if children:
        print(f"\nChildren ({len(children)}):")
        for c in children:
            if c.get("itemType") == "attachment":
                print(f"  [ATT] [{c['key']}] {c.get('title', 'Attachment')} [{c.get('contentType', '?')}]")
            else:
                print(f"  [NOTE] [{c['key']}] {c.get('title', 'Note')}")


def cmd_collections(_args):
    result = op_collections()
    cols = result["collections"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Collections ({len(cols)}):\n")
    for c in cols:
        print(f"[{c['key']}] {c['name']}")


def cmd_tags(_args):
    result = op_tags()
    tags = result["tags"]
    if _json_mode:
        _json_print(result)
        return
    print(f"Tags ({len(tags)}):\n")
    for t in tags:
        print(t["name"])


def cmd_children(args):
    result = op_children(args.key)
    children = result["children"]
    if _json_mode:
        _json_print(result)
        return
    if not children:
        print("No children found.")
        return
    for c in children:
        if c.get("itemType") == "attachment":
            print(f"[ATT] [{c['key']}] {c.get('title', 'Attachment')} [{c.get('contentType', '?')}]")
        else:
            print(f"[NOTE] [{c['key']}] {c.get('title', 'Note')}")


def cmd_create_item(args):
    meta = json.loads(args.meta_json) if args.meta_json else {}
    result = op_create_item(meta)
    if _json_mode:
        _json_print(result)
    else:
        print(result["item_key"])


def cmd_attach_pdf(args):
    result = op_attach_pdf(args.key, args.file)
    if _json_mode:
        _json_print(result)
    else:
        print(result["attachment_key"])


def cmd_arxiv(args):
    result = op_arxiv(args.arxiv, collection_name_or_key=args.collection)
    if _json_mode:
        _json_print(result)
    else:
        print(json.dumps(result, ensure_ascii=False))


def cmd_delete(args):
    if args.permanent and args.trash:
        print("Error: --permanent and --trash are mutually exclusive", file=sys.stderr)
        sys.exit(1)
    permanent = bool(args.permanent)
    keys = []
    if not args.yes:
        require_debug_bridge()
        mode = "permanently delete" if permanent else "move to trash"
        for key in args.keys:
            item = db_get_item(key)
            title = item.get("title", "Untitled") if item else "Missing item"
            if not args.yes:
                print(f"[{key}] {title}")
                confirm = input(f"{mode.capitalize()}? [y/N] ").strip().lower()
                if confirm != "y":
                    print("Skipped.")
                    continue
            keys.append(key)
    else:
        keys = args.keys

    result = op_delete_items(keys, permanent=permanent)
    if _json_mode:
        _json_print(result)
        return
    for item in result["invalid"]:
        print(f"Invalid item key: '{item['key']}'. Must be 8 alphanumeric characters.", file=sys.stderr)
    for item in result["missing"]:
        print(f"Item {item['key']} not found", file=sys.stderr)
    for item in result["deleted"]:
        print(f"OK: {item['title']} [{item['key']}] ({item.get('mode', 'unknown')})")
    for item in result["failed"]:
        print(f"Failed: {item.get('error', 'Unknown error')}", file=sys.stderr)


def cmd_add_identifier(args):
    result = op_add_identifier(
        args.identifier,
        id_type=args.id_type,
        collection=args.collection,
        tags=args.tags,
        force=getattr(args, "force", False),
    )
    if _json_mode:
        _json_print(result)
        return result["status"]
    if result["status"] == "duplicate":
        print(f"Already in library: {result['existing']['summary']}")
        print("Use --force to add anyway.")
    elif result["status"] == "added":
        for item in result["successful"]:
            print(f"Added: {item.get('title', 'untitled')} [{item.get('key', '')}]")
    elif result["status"] == "failed":
        for item in result.get("failed", []):
            print(f"Failed: {item.get('message', 'unknown error')}", file=sys.stderr)
    return result["status"]


def cmd_update(args):
    result = op_update_item(
        args.key,
        title=args.title,
        date=args.date,
        doi=args.doi,
        url=args.url,
        add_tags=args.add_tags,
        remove_tags=args.remove_tags,
        add_collection=args.add_collection,
    )
    if _json_mode:
        _json_print(result)
        return
    if result["status"] == "no_changes":
        print("No changes specified.")
        return
    print("Updated successfully.")


def cmd_export(args):
    result = op_export(format=args.format, collection=args.collection, output=args.output)
    if _json_mode:
        output = dict(result)
        if "text" in output:
            output["text"] = output["text"]
        _json_print(output)
        return
    if args.output:
        print(f"Exported to {args.output} ({result['bytes']} bytes)")
    else:
        print(result["text"])


def cmd_batch_add(args):
    result = op_batch_add(
        args.file,
        id_type=args.type,
        collection=args.collection,
        tags=args.tags,
        force=args.force,
    )
    if _json_mode:
        _json_print(result)
        return
    if not result["total"]:
        print("No identifiers found in file.")
        return
    for index, item in enumerate(result["results"], 1):
        print(f"[{index}/{result['total']}] {item.get('identifier', '')}")
    print(f"Added: {result['added']}")
    print(f"Skipped: {result['skipped']}")
    print(f"Failed: {result['failed']}")


def cmd_check_pdfs(_args):
    result = op_check_pdfs()
    if _json_mode:
        _json_print(result)
        return
    print("PDF Attachment Report")
    print(f"Total items: {result['total']}")
    print(f"With PDF:    {result['with_pdf']}")
    print(f"Without PDF: {result['without_pdf']}")
    if result["missing"]:
        print("\nItems missing PDFs:")
        for item in result["missing"]:
            print(f"  [{item['key']}] {item['title']}")


def cmd_crossref(args):
    result = op_crossref(args.file)
    if _json_mode:
        _json_print(result)
        return
    if not result["total"]:
        print("No citations found in file. Expected format: Author (Year)")
        return
    print(f"Citations in file: {result['total']}")
    print(f"Found in library:  {len(result['found'])}")
    print(f"Missing:           {len(result['missing'])}")


def cmd_find_dois(args):
    result = op_find_dois(apply=args.apply, limit=args.limit, collection=args.collection)
    if _json_mode:
        _json_print(result)
        return
    print(f"Found {result['processed']} items missing DOIs")
    for index, item in enumerate(result["results"], 1):
        print(f"[{index}/{result['processed']}] [{item['key']}] {item.get('title', '')[:80]}")
        if item["status"] == "matched":
            print(f"  Match: {item['doi']} (title similarity: {item['match']['similarity']}%)")
            if item.get("written"):
                print("  DOI written")
            elif item.get("writeError"):
                print(f"  Failed to write DOI: {item['writeError']}", file=sys.stderr)
    print(f"Processed: {result['processed']}")
    print(f"Matched: {result['matched']}")
    print(f"Unmatched: {result['unmatched']}")
    print(f"Already had DOI: {result['alreadyHadDoi']}")
    print(f"Wrong item type: {result['wrongItemType']}")
    if result["matched"] and not args.apply:
        print("Dry run mode. Use --apply to write DOIs.")


def cmd_fetch_pdfs(args):
    """Two modes:
    1) Local attach mode (debug bridge): --key + --file
    2) Remote OA fetch mode (Web API): scans items by DOI and attaches PDFs
    """
    result = op_fetch_pdfs(
        key=args.key,
        file=args.file,
        title=args.title,
        collection=args.collection,
        limit=args.limit,
        force=args.force,
        sources=args.sources,
        download_dir=args.download_dir,
        dry_run=args.dry_run,
        download_only=args.download_only,
        link_only=args.link_only,
    )
    if _json_mode:
        _json_print(result)
        return
    if args.key or args.file:
        if result.get("attachment_key"):
            print(f"Attached: {args.file} -> [{args.key}]")
        else:
            print("Attach failed", file=sys.stderr)
        return
    if not result["processed"]:
        print("No candidate items to fetch PDFs for.")
        return
    for index, item in enumerate(result["results"], 1):
        print(f"[{index}/{result['processed']}] [{item['key']}] {item.get('title', 'untitled')[:70]}")
        if item["status"] == "no_source":
            print("  No OA PDF source found")
        elif item["status"] == "dry_run":
            print(f"  DRY-RUN {item['source']}: {item['pdfUrl']}")
        elif item["status"] == "download_failed":
            print(f"  Download failed: {item['pdfUrl']}")
        elif item["status"] == "downloaded":
            print(f"  Saved: {item['localPath']}")
        elif item["status"] == "linked":
            print(f"  Linked URL attachment ({item['source']})")
        elif item["status"] == "link_failed":
            print("  Failed to create linked URL attachment")
        elif item["status"] == "attached":
            print(f"  Uploaded PDF ({item['source']})")
        elif item["status"] == "upload_failed":
            print("  Upload failed")
    print("\nfetch-pdfs summary")
    print(f"Processed: {result['processed']}")
    print(f"Downloaded: {result['downloaded']}")
    print(f"Attached(uploaded): {result['attached']}")
    print(f"Linked URL: {result['linked']}")
    print(f"Failed: {result['failed']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Zotero CLI — debug-bridge local workflows + Web API remote workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable text")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    p = subparsers.add_parser("ping", help="Check local debug-bridge connection/version")

    p = subparsers.add_parser("items", help="List local Zotero items via debug-bridge")
    p.add_argument("--limit", type=int, default=25, help="Max items to return")
    p.add_argument("--collection", help="Collection key")

    p = subparsers.add_parser("search", help="Search local items via debug-bridge")
    p.add_argument("query", help="Search query")
    p.add_argument("--limit", type=int, default=25, help="Max results")

    p = subparsers.add_parser("get", help="Get full local item details")
    p.add_argument("key", help="Item key")

    subparsers.add_parser("collections", help="List local collections")
    subparsers.add_parser("tags", help="List local tags")

    p = subparsers.add_parser("children", help="List local child items")
    p.add_argument("key", help="Parent item key")

    p = subparsers.add_parser("create-item", help="Create local item via debug-bridge")
    p.add_argument("--meta-json", default="{}", help="Item metadata JSON string")

    p = subparsers.add_parser("attach-pdf", help="Attach local PDF via debug-bridge")
    p.add_argument("--key", required=True, help="Parent item key")
    p.add_argument("--file", required=True, help="Local PDF path")

    p = subparsers.add_parser("arxiv", help="Import arXiv item + local PDF attachment")
    p.add_argument("arxiv", help="arXiv ID or URL")
    p.add_argument("--collection", help="Collection name or key")

    p = subparsers.add_parser("delete", help="Delete local items (default: trash)")
    p.add_argument("keys", nargs="+", help="Item key(s)")
    p.add_argument("--yes", action="store_true", help="Skip confirmation")
    p.add_argument("--permanent", action="store_true", help="Permanently delete")
    p.add_argument("--trash", action="store_true", help="Explicitly move to trash (default)")

    p = subparsers.add_parser("add-doi", help="Add item by DOI (Web API)")
    p.add_argument("identifier", help="DOI")
    p.add_argument("--collection", help="Add to collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Add even if duplicate detected")
    p.set_defaults(id_type="doi")

    p = subparsers.add_parser("add-isbn", help="Add item by ISBN (Web API)")
    p.add_argument("identifier", help="ISBN")
    p.add_argument("--collection", help="Add to collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Add even if duplicate detected")
    p.set_defaults(id_type="isbn")

    p = subparsers.add_parser("add-pmid", help="Add item by PMID (Web API)")
    p.add_argument("identifier", help="PMID")
    p.add_argument("--collection", help="Add to collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Add even if duplicate detected")
    p.set_defaults(id_type="pmid")

    p = subparsers.add_parser("update", help="Update metadata via Web API")
    p.add_argument("key", help="Item key")
    p.add_argument("--title", help="New title")
    p.add_argument("--date", help="New date")
    p.add_argument("--doi", help="Set DOI")
    p.add_argument("--url", help="Set URL")
    p.add_argument("--add-tags", help="Comma-separated tags to add")
    p.add_argument("--remove-tags", help="Comma-separated tags to remove")
    p.add_argument("--add-collection", help="Add to collection key")

    p = subparsers.add_parser("export", help="Export via Web API")
    p.add_argument("--format", default="bibtex", choices=["bibtex", "ris", "csljson"], help="Export format")
    p.add_argument("--collection", help="Collection key")
    p.add_argument("--output", help="Output file path")

    p = subparsers.add_parser("batch-add", help="Batch add identifiers via Web API")
    p.add_argument("file", help="File with one identifier per line")
    p.add_argument("--type", default="doi", choices=["doi", "isbn", "pmid"], help="Identifier type")
    p.add_argument("--collection", help="Collection key")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--force", action="store_true", help="Skip duplicate detection")

    subparsers.add_parser("check-pdfs", help="Report PDF attachment status via Web API")

    p = subparsers.add_parser("crossref", help="Cross-reference citations via Web API")
    p.add_argument("file", help="Citation text/markdown file")

    p = subparsers.add_parser("find-dois", help="Find missing DOIs via CrossRef and Web API")
    p.add_argument("--apply", action="store_true", help="Write matched DOIs")
    p.add_argument("--limit", type=int, default=None, help="Max items to process")
    p.add_argument("--collection", help="Collection key")

    p = subparsers.add_parser("fetch-pdfs", help="Fetch OA PDFs (Web API) or attach local file (--key --file)")
    p.add_argument("--key", help="Local attach mode: parent item key")
    p.add_argument("--file", help="Local attach mode: local PDF file path")
    p.add_argument("--title", default="Full Text PDF", help="Attachment title")
    p.add_argument("--collection", help="Remote mode: collection key scope")
    p.add_argument("--limit", type=int, default=None, help="Remote mode: max items")
    p.add_argument("--force", action="store_true", help="Remote mode: include items that already have PDFs")
    p.add_argument("--sources", default=",".join(PDF_SOURCES), help="Remote mode: comma-separated PDF sources")
    p.add_argument("--download-dir", default="pdfs", help="Remote mode: local download directory")
    p.add_argument("--dry-run", action="store_true", help="Remote mode: do not download/write")
    p.add_argument("--download-only", action="store_true", help="Remote mode: download locally, do not attach")
    p.add_argument("--link-only", action="store_true", help="Remote mode: create linked_url attachment instead of upload")

    return parser


def dispatch(args) -> None:
    if args.command == "ping":
        cmd_ping(args)
    elif args.command == "items":
        cmd_items(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "get":
        cmd_get(args)
    elif args.command == "collections":
        cmd_collections(args)
    elif args.command == "tags":
        cmd_tags(args)
    elif args.command == "children":
        cmd_children(args)
    elif args.command == "create-item":
        cmd_create_item(args)
    elif args.command == "attach-pdf":
        cmd_attach_pdf(args)
    elif args.command == "arxiv":
        cmd_arxiv(args)
    elif args.command == "delete":
        cmd_delete(args)
    elif args.command in ("add-doi", "add-isbn", "add-pmid"):
        result = cmd_add_identifier(args)
        if result == "failed":
            sys.exit(1)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "batch-add":
        cmd_batch_add(args)
    elif args.command == "check-pdfs":
        cmd_check_pdfs(args)
    elif args.command == "crossref":
        cmd_crossref(args)
    elif args.command == "find-dois":
        cmd_find_dois(args)
    elif args.command == "fetch-pdfs":
        cmd_fetch_pdfs(args)


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.json:
        _enable_json_mode()

    try:
        dispatch(args)
    except RuntimeError as e:
        if _json_mode:
            _json_error(str(e), getattr(e, "code", 0))
        else:
            print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
