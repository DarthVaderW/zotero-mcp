"""Configuration values for the Zotero MCP package."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


load_dotenv(ROOT_DIR / ".env")

API_BASE = "https://api.zotero.org"
LOCAL_API_BASE = os.environ.get(
    "ZOTERO_LOCAL_API_URL",
    "http://127.0.0.1:23119/api",
).rstrip("/")
LOCAL_API_APP_NAME = os.environ.get("ZOTERO_LOCAL_API_APP_NAME", "Zotero MCP").strip() or "Zotero MCP"
LOCAL_API_KEY = os.environ.get("ZOTERO_LOCAL_API_KEY", "").strip()
LOCAL_LIBRARY_PREFIX = os.environ.get("ZOTERO_LOCAL_LIBRARY_PREFIX", "/users/0").strip() or "/users/0"
BACKEND = os.environ.get("ZOTERO_BACKEND", "local").strip().lower() or "local"

CROSSREF_EMAIL = os.environ.get("CROSSREF_EMAIL", "").strip()
DOI_EXCLUDED_ITEM_TYPES = {"attachment", "note"}
PDF_SOURCES = ["unpaywall", "semanticscholar", "doi"]
