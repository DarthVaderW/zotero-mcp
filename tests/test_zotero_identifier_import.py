#!/usr/bin/env python3
"""No-secret tests for Zotero identifier workflows."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import intake_ops


class ZoteroIdentifierTest(unittest.TestCase):
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
            mock.patch.object(intake_ops, "ensure_local_api", return_value=None),
            mock.patch.object(
                intake_ops, "translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                intake_ops, "db_find_item_by_identifier", return_value=[]
            ),
            mock.patch.object(
                intake_ops, "create_item", return_value="NEW12345"
            ) as create_item,
            mock.patch.object(
                intake_ops,
                "db_add_item_to_collection",
                return_value={"collectionKey": "COLL1234"},
            ),
            mock.patch.object(intake_ops, "db_get_children", return_value=[]),
            mock.patch.object(intake_ops, "find_pdf_source", return_value=None),
        ):
            result = intake_ops.op_import_identifier(
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
            mock.patch.object(intake_ops, "ensure_local_api", return_value=None),
            mock.patch.object(
                intake_ops, "translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                intake_ops, "db_find_item_by_identifier", return_value=existing
            ),
            mock.patch.object(intake_ops, "create_item") as create_item,
            mock.patch.object(intake_ops, "db_get_children", return_value=[]),
            mock.patch.object(intake_ops, "find_pdf_source", return_value=None),
        ):
            result = intake_ops.op_import_identifier("10.1234/example")

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
            mock.patch.object(intake_ops, "ensure_local_api", return_value=None),
            mock.patch.object(
                intake_ops, "translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                intake_ops, "db_find_item_by_identifier", return_value=[]
            ),
            mock.patch.object(intake_ops, "create_item", return_value="NEW12345"),
            mock.patch.object(intake_ops, "db_get_children", return_value=[]),
            mock.patch.object(
                intake_ops,
                "find_pdf_source",
                return_value=(
                    "https://example.com/paper.pdf",
                    "https://example.com/source",
                    "unpaywall",
                ),
            ),
            mock.patch.object(
                intake_ops, "download_pdf", return_value=True
            ) as download_pdf,
            mock.patch.object(
                intake_ops, "attach_pdf_from_file", return_value="ATT12345"
            ) as attach_pdf,
        ):
            result = intake_ops.op_import_identifier("10.1234/oa")

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
            mock.patch.object(intake_ops, "ensure_local_api", return_value=None),
            mock.patch.object(
                intake_ops, "translate_identifier", return_value=[translated]
            ),
            mock.patch.object(
                intake_ops, "db_find_item_by_identifier", return_value=[]
            ),
            mock.patch.object(intake_ops, "create_item", return_value="NEW12345"),
            mock.patch.object(intake_ops, "db_get_children", return_value=[]),
            mock.patch.object(
                intake_ops,
                "find_pdf_source",
                return_value=(
                    "https://example.com/paper.pdf",
                    "https://example.com/source",
                    "unpaywall",
                ),
            ),
            mock.patch.object(intake_ops, "download_pdf", return_value=True),
            mock.patch.object(
                intake_ops,
                "attach_pdf_from_file",
                side_effect=RuntimeError("attach failed"),
            ),
        ):
            result = intake_ops.op_import_identifier("10.1234/oa")

        self.assertEqual(result["status"], "added")
        self.assertEqual(result["item_key"], "NEW12345")
        self.assertEqual(result["pdfStatus"], "attach_failed")
        self.assertIn("attach failed", result["warnings"][0])



if __name__ == "__main__":
    unittest.main(verbosity=2)
