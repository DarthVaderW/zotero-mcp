#!/usr/bin/env python3
"""No-secret tests for Zotero library workflows."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import doi_ops, library_ops, server


class ZoteroLibraryTest(unittest.TestCase):
    def test_crossref_matches_library_citations_without_network(self):
        citation_file = ROOT / "tests" / "citations.txt"
        citation_file.write_text("Smith (2020) and Doe (2021)", encoding="utf-8")
        items = [
            {
                "data": {
                    "key": "ABC12345",
                    "itemType": "journalArticle",
                    "title": "Known paper",
                    "date": "2020",
                    "creators": [{"lastName": "Smith"}],
                }
            }
        ]
        client = mock.Mock()
        client.library_prefix = "/users/0"
        client.get_all_json.return_value = items
        try:
            with mock.patch.object(doi_ops, "get_local_client", return_value=client):
                result = doi_ops.op_crossref(str(citation_file))
        finally:
            citation_file.unlink(missing_ok=True)

        client.probe.assert_called_once_with()
        client.get_all_json.assert_called_once_with("/users/0/items/top")
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            result["found"],
            [
                {
                    "author": "Smith",
                    "year": "2020",
                    "key": "ABC12345",
                    "title": "Known paper",
                }
            ],
        )
        self.assertEqual(result["missing"], [{"author": "Doe", "year": "2021"}])


    def test_find_dois_can_apply_matched_crossref_result(self):
        item = {
            "version": 7,
            "data": {
                "key": "ABC12345",
                "itemType": "journalArticle",
                "title": "Known paper",
                "date": "2020",
                "DOI": "",
                "creators": [{"lastName": "Smith"}],
            },
        }
        work = {
            "DOI": "10.1234/example",
            "title": ["Known paper"],
            "issued": {"date-parts": [[2020]]},
            "author": [{"family": "Smith"}],
        }
        client = mock.Mock()
        client.library_prefix = "/users/0"
        client.get_all_json.return_value = [
            item,
            {
                "data": {
                    "key": "HASDOI12",
                    "itemType": "journalArticle",
                    "DOI": "10.1/old",
                }
            },
            {"data": {"key": "NOTE1234", "itemType": "note"}},
        ]

        with (
            mock.patch.object(doi_ops, "get_local_client", return_value=client),
            mock.patch.object(doi_ops, "_crossref_search", return_value=[work]),
            mock.patch.object(
                doi_ops, "_patch_item_field", return_value=None
            ) as patch_field,
        ):
            result = doi_ops.op_find_dois(apply=True, sleep_seconds=0)

        client.probe.assert_called_once_with()
        client.get_all_json.assert_called_once_with("/users/0/items/top")
        patch_field.assert_called_once_with("ABC12345", "DOI", "10.1234/example", 7)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["alreadyHadDoi"], 1)
        self.assertEqual(result["wrongItemType"], 1)


    def test_export_paginates_and_returns_text_without_writing(self):
        captured = []

        def fake_request(path, params=None):
            captured.append((path, dict(params or {})))
            if len(captured) == 1:
                return (b"chunk-one", {"Total-Results": "150"}, 200)
            return (b"chunk-two", {"Total-Results": "150"}, 200)

        client = mock.Mock()
        client.library_prefix = "/users/0"
        client.request.side_effect = fake_request
        with mock.patch.object(library_ops, "get_local_client", return_value=client):
            result = library_ops.op_export(format="bibtex", collection="COLL1234")

        self.assertEqual(
            result,
            {
                "format": "bibtex",
                "collection": "COLL1234",
                "bytes": 19,
                "text": "chunk-one\nchunk-two",
            },
        )
        self.assertEqual(
            captured,
            [
                (
                    "/users/0/collections/COLL1234/items",
                    {"format": "bibtex", "limit": "100", "start": "0"},
                ),
                (
                    "/users/0/collections/COLL1234/items",
                    {"format": "bibtex", "limit": "100", "start": "100"},
                ),
            ],
        )


    def test_update_item_uses_local_version_precondition(self):
        client = mock.Mock()
        client.library_prefix = "/users/0"
        client.get_json.return_value = (
            {
                "version": 7,
                "data": {
                    "tags": [{"tag": "old"}],
                    "collections": [],
                },
            },
            {},
        )

        with mock.patch.object(library_ops, "get_local_client", return_value=client):
            result = library_ops.op_update_item(
                "ABC12345",
                title="New title",
                add_tags="new",
                remove_tags="old",
                add_collection="COLL1234",
            )

        client.patch_item.assert_called_once_with(
            "ABC12345",
            {
                "title": "New title",
                "tags": [{"tag": "new"}],
                "collections": ["COLL1234"],
            },
            version=7,
        )
        self.assertEqual(result["status"], "updated")


    def test_identifier_tool_normalizes_type_before_operation(self):
        with mock.patch.object(server, "op_import_identifier", return_value={"ok": True}) as operation:
            result = server.zotero_import_by_identifier("10.1000/test", id_type=" DOI ")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(operation.call_args.kwargs["id_type"], "doi")


    def test_check_pdfs_reads_only_local_library(self):
        client = mock.Mock()
        client.library_prefix = "/users/0"
        client.get_all_json.return_value = [
            {
                "data": {
                    "key": "PARENT01",
                    "itemType": "journalArticle",
                    "title": "Has PDF",
                }
            },
            {
                "data": {
                    "key": "PARENT02",
                    "itemType": "book",
                    "title": "Missing PDF",
                }
            },
            {
                "data": {
                    "key": "PDF00001",
                    "itemType": "attachment",
                    "parentItem": "PARENT01",
                    "contentType": "application/pdf",
                }
            },
        ]

        with mock.patch.object(library_ops, "get_local_client", return_value=client):
            result = library_ops.op_check_pdfs()

        client.get_all_json.assert_called_once_with("/users/0/items")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["with_pdf"], 1)
        self.assertEqual(result["without_pdf"], 1)
        self.assertEqual(
            result["missing"], [{"key": "PARENT02", "title": "Missing PDF"}]
        )


    def test_csl_json_export_combines_pages(self):
        client = mock.Mock()
        client.library_prefix = "/users/0"
        client.request.side_effect = [
            (b'[{"id":"one"}]', {"Total-Results": "101"}, 200),
            (b'[{"id":"two"}]', {"Total-Results": "101"}, 200),
        ]

        with mock.patch.object(library_ops, "get_local_client", return_value=client):
            result = library_ops.op_export(format="csljson")

        self.assertIn('"id": "one"', result["text"])
        self.assertIn('"id": "two"', result["text"])
        self.assertEqual(client.request.call_count, 2)


    def test_retired_backends_are_absent(self):
        retired_names = [
            "ZOTERO_" + "BACKEND",
            "ZOTERO_" + "API_KEY",
            "ZOTERO_" + "USER_ID",
            "ZOTERO_" + "GROUP_ID",
        ]
        checked_files = [
            ROOT / ".env.example",
            ROOT / "README.md",
            ROOT / "docs" / "CODEX_INTEGRATION.md",
            ROOT / "plugins" / "zotero-mcp" / ".mcp.json",
            ROOT / "plugins" / "zotero-mcp" / "claude.mcp.json",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in checked_files)
        for retired_name in retired_names:
            self.assertNotIn(retired_name, combined)
        self.assertFalse((ROOT / "zotero_mcp" / ("web_" + "api.py")).exists())
        self.assertFalse((ROOT / "zotero_mcp" / ("web_" + "items.py")).exists())



if __name__ == "__main__":
    unittest.main(verbosity=2)
