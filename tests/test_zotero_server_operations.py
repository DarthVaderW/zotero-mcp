#!/usr/bin/env python3
"""No-secret tests for the Zotero MCP structured operation surface."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
import urllib.error
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import operations, server, web_api


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
