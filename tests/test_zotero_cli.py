#!/usr/bin/env python3
"""Lightweight no-secret tests for the packaged Zotero CLI.

Run:
  python3 tests/test_zotero_cli.py
"""

import contextlib
import io
import pathlib
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(module_name):
    sys.path.insert(0, str(ROOT))
    return __import__(module_name, fromlist=[module_name.rsplit(".", 1)[-1]])


class ZoteroCLITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module("zotero_mcp.operations")
        cls.local_library = load_module("zotero_mcp.local_library")
        cls.validators = load_module("zotero_mcp.validators")
        cls.cli = load_module("zotero_mcp.cli")

    def run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, "-m", "zotero_mcp.cli", *args],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return proc

    def test_help_root(self):
        proc = self.run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("official Local API", proc.stdout)
        self.assertIn("import-doi", proc.stdout)
        for retired_command in ("fetch-pdfs", "add-doi", "batch-add"):
            self.assertNotIn(retired_command, proc.stdout)

    def test_help_attach_snapshot(self):
        proc = self.run_cli("attach-snapshot", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--url", proc.stdout)
        self.assertIn("--title", proc.stdout)

    def test_help_search_arxiv(self):
        proc = self.run_cli("search-arxiv", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--limit", proc.stdout)

    def test_help_capture_arxiv(self):
        proc = self.run_cli("capture-arxiv", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--confirmed-arxiv-id", proc.stdout)
        self.assertIn("--no-html", proc.stdout)

    def test_help_import_doi(self):
        proc = self.run_cli("import-doi", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--no-pdf", proc.stdout)
        self.assertIn("--collection", proc.stdout)

    def test_help_attach_arxiv_sidecars(self):
        proc = self.run_cli("attach-arxiv-sidecars", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--arxiv", proc.stdout)
        self.assertIn("--no-html", proc.stdout)

    def test_help_attachment_text(self):
        proc = self.run_cli("attachment-text", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--max-chars", proc.stdout)
        self.assertIn("--no-cache", proc.stdout)

    def test_attachment_text_cli_prints_text(self):
        args = SimpleNamespace(key="ATT12345", max_chars=20000, no_cache=False)
        stdout = io.StringIO()

        with (
            mock.patch.object(
                self.cli,
                "op_attachment_text",
                return_value={
                    "text": "readable text",
                    "warnings": [],
                    "source": "zotero-ft-cache",
                },
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.cli.cmd_attachment_text(args)

        self.assertEqual(stdout.getvalue(), "readable text\n")

    def test_attachment_text_cli_no_cache_flag(self):
        args = SimpleNamespace(key="ATT12345", max_chars=123, no_cache=True)

        with (
            mock.patch.object(
                self.cli,
                "op_attachment_text",
                return_value={
                    "text": "readable text",
                    "warnings": [],
                    "source": "attachment-file",
                },
            ) as attachment_text,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.cli.cmd_attachment_text(args)

        attachment_text.assert_called_once_with(
            "ATT12345", max_chars=123, prefer_cache=False
        )

    def test_attachment_text_cli_exits_nonzero_without_text(self):
        args = SimpleNamespace(key="ATT12345", max_chars=20000, no_cache=False)
        stderr = io.StringIO()

        with (
            mock.patch.object(
                self.cli,
                "op_attachment_text",
                return_value={
                    "text": "",
                    "warnings": ["PDF attachment has no Zotero full-text cache"],
                    "source": None,
                },
            ),
            contextlib.redirect_stderr(stderr),
        ):
            with self.assertRaises(SystemExit) as caught:
                self.cli.cmd_attachment_text(args)

        self.assertEqual(caught.exception.code, 1)
        self.assertIn("Warning: PDF attachment", stderr.getvalue())
        self.assertIn("No readable attachment text found.", stderr.getvalue())

    def test_arxiv_id_extract(self):
        self.assertEqual(self.mod._extract_arxiv_id("2401.01234"), "2401.01234")
        self.assertEqual(
            self.mod._extract_arxiv_id("https://arxiv.org/abs/2401.01234v2"),
            "2401.01234v2",
        )
        self.assertEqual(
            self.mod._extract_arxiv_id("https://arxiv.org/html/2401.01234v2"),
            "2401.01234v2",
        )

    def test_validators(self):
        self.assertEqual(self.validators.validate_id_type(" DOI "), "doi")
        with self.assertRaisesRegex(ValueError, "id_type"):
            self.validators.validate_id_type("unknown")
        self.assertTrue(self.validators.validate_doi("10.1000/abc"))
        self.assertFalse(self.validators.validate_doi("not-a-doi"))
        self.assertTrue(self.validators.validate_item_key("A1B2C3D4"))
        self.assertFalse(self.validators.validate_item_key("short"))
        self.assertTrue(self.validators.validate_isbn("978-0306406157"))
        self.assertFalse(self.validators.validate_isbn("abc"))

    def test_create_item_preserves_zotero_fields(self):
        client = load_module("zotero_mcp.local_api").LocalAPIClient()
        with mock.patch.object(client, "create_objects", return_value={"successful": {"0": {"key": "ABC12345"}}}) as create, \
                mock.patch.object(self.local_library, "get_local_client", return_value=client):
            key = self.mod.create_item(
                {
                    "itemType": "book",
                    "title": "Payload test",
                    "abstract": "Alias abstract",
                    "DOI": "10.1000/payload",
                    "publicationTitle": "Payload Journal",
                    "extra_fields": {"volume": "42"},
                }
            )
        captured = create.call_args.args[1][0]
        self.assertEqual(key, "ABC12345")
        self.assertEqual(create.call_args.args[0], f"{client.library_prefix}/items")
        self.assertEqual(captured["itemType"], "book")
        self.assertEqual(captured["title"], "Payload test")
        self.assertEqual(captured["abstractNote"], "Alias abstract")
        self.assertEqual(captured["DOI"], "10.1000/payload")
        self.assertEqual(captured["publicationTitle"], "Payload Journal")
        self.assertEqual(captured["volume"], "42")
        self.assertNotIn("abstract", captured)

    def test_create_item_requires_item_type(self):
        with self.assertRaisesRegex(RuntimeError, "itemType is required"):
            self.mod.create_item({"title": "Missing type"})

    def test_cli_reports_value_error_without_traceback(self):
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["zotero-mcp", "ping"]), \
                mock.patch.object(self.cli, "dispatch", side_effect=ValueError("invalid input")), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit_info:
                self.cli.main()
        self.assertEqual(exit_info.exception.code, 1)
        self.assertIn("invalid input", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
