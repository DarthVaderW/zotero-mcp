from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]


async def list_tools(url: str) -> list[str]:
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [tool.name for tool in tools.tools]


def wait_for_server(url: str, timeout: float = 10.0) -> list[str]:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return asyncio.run(list_tools(url))
        except Exception as error:  # noqa: BLE001 - smoke test reports final failure.
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"HTTP MCP server did not become ready: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=16817)
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--expect-tool", action="append", default=[])
    args = parser.parse_args()

    command = [
        sys.executable,
        "-m",
        "zotero_mcp.server",
        "--transport",
        "streamable-http",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--path",
        args.path,
    ]
    proc = subprocess.Popen(command, cwd=str(ROOT), env=os.environ.copy())
    try:
        url = f"http://{args.host}:{args.port}{args.path}"
        names = wait_for_server(url)
        print(f"tools: {len(names)}")
        for name in names:
            print(f"- {name}")
        missing = sorted(set(args.expect_tool) - set(names))
        if missing:
            print("missing expected tools:")
            for name in missing:
                print(f"- {name}")
            raise SystemExit(1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    main()
