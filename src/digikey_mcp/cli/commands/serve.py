"""serve: start the MCP server."""

from __future__ import annotations

import argparse
import os
from typing import Any

from ..common import Context, UsageError
from ..parsing import command, examples


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "serve",
        "Start the MCP server on stdio, for Claude Code and other MCP clients. It exposes the BOM, "
        "cart and DigiKey search tools. Running digikey-mcp with no arguments and a piped stdin "
        "does the same, so existing MCP client registrations keep working.",
        examples(
            ("serve", "run the server on stdio"),
            ("serve --sandbox", "use sandbox-api.digikey.com instead of production"),
        ),
    )
    p.add_argument("--sandbox", action="store_true", help="use the DigiKey sandbox (sets DIGIKEY_SANDBOX=true)")
    p.set_defaults(func=run)
    return p


def start_server() -> None:
    """Run the FastMCP server until stdin closes (imported late: it pulls in fastmcp)."""
    from ... import server

    server.main()


def run(args: argparse.Namespace, ctx: Context) -> int:
    if ctx.cache_dir is not None:
        raise UsageError("serve does not take --cache-dir", hint="the server keeps its state under XDG_CACHE_HOME")
    if args.sandbox:
        os.environ["DIGIKEY_SANDBOX"] = "true"
    if ctx.quiet:
        os.environ["DIGIKEY_MCP_QUIET"] = "1"
    start_server()
    return 0
