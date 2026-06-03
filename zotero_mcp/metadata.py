"""Small metadata formatting and matching helpers."""

from __future__ import annotations

import difflib
import re


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
