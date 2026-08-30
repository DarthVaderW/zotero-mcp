"""Identifier metadata translation helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from zotero_mcp.validators import require_doi, require_isbn


def _doi_to_item(doi):
    """Fall back to Crossref when Zotero's translation service cannot resolve a DOI."""
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
        item["date"] = "-".join(str(part) for part in issued[0])
    for author in work.get("author", []):
        item["creators"].append(
            {
                "creatorType": "author",
                "firstName": author.get("given", ""),
                "lastName": author.get("family", ""),
            }
        )
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
    translate_data = json.dumps({"url": lookup_url, "sessionid": "zotero-mcp"}).encode(
        "utf-8"
    )
    translate_req = urllib.request.Request(
        "https://translate.zotero.org/web",
        data=translate_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(translate_req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if id_type == "doi":
            translated = _doi_to_item(identifier)
            if translated:
                return translated
        raise RuntimeError(f"Translation failed: {exc.code} {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Translation failed: {exc}") from exc


def clean_translated_item_for_local(item, tags=None):
    """Prepare translator or Crossref metadata for Local API creation."""
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
