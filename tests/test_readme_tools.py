"""Keep the documented Zotero MCP tool names in sync with registration."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import unittest

from zotero_mcp import server


ROOT = Path(__file__).resolve().parents[1]
TOOL_RE = re.compile(r"`(zotero_[a-z_]+)`")
SECTION_RE = re.compile(r"^## Primary MCP workflows\n(.*?)(?=^## |\Z)",
                        re.DOTALL | re.MULTILINE)


class ReadmeToolsTest(unittest.TestCase):
    def test_documented_tools_equal_registered_tools(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        match = SECTION_RE.search(readme)
        self.assertIsNotNone(match, "README primary workflows section is missing")
        documented = set(TOOL_RE.findall(match.group(1)))
        self.assertTrue(documented)
        registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
        self.assertSetEqual(documented, registered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
