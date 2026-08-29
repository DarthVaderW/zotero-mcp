#!/usr/bin/env python3
"""Live Zotero 10+ Local API write smoke test.

This creates a parent item, child note, and imported text attachment, updates
the parent with a version precondition, verifies all objects, and moves the
parent to Zotero trash. It never prints the local API key.
"""

from __future__ import annotations

import json
import sys

from zotero_mcp.local_api import get_local_client


def main() -> int:
    client = get_local_client()
    probe = client.probe()
    parent_key = ""
    result: dict[str, object] = {
        "backend": probe.get("backend"),
        "zotero_version": probe.get("zotero_version"),
    }
    try:
        parent = {
            "itemType": "journalArticle",
            "title": "Codex Zotero MCP v0.3 Local API smoke test",
            "date": "2026-08-29",
            "creators": [{"creatorType": "author", "name": "Zotero MCP live test"}],
            "tags": [{"tag": "zotero-mcp-live-test"}],
            "collections": [],
            "relations": {},
        }
        created = client.create_objects(f"{client.library_prefix}/items", [parent])
        parent_key = client.first_success_key(created)
        if not parent_key:
            raise RuntimeError("Parent item creation returned no key")

        note = {
            "itemType": "note",
            "parentItem": parent_key,
            "note": "<p>Official Zotero Local API child-note smoke test.</p>",
            "tags": [],
            "relations": {},
        }
        note_key = client.first_success_key(
            client.create_objects(f"{client.library_prefix}/items", [note])
        )

        attachment_bytes = b"Official Zotero Local API attachment smoke test.\n"
        attachment_key = client.create_attachment(
            parent_key,
            filename="zotero-mcp-live-smoke.txt",
            content_type="text/plain",
            title="Zotero MCP live smoke attachment",
            data=attachment_bytes,
        )

        client.patch_item(
            parent_key,
            {
                "title": "Codex Zotero MCP v0.3 Local API smoke test (verified)",
                "tags": [
                    {"tag": "zotero-mcp-live-test"},
                    {"tag": "local-api-verified"},
                ],
            },
        )
        parent_after, _ = client.get_json(f"{client.library_prefix}/items/{parent_key}")
        children, _ = client.get_json(f"{client.library_prefix}/items/{parent_key}/children")
        child_keys = {item.get("key") or item.get("data", {}).get("key") for item in children}
        if parent_after.get("data", {}).get("title", "").endswith("(verified)") is False:
            raise RuntimeError("Updated title was not visible on readback")
        if {note_key, attachment_key} - child_keys:
            raise RuntimeError("Created child objects were not visible on readback")

        client.delete_item(parent_key)
        trash, _ = client.get_json(
            f"{client.library_prefix}/items/trash",
            params={"format": "json"},
        )
        in_trash = any(
            (item.get("key") or item.get("data", {}).get("key")) == parent_key
            for item in trash or []
        )
        result.update(
            {
                "parent_key": parent_key,
                "note_key": note_key,
                "attachment_key": attachment_key,
                "children_verified": len(children),
                "attachment_bytes": len(attachment_bytes),
                "cleanup": "trash" if in_trash else "deleted",
                "success": True,
            }
        )
        parent_key = ""
    finally:
        if parent_key:
            try:
                client.delete_item(parent_key)
            except Exception:
                result["cleanup_warning"] = "Could not move failed smoke-test item to trash"

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
