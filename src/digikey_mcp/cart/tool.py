"""The `cart_links` MCP tool: builds DigiKey cart links, never submits them."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Literal

from fastmcp.exceptions import ToolError

from .fastadd import (
    CartInputError,
    build_fastadd_form_html,
    build_fastadd_urls,
    build_quickorder_text,
    normalize_lines,
)

HTML_SUFFIXES = (".html", ".htm")


def _write_html(html_text: str, target: str) -> str:
    path = Path(target).expanduser()
    if path.suffix.lower() not in HTML_SUFFIXES:
        raise CartInputError("write_html_to must end in .html or .htm")
    if not path.parent.is_dir():
        raise CartInputError(f"write_html_to: directory {path.parent} does not exist")
    if path.is_dir():
        raise CartInputError("write_html_to is a directory, give a file path")
    path.write_text(html_text, encoding="utf-8")
    return str(path)


def cart_links(
    items: list[dict[str, Any]],
    output: Literal["urls", "html", "text"] = "urls",
    write_html_to: str | None = None,
    newcart: bool = True,
) -> dict[str, Any]:
    """Build links that put a resolved BOM into the user's DigiKey cart.

    Only builds links or text: nothing is submitted, no network call is made.
    The user opens the links in a signed-in browser, reviews the cart and pays.

    Args:
        items: Lines in BOM order. Each has digikey_part_number, quantity (a
            positive integer) and optionally customer_reference.
        output: "urls" returns FastAdd GET URLs chunked under 1700 characters
            (the first empties the cart, the rest add to it); "html" returns a
            page with POST forms of up to 400 parts each, for large BOMs;
            "text" returns "qty, part number, reference" lines for DigiKey's
            Quick Order box (importer format unconfirmed).
        write_html_to: With output="html", write the page to this .html path
            instead of returning it. The directory must already exist.
        newcart: True (default) makes the first link or form empty the DigiKey cart;
            False adds to the cart that is already open.
    """
    if output not in ("urls", "html", "text"):
        raise CartInputError("output must be 'urls', 'html' or 'text'")
    if write_html_to is not None and output != "html":
        raise CartInputError("write_html_to only applies to output='html'")
    line_count = len(normalize_lines(items))
    if output == "urls":
        chunks = build_fastadd_urls(items, newcart=newcart)
        return {
            "output": "urls",
            "line_count": line_count,
            "url_count": len(chunks),
            "urls": [c.as_dict() for c in chunks],
        }
    if output == "html":
        page = build_fastadd_form_html(items, newcart=newcart)
        result: dict[str, Any] = {
            "output": "html",
            "line_count": line_count,
            "form_count": page.count("<form "),
        }
        if write_html_to is not None:
            result["written_to"] = _write_html(page, write_html_to)
        else:
            result["html"] = page
        return result
    return {"output": "text", "line_count": line_count, "text": build_quickorder_text(items)}


def register(mcp: Any) -> None:
    """Add the cart tools to a FastMCP server."""

    @functools.wraps(cart_links)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return cart_links(*args, **kwargs)
        except ToolError:
            raise
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)) from None

    mcp.tool(wrapper)
