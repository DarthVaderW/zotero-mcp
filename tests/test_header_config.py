from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from zotero_mcp import server


@contextmanager
def headers(values: dict[str, str]) -> Iterator[None]:
    original = server.request_headers
    server.request_headers = lambda: values  # type: ignore[assignment]
    try:
        yield
    finally:
        server.request_headers = original  # type: ignore[assignment]


def main() -> None:
    assert server.CLI.name == "cli.py"
    assert server.CLI.exists()

    with headers({"authorization": "Bearer zotero-debug-token"}):
        assert server.env_from_headers()["ZOTERO_DEBUG_BRIDGE_TOKEN"] == "zotero-debug-token"

    with headers(
        {
            "x-zotero-debug-bridge-token": "header-token",
            "x-zotero-debug-bridge-url": "http://127.0.0.1:23119/debug-bridge/execute",
            "x-zotero-library-id": "1",
            "x-zotero-api-key": "web-api-key",
            "x-zotero-user-id": "123",
            "x-crossref-email": "reader@example.com",
        }
    ):
        env = server.env_from_headers()
        assert env["ZOTERO_DEBUG_BRIDGE_TOKEN"] == "header-token"
        assert env["ZOTERO_DEBUG_BRIDGE_URL"] == "http://127.0.0.1:23119/debug-bridge/execute"
        assert env["ZOTERO_LIBRARY_ID"] == "1"
        assert env["ZOTERO_API_KEY"] == "web-api-key"
        assert env["ZOTERO_USER_ID"] == "123"
        assert env["CROSSREF_EMAIL"] == "reader@example.com"

    try:
        server.zotero_fetch_pdf(key="ABC12345")
    except ValueError as error:
        assert "requires both 'key' and 'file'" in str(error)
    else:
        raise AssertionError("zotero_fetch_pdf should reject key without file")


if __name__ == "__main__":
    main()
