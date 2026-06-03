#!/usr/bin/env python3
"""No-secret tests for the Zotero MCP in-process runtime."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import runtime, server


class ZoteroServerRuntimeTest(unittest.TestCase):
    def test_search_runs_in_process_and_returns_json(self):
        fake_items = [{"key": "ABC12345", "title": "Result"}]

        with (
            mock.patch.object(runtime.cli, "ensure_debug_bridge", return_value=None),
            mock.patch.object(runtime.cli, "db_search", return_value=fake_items) as db_search,
        ):
            result = server.run_zotero(["search", "needle", "--limit", "3"])

        db_search.assert_called_once_with("needle", limit=3)
        self.assertEqual(result, {"total": 1, "items": fake_items})

    def test_text_commands_keep_stdout_shape(self):
        item = {"key": "ABC12345", "title": "A title"}

        with (
            mock.patch.object(runtime.cli, "require_debug_bridge", return_value=None),
            mock.patch.object(runtime.cli, "db_get_item", return_value=item),
            mock.patch.object(runtime.cli, "db_delete_item", return_value={"success": True, "mode": "trash"}),
        ):
            result = server.run_zotero(
                ["delete", "ABC12345", "--yes", "--trash"],
                expect_json=False,
            )

        self.assertIn("OK: A title [ABC12345] (trash)", result["stdout"])
        self.assertEqual(result["stderr"], "")

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
            server.run_zotero(["get", "bad-key"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
