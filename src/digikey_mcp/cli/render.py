"""Plain-text views of ingest results, resolved BOMs and search results."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from .common import Style


def clip(text: object, width: int) -> str:
    s = "" if text is None else str(text)
    s = " ".join(s.split())
    return s if len(s) <= width else s[: max(width - 1, 1)] + "~"


def table(headers: Sequence[str], rows: Sequence[Sequence[object]], widths: Sequence[int] | None = None,
          right: Sequence[int] = (), indent: int = 2) -> list[str]:
    """Left-aligned text table; cells are clipped to `widths` (default 30)."""
    caps = list(widths) if widths else [30] * len(headers)
    cells = [[clip(c, caps[i]) for i, c in enumerate(row)] for row in rows]
    sizes = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]

    def fmt(row: Sequence[str]) -> str:
        parts = [(c.rjust(sizes[i]) if i in right else c.ljust(sizes[i])) for i, c in enumerate(row)]
        return (" " * indent + "  ".join(parts)).rstrip()

    return [fmt(list(headers)), fmt(["-" * s for s in sizes])] + [fmt(r) for r in cells]


def money(value: float | None, places: int = 2) -> str:
    return "" if value is None else f"${value:,.{places}f}"


# ---- ingest ---------------------------------------------------------------


def ingest_text(result: Any, source: str, style: Style) -> str:
    out = [style.bold(f"BOM {source}"), f"  format: {result.fmt}"]
    rep = result.mapping_report
    if rep is not None:
        where = []
        if rep.sheet:
            where.append(f"sheet {rep.sheet}")
        if rep.header_row:
            where.append(f"header on row {rep.header_row}")
        if where:
            out.append("  " + ", ".join(where))
    lines = result.lines
    out.append(
        f"  lines: {len(lines)} ({len(result.orderable_lines)} orderable, {len(result.dnp_lines)} DNP, "
        f"{len(result.non_part_lines)} non-part, {sum(1 for ln in lines if ln.generic and ln.orderable)} generic)"
    )
    out += ["", style.bold("Column mapping")]
    if rep is not None and rep.mapped:
        for header, fld in rep.mapped.items():
            tag = "  (from --map)" if header in rep.overridden else ""
            out.append(f"  {clip(header, 36):<36} -> {fld}{tag}")
    else:
        out.append("  (no table columns: the input was read as free text)")
    if rep is not None:
        if rep.unmapped:
            out.append("  not mapped: " + ", ".join(clip(h, 30) for h in rep.unmapped))
        if rep.missing_fields:
            out.append(style.yellow("  no column for: " + ", ".join(rep.missing_fields)))
    if result.unparsed_rows:
        out += ["", style.bold(f"Unparsed rows ({len(result.unparsed_rows)})")]
        for u in result.unparsed_rows:
            out.append(f"  row {u.source_row}: {u.reason}: {clip(u.text, 60)}")
    if result.warnings:
        out += ["", style.bold(f"Warnings ({len(result.warnings)})")]
        out += [f"  {style.yellow(w)}" for w in result.warnings]
    if lines:
        rows = []
        for ln in lines:
            flags = [f for f, on in (("DNP", ln.dnp), ("generic", ln.generic and ln.orderable)) if on]
            if ln.category_hint in ("fiducial", "mounting_hole", "test_point"):
                flags.append(ln.category_hint)
            rows.append((ln.line_id, ",".join(ln.refdes), ln.quantity, ln.value or "", ln.footprint or "",
                         ln.mpn or "", ln.supplier_pn or "", " ".join(flags)))
        out += ["", style.bold("Lines")]
        out += table(["id", "refdes", "qty", "value", "footprint", "mpn", "digikey pn", "flags"], rows,
                     [4, 22, 4, 14, 22, 24, 22, 20], right=(0, 2))
    return "\n".join(out) + "\n"


def ingest_json(result: Any) -> dict[str, Any]:
    return {
        "format": result.fmt,
        "lines": [ln.to_dict() for ln in result.lines],
        "mapping_report": asdict(result.mapping_report) if result.mapping_report else None,
        "unparsed_rows": [asdict(u) for u in result.unparsed_rows],
        "warnings": list(result.warnings),
        "preamble": list(result.preamble),
    }


# ---- resolved BOM -----------------------------------------------------------


def status_text(status: str, style: Style) -> str:
    if status in ("resolved",):
        return style.green(status)
    if status in ("skipped", "dnp_skipped"):
        return style.dim(status)
    return style.yellow(status) if status in ("needs_decision", "ambiguous", "pending") else style.red(status)


def bom_text(bom: Any, style: Style) -> str:
    counts = ", ".join(f"{k} {v}" for k, v in bom.counts.items())
    out = [
        style.bold(f"Session {bom.session_id}"),
        f"  boards {bom.boards}, lines {len(bom.lines)}: {counts}",
        f"  estimated total of resolved lines: {money(bom.estimated_total)}",
    ]
    if bom.warnings:
        out += ["", style.bold(f"Warnings ({len(bom.warnings)})")] + [f"  {style.yellow(w)}" for w in bom.warnings]
    rows = [
        (ln.line.line_id, ln.cref, ln.status, ln.dk_pn or "", "" if ln.order_qty is None else ln.order_qty,
         money(ln.total_cost))
        for ln in bom.lines
    ]
    out += ["", style.bold("Lines")]
    # colour after padding would break alignment, so statuses stay plain in the table
    out += table(["id", "refdes", "status", "digikey pn", "order qty", "total"], rows,
                 [4, 24, 14, 24, 9, 12], right=(0, 4, 5))
    if bom.blocking:
        out += ["", style.bold(f"Needs attention ({len(bom.blocking)})")]
        for ln in bom.blocking:
            more = f" ({len(ln.candidates)} candidates)" if ln.candidates else ""
            out.append(f"  line {ln.line.line_id} [{ln.cref}] {status_text(ln.status, style)}{more}: {ln.reason}")
    return "\n".join(out) + "\n"


def option_lines(ln: Any) -> list[tuple[str, str]]:
    """(text, kind) for every pickable candidate and substitute of a line."""
    items: list[tuple[str, str]] = []
    for c in ln.candidates:
        price = "n/a" if c.unit_price is None else f"${c.unit_price:.4f} ea"
        items.append((f"{c.dk_pn}  {c.mpn} ({c.manufacturer}), stock {c.stock}, {price}\n       {clip(c.why, 90)}", c.dk_pn))
    for s in ln.substitutes:
        price = "n/a" if s.unit_price is None else f"${s.unit_price:.4f} ea"
        items.append((f"{s.dk_pn}  substitute {s.mpn} ({s.manufacturer}), stock {s.stock}, {price}\n       {clip(s.why, 90)}", s.dk_pn))
    return items


# ---- search and part --------------------------------------------------------


def product_row(p: dict[str, Any]) -> tuple[Any, ...]:
    pns = p.get("digikey_part_numbers") or []
    return (pns[0] if pns else "", p.get("manufacturer_part_number", ""), p.get("manufacturer", ""),
            p.get("quantity_available", ""), money(p.get("unit_price"), 4), p.get("description", ""))


def search_text(data: dict[str, Any], style: Style) -> str:
    exact = data.get("exact_matches") or []
    others = data.get("products") or []
    out = [style.bold(f"{data.get('products_count', len(exact) + len(others))} matches at DigiKey")]
    headers = ["digikey pn", "mpn", "manufacturer", "stock", "unit", "description"]
    widths = [22, 26, 22, 8, 10, 40]
    if exact:
        out += ["", "Exact matches"] + table(headers, [product_row(p) for p in exact], widths, right=(3, 4))
    if others:
        out += ["", "Results"] + table(headers, [product_row(p) for p in others], widths, right=(3, 4))
    if not exact and not others:
        out.append("  no results")
    return "\n".join(out) + "\n"


def part_text(p: dict[str, Any], style: Style) -> str:
    out = [style.bold(f"{p.get('manufacturer_part_number', '?')}  {p.get('manufacturer', '')}")]
    if p.get("description"):
        out.append(f"  {p['description']}")
    for label, key in (("status", "status"), ("stock", "quantity_available"), ("unit price", "unit_price"),
                       ("RoHS", "rohs_status"), ("datasheet", "datasheet_url"), ("page", "product_url")):
        if p.get(key) not in (None, ""):
            out.append(f"  {label}: {p[key]}")
    variations = p.get("variations") or []
    if variations:
        rows = []
        for v in variations:
            breaks = v.get("price_breaks") or []
            first = breaks[0] if breaks else {}
            rows.append((v.get("digikey_part_number", ""), v.get("packaging", ""), v.get("minimum_order_quantity", ""),
                         v.get("standard_package", ""), v.get("quantity_available", ""),
                         f"{first.get('quantity', '')}: {money(first.get('unit_price'), 4)}" if first else ""))
        out += ["", style.bold("Packaging")]
        out += table(["digikey pn", "packaging", "moq", "std pack", "stock", "first break"], rows,
                     [24, 22, 8, 9, 9, 18], right=(2, 3, 4))
    params = p.get("parameters") or {}
    if params:
        out += ["", style.bold("Parameters")]
        out += [f"  {clip(k, 34):<34} {clip(v, 40)}" for k, v in params.items()]
    return "\n".join(out) + "\n"


# ---- sessions and quota -----------------------------------------------------


def when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def quota_text(q: dict[str, Any], style: Style) -> str:
    out = [
        style.bold("DigiKey API quota (calls made by the resolver)"),
        f"  per minute: {q['used_last_minute']} used, {q['remaining_this_minute']} left of {q['per_minute_limit']}"
        + (f" (window frees in {duration(q['minute_window_frees_in_seconds'])})" if q["minute_window_frees_in_seconds"] else ""),
        f"  per day:    {q['used_today_utc']} used, {q['remaining_today']} left of {q['per_day_limit']}"
        f" (UTC day resets in {duration(q['day_resets_in_seconds'])})",
    ]
    if q["suspended_for_seconds"]:
        out.append(style.red(f"  DigiKey held calls off for another {duration(q['suspended_for_seconds'])}"))
    out.append(f"  {q['note']}")
    return "\n".join(out) + "\n"
