#!/usr/bin/env python3
"""No-secret tests for Zotero basic workflows."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import basic_ops, server


class ZoteroBasicTest(unittest.TestCase):
    def test_search_operation_returns_json(self):
        fake_items = [{"key": "ABC12345", "title": "Result"}]

        with (
            mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
            mock.patch.object(
                basic_ops, "db_search", return_value=fake_items
            ) as db_search,
        ):
            result = server.zotero_search_items("needle", limit=3)

        db_search.assert_called_once_with("needle", limit=3)
        self.assertEqual(result, {"total": 1, "items": fake_items})


    def test_delete_items_returns_structured_result(self):
        item = {"key": "ABC12345", "title": "A title"}

        with (
            mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
            mock.patch.object(basic_ops, "db_get_item", return_value=item),
            mock.patch.object(
                basic_ops,
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
            basic_ops,
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
                mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
                mock.patch.object(
                    basic_ops,
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
                mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
                mock.patch.object(
                    basic_ops,
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
            basic_ops, "ensure_local_api", return_value=None
        ) as ensure_api:
            with self.assertRaisesRegex(RuntimeError, "max_chars"):
                basic_ops.op_attachment_text("ATT12345", max_chars=0)
        ensure_api.assert_not_called()


    def test_attachment_text_rejects_unexpected_local_api_response(self):
        with (
            mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
            mock.patch.object(
                basic_ops, "db_get_attachment_file", return_value="not-a-dict"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected attachment response"):
                basic_ops.op_attachment_text("ATT12345")


    def test_attachment_text_warns_for_binary_without_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7")

            with (
                mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
                mock.patch.object(
                    basic_ops,
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
                mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
                mock.patch.object(
                    basic_ops,
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


    def test_attach_snapshot_operation_uses_local_api(self):
        with (
            mock.patch.object(basic_ops, "ensure_local_api", return_value=None),
            mock.patch.object(
                basic_ops, "db_add_snapshot", return_value="SNAP1234"
            ) as add_snapshot,
        ):
            result = basic_ops.op_attach_snapshot(
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



if __name__ == "__main__":
    unittest.main(verbosity=2)
