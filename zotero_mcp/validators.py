"""Validation helpers for Zotero identifiers and payloads."""

from __future__ import annotations

import re

def validate_doi(s):
    if not re.match(r"^10\.\d{4,}/\S+$", s):
        return False
    return True

def require_doi(s):
    if not re.match(r"^10\.\d{4,}/\S+$", s):
        raise RuntimeError(f"Invalid DOI format: '{s}'. Expected pattern: 10.xxxx/...")
    return s

def validate_item_key(s):
    if not re.match(r"^[A-Za-z0-9]{8}$", s):
        return False
    return True

def require_item_key(s):
    if not re.match(r"^[A-Za-z0-9]{8}$", s):
        raise RuntimeError(f"Invalid item key: '{s}'. Must be 8 alphanumeric characters.")
    return s

def validate_isbn(s):
    cleaned = s.replace("-", "").replace(" ", "")
    if not re.match(r"^\d{10}(\d{3})?$", cleaned):
        return False
    return True

def require_isbn(s):
    cleaned = s.replace("-", "").replace(" ", "")
    if not re.match(r"^\d{10}(\d{3})?$", cleaned):
        raise RuntimeError(f"Invalid ISBN: '{s}'. Must be 10 or 13 digits.")
    return s

def validate_id_type(value):
    normalized = str(value or "").strip().lower()
    if normalized not in {"doi", "isbn", "pmid"}:
        raise ValueError("id_type must be one of: doi, isbn, pmid")
    return normalized

def require_item_type(payload):
    item_type = str(payload.get("itemType") or "").strip()
    if not item_type:
        raise RuntimeError("itemType is required. Pass an explicit Zotero item type.")
    payload["itemType"] = item_type
    return item_type
