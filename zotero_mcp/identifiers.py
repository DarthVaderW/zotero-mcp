"""Identifier translation and add/batch-add operations."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from zotero_mcp.metadata import fmt_item_short
from zotero_mcp.validators import require_doi, require_isbn
from zotero_mcp.web_api import api_get_json, api_request, get_api_config


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

def clean_translated_item_for_local(item, tags=None):
    """Prepare translator/CrossRef metadata for local Debug Bridge creation."""
    payload = dict(item or {})
    for field in [
        "key",
        "version",
        "dateAdded",
        "dateModified",
        "relations",
        "collections",
        "attachments",
        "notes",
    ]:
        payload.pop(field, None)

    tag_values = []
    for tag in payload.pop("tags", []) or []:
        if isinstance(tag, str):
            tag_values.append(tag)
        elif isinstance(tag, dict) and tag.get("tag"):
            tag_values.append(str(tag["tag"]))
    tag_values.extend(tag.strip() for tag in (tags or "").split(",") if tag.strip())
    if tag_values:
        seen = set()
        payload["tags"] = []
        for tag in tag_values:
            if tag in seen:
                continue
            seen.add(tag)
            payload["tags"].append({"tag": tag})

    return payload


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
