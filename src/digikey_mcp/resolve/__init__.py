"""BOM resolver: BomLine records to exact DigiKey part numbers, order quantities and packaging.

Nothing is submitted and the network is touched only through DigiKeyClient.
The server wires the tools in with `register(mcp)`.
"""

from .models import ResolvedBom, ResolvedLine, ResolveError
from .prefs import Preferences, load_preferences
from .report import to_csv, to_markdown
from .session import apply_decisions, cart_items, load_bom, quota_status, resolve_bom
from .store import QuotaExhausted
from .tools import register

__all__ = [
    "Preferences",
    "QuotaExhausted",
    "ResolveError",
    "ResolvedBom",
    "ResolvedLine",
    "apply_decisions",
    "cart_items",
    "load_bom",
    "load_preferences",
    "quota_status",
    "register",
    "resolve_bom",
    "to_csv",
    "to_markdown",
]
