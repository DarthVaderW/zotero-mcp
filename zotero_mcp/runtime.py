"""In-process command runtime used by the Zotero MCP server."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from threading import RLock
from typing import Any

from zotero_mcp import cli


# The CLI command layer still writes to process-global stdout/stderr and toggles
# a process-global JSON mode. Serialize MCP calls until command handlers return
# structured objects directly.
_RUNTIME_LOCK = RLock()


def _exit_code(exc: SystemExit) -> int:
    if isinstance(exc.code, int):
        return exc.code
    if exc.code is None:
        return 0
    return 1


def _raise_command_error(stdout: str, stderr: str, code: int) -> None:
    message = stderr or stdout or f"zotero command exited with {code}"
    raise RuntimeError(message)


def run_zotero(args: list[str], expect_json: bool = True) -> dict[str, Any]:
    """Run a Zotero CLI command in-process and return the MCP-facing result."""
    with _RUNTIME_LOCK:
        stdout_io = io.StringIO()
        stderr_io = io.StringIO()
        parser = cli.build_parser()
        old_json_mode = cli._json_mode

        try:
            with redirect_stdout(stdout_io), redirect_stderr(stderr_io):
                try:
                    parsed = parser.parse_args(["--json", *args])
                    if not parsed.command:
                        parser.print_help()
                        raise SystemExit(1)
                    cli._set_json_mode(True)
                    cli.dispatch(parsed)
                except SystemExit as exc:
                    code = _exit_code(exc)
                    if code != 0:
                        _raise_command_error(
                            stdout_io.getvalue().strip(),
                            stderr_io.getvalue().strip(),
                            code,
                        )
        except RuntimeError:
            raise
        finally:
            cli._set_json_mode(old_json_mode)

        stdout = stdout_io.getvalue().strip()
        stderr = stderr_io.getvalue().strip()

    if expect_json:
        try:
            return json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            return {"stdout": stdout}
    return {"stdout": stdout, "stderr": stderr}
