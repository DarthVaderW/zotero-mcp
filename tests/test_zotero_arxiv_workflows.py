#!/usr/bin/env python3
"""No-secret tests for Zotero arxiv workflows."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import arxiv, arxiv_metadata, operations


class ZoteroArxivTest(unittest.TestCase):
    def test_find_arxiv_html_url_parses_abs_page_link(self):
        page = b'<a href="/html/2401.01234v1">HTML (experimental)</a>'

        with mock.patch.object(arxiv_metadata, "_read_url", return_value=page):
            result = arxiv_metadata._find_arxiv_html_url("2401.01234")

        self.assertEqual(result, "https://arxiv.org/html/2401.01234v1")


    def test_fetch_arxiv_metadata_falls_back_on_invalid_xml(self):
        fallback = {
            "title": "Fallback",
            "extra_fields": {"archiveLocation": "2401.01234"},
        }

        with (
            mock.patch.object(arxiv_metadata, "_read_url", return_value=b"<not-xml"),
            mock.patch.object(
                arxiv_metadata, "_fetch_arxiv_metadata_from_abs_page", return_value=fallback
            ) as from_abs,
        ):
            result = arxiv_metadata._fetch_arxiv_metadata("2401.01234")

        from_abs.assert_called_once_with("2401.01234")
        self.assertEqual(result, fallback)


    def test_arxiv_query_value_escape(self):
        self.assertEqual(
            arxiv_metadata._escape_arxiv_query_value('a "quoted" title'), r"a \"quoted\" title"
        )


    def test_arxiv_title_score_uses_metadata_similarity(self):
        self.assertGreater(
            arxiv_metadata._title_score("Retargeting Matters", "Retargeting Matters"), 0.9
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
                arxiv_metadata, "_fetch_arxiv_metadata_via_translator", return_value=dict(meta)
            ),
            mock.patch.object(
                arxiv_metadata, "_fetch_arxiv_metadata_from_abs_page", return_value=dict(meta)
            ),
            mock.patch.object(
                arxiv_metadata,
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
            result = arxiv.import_arxiv("2401.01234")

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
                arxiv_metadata, "_fetch_arxiv_metadata_via_translator", return_value=dict(meta)
            ),
            mock.patch.object(
                arxiv_metadata, "_fetch_arxiv_metadata_from_abs_page", return_value=dict(meta)
            ),
            mock.patch.object(arxiv_metadata, "_find_arxiv_html_url", return_value=None),
            mock.patch.object(arxiv, "create_item", return_value="ABC12345"),
            mock.patch.object(arxiv, "db_add_snapshot", return_value="SNAP1234"),
            mock.patch.object(arxiv, "_download_pdf", return_value=False),
            mock.patch.object(arxiv, "attach_pdf_from_file") as attach_pdf,
        ):
            result = arxiv.import_arxiv("2401.01234")

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
            mock.patch.object(arxiv_metadata, "_find_arxiv_html_url") as find_html,
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
                arxiv_metadata,
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
                arxiv_metadata,
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



if __name__ == "__main__":
    unittest.main(verbosity=2)
