"""Local Zotero library maintenance operations."""

from __future__ import annotations

import json

from zotero_mcp.errors import CommandError
from zotero_mcp.local_api import get_local_client
from zotero_mcp.validators import require_item_key


def _header(headers: dict[str, str], name: str, default: str = "") -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return str(value)
    return default


def _patch_item_field(item_key: str, field: str, value, version: int | str) -> None:
    """Patch one field using the caller's already-read object version."""
    require_item_key(item_key)
    client = get_local_client()
    client.request(
        f"{client.library_prefix}/items/{item_key}",
        method="PATCH",
        data={field: value},
        content_type="application/json",
        headers={"If-Unmodified-Since-Version": str(version)},
    )


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
    require_item_key(key)
    client = get_local_client()
    item, headers = client.get_json(f"{client.library_prefix}/items/{key}")
    data = item.get("data", {})
    version = (
        item.get("version")
        or data.get("version")
        or _header(headers, "Last-Modified-Version")
    )
    if version in (None, ""):
        raise CommandError(
            f"Could not determine current Zotero version for item {key}."
        )

    changes = {}
    if title:
        changes["title"] = title
    if date:
        changes["date"] = date
    if doi is not None:
        changes["DOI"] = doi
    if url is not None:
        changes["url"] = url

    current_tags = [
        tag["tag"]
        for tag in data.get("tags", [])
        if isinstance(tag, dict) and tag.get("tag")
    ]
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
        current_collections = list(data.get("collections", []))
        if add_collection not in current_collections:
            current_collections.append(add_collection)
            changes["collections"] = current_collections

    if not changes:
        return {"status": "no_changes", "key": key, "changes": {}}

    client.request(
        f"{client.library_prefix}/items/{key}",
        method="PATCH",
        data=changes,
        content_type="application/json",
        headers={"If-Unmodified-Since-Version": str(version)},
    )
    return {"status": "updated", "key": key, "changes": changes}


def op_export(format="bibtex", collection=None, output=None):
    if format not in {"bibtex", "ris", "csljson"}:
        raise RuntimeError("format must be one of: bibtex, ris, csljson")

    client = get_local_client()
    path = (
        f"{client.library_prefix}/collections/{collection}/items"
        if collection
        else f"{client.library_prefix}/items/top"
    )
    chunks: list[str] = []
    csl_records: list[object] = []
    start = 0
    page_size = 100
    while True:
        body, headers, _ = client.request(
            path,
            params={"format": format, "limit": str(page_size), "start": str(start)},
        )
        text = body.decode("utf-8", errors="replace")
        if format == "csljson" and text.strip():
            page = json.loads(text)
            if not isinstance(page, list):
                raise CommandError(
                    "Zotero returned an unexpected CSL JSON export response."
                )
            csl_records.extend(page)
        elif text.strip():
            chunks.append(text)

        total_text = _header(headers, "Total-Results")
        total = int(total_text) if total_text.isdigit() else None
        start += page_size
        if total is None or start >= total:
            break

    export_text = (
        json.dumps(csl_records, ensure_ascii=False, indent=2)
        if format == "csljson"
        else "\n".join(chunks)
    )
    result = {
        "format": format,
        "collection": collection,
        "bytes": len(export_text.encode("utf-8")),
    }
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(export_text)
        result["output"] = output
    else:
        result["text"] = export_text
    return result


def op_check_pdfs():
    client = get_local_client()
    all_items = client.get_all_json(f"{client.library_prefix}/items")

    parents = {}
    pdf_parents = set()
    for item in all_items:
        data = item.get("data", {})
        item_type = data.get("itemType", "")
        if item_type == "attachment":
            content_type = data.get("contentType", "")
            filename = data.get("filename", "")
            if (
                "pdf" in content_type.lower() or filename.lower().endswith(".pdf")
            ) and data.get("parentItem"):
                pdf_parents.add(data["parentItem"])
        elif item_type != "note" and data.get("key"):
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
