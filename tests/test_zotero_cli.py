#!/usr/bin/env python3
"""Lightweight no-secret tests for the packaged Zotero CLI.

Run:
  python3 tests/test_zotero_cli.py
"""

import contextlib
import io
import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(module_name):
    sys.path.insert(0, str(ROOT))
    return __import__(module_name, fromlist=[module_name.rsplit(".", 1)[-1]])


class ZoteroCLITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module("zotero_mcp.operations")
        cls.local_ops = load_module("zotero_mcp.local_ops")
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
        self.assertIn("fetch-pdfs", proc.stdout)
        self.assertIn("debug-bridge", proc.stdout)

    def test_help_fetch_pdfs(self):
        proc = self.run_cli("fetch-pdfs", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--key", proc.stdout)
        self.assertIn("--dry-run", proc.stdout)
        self.assertIn("--link-only", proc.stdout)

    def test_help_attach_snapshot(self):
        proc = self.run_cli("attach-snapshot", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--url", proc.stdout)
        self.assertIn("--title", proc.stdout)

    def test_help_arxiv_has_html_toggle(self):
        proc = self.run_cli("arxiv", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--no-html", proc.stdout)
        self.assertIn("--force", proc.stdout)

    def test_help_search_arxiv(self):
        proc = self.run_cli("search-arxiv", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--limit", proc.stdout)

    def test_help_capture_arxiv(self):
        proc = self.run_cli("capture-arxiv", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--confirmed-arxiv-id", proc.stdout)
        self.assertIn("--no-html", proc.stdout)

    def test_capture_arxiv_empty_paper_exits_cleanly(self):
        proc = self.run_cli("capture-arxiv", "")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.stderr.strip(), "paper is required")

    def test_capture_arxiv_empty_paper_exits_cleanly_json(self):
        proc = self.run_cli("--json", "capture-arxiv", "")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", proc.stderr)
        payload = json.loads(proc.stderr)
        self.assertEqual(payload, {"error": "paper is required", "code": 0})

    def test_search_arxiv_empty_query_exits_cleanly(self):
        proc = self.run_cli("search-arxiv", "")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.stderr.strip(), "query is required")

    def test_search_arxiv_empty_query_exits_cleanly_json(self):
        proc = self.run_cli("--json", "search-arxiv", "")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", proc.stderr)
        payload = json.loads(proc.stderr)
        self.assertEqual(payload, {"error": "query is required", "code": 0})

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
                return_value={"text": "readable text", "warnings": [], "source": "zotero-ft-cache"},
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
                return_value={"text": "readable text", "warnings": [], "source": "attachment-file"},
            ) as attachment_text,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.cli.cmd_attachment_text(args)

        attachment_text.assert_called_once_with("ATT12345", max_chars=123, prefer_cache=False)

    def test_attachment_text_cli_exits_nonzero_without_text(self):
        args = SimpleNamespace(key="ATT12345", max_chars=20000, no_cache=False)
        stderr = io.StringIO()

        with (
            mock.patch.object(
                self.cli,
                "op_attachment_text",
                return_value={"text": "", "warnings": ["PDF attachment has no Zotero full-text cache"], "source": None},
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
        self.assertEqual(self.mod._extract_arxiv_id("https://arxiv.org/abs/2401.01234v2"), "2401.01234v2")
        self.assertEqual(self.mod._extract_arxiv_id("https://arxiv.org/html/2401.01234v2"), "2401.01234v2")

    def test_validators(self):
        self.assertTrue(self.mod.validate_doi("10.1000/abc"))
        self.assertFalse(self.mod.validate_doi("not-a-doi"))
        self.assertTrue(self.mod.validate_item_key("A1B2C3D4"))
        self.assertFalse(self.mod.validate_item_key("short"))
        self.assertTrue(self.mod.validate_isbn("978-0306406157"))
        self.assertFalse(self.mod.validate_isbn("abc"))

    def test_build_pdf_filename(self):
        d = {"creators": [{"lastName": "smith"}], "date": "2021-10-01"}
        name = self.mod._make_pdf_filename(d, "ABC12345")
        self.assertEqual(name, "Smith2021_ABC12345.pdf")

    def test_create_item_preserves_zotero_fields(self):
        captured = {}
        original = self.local_ops.db_create_item

        def fake_create_item(payload):
            captured.update(payload)
            return {"success": True, "key": "ABC12345"}

        self.local_ops.db_create_item = fake_create_item
        try:
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
        finally:
            self.local_ops.db_create_item = original

        self.assertEqual(key, "ABC12345")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
