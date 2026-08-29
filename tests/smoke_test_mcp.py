from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


async def run(expect_tool: list[str]) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "zotero_mcp.server"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print(f"tools: {len(names)}")
            for name in names:
                print(f"- {name}")
            missing = sorted(set(expect_tool) - set(names))
            if missing:
                print("missing expected tools:")
                for name in missing:
                    print(f"- {name}")
                return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-tool", action="append", default=[])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.expect_tool)))


if __name__ == "__main__":
    main()
