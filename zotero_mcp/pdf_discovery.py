"""Open-access PDF source discovery."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from zotero_mcp.config import CROSSREF_EMAIL


def _try_unpaywall(doi):
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='')}"
    if CROSSREF_EMAIL:
        url += "?" + urllib.parse.urlencode({"email": CROSSREF_EMAIL})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        location = data.get("best_oa_location") or {}
        pdf_url = location.get("url_for_pdf")
        return (pdf_url, pdf_url) if pdf_url else None
    except Exception:
        return None


def _try_semantic_scholar(doi):
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/DOI:"
        f"{urllib.parse.quote(doi, safe='')}?fields=openAccessPdf"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pdf_url = (data.get("openAccessPdf") or {}).get("url")
        return (pdf_url, pdf_url) if pdf_url else None
    except Exception:
        return None


def _try_doi_content_negotiation(doi):
    url = f"https://doi.org/{urllib.parse.quote(doi, safe='/:')}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/pdf"}, method="HEAD"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if "application/pdf" in resp.headers.get("Content-Type", ""):
                return (resp.url, url)
        return None
    except Exception:
        return None


def _find_pdf_source(doi, sources):
    source_functions = {
        "unpaywall": (_try_unpaywall, 1),
        "semanticscholar": (_try_semantic_scholar, 1),
        "doi": (_try_doi_content_negotiation, 2),
    }
    for source in sources:
        if source not in source_functions:
            continue
        function, delay = source_functions[source]
        result = function(doi)
        if result:
            return (result[0], result[1], source)
        time.sleep(delay)
    return None
