"""Shared exceptions for Zotero MCP operations and CLI."""

from __future__ import annotations


class CommandError(RuntimeError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code
