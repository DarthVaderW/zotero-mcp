"""Zotero Web API item update, export, and PDF coverage operations."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from zotero_mcp.config import API_BASE
from zotero_mcp.validators import require_item_key
from zotero_mcp.web_api import api_get_json, api_request, get_api_config, paginate_all


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
