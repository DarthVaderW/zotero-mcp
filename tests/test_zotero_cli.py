#!/usr/bin/env python3
"""Lightweight no-secret tests for scripts/zotero.py.

Run:
  python3 tests/test_zotero_cli.py
"""

import importlib.util
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zotero.py"


def load_module():
    spec = importlib.util.spec_from_file_location("zotero_cli", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ZoteroCLITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
