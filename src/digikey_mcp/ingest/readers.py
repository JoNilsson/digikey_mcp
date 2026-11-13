"""Low-level readers that turn each input format into a header + rows table."""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field

from .columns import find_header_row, header_is_recognised
from .models import IngestError, MissingDependencyError

CSV_DELIMITERS = (",", ";", "\t", "|")
_SEPARATOR_ROW = re.compile(r"^[\s|:+=_-]+$")


@dataclass
class Table:
    headers: list[str]
    #: (source row number, cells) for every non-blank row after the header
    rows: list[tuple[int, list[str]]]
    header_row: int | None = None
    #: non-blank rows before the header
    preamble: list[tuple[int, str]] = field(default_factory=list)
    sheet: str | None = None
    #: rows whose cell count differs from the header cannot be aligned reliably
    strict_width: bool = False


def decode_bytes(data: bytes) -> tuple[str, str | None]:
    """Decode text bytes. Returns (text, warning or None)."""
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "input is not UTF-8; decoded as Windows-1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "input is not UTF-8; decoded as Latin-1"


def _blank(cells: Iterable[str]) -> bool:
    return not any(c.strip() for c in cells)


def table_from_rows(
    rows: list[list[str]],
    override_keys: Iterable = (),
    *,
    row_numbers: list[int] | None = None,
    strict_width: bool = False,
    sheet: str | None = None,
) -> Table | None:
    """Build a Table when a header row can be found, else None."""
    idx = find_header_row(rows, override_keys)
    if idx is None:
        return None
    numbers = row_numbers or list(range(1, len(rows) + 1))
    preamble = [
        (numbers[i], " | ".join(c for c in rows[i] if c.strip()))
        for i in range(idx)
        if not _blank(rows[i])
    ]
    data = [(numbers[i], rows[i]) for i in range(idx + 1, len(rows)) if not _blank(rows[i])]
    return Table(
        headers=[c.strip() for c in rows[idx]],
        rows=data,
        header_row=numbers[idx],
        preamble=preamble,
        sheet=sheet,
        strict_width=strict_width,
    )


def table_score(table: Table | None) -> tuple[int, int]:
    if table is None:
        return (-1, -1)
    score, hits, _n = header_is_recognised(table.headers)
    return (score, len(table.rows))


def _trim_pipe_edges(rows: list[list[str]]) -> list[list[str]]:
    if rows and all(r and not r[0].strip() for r in rows):
        rows = [r[1:] for r in rows]
    if rows and all(r and not r[-1].strip() for r in rows):
        rows = [r[:-1] for r in rows]
    return rows


def read_delimited(text: str, override_keys: Iterable = ()) -> Table | None:
    """Parse comma, semicolon, tab or pipe delimited text with a recognisable header."""
    override_keys = list(override_keys)
    best: Table | None = None
    for delim in CSV_DELIMITERS:
        if delim not in text:
            continue
        rows: list[list[str]] = []
        numbers: list[int] = []
        try:
            for n, rec in enumerate(csv.reader(io.StringIO(text), delimiter=delim), 1):
                if len(rec) == 1 and _SEPARATOR_ROW.match(rec[0] or "x"):
                    continue
                if delim == "|" and rec and all(_SEPARATOR_ROW.match(c or "-") for c in rec):
                    continue
                rows.append([c.strip() for c in rec])
                numbers.append(n)
        except csv.Error:
            continue
        if delim == "|":
            rows = _trim_pipe_edges(rows)
        table = table_from_rows(rows, override_keys, row_numbers=numbers)
        if table_score(table) > table_score(best):
            best = table
    return best


def read_whitespace(text: str, override_keys: Iterable = ()) -> Table | None:
    """Parse aligned text columns: runs of 2+ spaces, then single spaces."""
    lines = [(n, ln.strip()) for n, ln in enumerate(text.splitlines(), 1) if ln.strip()]
    lines = [(n, ln) for n, ln in lines if not _SEPARATOR_ROW.match(ln)]
    for splitter in (lambda s: re.split(r"\s{2,}", s), str.split):
        rows = [splitter(ln) for _n, ln in lines]
        table = table_from_rows(
            rows, override_keys, row_numbers=[n for n, _ in lines], strict_width=True
        )
        if table:
            return table
    return None


# --------------------------------------------------------------------- xlsx


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return re.sub(r"\s*\n\s*", " ", str(v)).strip()


def read_xlsx(data: bytes, override_keys: Iterable = ()) -> tuple[Table | None, list[str]]:
    try:
        import openpyxl
    except ImportError as exc:
        raise MissingDependencyError(
            "reading .xlsx BOMs needs the 'openpyxl' package (pip install openpyxl)"
        ) from exc
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for corrupt files
        raise IngestError(f"cannot open workbook: {exc}") from exc
    override_keys = list(override_keys)
    warnings: list[str] = []
    best: Table | None = None
    tried: list[str] = []
    try:
        for ws in wb.worksheets:
            rows = [[_cell_text(v) for v in row] for row in ws.iter_rows(values_only=True)]
            table = table_from_rows(rows, override_keys, sheet=ws.title)
            tried.append(ws.title)
            if table_score(table) > table_score(best):
                best = table
    finally:
        wb.close()
    if best is not None and len(tried) > 1:
        others = [t for t in tried if t != best.sheet]
        warnings.append(f"used sheet {best.sheet!r}; other sheets not read: {', '.join(others)}")
    return best, warnings


# ---------------------------------------------------------------------- pdf


def read_pdf(data: bytes, override_keys: Iterable = ()) -> tuple[Table | None, str, list[str]]:
    """Extract a table (pdfplumber only) and the plain text of a PDF.

    Returns (table or None, text, warnings).
    """
    warnings: list[str] = []
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None
    if pdfplumber is not None:
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                texts = [(page.extract_text() or "") for page in pdf.pages]
                rows: list[list[str]] = []
                for page in pdf.pages:
                    for tbl in page.extract_tables() or []:
                        for row in tbl:
                            rows.append([_cell_text(c) for c in row])
            table = table_from_rows(rows, list(override_keys)) if rows else None
            return table, "\n".join(texts), warnings
        except Exception as exc:
            warnings.append(f"pdfplumber failed ({exc}); trying pypdf")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise MissingDependencyError(
            "reading PDF BOMs needs 'pdfplumber' or 'pypdf' (pip install pdfplumber)"
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:
        raise IngestError(f"cannot read PDF: {exc}") from exc
    return None, text, warnings


# ---------------------------------------------------------------------- xml


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(data: bytes | str) -> ET.Element:
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    if b"<!ENTITY" in raw:
        raise IngestError("XML with entity declarations is not accepted")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise IngestError(f"invalid XML: {exc}") from exc


def _kicad_rows(root: ET.Element) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for comp in root.iter():
        if _local(comp.tag) != "comp":
            continue
        row: dict[str, str] = {"Reference": comp.get("ref", "")}
        dnp = False
        for child in comp:
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if tag == "value":
                row["Value"] = text
            elif tag == "footprint":
                row["Footprint"] = text
            elif tag == "datasheet":
                if text and text != "~":
                    row["Datasheet"] = text
            elif tag == "libsource":
                if child.get("description"):
                    row["Description"] = child.get("description", "")
            elif tag == "fields":
                for fld in child:
                    name = fld.get("name")
                    if name:
                        row[name] = (fld.text or "").strip()
            elif tag == "property":
                name = (child.get("name") or "").lower()
                if name in ("dnp", "do_not_populate"):
                    dnp = True
                elif name in ("exclude_from_bom", "exclude from bom"):
                    row["Exclude from BOM"] = "yes"
        if dnp:
            row["DNP"] = "yes"
        rows.append(row)
    return rows


def _generic_rows(root: ET.Element) -> list[dict[str, str]]:
    """Most repeated record-like element (attributes and leaf children become cells)."""
    best: list[ET.Element] = []
    for parent in root.iter():
        groups: dict[str, list[ET.Element]] = {}
        for child in parent:
            groups.setdefault(_local(child.tag), []).append(child)
        for members in groups.values():
            if len(members) > len(best) and (members[0].attrib or len(members[0])):
                best = members
    rows = []
    for el in best:
        row = {k: v.strip() for k, v in el.attrib.items()}
        for child in el:
            if len(child) == 0:
                text = (child.text or "").strip()
                row[_local(child.tag)] = text
                for k, v in child.attrib.items():
                    row.setdefault(f"{_local(child.tag)} {k}", v.strip())
        rows.append(row)
    return rows


def read_xml(data: bytes | str, override_keys: Iterable = ()) -> Table | None:
    root = _parse_xml(data)
    rows = _kicad_rows(root) or _generic_rows(root)
    if not rows:
        return None
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    cells = [[row.get(h, "") for h in headers] for row in rows]
    table = Table(
        headers=headers,
        rows=[(i, c) for i, c in enumerate(cells, 1)],
        header_row=None,
    )
    return table
