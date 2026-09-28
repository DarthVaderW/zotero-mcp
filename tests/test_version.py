"""Check the package, lockfile and plugin release versions stay aligned."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VersionSyncTest(unittest.TestCase):
    def test_release_versions_match(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project = pyproject.split("[project]", 1)[1].split("\n[", 1)[0]
        match = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
        self.assertIsNotNone(match)
        version = match.group(1)

        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        package = lock.split('name = "zotero-mcp"', 1)[1].split("[[package]]", 1)[0]
        self.assertIn(f'version = "{version}"', package)

        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(marketplace["plugins"][0]["version"], version)
        for subdir in (".claude-plugin", ".codex-plugin"):
            path = ROOT / "plugins" / "zotero-mcp" / subdir / "plugin.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], version)


if __name__ == "__main__":
    unittest.main(verbosity=2)
