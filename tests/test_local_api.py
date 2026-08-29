#!/usr/bin/env python3
"""No-secret tests for the official Zotero Local API backend."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from zotero_mcp.errors import CommandError
from zotero_mcp import local_api


class ZoteroLocalAPITest(unittest.TestCase):
    def test_credentials_are_partitioned_by_server_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = local_api.LocalCredentialStore(Path(temp_dir) / "credentials.json")
            store.save("server-one", "key-one")
            store.save("server-two", "key-two")
            self.assertEqual(store.get("server-one"), "key-one")
            self.assertEqual(store.get("server-two"), "key-two")
            store.remove("server-one")
            self.assertEqual(store.get("server-one"), "")
            self.assertEqual(store.get("server-two"), "key-two")

    def test_probe_reads_instance_and_zotero_versions(self):
        client = local_api.LocalAPIClient(base_url="http://127.0.0.1:23119/api")
        with mock.patch.object(
            client,
            "_request_once",
            side_effect=[
                (b"Nothing to see here.", {"Zotero-Server-ID": "server-one", "Zotero-API-Version": "3", "Zotero-Schema-Version": "44"}, 200),
                (b"ok", {"X-Zotero-Version": "10.0.1"}, 200),
            ],
        ):
            result = client.probe()
        self.assertEqual(result["backend"], "local_api")
        self.assertEqual(result["server_id"], "server-one")
        self.assertEqual(result["zotero_version"], "10.0.1")

    def test_authorize_persists_only_remembered_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = local_api.LocalCredentialStore(Path(temp_dir) / "credentials.json")
            client = local_api.LocalAPIClient(credential_store=store)
            client.server_id = "server-one"
            with mock.patch.object(
                client,
                "_request_once",
                return_value=(json.dumps({"key": "secret-key", "remember": True}).encode(), {}, 200),
            ) as request:
                self.assertEqual(client.authorize(), "secret-key")
            self.assertEqual(store.get("server-one"), "secret-key")
            headers = request.call_args.kwargs["headers"]
            self.assertEqual(headers["Zotero-Server-ID"], "server-one")
            self.assertNotIn("Zotero-API-Key", headers)

    def test_write_uses_remembered_key_and_server_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = local_api.LocalCredentialStore(Path(temp_dir) / "credentials.json")
            store.save("server-one", "remembered-key")
            client = local_api.LocalAPIClient(credential_store=store)
            client.server_id = "server-one"
            with mock.patch.object(client, "_request_once", return_value=(b"{}", {}, 200)) as request:
                client.request(
                    "/users/0/items",
                    method="POST",
                    data=[],
                    content_type="application/json",
                )
            headers = request.call_args.kwargs["headers"]
            self.assertEqual(headers["Zotero-API-Key"], "remembered-key")
            self.assertEqual(headers["Zotero-Server-ID"], "server-one")

    def test_invalid_remembered_key_reauthorizes_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = local_api.LocalCredentialStore(Path(temp_dir) / "credentials.json")
            store.save("server-one", "stale-key")
            client = local_api.LocalAPIClient(credential_store=store)
            client.server_id = "server-one"
            with (
                mock.patch.object(
                    client,
                    "_request_once",
                    side_effect=[CommandError("unauthorized", 401), (b"{}", {}, 200)],
                ) as request,
                mock.patch.object(client, "authorize", return_value="fresh-key") as authorize,
            ):
                client.request("/users/0/items", method="POST", data=[])
            authorize.assert_called_once_with()
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args.kwargs["headers"]["Zotero-API-Key"], "fresh-key")

    def test_create_attachment_runs_official_three_phase_upload(self):
        client = local_api.LocalAPIClient()
        client.server_id = "server-one"
        client._api_key = "key-one"
        authorization = {
            "url": "http://127.0.0.1:23119/api/local/uploads/upload-one",
            "uploadKey": "upload-one",
            "contentType": "application/octet-stream",
            "prefix": "",
            "suffix": "",
        }
        with (
            mock.patch.object(
                client,
                "create_objects",
                return_value={"successful": {"0": {"key": "ATT12345"}}},
            ),
            mock.patch.object(
                client,
                "request",
                side_effect=[
                    (json.dumps(authorization).encode(), {}, 200),
                    (b"", {}, 204),
                ],
            ) as request,
            mock.patch.object(client, "_request_once", return_value=(b"", {}, 201)) as upload,
        ):
            key = client.create_attachment(
                "PARENT12",
                filename="paper.txt",
                content_type="text/plain",
                title="Paper",
                data=b"hello",
            )
        self.assertEqual(key, "ATT12345")
        self.assertEqual(request.call_count, 2)
        upload.assert_called_once()
        self.assertEqual(upload.call_args.kwargs["data"], b"hello")

    def test_delete_item_is_recoverable_trash_patch(self):
        client = local_api.LocalAPIClient()
        with mock.patch.object(client, "patch_item") as patch_item:
            client.delete_item("ABC12345")
        patch_item.assert_called_once_with("ABC12345", {"deleted": True})

    def test_get_all_json_paginates_and_honors_max_items(self):
        client = local_api.LocalAPIClient()
        first_page = [{"key": f"ITEM{i:04d}"} for i in range(100)]
        second_page = [{"key": f"ITEM{i:04d}"} for i in range(100, 120)]
        with mock.patch.object(
            client,
            "get_json",
            side_effect=[
                (first_page, {"Total-Results": "150"}),
                (second_page, {"Total-Results": "150"}),
            ],
        ) as get_json:
            items = client.get_all_json("/users/0/items", max_items=120)
        self.assertEqual(len(items), 120)
        self.assertEqual(get_json.call_args_list[0].kwargs["params"]["start"], "0")
        self.assertEqual(get_json.call_args_list[0].kwargs["params"]["limit"], "100")
        self.assertEqual(get_json.call_args_list[1].kwargs["params"]["start"], "100")
        self.assertEqual(get_json.call_args_list[1].kwargs["params"]["limit"], "20")


if __name__ == "__main__":
    unittest.main(verbosity=2)
