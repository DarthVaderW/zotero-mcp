"""Official Zotero Local API HTTP client and per-instance authorization.

Zotero 10+ exposes its Local API under ``localhost:23119/api``. Reads are
unauthenticated. Writes use a user-approved local key and must include the
instance-specific ``Zotero-Server-ID`` header. Remembered keys are stored per
server ID outside the package directory so ``uvx`` upgrades do not lose them.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
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


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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

    @staticmethod
    def item_version(item: dict[str, Any], headers: dict[str, str]) -> int | str:
        version = item.get("version")
        if version in (None, ""):
            version = item.get("data", {}).get("version")
        if version in (None, ""):
            version = next((value for key, value in headers.items()
                            if key.lower() == "last-modified-version"), None)
        if version in (None, ""):
            raise CommandError("Could not determine current Zotero item version.")
        return version

    def patch_item(
        self, item_key: str, changes: dict[str, Any], *, version: int | str | None = None,
    ) -> None:
        if version is None:
            item, headers = self.get_json(f"{self.library_prefix}/items/{item_key}")
            version = self.item_version(item, headers)
        if version in (None, ""):
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
        version = self.item_version(item, headers)
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
