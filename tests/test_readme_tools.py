#!/usr/bin/env python3
"""Guard against README/MCP tool-surface drift.

The README's "## MCP Tools" section documents every registered MCP tool by
name. This test fails if a tool is registered in zotero_mcp/server.py without
being documented there, or if the README names a tool that no longer exists.

Run:
  uv run python tests/test_readme_tools.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zotero_mcp import server

TOOL_NAME_RE = re.compile(r"`(zotero_[a-z_]+)`")
SECTION_RE = re.compile(r"^## MCP Tools\n(.*?)^## ", re.DOTALL | re.MULTILINE)


def _documented_tool_names() -> set[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = SECTION_RE.search(readme)
    if not match:
        raise AssertionError("README.md has no '## MCP Tools' section")
    return set(TOOL_NAME_RE.findall(match.group(1)))


def _registered_tool_names() -> set[str]:
    tools = asyncio.run(server.mcp.list_tools())
    return {tool.name for tool in tools}


class ReadmeToolsTest(unittest.TestCase):
    def test_readme_documents_every_registered_tool(self):
        registered = _registered_tool_names()
        documented = _documented_tool_names()

        missing_from_readme = registered - documented
        stale_in_readme = documented - registered

        self.assertEqual(
            missing_from_readme,
            set(),
            "Tools registered in server.py but missing from the README "
            f"'## MCP Tools' section: {sorted(missing_from_readme)}",
        )
        self.assertEqual(
            stale_in_readme,
            set(),
            "Tool names in the README '## MCP Tools' section that are not "
            f"registered in server.py: {sorted(stale_in_readme)}",
        )

    def test_readme_mcp_tools_section_is_nonempty(self):
        self.assertGreater(len(_documented_tool_names()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
