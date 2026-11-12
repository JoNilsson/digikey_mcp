"""Markdown and CSV reports of a resolved BOM."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .models import BLOCKING, CARTABLE, ResolvedBom, ResolvedLine

COLUMNS = ["line", "refdes", "qty_per_board", "needed", "status", "digikey_pn", "mpn", "manufacturer",
           "packaging", "order_qty", "unit_price", "total_cost", "flags", "reason", "options"]


def _cell(text: object) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _row(ln: ResolvedLine) -> list[str]:
    return [
        str(ln.line.line_id),
        ",".join(ln.line.refdes),
        str(ln.line.quantity),
        str(ln.needed_qty),
        ln.status,
        ln.dk_pn or "",
        ln.mpn or "",
        ln.manufacturer or "",
        ln.packaging or "",
        "" if ln.order_qty is None else str(ln.order_qty),
        "" if ln.unit_price is None else f"{ln.unit_price:.4f}",
        "" if ln.total_cost is None else f"{ln.total_cost:.2f}",
        "; ".join(ln.flags),
        ln.reason,
        " | ".join(o.label() for o in ln.alternatives),
    ]


def to_csv(bom: ResolvedBom) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for ln in bom.lines:
        writer.writerow(_row(ln))
    return out.getvalue()


def to_markdown(bom: ResolvedBom) -> str:
    counts = ", ".join(f"{k} {v}" for k, v in bom.counts.items())
    lines = [
        f"# BOM resolution {bom.session_id}",
        "",
        f"Boards: {bom.boards}. Lines: {len(bom.lines)} ({counts}). Estimated total: ${bom.estimated_total:,.2f}.",
    ]
    if not bom.complete:
        lines += ["", f"Incomplete: {bom.stopped or 'lines pending'}"]
    lines += ["", "| Line | Ref | Need | Status | DigiKey PN | MPN | Manufacturer | Pack | Order | Unit | Total | Note |",
              "| --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |"]
    for ln in bom.lines:
        r = _row(ln)
        lines.append("| " + " | ".join(_cell(x) for x in (r[0], r[1], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[13])) + " |")
    blocking = [ln for ln in bom.lines if ln.status in BLOCKING]
    if blocking:
        lines += ["", "## Blocking lines (excluded from the cart)", ""]
        for ln in blocking:
            lines.append(f"* Line {ln.line.line_id} ({','.join(ln.line.refdes)}), {ln.status}: {ln.reason}")
            for c in ln.candidates:
                price = "n/a" if c.unit_price is None else f"${c.unit_price:.4f}"
                lines.append(f"  * {c.dk_pn} {c.mpn} ({c.manufacturer}), stock {c.stock}, {price} each: {c.why}")
            for s in ln.substitutes:
                lines.append(f"  * substitute {s.dk_pn} {s.mpn} ({s.manufacturer}), stock {s.stock}: {s.why}")
    shown = [ln for ln in bom.lines if ln.status in CARTABLE and len(ln.alternatives) > 1]
    if shown:
        lines += ["", "## Packaging comparison", ""]
        for ln in shown:
            lines.append(f"* Line {ln.line.line_id} {ln.mpn} (need {ln.needed_qty}):")
            for o in ln.alternatives:
                chosen = " <- chosen" if o.dk_pn == ln.dk_pn else ""
                lines.append(f"  * {o.label()}{chosen}")
            for text in ln.break_even:
                lines.append(f"  * break-even: {text}")
    return "\n".join(lines) + "\n"


def write_report(text: str, target: str, suffixes: tuple[str, ...]) -> str:
    path = Path(target).expanduser()
    if path.suffix.lower() not in suffixes:
        raise ValueError(f"write_to must end in {' or '.join(suffixes)}")
    if path.is_dir():
        raise ValueError("write_to is a directory, give a file path")
    if not path.parent.is_dir():
        raise ValueError(f"write_to: directory {path.parent} does not exist")
    path.write_text(text, encoding="utf-8")
    return str(path)
