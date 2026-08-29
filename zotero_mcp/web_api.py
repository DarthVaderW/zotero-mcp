"""Unified Zotero API helpers.

The default backend is Zotero's official Local API. Set ``ZOTERO_BACKEND=web``
to use zotero.org with a Web API key instead.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from zotero_mcp.config import API_BASE, BACKEND
from zotero_mcp.errors import CommandError
from zotero_mcp.local_api import get_local_client

_MAX_RETRIES = 2
_RETRY_CODES = {429, 503}

def get_api_config() -> tuple[str, str]:
    if BACKEND == "local":
        client = get_local_client()
        client.probe()
        return "", client.library_prefix
    if BACKEND != "web":
        raise RuntimeError("ZOTERO_BACKEND must be either 'local' or 'web'.")
    api_key = os.environ.get("ZOTERO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Error: ZOTERO_API_KEY environment variable not set\n"
            "Create a key at https://www.zotero.org/settings/keys/new"
        )

    user_id = os.environ.get("ZOTERO_USER_ID")
    group_id = os.environ.get("ZOTERO_GROUP_ID")
    if not user_id and not group_id:
        raise RuntimeError("Error: Set ZOTERO_USER_ID or ZOTERO_GROUP_ID")

    prefix = f"/users/{user_id}" if user_id else f"/groups/{group_id}"
    return api_key, prefix

def api_request(path, api_key="", method="GET", data=None, content_type=None, params=None, headers=None):
    method = method.upper()
    if BACKEND == "local":
        request_headers = dict(headers or {})
        if method == "POST" and path.rstrip("/").endswith(("/items", "/collections", "/searches")):
            if not any(name in request_headers for name in ("Zotero-Write-Token", "If-Unmodified-Since-Version")):
                request_headers["Zotero-Write-Token"] = uuid4().hex
        body, response_headers, _ = get_local_client().request(
            path,
            method=method,
            data=data,
            content_type=content_type,
            params=params,
            headers=request_headers,
        )
        return body.decode("utf-8", errors="replace"), response_headers

    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request_headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
        **(headers or {}),
    }
    if content_type:
        request_headers["Content-Type"] = content_type

    body = None
    if data is not None:
        if isinstance(data, str):
            body = data.encode("utf-8")
        elif isinstance(data, bytes):
            body = data
        else:
            body = json.dumps(data).encode("utf-8")
            if not content_type:
                request_headers["Content-Type"] = "application/json"

    for attempt in range(_MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_CODES and attempt < _MAX_RETRIES:
                time.sleep((attempt + 1) * 2)
                continue
            err_body = e.read().decode("utf-8") if e.fp else ""
            msg = f"API Error {e.code}: {e.reason}"
            if err_body:
                msg += f"\n{err_body[:500]}"
            raise CommandError(msg, e.code) from e
        except urllib.error.URLError as e:
            if attempt < _MAX_RETRIES:
                time.sleep((attempt + 1) * 2)
                continue
            msg = f"Network error: {e.reason}"
            raise CommandError(msg, 0) from e

    raise CommandError(f"Request failed after {_MAX_RETRIES + 1} attempts", 0)

def api_get_json(path, api_key, params=None):
    body, headers = api_request(path, api_key, params=params)
    return (json.loads(body) if body.strip() else {}), headers

def paginate_all(path, api_key, params=None):
    params = dict(params or {})
    params.setdefault("limit", "100")
    all_items = []
    start = 0
    while True:
        params["start"] = str(start)
        items, headers = api_get_json(path, api_key, params=params)
        if not isinstance(items, list):
            return [items]
        all_items.extend(items)
        total = int(headers.get("Total-Results", len(all_items)))
        if len(all_items) >= total:
            break
        start = len(all_items)
    return all_items
