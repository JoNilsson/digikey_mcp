"""MCP tools of the resolver. They never submit anything and reach DigiKey only through DigiKeyClient."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, Literal

from fastmcp.exceptions import ToolError

from ..client import DigiKeyError
from . import report, session
from .models import ResolveError


def _default_client() -> Any:
    from ..server import get_client  # imported late: server imports this package

    return get_client()


def _guard(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (ResolveError, DigiKeyError, ValueError, OSError) as exc:
            raise ToolError(str(exc)) from None

    return wrapper


def register(mcp: Any, client_factory: Callable[[], Any] | None = None) -> None:
    """Add the BOM resolver tools to a FastMCP server."""
    get_client = client_factory or _default_client

    @_guard
    def resolve_bom(
        source: str | None = None,
        boards: int = 1,
        preferences_path: str | None = None,
        session_id: str | None = None,
        source_is_text: bool = False,
    ) -> dict[str, Any]:
        """Resolve a BOM to exact DigiKey part numbers, order quantities and packaging.

        Per line: a DigiKey part number is looked up directly; MPN with manufacturer
        and MPN alone are searched; generic lines (no MPN) are never auto-picked and
        come back as needs_decision with ranked candidates. Quantity is boards x
        per-board quantity plus overage, rounded to MOQ and order multiples. Stock,
        part status and marketplace sellers are checked; unavailable parts list
        substitutes but are never substituted. Nothing is ordered or submitted.

        Statuses: resolved, ambiguous, needs_decision, unavailable, dnp_skipped,
        error, skipped (decided skip), pending (not reached yet).

        If the DigiKey quota runs out, or the time budget ends, the result has
        complete=false and a `stopped` message; call again with the same session_id
        (and no source) to resume. Calling with an existing session_id and a changed
        source or boards re-resolves the revised BOM and reuses earlier decisions
        where a line's identity (MPN, else value+footprint) is unchanged.

        Args:
            source: Path to a BOM file (csv, xlsx, xml, pdf) or, with source_is_text,
                the BOM text itself. Omit to resume session_id.
            boards: Number of boards to build.
            preferences_path: TOML file with overage, packaging, generic-search,
                quota and cache preferences. Defaults apply when omitted.
            session_id: Names or resumes a resolution session; generated if omitted.
            source_is_text: Treat `source` as pasted BOM text, not a file path.
        """
        result = session.resolve_bom(
            source, boards, preferences_path, session_id, source_is_text, client=get_client()
        )
        return result.to_dict()

    @_guard
    def apply_decisions(session_id: str, decisions: dict[str, str]) -> dict[str, Any]:
        """Decide lines of a session: maps line_id to a DigiKey part number or 'skip'.

        A part number is looked up and verified like any other line (stock, status,
        packaging), so a candidate, a substitute or any part the user names can be
        chosen. Decisions persist with the session and survive BOM revisions.

        Args:
            session_id: The resolution session.
            decisions: {"3": "311-10.0KHRCT-ND", "7": "skip"}
        """
        return session.apply_decisions(session_id, decisions, client=get_client()).to_dict()

    @_guard
    def bom_report(
        session_id: str,
        format: Literal["markdown", "csv"] = "markdown",
        write_to: str | None = None,
    ) -> dict[str, Any]:
        """Report a resolved BOM as a markdown table or CSV.

        Args:
            session_id: The resolution session.
            format: "markdown" or "csv".
            write_to: Write the report to this .md or .csv path (the directory must
                exist) instead of returning it.
        """
        if format not in ("markdown", "csv"):
            raise ValueError("format must be 'markdown' or 'csv'")
        bom = session.load_bom(session_id)
        text = report.to_markdown(bom) if format == "markdown" else report.to_csv(bom)
        out: dict[str, Any] = {"session_id": session_id, "format": format, "counts": bom.counts}
        if write_to is not None:
            suffixes = (".md", ".markdown") if format == "markdown" else (".csv",)
            out["written_to"] = report.write_report(text, write_to, suffixes)
        else:
            out["report"] = text
        return out

    @_guard
    def cart_items_for_session(session_id: str) -> dict[str, Any]:
        """Cart items of a session, ready for cart_links.

        Lists (digikey_part_number, quantity, customer_reference) for lines that are
        resolved or decided and not skipped; the customer reference is the ref-des
        list. Ambiguous, needs_decision, unavailable, error and pending lines are
        listed under `blocking` and left out.
        """
        return session.cart_items_summary(session_id)

    @_guard
    def quota_status(session_id: str | None = None) -> dict[str, Any]:
        """DigiKey calls used and remaining this minute and UTC day (resolver calls only)."""
        return session.quota_status(session_id)

    for tool in (resolve_bom, apply_decisions, bom_report, cart_items_for_session, quota_status):
        mcp.tool(tool)
