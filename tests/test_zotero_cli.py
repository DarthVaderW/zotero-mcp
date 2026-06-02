#!/usr/bin/env python3
"""Lightweight no-secret tests for the packaged Zotero CLI.

Run:
  python3 tests/test_zotero_cli.py
"""

import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module():
    sys.path.insert(0, str(ROOT))
    return __import__("zotero_mcp.cli", fromlist=["cli"])


class ZoteroCLITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

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

    def test_arxiv_id_extract(self):
        self.assertEqual(self.mod._extract_arxiv_id("2401.01234"), "2401.01234")
        self.assertEqual(self.mod._extract_arxiv_id("https://arxiv.org/abs/2401.01234v2"), "2401.01234v2")

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
        original = self.mod.db_create_item

        def fake_create_item(payload):
            captured.update(payload)
            return {"success": True, "key": "ABC12345"}

        self.mod.db_create_item = fake_create_item
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
            self.mod.db_create_item = original

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
