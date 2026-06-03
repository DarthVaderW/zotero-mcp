"""CrossRef citation checks and DOI discovery operations."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from zotero_mcp.config import CROSSREF_EMAIL, DOI_EXCLUDED_ITEM_TYPES
from zotero_mcp.metadata import _extract_year, _first_author_last, _title_similarity
from zotero_mcp.web_api import get_api_config, paginate_all
from zotero_mcp.web_items import _patch_item_field


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
    except Exception:
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
