"""Local Zotero write operations built on the official Zotero Local API."""

from __future__ import annotations

from zotero_mcp.local_api import db_add_attachment, db_create_item
from zotero_mcp.validators import require_item_type


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
