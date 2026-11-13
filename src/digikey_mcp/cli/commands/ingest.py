"""ingest: parse a BOM file and show the column mapping report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ...ingest import FORMATS, ingest
from ...ingest.columns import FIELDS, validate_override
from ..common import PROG, CliError, Context, UsageError, out
from ..parsing import command, examples
from .. import render


def parse_map(pairs: list[str] | None) -> dict[Any, str]:
    """`FIELD=COLUMN` items to the mapping ingest() takes (column header text or 0-based index)."""
    mapping: dict[Any, str] = {}
    for item in pairs or []:
        fld, sep, col = item.partition("=")
        fld, col = fld.strip(), col.strip()
        if not sep or not fld or not col:
            raise UsageError(f"--map expects FIELD=COLUMN, got {item!r}",
                             hint=f"fields: {', '.join(FIELDS)}; COLUMN is a header text or a 0-based index")
        mapping[int(col) if col.isdigit() else col] = fld
    try:
        validate_override(mapping)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    return mapping


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "ingest",
        "Parse a BOM (KiCad or Altium CSV, KiCad XML, other CSV/TSV/XML, .xlsx, PDF or pasted text) "
        "and show what was understood: the column mapping, unparsed rows, warnings and every line. "
        "Makes no DigiKey calls and needs no credentials. Use it to check a BOM before resolving it.",
        examples(
            ("ingest bom.csv", "show the column mapping and the parsed lines"),
            ("ingest parts.csv --map mpn='Teilenummer' --map refdes=0",
             "map a header or a 0-based column index to a field when the guess is wrong"),
            ("ingest - --format text < pasted.txt", "read BOM text from stdin"),
        ) + "\n\nFields for --map: " + ", ".join(FIELDS) + " ('ignore' skips a column).",
    )
    p.add_argument("source", metavar="SOURCE", help="BOM file, or - to read text from stdin")
    p.add_argument("--format", choices=FORMATS, default=None, help="force the input format instead of detecting it")
    p.add_argument("--map", action="append", metavar="FIELD=COLUMN", default=None,
                   help="map a column (header text or 0-based index) to a field; repeatable")
    p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace, ctx: Context) -> int:
    mapping = parse_map(args.map)
    if args.source == "-":
        data: Any = sys.stdin.read().encode("utf-8")
        label = "(stdin)"
    else:
        path = Path(args.source).expanduser()
        if not path.is_file():
            raise CliError(f"BOM file not found: {args.source}", hint="check the path, or use - to read stdin")
        data, label = path, args.source
    result = ingest(data, fmt=args.format, mapping=mapping or None)
    if args.json:
        out(json.dumps(render.ingest_json(result), indent=2))
    else:
        out(render.ingest_text(result, label, ctx.style).rstrip("\n"))
        ctx.info(f"Next: {PROG} resolve {args.source}")
    return 0
