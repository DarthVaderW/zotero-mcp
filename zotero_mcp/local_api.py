"""Official Zotero Local API transport and local-library helpers.

Zotero 10+ exposes the Web API under ``localhost:23119/api``. Reads are
unauthenticated. Writes use a user-approved local API key and must include the
instance-specific ``Zotero-Server-ID`` header. Remembered keys are stored per
server ID outside the package directory so ``uvx`` upgrades do not lose them.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from zotero_mcp.config import (
    LOCAL_API_APP_NAME,
    LOCAL_API_BASE,
    LOCAL_API_KEY,
    LOCAL_LIBRARY_PREFIX,
)
from zotero_mcp.errors import CommandError
from zotero_mcp.validators import require_item_type


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024


def _default_credentials_path() -> Path:
    override = os.environ.get("ZOTERO_MCP_CREDENTIALS_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        return root / "zotero-mcp" / "credentials.json"
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / "zotero-mcp" / "credentials.json"
    root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return root / "zotero-mcp" / "credentials.json"


def sys_platform() -> str:
    # Kept as a tiny function so platform-specific path selection is testable.
    import sys

    return sys.platform


class LocalCredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = path or _default_credentials_path()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"servers": {}}
        if not isinstance(data, dict) or not isinstance(data.get("servers"), dict):
            return {"servers": {}}
        return data

    def get(self, server_id: str) -> str:
        entry = self._load().get("servers", {}).get(server_id, {})
        return str(entry.get("key", "")) if isinstance(entry, dict) else ""

    def save(self, server_id: str, key: str) -> None:
        data = self._load()
        data.setdefault("servers", {})[server_id] = {"key": key}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            if os.name != "nt":
                temp_path.chmod(0o600)
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)

    def remove(self, server_id: str) -> None:
        data = self._load()
        servers = data.setdefault("servers", {})
        if server_id not in servers:
            return
        del servers[server_id]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            if os.name != "nt":
                temp_path.chmod(0o600)
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)


class LocalAPIClient:
    def __init__(
        self,
        base_url: str = LOCAL_API_BASE,
        library_prefix: str = LOCAL_LIBRARY_PREFIX,
        app_name: str = LOCAL_API_APP_NAME,
        api_key: str = LOCAL_API_KEY,
        credential_store: LocalCredentialStore | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.library_prefix = "/" + library_prefix.strip("/")
        self.app_name = app_name
        self._configured_key = api_key
        self._api_key = api_key
        self._credentials = credential_store or LocalCredentialStore()
        self.server_id = ""
        self.api_version = ""
        self.schema_version = ""
        self.zotero_version = ""

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"

    @staticmethod
    def _encode_data(data: Any, content_type: str | None) -> tuple[bytes | None, str | None]:
        if data is None:
            return None, content_type
        if isinstance(data, bytes):
            return data, content_type
        if isinstance(data, str):
            return data.encode("utf-8"), content_type
        if content_type == "application/x-www-form-urlencoded":
            return urllib.parse.urlencode(data).encode("utf-8"), content_type
        return json.dumps(data).encode("utf-8"), content_type or "application/json"

    def _request_once(
        self,
        path_or_url: str,
        *,
        method: str = "GET",
        data: Any = None,
        content_type: str | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> tuple[bytes, dict[str, str], int]:
        url = self._url(path_or_url)
        if params:
            separator = "&" if "?" in url else "?"
            url += separator + urllib.parse.urlencode(params, doseq=True)
        body, resolved_content_type = self._encode_data(data, content_type)
        request_headers = dict(headers or {})
        if resolved_content_type:
            request_headers.setdefault("Content-Type", resolved_content_type)
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read(), dict(response.headers.items()), response.status
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            message = f"Zotero Local API {exc.code}: {exc.reason}"
            if detail:
                message += f"\n{detail[:1000]}"
            raise CommandError(message, exc.code) from exc
        except urllib.error.URLError as exc:
            raise CommandError(
                "Cannot reach Zotero Local API. Start Zotero and enable "
                "Settings > Advanced > Allow other applications on this computer to communicate with Zotero. "
                f"({exc.reason})",
                0,
            ) from exc

    def probe(self) -> dict[str, str]:
        body, headers, _ = self._request_once(
            "/",
            headers={"Zotero-API-Version": "3", "Zotero-Allowed-Request": "1"},
            timeout=10,
        )
        del body
        server_id = headers.get("Zotero-Server-ID", "")
        if not server_id:
            raise CommandError("Running Zotero does not expose Zotero 10+ Local API server identification.")
        if self.server_id and self.server_id != server_id:
            self._api_key = self._configured_key
        self.server_id = server_id
        self.api_version = headers.get("Zotero-API-Version", "")
        self.schema_version = headers.get("Zotero-Schema-Version", "")
        try:
            origin = self.base_url.split("/api", 1)[0]
            _, ping_headers, _ = self._request_once(
                f"{origin}/connector/ping",
                headers={"Zotero-Allowed-Request": "1"},
                timeout=10,
            )
            self.zotero_version = ping_headers.get("X-Zotero-Version", "")
        except CommandError:
            self.zotero_version = ""
        return {
            "backend": "local_api",
            "zotero_version": self.zotero_version,
            "api_version": self.api_version,
            "schema_version": self.schema_version,
            "server_id": self.server_id,
        }

    def authorize(self) -> str:
        if not self.server_id:
            self.probe()
        body, _, _ = self._request_once(
            "/local/authorize",
            method="POST",
            data={"appName": self.app_name},
            content_type="application/json",
            headers={
                "Zotero-API-Version": "3",
                "Zotero-Allowed-Request": "1",
                "Zotero-Server-ID": self.server_id,
            },
            timeout=180,
        )
        response = json.loads(body.decode("utf-8"))
        key = str(response.get("key", ""))
        if not key:
            raise CommandError("Zotero did not grant Local API write access.", 403)
        self._api_key = key
        if response.get("remember"):
            self._credentials.save(self.server_id, key)
        return key

    def _write_key(self) -> str:
        if not self.server_id:
            self.probe()
        if self._api_key:
            return self._api_key
        remembered = self._credentials.get(self.server_id)
        if remembered:
            self._api_key = remembered
            return remembered
        return self.authorize()

    def request(
        self,
        path_or_url: str,
        *,
        method: str = "GET",
        data: Any = None,
        content_type: str | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> tuple[bytes, dict[str, str], int]:
        if not self.server_id:
            self.probe()
        method = method.upper()
        request_headers = {
            "Zotero-API-Version": "3",
            "Zotero-Allowed-Request": "1",
            "Zotero-Server-ID": self.server_id,
            **(headers or {}),
        }
        is_write = method in _WRITE_METHODS
        if is_write:
            request_headers["Zotero-API-Key"] = self._write_key()
        for attempt in range(2):
            try:
                return self._request_once(
                    path_or_url,
                    method=method,
                    data=data,
                    content_type=content_type,
                    params=params,
                    headers=request_headers,
                    timeout=timeout,
                )
            except CommandError as exc:
                if not is_write or exc.code != 401 or attempt or self._configured_key:
                    raise
                self._credentials.remove(self.server_id)
                self._api_key = ""
                request_headers["Zotero-API-Key"] = self.authorize()
        raise CommandError("Zotero Local API request failed.")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        body, headers, _ = self.request(path, params=params)
        return (json.loads(body.decode("utf-8")) if body.strip() else {}), headers

    def get_all_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read a paginated Local API collection without silently truncating it."""
        if max_items is not None and max_items <= 0:
            return []
        results: list[dict[str, Any]] = []
        start = 0
        while True:
            remaining = None if max_items is None else max_items - len(results)
            page_size = 100 if remaining is None else min(100, remaining)
            query = dict(params or {})
            query.update({"start": str(start), "limit": str(page_size)})
            page, headers = self.get_json(path, params=query)
            if not isinstance(page, list):
                raise CommandError(f"Unexpected paginated response from Zotero Local API: {path}")
            results.extend(item for item in page if isinstance(item, dict))
            total_text = next(
                (value for name, value in headers.items() if name.lower() == "total-results"),
                "",
            )
            total = int(total_text) if str(total_text).isdigit() else None
            if not page or len(page) < page_size or (total is not None and len(results) >= total):
                break
            if max_items is not None and len(results) >= max_items:
                break
            start += len(page)
        return results[:max_items] if max_items is not None else results

    def create_objects(self, path: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
        body, _, _ = self.request(
            path,
            method="POST",
            data=objects,
            content_type="application/json",
            headers={"Zotero-Write-Token": uuid4().hex},
        )
        response = json.loads(body.decode("utf-8")) if body.strip() else {}
        failed = response.get("failed", {})
        if failed:
            raise CommandError(f"Zotero rejected object creation: {json.dumps(failed, ensure_ascii=False)}", 400)
        return response

    @staticmethod
    def first_success_key(response: dict[str, Any]) -> str:
        successes = response.get("successful") or response.get("success") or {}
        if not successes:
            return ""
        value = next(iter(successes.values()))
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return str(value.get("key") or value.get("data", {}).get("key") or "")
        return ""

    def patch_item(self, item_key: str, changes: dict[str, Any]) -> None:
        item, headers = self.get_json(f"{self.library_prefix}/items/{item_key}")
        version = item.get("version") or item.get("data", {}).get("version") or headers.get("Last-Modified-Version")
        if version is None:
            raise CommandError(f"Could not determine current Zotero version for item {item_key}.")
        self.request(
            f"{self.library_prefix}/items/{item_key}",
            method="PATCH",
            data=changes,
            content_type="application/json",
            headers={"If-Unmodified-Since-Version": str(version)},
        )

    def delete_item(self, item_key: str) -> None:
        """Move an item to Zotero trash using the editable ``deleted`` flag."""
        self.patch_item(item_key, {"deleted": True})

    def erase_item(self, item_key: str) -> None:
        """Permanently erase an item. This is not exposed by the MCP server."""
        item, headers = self.get_json(f"{self.library_prefix}/items/{item_key}")
        version = item.get("version") or item.get("data", {}).get("version") or headers.get("Last-Modified-Version")
        if version is None:
            raise CommandError(f"Could not determine current Zotero version for item {item_key}.")
        self.request(
            f"{self.library_prefix}/items/{item_key}",
            method="DELETE",
            headers={"If-Unmodified-Since-Version": str(version)},
        )

    def create_attachment(
        self,
        parent_key: str,
        *,
        filename: str,
        content_type: str,
        title: str,
        data: bytes,
        link_mode: str = "imported_file",
        url: str = "",
        mtime_ms: int | None = None,
    ) -> str:
        attachment = {
            "itemType": "attachment",
            "parentItem": parent_key,
            "linkMode": link_mode,
            "title": title,
            "filename": filename,
            "contentType": content_type,
            "charset": "utf-8" if content_type.startswith("text/") else "",
            "url": url,
            "tags": [],
            "relations": {},
        }
        response = self.create_objects(f"{self.library_prefix}/items", [attachment])
        attachment_key = self.first_success_key(response)
        if not attachment_key:
            raise CommandError("Zotero created no attachment item.")
        try:
            self.upload_attachment_bytes(
                attachment_key,
                filename=filename,
                data=data,
                mtime_ms=mtime_ms or int(time.time() * 1000),
            )
        except Exception:
            try:
                self.erase_item(attachment_key)
            except Exception:
                pass
            raise
        return attachment_key

    def upload_attachment_bytes(self, item_key: str, *, filename: str, data: bytes, mtime_ms: int) -> None:
        file_path = f"{self.library_prefix}/items/{item_key}/file"
        form = {
            "md5": hashlib.md5(data).hexdigest(),
            "filename": filename,
            "filesize": str(len(data)),
            "mtime": str(mtime_ms),
        }
        body, _, _ = self.request(
            file_path,
            method="POST",
            data=form,
            content_type="application/x-www-form-urlencoded",
            headers={"If-None-Match": "*"},
        )
        authorization = json.loads(body.decode("utf-8")) if body.strip() else {}
        if authorization.get("exists"):
            return
        upload_key = str(authorization.get("uploadKey", ""))
        upload_url = str(authorization.get("url", ""))
        if not upload_key or not upload_url:
            raise CommandError("Zotero did not return a file upload authorization.")
        upload_url = urllib.parse.urljoin(f"{self.base_url}/", upload_url)
        payload = (
            str(authorization.get("prefix", "")).encode("utf-8")
            + data
            + str(authorization.get("suffix", "")).encode("utf-8")
        )
        self._request_once(
            upload_url,
            method="POST",
            data=payload,
            content_type=str(authorization.get("contentType") or "application/octet-stream"),
            timeout=180,
        )
        self.request(
            file_path,
            method="POST",
            data={"upload": upload_key},
            content_type="application/x-www-form-urlencoded",
            headers={"If-None-Match": "*"},
        )


_CLIENT: LocalAPIClient | None = None


def get_local_client() -> LocalAPIClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LocalAPIClient()
    return _CLIENT


def reset_local_client() -> None:
    global _CLIENT
    _CLIENT = None


def ensure_local_api() -> dict[str, str]:
    return get_local_client().probe()


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


def db_ping() -> str:
    return ensure_local_api().get("zotero_version", "")


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
    normalized_type = str(id_type or "doi").lower().strip()
    if normalized_type not in {"doi", "isbn", "pmid"}:
        raise RuntimeError("id_type must be one of: doi, isbn, pmid")
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


def db_create_item(item_data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item_data or {})
    require_item_type(payload)
    payload.setdefault("title", "")
    client = get_local_client()
    response = client.create_objects(f"{client.library_prefix}/items", [payload])
    key = client.first_success_key(response)
    return {"key": key, "success": bool(key), "skippedFields": []}


def db_add_attachment(parent_key: str, file_path: str, title: str = "Full Text PDF") -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return {"success": False, "error": f"File not found: {path}"}
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        key = get_local_client().create_attachment(
            parent_key,
            filename=path.name,
            content_type=content_type,
            title=title,
            data=path.read_bytes(),
            link_mode="imported_file",
            mtime_ms=int(path.stat().st_mtime * 1000),
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "attachment_key": key}


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
    raw_item, _ = client.get_json(f"{client.library_prefix}/items/{item_key}")
    current = list(_data(raw_item).get("collections", []))
    if collection_key not in current:
        current.append(collection_key)
        client.patch_item(item_key, {"collections": current})
    return {"itemKey": item_key, "collectionKey": collection_key, "collectionName": collection.get("name", target)}


def db_delete_item(key: str, permanent: bool = False) -> dict[str, Any]:
    if permanent:
        get_local_client().erase_item(key)
        return {"success": True, "mode": "permanent"}
    get_local_client().delete_item(key)
    return {"success": True, "mode": "trash"}
