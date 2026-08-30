#!/usr/bin/env python3
"""No-secret tests for the Zotero MCP structured operation surface."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import arxiv, doi_ops, library_ops, operations, server


class ZoteroServerOperationsTest(unittest.TestCase):
    def test_search_operation_returns_json(self):
        fake_items = [{"key": "ABC12345", "title": "Result"}]

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "db_search", return_value=fake_items
            ) as db_search,
        ):
            result = server.zotero_search_items("needle", limit=3)

        db_search.assert_called_once_with("needle", limit=3)
        self.assertEqual(result, {"total": 1, "items": fake_items})

    def test_delete_items_returns_structured_result(self):
        item = {"key": "ABC12345", "title": "A title"}

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(operations, "db_get_item", return_value=item),
            mock.patch.object(
                operations,
                "db_delete_item",
                return_value={"success": True, "mode": "trash"},
            ),
        ):
            result = server.zotero_delete_items(["ABC12345"])

        self.assertEqual(
            result["deleted"],
            [{"key": "ABC12345", "title": "A title", "mode": "trash"}],
        )
        self.assertEqual(result["failed"], [])

    def test_server_search_calls_structured_operation(self):
        expected = {"total": 1, "items": [{"key": "ABC12345"}]}

        with mock.patch.object(server, "op_search", return_value=expected) as op_search:
            result = server.zotero_search_items("needle", limit=3)

        op_search.assert_called_once_with("needle", limit=3)
        self.assertEqual(result, expected)

    def test_server_preserves_root_relative_file_paths(self):
        with mock.patch.object(
            server, "op_attach_pdf", return_value={"attachment_key": "ATT12345"}
        ) as op_attach:
            result = server.zotero_attach_pdf("ABC12345", "paper.pdf")

        op_attach.assert_called_once_with("ABC12345", str(server.ROOT / "paper.pdf"))
        self.assertEqual(result, {"attachment_key": "ATT12345"})

    def test_local_api_error_propagates_without_process_exit(self):
        with mock.patch.object(
            operations,
            "ensure_local_api",
            side_effect=RuntimeError("Cannot reach Zotero Local API"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Cannot reach Zotero Local API"):
                server.zotero_get_item("ABC12345")

    def test_attachment_text_prefers_zotero_full_text_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "paper.html"
            cache = root / ".zotero-ft-cache"
            html.write_text("<html>raw html</html>", encoding="utf-8")
            cache.write_text("clean full text", encoding="utf-8")

            with (
                mock.patch.object(operations, "ensure_local_api", return_value=None),
                mock.patch.object(
                    operations,
                    "db_get_attachment_file",
                    return_value={
                        "key": "ATT12345",
                        "title": "arXiv HTML Snapshot",
                        "contentType": "text/html",
                        "filePath": str(html),
                        "storageDirectory": str(root),
                    },
                ),
            ):
                result = server.zotero_get_attachment_text("ATT12345")

        self.assertEqual(result["source"], "zotero-ft-cache")
        self.assertEqual(result["text"], "clean full text")
        self.assertTrue(result["cacheExists"])
        self.assertFalse(result["truncated"])

    def test_attachment_text_truncates_and_can_read_raw_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "paper.html"
            html.write_text("abcdef", encoding="utf-8")

            with (
                mock.patch.object(operations, "ensure_local_api", return_value=None),
                mock.patch.object(
                    operations,
                    "db_get_attachment_file",
                    return_value={
                        "key": "ATT12345",
                        "title": "arXiv HTML Snapshot",
                        "contentType": "text/html",
                        "filePath": str(html),
                        "storageDirectory": str(root),
                    },
                ),
            ):
                result = server.zotero_get_attachment_text(
                    "ATT12345", max_chars=3, prefer_cache=False
                )

        self.assertEqual(result["source"], "attachment-file")
        self.assertEqual(result["text"], "abc")
        self.assertTrue(result["truncated"])

    def test_attachment_text_rejects_bad_max_chars_cleanly(self):
        with mock.patch.object(
            operations, "ensure_local_api", return_value=None
        ) as ensure_api:
            with self.assertRaisesRegex(RuntimeError, "max_chars"):
                operations.op_attachment_text("ATT12345", max_chars=0)
        ensure_api.assert_not_called()

    def test_attachment_text_rejects_unexpected_local_api_response(self):
        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "db_get_attachment_file", return_value="not-a-dict"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected attachment response"):
                operations.op_attachment_text("ATT12345")

    def test_attachment_text_warns_for_binary_without_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7")

            with (
                mock.patch.object(operations, "ensure_local_api", return_value=None),
                mock.patch.object(
                    operations,
                    "db_get_attachment_file",
                    return_value={
                        "key": "ATT12345",
                        "title": "PDF",
                        "contentType": "application/pdf",
                        "filePath": str(pdf),
                        "storageDirectory": str(root),
                    },
                ),
            ):
                result = server.zotero_get_attachment_text(
                    "ATT12345", prefer_cache=False
                )

        self.assertIsNone(result["source"])
        self.assertEqual(result["text"], "")
        self.assertIn("not text-readable", result["warnings"][0])

    def test_attachment_text_falls_back_to_cache_when_raw_file_is_binary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "paper"
            cache = root / ".zotero-ft-cache"
            pdf.write_bytes(b"%PDF-1.7")
            cache.write_text("indexed pdf text", encoding="utf-8")

            with (
                mock.patch.object(operations, "ensure_local_api", return_value=None),
                mock.patch.object(
                    operations,
                    "db_get_attachment_file",
                    return_value={
                        "key": "ATT12345",
                        "title": "PDF",
                        "contentType": "application/pdf",
                        "filePath": str(pdf),
                        "storageDirectory": str(root),
                    },
                ),
            ):
                result = server.zotero_get_attachment_text(
                    "ATT12345", prefer_cache=False
                )

        self.assertEqual(result["source"], "zotero-ft-cache")
        self.assertEqual(result["text"], "indexed pdf text")
        self.assertTrue(result["cacheExists"])

    def test_find_arxiv_html_url_parses_abs_page_link(self):
        page = b'<a href="/html/2401.01234v1">HTML (experimental)</a>'

        with mock.patch.object(arxiv, "_read_url", return_value=page):
            result = arxiv._find_arxiv_html_url("2401.01234")

        self.assertEqual(result, "https://arxiv.org/html/2401.01234v1")

    def test_fetch_arxiv_metadata_falls_back_on_invalid_xml(self):
        fallback = {
            "title": "Fallback",
            "extra_fields": {"archiveLocation": "2401.01234"},
        }

        with (
            mock.patch.object(arxiv, "_read_url", return_value=b"<not-xml"),
            mock.patch.object(
                arxiv, "_fetch_arxiv_metadata_from_abs_page", return_value=fallback
            ) as from_abs,
        ):
            result = arxiv._fetch_arxiv_metadata("2401.01234")

        from_abs.assert_called_once_with("2401.01234")
        self.assertEqual(result, fallback)

    def test_arxiv_query_value_escape(self):
        self.assertEqual(
            arxiv._escape_arxiv_query_value('a "quoted" title'), r"a \"quoted\" title"
        )

    def test_arxiv_title_score_uses_metadata_similarity(self):
        self.assertGreater(
            arxiv._title_score("Retargeting Matters", "Retargeting Matters"), 0.9
        )

    def test_import_arxiv_keeps_snapshot_result(self):
        meta = {
            "itemType": "preprint",
            "title": "arXiv wrapper test",
            "url": "https://arxiv.org/abs/2401.01234",
            "extra_fields": {"DOI": "10.48550/arXiv.2401.01234"},
            "__pdf_url": "https://arxiv.org/pdf/2401.01234",
        }

        with (
            mock.patch.object(
                arxiv, "_fetch_arxiv_metadata_via_translator", return_value=dict(meta)
            ),
            mock.patch.object(
                arxiv, "_fetch_arxiv_metadata_from_abs_page", return_value=dict(meta)
            ),
            mock.patch.object(
                arxiv,
                "_find_arxiv_html_url",
                return_value="https://arxiv.org/html/2401.01234v1",
            ),
            mock.patch.object(arxiv, "create_item", return_value="ABC12345"),
            mock.patch.object(
                arxiv, "db_add_snapshot", side_effect=["SNAP1234", "HTML1234"]
            ) as add_snapshot,
            mock.patch.object(arxiv, "_download_pdf", return_value=True),
            mock.patch.object(arxiv, "attach_pdf_from_file", return_value="ATT12345"),
        ):
            result = operations.import_arxiv("2401.01234")

        self.assertEqual(
            add_snapshot.call_args_list,
            [
                mock.call("ABC12345", "https://arxiv.org/abs/2401.01234"),
                mock.call(
                    "ABC12345",
                    "https://arxiv.org/html/2401.01234v1",
                    title="arXiv HTML Snapshot",
                ),
            ],
        )
        self.assertEqual(result["snapshot_key"], "SNAP1234")
        self.assertEqual(result["html_snapshot_key"], "HTML1234")
        self.assertEqual(result["htmlSnapshotKey"], "HTML1234")
        self.assertEqual(result["arxivHtmlUrl"], "https://arxiv.org/html/2401.01234v1")

    def test_import_arxiv_returns_item_when_pdf_attachment_fails(self):
        meta = {
            "itemType": "preprint",
            "title": "arXiv wrapper test",
            "url": "https://arxiv.org/abs/2401.01234",
            "extra_fields": {"DOI": "10.48550/arXiv.2401.01234"},
            "__pdf_url": "https://arxiv.org/pdf/2401.01234",
        }

        with (
            mock.patch.object(
                arxiv, "_fetch_arxiv_metadata_via_translator", return_value=dict(meta)
            ),
            mock.patch.object(
                arxiv, "_fetch_arxiv_metadata_from_abs_page", return_value=dict(meta)
            ),
            mock.patch.object(arxiv, "_find_arxiv_html_url", return_value=None),
            mock.patch.object(arxiv, "create_item", return_value="ABC12345"),
            mock.patch.object(arxiv, "db_add_snapshot", return_value="SNAP1234"),
            mock.patch.object(arxiv, "_download_pdf", return_value=False),
            mock.patch.object(arxiv, "attach_pdf_from_file") as attach_pdf,
        ):
            result = operations.import_arxiv("2401.01234")

        attach_pdf.assert_not_called()
        self.assertEqual(result["item_key"], "ABC12345")
        self.assertIsNone(result["attachment_key"])
        self.assertIsNone(result["pdfAttachmentKey"])
        self.assertIn(
            "pdf attachment failed: Failed to download arXiv PDF", result["warnings"][0]
        )

    def test_attach_arxiv_sidecars_reuses_existing_pdf_and_html(self):
        children = [
            {
                "key": "PDF12345",
                "itemType": "attachment",
                "title": "Preprint PDF",
                "contentType": "application/pdf",
                "url": "",
            },
            {
                "key": "HTML1234",
                "itemType": "attachment",
                "title": "arXiv HTML Snapshot",
                "contentType": "text/html",
                "url": "https://arxiv.org/html/2401.01234v1",
            },
        ]

        with (
            mock.patch.object(arxiv, "_download_pdf") as download_pdf,
            mock.patch.object(arxiv, "_find_arxiv_html_url") as find_html,
            mock.patch.object(arxiv, "db_add_snapshot") as add_snapshot,
        ):
            result = arxiv.attach_arxiv_sidecars(
                "ABC12345", "2401.01234v1", children=children
            )

        download_pdf.assert_not_called()
        find_html.assert_not_called()
        add_snapshot.assert_not_called()
        self.assertEqual(result["attachment_key"], "PDF12345")
        self.assertEqual(result["html_snapshot_key"], "HTML1234")
        self.assertEqual(result["sidecars"]["pdf"]["status"], "existing")
        self.assertEqual(result["sidecars"]["html"]["status"], "existing")

    def test_attach_arxiv_sidecars_adds_missing_pdf_and_html(self):
        with (
            mock.patch.object(
                arxiv, "_download_pdf", return_value=True
            ) as download_pdf,
            mock.patch.object(
                arxiv, "attach_pdf_from_file", return_value="PDF12345"
            ) as attach_pdf,
            mock.patch.object(
                arxiv,
                "_find_arxiv_html_url",
                return_value="https://arxiv.org/html/2401.01234v1",
            ),
            mock.patch.object(
                arxiv, "db_add_snapshot", return_value="HTML1234"
            ) as add_snapshot,
        ):
            result = arxiv.attach_arxiv_sidecars("ABC12345", "2401.01234", children=[])

        download_pdf.assert_called_once()
        attach_pdf.assert_called_once()
        add_snapshot.assert_called_once_with(
            "ABC12345",
            "https://arxiv.org/html/2401.01234v1",
            title="arXiv HTML Snapshot",
        )
        self.assertEqual(result["attachment_key"], "PDF12345")
        self.assertEqual(result["html_snapshot_key"], "HTML1234")
        self.assertEqual(result["sidecars"]["pdf"]["status"], "added")
        self.assertEqual(result["sidecars"]["html"]["status"], "added")

    def test_attach_arxiv_sidecars_does_not_treat_abs_snapshot_as_html(self):
        children = [
            {
                "key": "PDF12345",
                "itemType": "attachment",
                "title": "Preprint PDF",
                "contentType": "application/pdf",
                "url": "",
            },
            {
                "key": "ABS12345",
                "itemType": "attachment",
                "title": "Web Page Snapshot",
                "contentType": "text/html",
                "url": "https://arxiv.org/abs/2401.01234",
            },
        ]

        with (
            mock.patch.object(
                arxiv,
                "_find_arxiv_html_url",
                return_value="https://arxiv.org/html/2401.01234v1",
            ),
            mock.patch.object(
                arxiv, "db_add_snapshot", return_value="HTML1234"
            ) as add_snapshot,
        ):
            result = arxiv.attach_arxiv_sidecars(
                "ABC12345", "2401.01234", children=children
            )

        add_snapshot.assert_called_once_with(
            "ABC12345",
            "https://arxiv.org/html/2401.01234v1",
            title="arXiv HTML Snapshot",
        )
        self.assertEqual(result["html_snapshot_key"], "HTML1234")
        self.assertEqual(result["sidecars"]["html"]["status"], "added")

    def test_op_arxiv_reuses_existing_item_by_default(self):
        existing = [{"key": "ABC12345", "title": "Existing"}]
        sidecars = {
            "attachment_key": "PDF12345",
            "html_snapshot_key": "HTML1234",
            "pdfAttachmentKey": "PDF12345",
            "htmlSnapshotKey": "HTML1234",
            "arxiv_abs_url": "https://arxiv.org/abs/2401.01234",
            "arxiv_pdf_url": "https://arxiv.org/pdf/2401.01234",
            "arxiv_html_url": "https://arxiv.org/html/2401.01234v1",
            "arxivAbsUrl": "https://arxiv.org/abs/2401.01234",
            "arxivPdfUrl": "https://arxiv.org/pdf/2401.01234",
            "arxivHtmlUrl": "https://arxiv.org/html/2401.01234v1",
            "warnings": [],
            "sidecars": {"pdf": {"status": "added"}, "html": {"status": "added"}},
        }

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "db_find_arxiv_item", return_value=existing
            ) as find_item,
            mock.patch.object(
                operations, "attach_arxiv_sidecars", return_value=sidecars
            ) as top_up,
            mock.patch.object(operations, "import_arxiv") as import_item,
        ):
            result = operations.op_arxiv("2401.01234")

        find_item.assert_called_once_with("2401.01234")
        top_up.assert_called_once_with("ABC12345", "2401.01234", attach_html=True)
        import_item.assert_not_called()
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["item_key"], "ABC12345")
        self.assertEqual(result["attachment_key"], "PDF12345")
        self.assertEqual(result["html_snapshot_key"], "HTML1234")
        self.assertEqual(result["sidecars"]["html"]["status"], "added")

    def test_op_arxiv_existing_survives_sidecar_failure(self):
        existing = [{"key": "ABC12345", "title": "Existing"}]

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(operations, "db_find_arxiv_item", return_value=existing),
            mock.patch.object(
                operations,
                "attach_arxiv_sidecars",
                side_effect=RuntimeError("network down"),
            ),
            mock.patch.object(operations, "import_arxiv") as import_item,
        ):
            result = operations.op_arxiv("2401.01234")

        import_item.assert_not_called()
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["item_key"], "ABC12345")
        self.assertIn("sidecar top-up failed: network down", result["warnings"])

    def test_op_arxiv_force_skips_duplicate_check(self):
        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(operations, "db_find_arxiv_item") as find_item,
            mock.patch.object(
                operations, "import_arxiv", return_value={"item_key": "NEW12345"}
            ) as import_item,
        ):
            result = operations.op_arxiv("2401.01234", force=True)

        find_item.assert_not_called()
        import_item.assert_called_once_with(
            "2401.01234", collection_name_or_key=None, attach_html=True
        )
        self.assertEqual(result["status"], "added")

    def test_capture_arxiv_title_is_read_only_until_confirmed(self):
        candidates = [{"arxiv_id": "2401.01234", "title": "Candidate"}]

        with (
            mock.patch.object(
                operations,
                "search_arxiv",
                return_value={
                    "query": "Candidate",
                    "total": 1,
                    "candidates": candidates,
                },
            ) as search,
            mock.patch.object(operations, "import_arxiv") as import_item,
        ):
            result = operations.op_capture_arxiv("Candidate")

        search.assert_called_once_with("Candidate", limit=5)
        import_item.assert_not_called()
        self.assertEqual(result["status"], "needs_selection")
        self.assertEqual(result["candidates"], candidates)

    def test_capture_arxiv_confirmed_candidate_writes(self):
        with mock.patch.object(
            operations,
            "op_arxiv",
            return_value={"status": "added", "item_key": "ABC12345"},
        ) as op_arxiv:
            result = operations.op_capture_arxiv(
                "Candidate title",
                confirmed_arxiv_id="2401.01234",
                collection="Inbox",
                attach_html=False,
            )

        op_arxiv.assert_called_once_with(
            "2401.01234",
            collection_name_or_key="Inbox",
            attach_html=False,
            force=False,
        )
        self.assertEqual(result, {"status": "added", "item_key": "ABC12345"})

    def test_capture_arxiv_bare_id_writes(self):
        with mock.patch.object(
            operations,
            "op_arxiv",
            return_value={"status": "added", "item_key": "ABC12345"},
        ) as op_arxiv:
            result = operations.op_capture_arxiv(
                "https://arxiv.org/html/2401.01234v1",
                collection="Inbox",
                attach_html=True,
            )

        op_arxiv.assert_called_once_with(
            "2401.01234v1",
            collection_name_or_key="Inbox",
            attach_html=True,
            force=False,
        )
        self.assertEqual(result, {"status": "added", "item_key": "ABC12345"})

    def test_attach_snapshot_operation_uses_local_api(self):
        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "db_add_snapshot", return_value="SNAP1234"
            ) as add_snapshot,
        ):
            result = operations.op_attach_snapshot(
                "ABC12345",
                "https://arxiv.org/html/2401.01234v1",
                title="arXiv HTML Snapshot",
            )

        add_snapshot.assert_called_once_with(
            "ABC12345",
            "https://arxiv.org/html/2401.01234v1",
            title="arXiv HTML Snapshot",
        )
        self.assertEqual(
            result,
            {
                "snapshot_key": "SNAP1234",
                "url": "https://arxiv.org/html/2401.01234v1",
                "title": "arXiv HTML Snapshot",
            },
        )

    def test_server_local_tools_call_structured_operations(self):
        with mock.patch.object(
            server, "op_check_pdfs", return_value={"total": 0}
        ) as op_check:
            self.assertEqual(server.zotero_check_pdfs(), {"total": 0})
        op_check.assert_called_once_with()

        expected = {"status": "updated", "key": "ABC12345", "changes": {"title": "T"}}
        with mock.patch.object(
            server, "op_update_item", return_value=expected
        ) as op_update:
            result = server.zotero_update_item("ABC12345", title="T")
        op_update.assert_called_once_with(
            "ABC12345",
            title="T",
            date=None,
            doi=None,
            url=None,
            add_tags=None,
            remove_tags=None,
            add_collection=None,
        )
        self.assertEqual(result, expected)

        with mock.patch.object(
            server, "op_attach_snapshot", return_value={"snapshot_key": "SNAP1234"}
        ) as op_snapshot:
            result = server.zotero_attach_snapshot(
                "ABC12345", "https://arxiv.org/html/2401.01234v1", title="HTML"
            )
        op_snapshot.assert_called_once_with(
            "ABC12345", "https://arxiv.org/html/2401.01234v1", title="HTML"
        )
        self.assertEqual(result, {"snapshot_key": "SNAP1234"})

        with mock.patch.object(
            server, "op_search_arxiv", return_value={"total": 0, "candidates": []}
        ) as op_search:
            result = server.zotero_search_arxiv("needle", limit=4)
        op_search.assert_called_once_with("needle", limit=4)
        self.assertEqual(result, {"total": 0, "candidates": []})

        expected_import = {"status": "added", "item_key": "NEW12345"}
        with mock.patch.object(
            server, "op_import_identifier", return_value=expected_import
        ) as op_import:
            result = server.zotero_import_by_identifier(
                "10.1234/example",
                collection="Inbox",
                tags="reading",
                attach_pdf=False,
            )
        op_import.assert_called_once_with(
            "10.1234/example",
            id_type="doi",
            collection="Inbox",
            tags="reading",
            force=False,
            attach_pdf=False,
        )
        self.assertEqual(result, expected_import)

        expected_sidecars = {"status": "updated", "item_key": "ABC12345"}
        with mock.patch.object(
            server, "op_attach_arxiv_sidecars", return_value=expected_sidecars
        ) as op_sidecars:
            result = server.zotero_attach_arxiv_sidecars(
                "ABC12345", "2401.01234", attach_html=False
            )
        op_sidecars.assert_called_once_with("ABC12345", "2401.01234", attach_html=False)
        self.assertEqual(result, expected_sidecars)

        expected_capture = {"status": "needs_selection", "candidates": []}
        with mock.patch.object(
            server, "op_capture_arxiv", return_value=expected_capture
        ) as op_capture:
            result = server.zotero_capture_arxiv(
                "needle", collection="Inbox", attach_html=False
            )
        op_capture.assert_called_once_with(
            "needle",
            confirmed_arxiv_id=None,
            collection="Inbox",
            attach_html=False,
            force=False,
        )
        self.assertEqual(result, expected_capture)

    def test_import_identifier_creates_local_item(self):
        translated = {
            "itemType": "journalArticle",
            "title": "Translated paper",
            "DOI": "10.1234/example",
            "key": "OLDKEY12",
            "version": 9,
            "relations": {},
            "attachments": [{"title": "remote"}],
            "tags": [{"tag": "existing"}],
        }

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "_translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                operations, "db_find_item_by_identifier", return_value=[]
            ),
            mock.patch.object(
                operations, "create_item", return_value="NEW12345"
            ) as create_item,
            mock.patch.object(
                operations,
                "db_add_item_to_collection",
                return_value={"collectionKey": "COLL1234"},
            ),
            mock.patch.object(operations, "db_get_children", return_value=[]),
            mock.patch.object(operations, "_find_pdf_source", return_value=None),
        ):
            result = operations.op_import_identifier(
                "10.1234/example",
                collection="COLL1234",
                tags="reading, priority",
            )

        payload = create_item.call_args.args[0]
        self.assertEqual(result["status"], "added")
        self.assertEqual(result["item_key"], "NEW12345")
        self.assertEqual(result["pdfStatus"], "needs_user_file")
        self.assertEqual(
            payload["tags"],
            [{"tag": "existing"}, {"tag": "reading"}, {"tag": "priority"}],
        )
        for removed_field in ("key", "version", "relations", "attachments"):
            self.assertNotIn(removed_field, payload)

    def test_import_identifier_reuses_existing_and_skips_create(self):
        translated = {
            "itemType": "journalArticle",
            "title": "Known paper",
            "DOI": "10.1234/example",
        }
        existing = [{"key": "ABC12345", "title": "Known paper", "match": {"doi": True}}]

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "_translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                operations, "db_find_item_by_identifier", return_value=existing
            ),
            mock.patch.object(operations, "create_item") as create_item,
            mock.patch.object(operations, "db_get_children", return_value=[]),
            mock.patch.object(operations, "_find_pdf_source", return_value=None),
        ):
            result = operations.op_import_identifier("10.1234/example")

        create_item.assert_not_called()
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["item_key"], "ABC12345")
        self.assertEqual(result["pdfStatus"], "needs_user_file")

    def test_import_identifier_attaches_open_pdf_locally(self):
        translated = {
            "itemType": "journalArticle",
            "title": "OA paper",
            "DOI": "10.1234/oa",
        }

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "_translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                operations, "db_find_item_by_identifier", return_value=[]
            ),
            mock.patch.object(operations, "create_item", return_value="NEW12345"),
            mock.patch.object(operations, "db_get_children", return_value=[]),
            mock.patch.object(
                operations,
                "_find_pdf_source",
                return_value=(
                    "https://example.com/paper.pdf",
                    "https://example.com/source",
                    "unpaywall",
                ),
            ),
            mock.patch.object(
                operations, "_download_pdf", return_value=True
            ) as download_pdf,
            mock.patch.object(
                operations, "attach_pdf_from_file", return_value="ATT12345"
            ) as attach_pdf,
        ):
            result = operations.op_import_identifier("10.1234/oa")

        download_pdf.assert_called_once()
        attach_pdf.assert_called_once()
        self.assertEqual(result["pdfStatus"], "attached")
        self.assertEqual(result["pdfAttachmentKey"], "ATT12345")

    def test_import_identifier_keeps_item_key_when_pdf_attach_fails(self):
        translated = {
            "itemType": "journalArticle",
            "title": "OA paper",
            "DOI": "10.1234/oa",
        }

        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "_translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                operations, "db_find_item_by_identifier", return_value=[]
            ),
            mock.patch.object(operations, "create_item", return_value="NEW12345"),
            mock.patch.object(operations, "db_get_children", return_value=[]),
            mock.patch.object(
                operations,
                "_find_pdf_source",
                return_value=(
                    "https://example.com/paper.pdf",
                    "https://example.com/source",
                    "unpaywall",
                ),
            ),
            mock.patch.object(operations, "_download_pdf", return_value=True),
            mock.patch.object(
                operations,
                "attach_pdf_from_file",
                side_effect=RuntimeError("attach failed"),
            ),
        ):
            result = operations.op_import_identifier("10.1234/oa")

        self.assertEqual(result["status"], "added")
        self.assertEqual(result["item_key"], "NEW12345")
        self.assertEqual(result["pdfStatus"], "attach_failed")
        self.assertIn("attach failed", result["warnings"][0])

    def test_attach_arxiv_sidecars_targets_known_item(self):
        sidecars = {"pdfAttachmentKey": "PDF12345", "warnings": []}
        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(
                operations, "db_get_item", return_value={"key": "ABC12345"}
            ),
            mock.patch.object(operations, "db_get_children", return_value=[]),
            mock.patch.object(
                operations, "attach_arxiv_sidecars", return_value=sidecars
            ) as attach_sidecars,
        ):
            result = operations.op_attach_arxiv_sidecars(
                "ABC12345", "https://arxiv.org/abs/2401.01234v2"
            )

        attach_sidecars.assert_called_once_with(
            "ABC12345", "2401.01234v2", attach_html=True, children=[]
        )
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["arxivId"], "2401.01234v2")
        self.assertEqual(result["pdfAttachmentKey"], "PDF12345")

    def test_attach_arxiv_sidecars_reports_invalid_id_cleanly(self):
        with (
            mock.patch.object(operations, "ensure_local_api", return_value=None),
            mock.patch.object(operations, "db_get_item") as db_get_item,
        ):
            with self.assertRaisesRegex(RuntimeError, "Invalid arXiv ID"):
                operations.op_attach_arxiv_sidecars("ABC12345", "not-an-arxiv-id")

        db_get_item.assert_not_called()

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

        client.request.assert_called_once_with(
            "/users/0/items/ABC12345",
            method="PATCH",
            data={
                "title": "New title",
                "tags": [{"tag": "new"}],
                "collections": ["COLL1234"],
            },
            content_type="application/json",
            headers={"If-Unmodified-Since-Version": "7"},
        )
        self.assertEqual(result["status"], "updated")

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
