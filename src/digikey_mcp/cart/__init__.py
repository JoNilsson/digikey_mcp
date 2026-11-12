"""Cart handoff: DigiKey FastAdd links, POST form page and quick-order text.

Builds links only; nothing is submitted and no network call is made. The
server wires the tool in with `register(mcp)`.
"""

from .fastadd import (
    CartInputError,
    CartLine,
    UrlChunk,
    build_fastadd_form_html,
    build_fastadd_urls,
    build_quickorder_text,
    normalize_lines,
)
from .tool import cart_links, register

__all__ = [
    "CartInputError",
    "CartLine",
    "UrlChunk",
    "build_fastadd_form_html",
    "build_fastadd_urls",
    "build_quickorder_text",
    "cart_links",
    "normalize_lines",
    "register",
]
