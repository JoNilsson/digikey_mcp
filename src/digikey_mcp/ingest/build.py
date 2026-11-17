"""Turn mapped rows into BomLine records and group per-designator rows."""

from __future__ import annotations

import math
import re

from .classify import (
    FALSE_WORDS,
    TRUE_WORDS,
    extract_package,
    has_dnp_marker,
    infer_category,
    strip_dnp,
)
from .columns import NOTE_HEADER, map_headers, norm_header
from .models import NON_PART_CATEGORIES, BomLine, MappingReport, UnparsedRow
from .readers import Table
from .refdes import expand_refdes, natural_key
from .values import normalise_value

DIGIKEY_PN = re.compile(r"^\d{1,5}-[\w.\-]+-ND$", re.I)
_PLACEHOLDERS = frozenset({"", "n/a", "na", "-", "--", "tbd", "none", "?", "null", "~"})
_PN_SPLIT = re.compile(r"\s*(?:[;\n]|\s\|\s|\s+or\s+)\s*")
_PASSIVES = ("resistor", "capacitor", "inductor")


def _clean(cell: str | None) -> str | None:
    s = re.sub(r"\s+", " ", cell or "").strip()
    return None if s.lower() in _PLACEHOLDERS else s


def _part_numbers(cell: str | None) -> list[str]:
    out = []
    for piece in _PN_SPLIT.split((cell or "").strip()):
        piece = re.sub(r"\s+", " ", piece).strip()
        if piece.lower() not in _PLACEHOLDERS:
            out.append(piece)
    return out


def _parse_qty(cell: str | None, notes: list[str]) -> int | None:
    if not cell or not cell.strip():
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", cell)
    if not m:
        notes.append(f"quantity {cell.strip()!r} is not a number")
        return None
    num = float(m.group().replace(",", "."))
    if num != int(num):
        notes.append(f"non-integer quantity {cell.strip()!r} rounded up")
    return int(math.ceil(num))


def build_line(
    fields: dict[str, str],
    raw: dict,
    row_no: int | None,
    *,
    note_text: tuple[str, ...] = (),
    desc_is_identity: bool = False,
    extra_notes: tuple[str, ...] = (),
) -> tuple[BomLine | None, str]:
    """Build one BomLine from canonical-field cells. Returns (line, reason if None)."""
    notes = list(extra_notes)
    dnp = False

    ref_cell, hit = strip_dnp(fields.get("refdes", ""))
    dnp = dnp or hit
    refs, ref_notes = expand_refdes(ref_cell)
    notes += ref_notes

    value_raw = _clean(fields.get("value"))
    value_text, hit = strip_dnp(value_raw or "")
    dnp = dnp or hit
    description = _clean(fields.get("description"))
    if has_dnp_marker(description):
        dnp = True
    footprint = _clean(fields.get("footprint"))
    manufacturer = _clean(fields.get("manufacturer"))

    # part numbers: MPN column, DigiKey column, generic supplier column
    supplier_pn: str | None = None
    mpn_candidates = []
    for pn in _part_numbers(fields.get("mpn")):
        cleaned, hit = strip_dnp(pn)
        dnp = dnp or hit
        if not cleaned or cleaned.lower() in _PLACEHOLDERS:
            continue
        if DIGIKEY_PN.match(cleaned):
            supplier_pn = supplier_pn or cleaned
            notes.append(f"DigiKey part number {cleaned!r} found in the MPN column")
        else:
            mpn_candidates.append(cleaned)
    mpn = mpn_candidates[0] if mpn_candidates else None
    if len(mpn_candidates) > 1:
        notes.append("alternate part numbers: " + ", ".join(mpn_candidates[1:]))
    dk_candidates = _part_numbers(fields.get("digikey_pn"))
    if dk_candidates:
        supplier_pn = dk_candidates[0]
        if len(dk_candidates) > 1:
            notes.append("additional DigiKey part numbers: " + ", ".join(dk_candidates[1:]))
    for pn in _part_numbers(fields.get("supplier_pn")):
        if DIGIKEY_PN.match(pn):
            supplier_pn = supplier_pn or pn
        else:
            notes.append(f"non-DigiKey supplier part number {pn!r} not used")

    # explicit DNP / populate columns and remarks
    dnp_cell = (fields.get("dnp") or "").strip()
    if dnp_cell.lower() not in FALSE_WORDS:
        dnp = True
        if dnp_cell.lower() not in TRUE_WORDS and not has_dnp_marker(dnp_cell):
            notes.append(f"DNP column value {dnp_cell!r} treated as do-not-populate")
    pop_cell = (fields.get("populate") or "").strip().lower()
    if pop_cell and (pop_cell in FALSE_WORDS or has_dnp_marker(pop_cell)):
        dnp = True
    for text in note_text:
        if has_dnp_marker(text):
            dnp = True
            notes.append(f"DNP marker in remark {text.strip()!r}")

    if not (refs or mpn or supplier_pn or value_raw or (desc_is_identity and description)):
        return None, "no designator, value or part number"

    quantity = _parse_qty(fields.get("quantity"), notes)
    if quantity is None:
        quantity = len(refs) if refs else 1
        if not refs:
            notes.append("no quantity or designators; quantity assumed 1")
    elif refs and quantity != len(refs):
        notes.append(f"quantity {quantity} differs from {len(refs)} designators")

    category = infer_category(refs, value_text, footprint, description, bool(mpn or supplier_pn))
    info = normalise_value(value_text, category if category in _PASSIVES else None)
    if category == "other" and info.unit:
        category = {"ohm": "resistor", "F": "capacitor", "H": "inductor"}[info.unit]
    generic = not (mpn or supplier_pn)
    if category in NON_PART_CATEGORIES and generic:
        notes.append(f"{category.replace('_', ' ')}: not a purchasable part")
    if dnp:
        notes.append("do not populate")

    line = BomLine(
        line_id=0,
        refdes=refs,
        quantity=quantity,
        value=info.canonical or None,
        value_raw=value_raw,
        value_si=info.number,
        value_unit=info.unit,
        tolerance=info.tolerance,
        footprint=footprint,
        package=extract_package(footprint),
        description=description,
        mpn=mpn,
        manufacturer=manufacturer,
        supplier_pn=supplier_pn,
        dnp=dnp,
        category_hint=category,
        generic=generic,
        raw=raw,
        source_row=row_no,
        source_rows=[row_no] if row_no is not None else [],
        notes=_dedupe(notes),
    )
    return line, ""


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [i for i in items if not (i in seen or seen.add(i))]


def lines_from_table(
    table: Table, override: dict
) -> tuple[list[BomLine], list[UnparsedRow], MappingReport, list[str]]:
    """Map a table's columns and build ungrouped BomLines."""
    index_field, report, warnings = map_headers(
        table.headers, override, header_row=table.header_row, sheet=table.sheet
    )
    if "refdes" not in index_field.values() and "quantity" in index_field.values():
        warnings.append("no designator column mapped; quantities are taken from the quantity column")
    unused = [k for k in override if isinstance(k, str) and k not in {norm_header(h) for h in table.headers}]
    for key in unused:
        warnings.append(f"mapping override for header {key!r} matched no column")

    keys: list[str] = []
    for i, h in enumerate(table.headers):
        key = h or f"column {i + 1}"
        while key in keys:
            key += " #2"
        keys.append(key)
    note_cols = [
        i for i, h in enumerate(table.headers) if i not in index_field and h and NOTE_HEADER.search(h)
    ]
    identity_fields = {"refdes", "value", "mpn", "digikey_pn", "supplier_pn"}
    desc_is_identity = not (identity_fields & set(index_field.values()))
    header_norm = [norm_header(h) for h in table.headers]

    lines: list[BomLine] = []
    unparsed: list[UnparsedRow] = []
    ncols = len(table.headers)
    for row_no, cells in table.rows:
        text = " | ".join(c for c in cells if c.strip())
        if [norm_header(c) for c in cells] == header_norm:
            continue  # repeated header (page break)
        extra: list[str] = []
        if len(cells) != ncols:
            if table.strict_width:
                unparsed.append(
                    UnparsedRow(row_no, text, f"{len(cells)} columns, header has {ncols}")
                )
                continue
            if len(cells) > ncols:
                if any(c.strip() for c in cells[ncols:]):
                    extra.append("row has more cells than the header; extra cells ignored")
                cells = cells[:ncols]
            else:
                cells = cells + [""] * (ncols - len(cells))
        fields: dict[str, str] = {}
        for i, fld in index_field.items():
            if fld in ("ignore", "item"):
                continue
            if cells[i].strip() and not fields.get(fld, "").strip():
                fields[fld] = cells[i]
        raw = dict(zip(keys, cells, strict=True))
        line, reason = build_line(
            fields,
            raw,
            row_no,
            note_text=tuple(cells[i] for i in note_cols),
            desc_is_identity=desc_is_identity,
            extra_notes=tuple(extra),
        )
        if line is None:
            unparsed.append(UnparsedRow(row_no, text, reason))
        else:
            lines.append(line)
    return lines, unparsed, report, warnings


# ------------------------------------------------------------------ grouping


def _group_key(line: BomLine, unique: int):
    if line.mpn:
        return ("mpn", re.sub(r"\s+", "", line.mpn).upper(), line.dnp)
    if line.supplier_pn:
        return ("dk", line.supplier_pn.upper(), line.dnp)
    if line.value:
        return (
            "generic",
            line.category_hint,
            line.value.lower(),
            (line.footprint or "").lower(),
            line.dnp,
        )
    return ("row", unique)


def group_lines(lines: list[BomLine]) -> list[BomLine]:
    """Merge rows that describe the same part (same MPN, or value + footprint)."""
    groups: dict = {}
    merged_counts: dict = {}
    for n, line in enumerate(lines):
        key = _group_key(line, n)
        if key not in groups:
            groups[key] = line
            merged_counts[key] = 1
            continue
        head = groups[key]
        merged_counts[key] += 1
        head.refdes = sorted(set(head.refdes) | set(line.refdes), key=natural_key)
        head.quantity += line.quantity
        head.source_rows += line.source_rows
        for attr in (
            "value", "value_raw", "value_si", "value_unit", "tolerance", "footprint", "package",
            "description", "mpn", "manufacturer", "supplier_pn",
        ):
            if getattr(head, attr) in (None, "") and getattr(line, attr) not in (None, ""):
                setattr(head, attr, getattr(line, attr))
        head.notes = _dedupe(head.notes + line.notes)
    out = []
    for i, (key, line) in enumerate(groups.items(), 1):
        line.line_id = i
        if merged_counts[key] > 1:
            line.notes.append(f"merged {merged_counts[key]} source rows")
        out.append(line)
    return out


def duplicate_designator_warnings(lines: list[BomLine]) -> list[str]:
    """Designators that occur on more than one source row (call before grouping)."""
    seen: dict[str, list[int | None]] = {}
    for ln in lines:
        for ref in ln.refdes:
            seen.setdefault(ref, []).append(ln.source_row)
    return [
        f"designator {ref} appears on rows {', '.join(str(r) for r in rows)}"
        for ref, rows in seen.items()
        if len(rows) > 1
    ]
