"""PDF download helpers shared by Zotero operations."""

from __future__ import annotations

import os
import shutil
import time
import urllib.request

from zotero_mcp.config import CROSSREF_EMAIL


def _pdf_user_agent() -> str:
    contact = f"; mailto:{CROSSREF_EMAIL}" if CROSSREF_EMAIL else ""
    return f"Mozilla/5.0 (compatible; ZoteroCLI/1.0{contact})"


def _download_pdf(url, dest_path):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _pdf_user_agent(),
            "Accept": "application/pdf,*/*",
        },
    )
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                with open(dest_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            with open(dest_path, "rb") as f:
                if f.read(5) != b"%PDF-":
                    os.unlink(dest_path)
                    return False
            return True
        except Exception:
            if os.path.exists(dest_path):
                os.unlink(dest_path)
            time.sleep(1)
    return False
