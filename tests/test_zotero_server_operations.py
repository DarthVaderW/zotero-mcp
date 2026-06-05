#!/usr/bin/env python3
"""No-secret tests for the Zotero MCP structured operation surface."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import arxiv, debug_bridge, doi_ops, identifiers, operations, pdf_discovery, server, web_api, web_items


class ZoteroServerOperationsTest(unittest.TestCase):
    def test_search_operation_returns_json(self):
        fake_items = [{"key": "ABC12345", "title": "Result"}]

        with (
            mock.patch.object(operations, "ensure_debug_bridge", return_value=None),
            mock.patch.object(operations, "db_search", return_value=fake_items) as db_search,
        ):
            result = server.zotero_search_items("needle", limit=3)

        db_search.assert_called_once_with("needle", limit=3)
        self.assertEqual(result, {"total": 1, "items": fake_items})

    def test_delete_items_returns_structured_result(self):
        item = {"key": "ABC12345", "title": "A title"}

        with (
            mock.patch.object(operations, "ensure_debug_bridge", return_value=None),
            mock.patch.object(operations, "db_get_item", return_value=item),
            mock.patch.object(operations, "db_delete_item", return_value={"success": True, "mode": "trash"}),
        ):
            result = server.zotero_delete_items(["ABC12345"])

        self.assertEqual(result["deleted"], [{"key": "ABC12345", "title": "A title", "mode": "trash"}])
        self.assertEqual(result["failed"], [])

    def test_server_search_calls_structured_operation(self):
        expected = {"total": 1, "items": [{"key": "ABC12345"}]}

        with mock.patch.object(server, "op_search", return_value=expected) as op_search:
            result = server.zotero_search_items("needle", limit=3)

        op_search.assert_called_once_with("needle", limit=3)
        self.assertEqual(result, expected)

    def test_server_preserves_root_relative_file_paths(self):
        with mock.patch.object(server, "op_attach_pdf", return_value={"attachment_key": "ATT12345"}) as op_attach:
            result = server.zotero_attach_pdf("ABC12345", "paper.pdf")

        op_attach.assert_called_once_with("ABC12345", str(server.ROOT / "paper.pdf"))
        self.assertEqual(result, {"attachment_key": "ATT12345"})

    def test_nonzero_command_exit_raises_runtime_error(self):
        with self.assertRaisesRegex(RuntimeError, "ZOTERO_DEBUG_BRIDGE_TOKEN"):
            server.zotero_get_item("ABC12345")

    def test_api_request_raises_instead_of_exiting(self):
        error = urllib.error.HTTPError(
            "https://api.zotero.org/users/1/items",
            403,
            "Forbidden",
            {},
            None,
        )

        with mock.patch.object(web_api.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(operations.CommandError) as ctx:
                operations.api_request("/users/1/items", "api-key")

        self.assertEqual(ctx.exception.code, 403)
        self.assertIn("API Error 403", str(ctx.exception))

    def test_debug_bridge_wrappers_execute_without_orphaned_names(self):
        with mock.patch.object(debug_bridge, "debug_bridge", return_value={"key": "ABC12345", "success": True}) as bridge:
            result = debug_bridge.db_create_item({"itemType": "book", "title": "Wrapper test"})

        self.assertEqual(result, {"key": "ABC12345", "success": True})
        self.assertIn('new Zotero.Item("book")', bridge.call_args.args[0])

        with self.assertRaisesRegex(RuntimeError, "itemType is required"):
            debug_bridge.db_create_item({"title": "Missing type"})

        with mock.patch.object(debug_bridge, "debug_bridge", return_value=[]) as bridge:
            result = debug_bridge.db_find_arxiv_item("2401.01234v2")

        self.assertEqual(result, [])
        self.assertIn("10.48550/arxiv.2401.01234", bridge.call_args.args[0])
        self.assertIn("archiveLocation", bridge.call_args.args[0])
        self.assertIn("Zotero.Items.getAll(lib, false)", bridge.call_args.args[0])
        self.assertNotIn('["itemType", "title"', bridge.call_args.args[0])
        self.assertIn("if (item.loadAllData) await item.loadAllData();", bridge.call_args.args[0])
        self.assertIn('next === "v"', bridge.call_args.args[0])
        self.assertIn("const extraLower = extra.toLowerCase();", bridge.call_args.args[0])

    def test_debug_bridge_snapshot_wrapper_accepts_title(self):
        with mock.patch.object(debug_bridge, "debug_bridge", return_value="SNAP1234") as bridge:
            result = debug_bridge.db_add_snapshot(
                "ABC12345",
                "https://arxiv.org/html/2401.01234v1",
                title="arXiv HTML Snapshot",
            )

        self.assertEqual(result, "SNAP1234")
        self.assertIn("arXiv HTML Snapshot", bridge.call_args.args[0])

    def test_debug_bridge_children_include_attachment_url(self):
        with mock.patch.object(debug_bridge, "debug_bridge", return_value=[]) as bridge:
            result = debug_bridge.db_get_children("ABC12345")

        self.assertEqual(result, [])
        self.assertIn('url: att.getField("url")', bridge.call_args.args[0])

    def test_find_arxiv_html_url_parses_abs_page_link(self):
        page = b'<a href="/html/2401.01234v1">HTML (experimental)</a>'

        with mock.patch.object(arxiv, "_read_url", return_value=page):
            result = arxiv._find_arxiv_html_url("2401.01234")

        self.assertEqual(result, "https://arxiv.org/html/2401.01234v1")

    def test_arxiv_query_value_escape(self):
        self.assertEqual(arxiv._escape_arxiv_query_value('a "quoted" title'), r'a \"quoted\" title')

    def test_arxiv_title_score_uses_metadata_similarity(self):
        self.assertGreater(arxiv._title_score("Retargeting Matters", "Retargeting Matters"), 0.9)

    def test_debug_bridge_attachment_wrapper_checks_local_path(self):
        pdf = ROOT / "tests" / "fixture.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        try:
            with mock.patch.object(debug_bridge, "debug_bridge", return_value="ATT12345") as bridge:
                result = debug_bridge.db_add_attachment("ABC12345", str(pdf), title="Full Text PDF")
        finally:
            pdf.unlink(missing_ok=True)

        self.assertEqual(result, {"success": True, "attachment_key": "ATT12345"})
        self.assertIn(str(pdf), bridge.call_args.args[0])

    def test_import_arxiv_keeps_snapshot_result(self):
        meta = {
            "itemType": "preprint",
            "title": "arXiv wrapper test",
            "url": "https://arxiv.org/abs/2401.01234",
            "extra_fields": {"DOI": "10.48550/arXiv.2401.01234"},
            "__pdf_url": "https://arxiv.org/pdf/2401.01234",
        }

        with (
            mock.patch.object(arxiv, "_fetch_arxiv_metadata_via_translator", return_value=dict(meta)),
            mock.patch.object(arxiv, "_fetch_arxiv_metadata_from_abs_page", return_value=dict(meta)),
            mock.patch.object(arxiv, "_find_arxiv_html_url", return_value="https://arxiv.org/html/2401.01234v1"),
            mock.patch.object(arxiv, "create_item", return_value="ABC12345"),
            mock.patch.object(arxiv, "db_add_snapshot", side_effect=["SNAP1234", "HTML1234"]) as add_snapshot,
            mock.patch.object(arxiv, "_download_pdf", return_value=True),
            mock.patch.object(arxiv, "attach_pdf_from_file", return_value="ATT12345"),
        ):
            result = operations.import_arxiv("2401.01234")

        self.assertEqual(
            add_snapshot.call_args_list,
            [
                mock.call("ABC12345", "https://arxiv.org/abs/2401.01234"),
                mock.call("ABC12345", "https://arxiv.org/html/2401.01234v1", title="arXiv HTML Snapshot"),
            ],
        )
        self.assertEqual(result["snapshot_key"], "SNAP1234")
        self.assertEqual(result["html_snapshot_key"], "HTML1234")
        self.assertEqual(result["htmlSnapshotKey"], "HTML1234")
        self.assertEqual(result["arxivHtmlUrl"], "https://arxiv.org/html/2401.01234v1")

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
            result = arxiv.attach_arxiv_sidecars("ABC12345", "2401.01234v1", children=children)

        download_pdf.assert_not_called()
        find_html.assert_not_called()
        add_snapshot.assert_not_called()
        self.assertEqual(result["attachment_key"], "PDF12345")
        self.assertEqual(result["html_snapshot_key"], "HTML1234")
        self.assertEqual(result["sidecars"]["pdf"]["status"], "existing")
        self.assertEqual(result["sidecars"]["html"]["status"], "existing")

    def test_attach_arxiv_sidecars_adds_missing_pdf_and_html(self):
        with (
            mock.patch.object(arxiv, "_download_pdf", return_value=True) as download_pdf,
            mock.patch.object(arxiv, "attach_pdf_from_file", return_value="PDF12345") as attach_pdf,
            mock.patch.object(arxiv, "_find_arxiv_html_url", return_value="https://arxiv.org/html/2401.01234v1"),
            mock.patch.object(arxiv, "db_add_snapshot", return_value="HTML1234") as add_snapshot,
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
            mock.patch.object(operations, "ensure_debug_bridge", return_value=None),
            mock.patch.object(operations, "db_find_arxiv_item", return_value=existing) as find_item,
            mock.patch.object(operations, "attach_arxiv_sidecars", return_value=sidecars) as top_up,
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
            mock.patch.object(operations, "ensure_debug_bridge", return_value=None),
            mock.patch.object(operations, "db_find_arxiv_item", return_value=existing),
            mock.patch.object(operations, "attach_arxiv_sidecars", side_effect=RuntimeError("network down")),
            mock.patch.object(operations, "import_arxiv") as import_item,
        ):
            result = operations.op_arxiv("2401.01234")

        import_item.assert_not_called()
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["item_key"], "ABC12345")
        self.assertIn("sidecar top-up failed: network down", result["warnings"])

    def test_op_arxiv_force_skips_duplicate_check(self):
        with (
            mock.patch.object(operations, "ensure_debug_bridge", return_value=None),
            mock.patch.object(operations, "db_find_arxiv_item") as find_item,
            mock.patch.object(operations, "import_arxiv", return_value={"item_key": "NEW12345"}) as import_item,
        ):
            result = operations.op_arxiv("2401.01234", force=True)

        find_item.assert_not_called()
        import_item.assert_called_once_with("2401.01234", collection_name_or_key=None, attach_html=True)
        self.assertEqual(result["status"], "added")

    def test_capture_arxiv_title_is_read_only_until_confirmed(self):
        candidates = [{"arxiv_id": "2401.01234", "title": "Candidate"}]

        with (
            mock.patch.object(operations, "search_arxiv", return_value={"query": "Candidate", "total": 1, "candidates": candidates}) as search,
            mock.patch.object(operations, "import_arxiv") as import_item,
        ):
            result = operations.op_capture_arxiv("Candidate")

        search.assert_called_once_with("Candidate", limit=5)
        import_item.assert_not_called()
        self.assertEqual(result["status"], "needs_selection")
        self.assertEqual(result["candidates"], candidates)

    def test_capture_arxiv_confirmed_candidate_writes(self):
        with mock.patch.object(operations, "op_arxiv", return_value={"status": "added", "item_key": "ABC12345"}) as op_arxiv:
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

    def test_attach_snapshot_operation_uses_debug_bridge(self):
        with (
            mock.patch.object(operations, "ensure_debug_bridge", return_value=None),
            mock.patch.object(operations, "db_add_snapshot", return_value="SNAP1234") as add_snapshot,
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

    def test_fetch_pdfs_local_mode_preserves_debug_bridge_guard(self):
        with (
            mock.patch.object(pdf_discovery, "ensure_debug_bridge", return_value=None) as ensure_bridge,
            mock.patch.object(pdf_discovery, "attach_pdf_from_file", return_value="ATT12345") as attach_pdf,
        ):
            result = pdf_discovery.op_fetch_pdfs(key="ABC12345", file="/tmp/paper.pdf")

        ensure_bridge.assert_called_once_with()
        attach_pdf.assert_called_once_with("ABC12345", "/tmp/paper.pdf", title="Full Text PDF")
        self.assertEqual(result, {"attachment_key": "ATT12345"})

    def test_server_web_api_tools_call_structured_operations(self):
        with mock.patch.object(server, "op_check_pdfs", return_value={"total": 0}) as op_check:
            self.assertEqual(server.zotero_check_pdfs(), {"total": 0})
        op_check.assert_called_once_with()

        expected = {"status": "updated", "key": "ABC12345", "changes": {"title": "T"}}
        with mock.patch.object(server, "op_update_item", return_value=expected) as op_update:
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

        with mock.patch.object(server, "op_attach_snapshot", return_value={"snapshot_key": "SNAP1234"}) as op_snapshot:
            result = server.zotero_attach_snapshot("ABC12345", "https://arxiv.org/html/2401.01234v1", title="HTML")
        op_snapshot.assert_called_once_with("ABC12345", "https://arxiv.org/html/2401.01234v1", title="HTML")
        self.assertEqual(result, {"snapshot_key": "SNAP1234"})

        with mock.patch.object(server, "op_search_arxiv", return_value={"total": 0, "candidates": []}) as op_search:
            result = server.zotero_search_arxiv("needle", limit=4)
        op_search.assert_called_once_with("needle", limit=4)
        self.assertEqual(result, {"total": 0, "candidates": []})

        expected_capture = {"status": "needs_selection", "candidates": []}
        with mock.patch.object(server, "op_capture_arxiv", return_value=expected_capture) as op_capture:
            result = server.zotero_capture_arxiv("needle", collection="Inbox", attach_html=False)
        op_capture.assert_called_once_with(
            "needle",
            confirmed_arxiv_id=None,
            collection="Inbox",
            attach_html=False,
            force=False,
        )
        self.assertEqual(result, expected_capture)

    def test_add_identifier_cleans_translated_item_and_posts_json(self):
        translated = {
            "itemType": "journalArticle",
            "title": "Translated paper",
            "key": "OLDKEY12",
            "version": 9,
            "relations": {},
            "tags": [{"tag": "existing"}],
        }
        response = {"successful": {"0": {"key": "NEW12345", "data": {"title": "Translated paper"}}}}

        with (
            mock.patch.object(identifiers, "get_api_config", return_value=("api-key", "/users/1")),
            mock.patch.object(identifiers, "_translate_identifier", return_value=[translated]),
            mock.patch.object(identifiers, "_check_duplicate_by_metadata", return_value=None),
            mock.patch.object(identifiers, "api_request", return_value=(json.dumps(response), {})) as api_request,
        ):
            result = identifiers.op_add_identifier(
                "10.1234/example",
                collection="COLL1234",
                tags="reading, priority",
            )

        posted_items = api_request.call_args.kwargs["data"]
        self.assertEqual(result["status"], "added")
        self.assertEqual(result["successful"], [{"key": "NEW12345", "title": "Translated paper"}])
        self.assertEqual(posted_items[0]["collections"], ["COLL1234"])
        self.assertEqual(posted_items[0]["tags"], [{"tag": "existing"}, {"tag": "reading"}, {"tag": "priority"}])
        for removed_field in ("key", "version", "relations"):
            self.assertNotIn(removed_field, posted_items[0])

    def test_batch_add_reports_added_duplicate_and_failed(self):
        ids_file = ROOT / "tests" / "identifiers.txt"
        ids_file.write_text("# skip me\n10.1/one\n\n10.2/two\n10.3/three\n", encoding="utf-8")
        try:
            with (
                mock.patch.object(identifiers, "get_api_config", return_value=("api-key", "/users/1")),
                mock.patch.object(
                    identifiers,
                    "op_add_identifier",
                    side_effect=[
                        {"status": "added", "identifier": "10.1/one"},
                        {"status": "duplicate", "identifier": "10.2/two"},
                        RuntimeError("bad identifier"),
                    ],
                ) as add_identifier,
                mock.patch.object(identifiers.time, "sleep") as sleep,
            ):
                result = identifiers.op_batch_add(str(ids_file), tags="todo", sleep_seconds=0)
        finally:
            ids_file.unlink(missing_ok=True)

        self.assertEqual(result["total"], 3)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(add_identifier.call_count, 3)
        sleep.assert_not_called()

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
        try:
            with (
                mock.patch.object(doi_ops, "get_api_config", return_value=("api-key", "/users/1")),
                mock.patch.object(doi_ops, "paginate_all", return_value=items),
            ):
                result = doi_ops.op_crossref(str(citation_file))
        finally:
            citation_file.unlink(missing_ok=True)

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["found"], [{"author": "Smith", "year": "2020", "key": "ABC12345", "title": "Known paper"}])
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

        with (
            mock.patch.object(doi_ops, "get_api_config", return_value=("api-key", "/users/1")),
            mock.patch.object(
                doi_ops,
                "paginate_all",
                return_value=[
                    item,
                    {"data": {"key": "HASDOI12", "itemType": "journalArticle", "DOI": "10.1/old"}},
                    {"data": {"key": "NOTE1234", "itemType": "note"}},
                ],
            ),
            mock.patch.object(doi_ops, "_crossref_search", return_value=[work]),
            mock.patch.object(doi_ops, "_patch_item_field", return_value=204) as patch_field,
        ):
            result = doi_ops.op_find_dois(apply=True, sleep_seconds=0)

        patch_field.assert_called_once_with("api-key", "/users/1", "ABC12345", "DOI", "10.1234/example", 7)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["alreadyHadDoi"], 1)
        self.assertEqual(result["wrongItemType"], 1)

    def test_export_paginates_and_returns_text_without_writing(self):
        captured = []

        def fake_api_request(path, api_key, params=None):
            captured.append((path, api_key, dict(params or {})))
            if len(captured) == 1:
                return ("chunk-one", {"Total-Results": "150"})
            return ("chunk-two", {"Total-Results": "150"})

        with (
            mock.patch.object(web_items, "get_api_config", return_value=("api-key", "/users/1")),
            mock.patch.object(web_items, "api_request", side_effect=fake_api_request),
        ):
            result = web_items.op_export(format="bibtex", collection="COLL1234")

        self.assertEqual(result, {"format": "bibtex", "collection": "COLL1234", "bytes": 19, "text": "chunk-one\nchunk-two"})
        self.assertEqual(
            captured,
            [
                ("/users/1/collections/COLL1234/items", "api-key", {"format": "bibtex", "limit": "100", "start": "0"}),
                ("/users/1/collections/COLL1234/items", "api-key", {"format": "bibtex", "limit": "100", "start": "100"}),
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
