"""Public entry point: ingest(path_or_text) -> IngestResult."""

from __future__ import annotations

import os
from pathlib import Path

from .build import (
    duplicate_designator_warnings,
    group_lines,
    lines_from_table,
)
from .columns import validate_override
from .models import IngestError, IngestResult, MappingReport, UnparsedRow
from .readers import (
    Table,
    decode_bytes,
    read_delimited,
    read_pdf,
    read_whitespace,
    read_xlsx,
    read_xml,
)
from .textparse import parse_free_lines

FORMATS = ("csv", "xml", "xlsx", "pdf", "text")
_EXT_FORMAT = {
    ".csv": "csv", ".tsv": "csv", ".xml": "xml", ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".pdf": "pdf", ".txt": None, ".text": None,
}
_PATH_LIKE_EXT = tuple(_EXT_FORMAT) + (".xls",)


def _load(source) -> tuple[bytes | str, str | None]:
    """Return (content, file extension or None)."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), None
    if isinstance(source, os.PathLike):
        path = Path(source)
        return path.read_bytes(), path.suffix.lower()
    if not isinstance(source, str):
        raise TypeError("ingest() takes a file path, raw bytes or BOM text")
    if "\n" not in source and len(source) <= 1024:
        try:
            exists = os.path.isfile(source)
        except (OSError, ValueError):
            exists = False
        if exists:
            path = Path(source)
            return path.read_bytes(), path.suffix.lower()
        if source.lower().endswith(_PATH_LIKE_EXT) and not any(c.isspace() for c in source.strip()):
            raise FileNotFoundError(f"BOM file not found: {source}")
    return source, None


def _detect_format(content: bytes | str, ext: str | None, fmt: str | None) -> str:
    if fmt:
        fmt = {"tsv": "csv", "txt": "text"}.get(fmt.lower(), fmt.lower())
        if fmt not in FORMATS:
            raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}")
        return fmt
    if isinstance(content, bytes):
        if content.startswith(b"PK\x03\x04"):
            return "xlsx"
        if content.startswith(b"%PDF"):
            return "pdf"
    if ext == ".xls":
        raise IngestError("legacy .xls is not supported; save the workbook as .xlsx or CSV")
    if ext in _EXT_FORMAT and _EXT_FORMAT[ext]:
        return _EXT_FORMAT[ext]  # type: ignore[return-value]
    head = content[:2048].lstrip() if isinstance(content, bytes) else content.lstrip()[:2048]
    if head[:1] in ("<", b"<"):
        return "xml"
    return "text"  # refined to csv once a delimited header is found


def ingest(path_or_text, *, fmt: str | None = None, mapping: dict | None = None) -> IngestResult:
    """Read a BOM from a file path, bytes or pasted text.

    ``fmt`` forces csv, xml, xlsx, pdf or text; by default it is sniffed from
    the content and extension. ``mapping`` overrides the header synonym table:
    ``{"Teilenummer": "mpn", 3: "value"}`` (header text or 0-based column
    index -> field name; None or "ignore" skips a column).
    """
    override = validate_override(mapping)
    content, ext = _load(path_or_text)
    fmt = _detect_format(content, ext, fmt)
    result = IngestResult(fmt=fmt)
    override_keys = list(override)

    table: Table | None = None
    free_lines: list[tuple[int, str]] = []

    if fmt == "xlsx":
        data = content if isinstance(content, bytes) else content.encode("latin-1", "ignore")
        table, warns = read_xlsx(data, override_keys)
        result.warnings += warns
    elif fmt == "xml":
        text = _as_text(content, result)
        table = read_xml(text)
        if table is None:
            result.warnings.append("XML contains no component records")
    else:
        if fmt == "pdf":
            data = content if isinstance(content, bytes) else content.encode("latin-1", "ignore")
            table, text, warns = read_pdf(data, override_keys)
            result.warnings += warns
        else:
            text = _as_text(content, result)
        if table is None:
            table = read_delimited(text, override_keys)
            if table is not None and fmt == "text":
                result.fmt = "csv"
        if table is None and fmt in ("text", "pdf"):
            table = read_whitespace(text, override_keys)
        if table is None:
            free_lines = [(n, ln.strip()) for n, ln in enumerate(text.splitlines(), 1) if ln.strip()]

    if table is None and free_lines and any(isinstance(k, int) for k in override):
        table = _headerless_table(free_lines, override)
        free_lines = []

    if table is not None:
        lines, unparsed, report, warns = lines_from_table(table, override)
        result.mapping_report = report
        result.warnings += warns
        result.unparsed_rows += unparsed
        result.preamble = [f"row {n}: {text}" for n, text in table.preamble]
    elif free_lines or fmt in ("text", "pdf", "csv"):
        if fmt == "csv":
            result.warnings.append(
                "no header row recognised; pass mapping={0: 'refdes', ...} by column index "
                "or rename the headers"
            )
            result.unparsed_rows += [
                UnparsedRow(n, t, "no header row recognised") for n, t in free_lines
            ]
            lines = []
        else:
            lines, unparsed = parse_free_lines(free_lines)
            result.unparsed_rows += unparsed
            if free_lines and not lines:
                result.warnings.append("no BOM table or designator lines recognised")
            result.mapping_report = MappingReport()
    else:
        lines = []
        result.warnings.append("no BOM table with a recognisable header row found")

    result.warnings += duplicate_designator_warnings(lines)
    result.lines = group_lines(lines)
    return result


def _as_text(content: bytes | str, result: IngestResult) -> str:
    if isinstance(content, str):
        return content
    text, warn = decode_bytes(content)
    if warn:
        result.warnings.append(warn)
    return text


def _headerless_table(lines: list[tuple[int, str]], override: dict) -> Table | None:
    """Split lines on the best delimiter when the caller maps columns by index."""
    import csv

    best: list[list[str]] | None = None
    for delim in (",", ";", "\t", "|"):
        rows = [next(csv.reader([t], delimiter=delim), []) for _n, t in lines]
        if best is None or max(len(r) for r in rows) > max(len(r) for r in best):
            best = rows
    if not best or max(len(r) for r in best) < 2:
        return None
    width = max(len(r) for r in best)
    return Table(
        headers=[f"column {i + 1}" for i in range(width)],
        rows=[(n, r) for (n, _t), r in zip(lines, best, strict=True) if any(c.strip() for c in r)],
        header_row=None,
    )
