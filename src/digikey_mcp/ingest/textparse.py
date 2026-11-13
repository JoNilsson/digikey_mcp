"""Heuristic parsing of pasted / PDF text that has no recognisable header."""

from __future__ import annotations

import re

from .build import DIGIKEY_PN, build_line
from .classify import KNOWN_PREFIXES, PACKAGE_TOKEN
from .models import BomLine, UnparsedRow
from .refdes import expand_refdes, refdes_prefix
from .values import normalise_value

_REF_ITEM = r"[A-Za-z]{1,4}\d+[A-Za-z]?(?:\s*[-~–]\s*[A-Za-z]{0,4}\d+)?"
_LEADING_REFS = re.compile(rf"^(?P<refs>{_REF_ITEM}(?:\s*,\s*{_REF_ITEM})*)(?=[\s:;,]|$)")
_LEADING_QTY = re.compile(r"^(?P<qty>\d+)\s*[xX×]\s+(?P<rest>.+)$")
_QTY_TOKEN = [
    re.compile(r"^[xX×](\d+)$"),
    re.compile(r"^(\d+)[xX×]$"),
    re.compile(r"^qty[:=]?(\d+)$", re.I),
]


def _is_value_token(tok: str) -> bool:
    info = normalise_value(tok)
    return info.parsed and bool(re.search(r"[A-Za-zµΩμ]", tok))


def parse_free_lines(
    lines: list[tuple[int, str]]
) -> tuple[list[BomLine], list[UnparsedRow]]:
    """Parse "R1, R2  10k  0603" style lines; anything unsure goes to unparsed."""
    parsed: list[BomLine] = []
    unparsed: list[UnparsedRow] = []
    for row_no, text in lines:
        fields = _parse_line(text)
        if fields is None:
            unparsed.append(UnparsedRow(row_no, text, "no designator or quantity prefix recognised"))
            continue
        if isinstance(fields, str):
            unparsed.append(UnparsedRow(row_no, text, fields))
            continue
        line, reason = build_line(
            fields,
            {"text": text},
            row_no,
            desc_is_identity=True,
            extra_notes=("parsed from free text without a header; check fields",),
        )
        if line is None:
            unparsed.append(UnparsedRow(row_no, text, reason))
        else:
            parsed.append(line)
    return parsed, unparsed


def _parse_line(text: str):
    """Return a field dict, an unparsed-reason string, or None when no anchor is found."""
    s = text.strip()
    fields: dict[str, str] = {}
    m = _LEADING_REFS.match(s)
    if m:
        refs, _ = expand_refdes(m.group("refs"))
        if not refs or any(refdes_prefix(r) not in KNOWN_PREFIXES for r in refs):
            return None
        fields["refdes"] = m.group("refs")
        rest = s[m.end():].lstrip(" :;,")
    else:
        q = _LEADING_QTY.match(s)
        if not q:
            return None
        fields["quantity"] = q.group("qty")
        rest = q.group("rest")
    tokens = rest.split()
    leftover: list[str] = []
    for tok in tokens:
        qty = next((rx.match(tok) for rx in _QTY_TOKEN if rx.match(tok)), None)
        if qty and "quantity" not in fields:
            fields["quantity"] = qty.group(1)
        elif DIGIKEY_PN.match(tok) and "digikey_pn" not in fields:
            fields["digikey_pn"] = tok
        elif PACKAGE_TOKEN.match(tok) and "footprint" not in fields:
            fields["footprint"] = tok
        elif "value" not in fields and _is_value_token(tok):
            fields["value"] = tok
        else:
            leftover.append(tok)
    if leftover and leftover[-1].isdigit() and "quantity" not in fields and "value" in fields:
        fields["quantity"] = leftover.pop()
    if leftover and all(re.fullmatch(r"\d+(?:\.\d+)?", t) for t in leftover):
        return f"ambiguous bare number {' '.join(leftover)!r}; not a value or quantity"
    if leftover:
        fields["description"] = " ".join(leftover)
    if not (set(fields) - {"refdes", "quantity"}):
        return "designator found but no value, package or part number"
    return fields
