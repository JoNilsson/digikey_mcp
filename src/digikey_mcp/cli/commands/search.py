"""search: keyword search with trimmed results."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .. import common, render
from ..common import CliError, Context, UsageError, out
from ..parsing import command, examples
from .resolve import positive_int


def call_tool(name: str, *args: Any, **kwargs: Any) -> Any:
    """Run the MCP tool function of the same name so the CLI and MCP give identical results."""
    from fastmcp.exceptions import ToolError

    from ... import server

    server.set_client(common.get_client())
    tool = getattr(server, name)
    fn = getattr(tool, "fn", tool)  # older fastmcp wraps the function in a tool object
    try:
        return fn(*args, **kwargs)
    except ToolError as exc:
        raise CliError(str(exc)) from None
    finally:
        server.set_client(None)


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "search",
        "Search DigiKey by keyword, MPN or DigiKey part number and show trimmed results: part numbers, "
        "manufacturer, stock and price. Use 'part' for one product in detail. Needs DigiKey credentials.",
        examples(
            ("search 'RC0603FR-0710KL'", "find a part by MPN"),
            ("search 10k 0603 resistor --in-stock --limit 5", "five in-stock matches"),
            ("search LM358 --json", "trimmed results as JSON"),
        ),
    )
    p.add_argument("keywords", nargs="+", metavar="KEYWORD", help="search words, an MPN or a DigiKey part number")
    p.add_argument("--limit", type=positive_int, default=10, metavar="N", help="results to return, 1 to 50")
    p.add_argument("--in-stock", action="store_true", help="only parts DigiKey has in stock")
    p.add_argument("--json", action="store_true", help="print the trimmed result as JSON on stdout")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace, ctx: Context) -> int:
    if args.limit > 50:
        raise UsageError("--limit must be between 1 and 50")
    data = call_tool(
        "keyword_search", " ".join(args.keywords), limit=args.limit,
        search_options=["InStock"] if args.in_stock else None, include_parameters=False,
    )
    out(json.dumps(data, indent=2) if args.json else render.search_text(data, ctx.style).rstrip("\n"))
    return 0
