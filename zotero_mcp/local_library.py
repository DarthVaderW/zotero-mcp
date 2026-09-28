"""Item, collection and attachment workflows over Zotero's official Local API."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from zotero_mcp.errors import CommandError
from zotero_mcp.local_api import get_local_client
from zotero_mcp.validators import require_item_type, validate_id_type


_MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024


def _data(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("data", item)


def _creator_names(creators: list[dict[str, Any]]) -> str:
    names = []
    for creator in creators or []:
        name = str(creator.get("name", "")).strip()
        if not name:
            name = " ".join(
                part for part in (str(creator.get("firstName", "")).strip(), str(creator.get("lastName", "")).strip()) if part
            )
        if name:
            names.append(name)
    return ", ".join(names)


def _short_item(item: dict[str, Any]) -> dict[str, Any]:
    data = _data(item)
    return {
        "key": data.get("key", item.get("key", "")),
        "version": data.get("version", item.get("version")),
        "itemType": data.get("itemType", ""),
        "title": data.get("title") or item.get("meta", {}).get("title", "") or "Untitled",
        "creators": _creator_names(data.get("creators", [])),
        "dateAdded": data.get("dateAdded", ""),
        "dateModified": data.get("dateModified", ""),
        "DOI": data.get("DOI", ""),
        "ISBN": data.get("ISBN", ""),
        "url": data.get("url", ""),
        "abstractNote": data.get("abstractNote", ""),
        "archiveLocation": data.get("archiveLocation", ""),
        "extra": data.get("extra", ""),
    }


def db_get_items(limit: int = 100, collection_key: str | None = None) -> list[dict[str, Any]]:
    client = get_local_client()
    path = (
        f"{client.library_prefix}/collections/{collection_key}/items/top"
        if collection_key
        else f"{client.library_prefix}/items/top"
    )
    items = client.get_all_json(
        path,
        params={"format": "json", "sort": "dateModified", "direction": "desc"},
        max_items=limit,
    )
    return [_short_item(item) for item in items or []]


def db_search(query: str, limit: int = 50) -> list[dict[str, Any]]:
    client = get_local_client()
    items = client.get_all_json(
        f"{client.library_prefix}/items/top",
        params={"q": query, "qmode": "everything", "format": "json"},
        max_items=limit,
    )
    return [_short_item(item) for item in items or []]


def _candidate_items(*queries: str) -> list[dict[str, Any]]:
    client = get_local_client()
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for query in queries:
        if not query:
            continue
        items = client.get_all_json(
            f"{client.library_prefix}/items/top",
            params={"q": query, "qmode": "everything", "format": "json"},
            max_items=100,
        )
        for item in items or []:
            key = str(_data(item).get("key", item.get("key", "")))
            if key and key not in seen:
                seen.add(key)
                results.append(item)
    return results


def db_find_item_by_identifier(identifier: str, id_type: str = "doi", title: str | None = None) -> list[dict[str, Any]]:
    normalized_type = validate_id_type(id_type or "doi")
    target = str(identifier or "").strip()
    target_title = re.sub(r"\W+", " ", str(title or "").lower()).strip()
    target_doi = target.lower().rstrip("/")
    target_isbn = re.sub(r"[^0-9X]", "", target.upper())
    matches = []
    for raw in _candidate_items(target, str(title or "")):
        item = _short_item(raw)
        data = _data(raw)
        item_title = re.sub(r"\W+", " ", str(data.get("title", "")).lower()).strip()
        item_doi = str(data.get("DOI", "")).lower().strip().rstrip("/")
        item_isbn = re.sub(r"[^0-9X]", "", str(data.get("ISBN", "")).upper())
        extra = str(data.get("extra", ""))
        url = str(data.get("url", ""))
        by_doi = normalized_type == "doi" and bool(item_doi) and item_doi == target_doi
        by_isbn = normalized_type == "isbn" and bool(target_isbn) and target_isbn in item_isbn
        by_pmid = normalized_type == "pmid" and bool(target) and (
            f"PMID: {target}" in extra
            or f"PMID {target}" in extra
            or f"/pubmed/{target}" in url
            or f"pubmed.ncbi.nlm.nih.gov/{target}" in url
        )
        by_title = bool(target_title) and item_title == target_title
        if by_doi or by_isbn or by_pmid or by_title:
            item["match"] = {"doi": by_doi, "isbn": by_isbn, "pmid": by_pmid, "title": by_title}
            matches.append(item)
    return matches


def db_find_arxiv_item(arxiv_id: str) -> list[dict[str, Any]]:
    base_id = re.sub(r"v\d+$", "", str(arxiv_id), flags=re.IGNORECASE)
    target_doi = f"10.48550/arxiv.{base_id}".lower()
    fragments = [f"/abs/{base_id}", f"/abs/{arxiv_id}", f"/pdf/{base_id}", f"/pdf/{arxiv_id}"]
    matches = []
    for raw in _candidate_items(base_id, target_doi):
        data = _data(raw)
        item_doi = str(data.get("DOI", "")).lower().strip().rstrip("/")
        url = str(data.get("url", ""))
        archive_location = str(data.get("archiveLocation", ""))
        extra = str(data.get("extra", ""))
        by_doi = item_doi == target_doi
        by_url = any(fragment in url for fragment in fragments)
        by_archive = archive_location in {str(arxiv_id), base_id}
        by_extra = str(arxiv_id) in extra or base_id in extra or target_doi in extra.lower()
        if by_doi or by_url or by_archive or by_extra:
            item = _short_item(raw)
            item["match"] = {"doi": by_doi, "url": by_url, "archiveLocation": by_archive, "extra": by_extra}
            matches.append(item)
    return matches


def db_get_item(key: str) -> dict[str, Any] | None:
    client = get_local_client()
    try:
        item, _ = client.get_json(f"{client.library_prefix}/items/{key}", params={"format": "json"})
    except CommandError as exc:
        if exc.code == 404:
            return None
        raise
    return _short_item(item)


def db_get_children(key: str) -> list[dict[str, Any]]:
    client = get_local_client()
    items = client.get_all_json(f"{client.library_prefix}/items/{key}/children", params={"format": "json"})
    children = []
    for raw in items or []:
        data = _data(raw)
        title = data.get("title", "")
        if data.get("itemType") == "note" and not title:
            title = re.sub(r"<[^>]+>", " ", str(data.get("note", "")))
            title = re.sub(r"\s+", " ", title).strip()[:120] or "Note"
        children.append(
            {
                "key": data.get("key", raw.get("key", "")),
                "itemType": data.get("itemType", ""),
                "title": title or "Attachment",
                "contentType": data.get("contentType", ""),
                "url": data.get("url", ""),
                "linkMode": data.get("linkMode", ""),
                "filename": data.get("filename", ""),
            }
        )
    return children


def _file_url_to_path(value: str) -> Path | None:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme != "file":
        return None
    path = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
    if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return Path(path)


def db_get_attachment_file(key: str) -> dict[str, Any] | None:
    client = get_local_client()
    try:
        raw, _ = client.get_json(f"{client.library_prefix}/items/{key}", params={"format": "json"})
    except CommandError as exc:
        if exc.code == 404:
            return None
        raise
    data = _data(raw)
    if data.get("itemType") != "attachment":
        raise RuntimeError("Item is not an attachment")
    file_path = None
    try:
        body, _, _ = client.request(f"{client.library_prefix}/items/{key}/file/view/url")
        file_path = _file_url_to_path(body.decode("utf-8", errors="replace"))
    except CommandError as exc:
        if exc.code not in {400, 404}:
            raise
    return {
        "key": key,
        "parentKey": data.get("parentItem", ""),
        "itemType": "attachment",
        "title": data.get("title", "") or "Attachment",
        "contentType": data.get("contentType", ""),
        "url": data.get("url", ""),
        "filePath": str(file_path) if file_path else "",
        "storageDirectory": str(file_path.parent) if file_path else "",
    }


def db_get_collections() -> list[dict[str, Any]]:
    client = get_local_client()
    items = client.get_all_json(f"{client.library_prefix}/collections", params={"format": "json"})
    return [
        {
            "key": _data(item).get("key", item.get("key", "")),
            "name": _data(item).get("name", ""),
            "parentCollection": _data(item).get("parentCollection", False),
        }
        for item in items or []
    ]


def db_get_tags() -> list[dict[str, Any]]:
    client = get_local_client()
    items = client.get_all_json(f"{client.library_prefix}/tags", params={"format": "json"})
    return [{"name": item.get("tag", ""), "type": item.get("meta", {}).get("type", 0)} for item in items or []]


def _snapshot_filename(page_url: str) -> str:
    name = Path(urllib.parse.urlparse(page_url).path).name or "snapshot"
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "snapshot"
    if not name.lower().endswith((".html", ".htm")):
        name += ".html"
    return name


def db_add_snapshot(parent_key: str, page_url: str, title: str = "Web Page Snapshot") -> str:
    request = urllib.request.Request(
        page_url,
        headers={"User-Agent": "zotero-mcp/0.3 (+https://github.com/DarthVaderW/zotero-mcp)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(_MAX_SNAPSHOT_BYTES + 1)
            content_type = response.headers.get_content_type() or "text/html"
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Snapshot download failed: {exc}") from exc
    if len(data) > _MAX_SNAPSHOT_BYTES:
        raise RuntimeError("Snapshot exceeds the 50 MiB safety limit.")
    return get_local_client().create_attachment(
        parent_key,
        filename=_snapshot_filename(page_url),
        content_type=content_type,
        title=title,
        data=data,
        link_mode="imported_url",
        url=page_url,
    )


def db_add_item_to_collection(item_key: str, collection_name_or_key: str) -> dict[str, Any]:
    client = get_local_client()
    target = str(collection_name_or_key or "").strip()
    if not target:
        raise RuntimeError("Collection name or key is required")
    collection = None
    if re.fullmatch(r"[A-Za-z0-9]{8}", target):
        try:
            raw, _ = client.get_json(f"{client.library_prefix}/collections/{target}")
            collection = _data(raw)
        except CommandError as exc:
            if exc.code != 404:
                raise
    if collection is None:
        for candidate in db_get_collections():
            if candidate.get("name") == target:
                collection = candidate
                break
    if collection is None:
        response = client.create_objects(
            f"{client.library_prefix}/collections",
            [{"name": target, "parentCollection": False, "relations": {}}],
        )
        key = client.first_success_key(response)
        if not key:
            raise RuntimeError(f"Failed to create collection: {target}")
        collection = {"key": key, "name": target}
    collection_key = str(collection.get("key", ""))
    raw_item, headers = client.get_json(f"{client.library_prefix}/items/{item_key}")
    current = list(_data(raw_item).get("collections", []))
    if collection_key not in current:
        current.append(collection_key)
        client.patch_item(item_key, {"collections": current},
                          version=client.item_version(raw_item, headers))
    return {"itemKey": item_key, "collectionKey": collection_key, "collectionName": collection.get("name", target)}


def db_delete_item(key: str, permanent: bool = False) -> dict[str, Any]:
    if permanent:
        get_local_client().erase_item(key)
        return {"success": True, "mode": "permanent"}
    get_local_client().delete_item(key)
    return {"success": True, "mode": "trash"}


def create_item(meta: dict[str, Any]) -> str:
    """Normalize a caller's item payload and return its created key."""
    payload = dict(meta or {})
    if "abstractNote" not in payload and "abstract" in payload:
        payload["abstractNote"] = payload["abstract"]
    payload.pop("abstract", None)
    extra_fields = payload.pop("extra_fields", {})
    if isinstance(extra_fields, dict):
        payload.update(extra_fields)
    require_item_type(payload)
    payload.setdefault("title", "")
    client = get_local_client()
    response = client.create_objects(f"{client.library_prefix}/items", [payload])
    key = client.first_success_key(response)
    if key:
        return key
    failure = {"key": key, "success": False, "skippedFields": []}
    raise RuntimeError(f"Create item failed: {failure}")


def attach_pdf_from_file(parent_item_key: str, pdf_path: str, title: str = "Full Text PDF") -> str:
    """Attach a local PDF and return its Zotero attachment key."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"File not found: {path}")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        return get_local_client().create_attachment(
            parent_item_key,
            filename=path.name,
            content_type=content_type,
            title=title,
            data=path.read_bytes(),
            link_mode="imported_file",
            mtime_ms=int(path.stat().st_mtime * 1000),
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
