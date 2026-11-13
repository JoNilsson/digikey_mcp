"""report: a session as markdown, CSV or JSON."""

from __future__ import annotations

import argparse
import json
from typing import Any

from ..common import CliError, Context, out
from ..parsing import command, examples

SUFFIXES = {"md": (".md", ".markdown"), "csv": (".csv",), "json": (".json",)}


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "report",
        "Render a resolved session as a markdown table, CSV or JSON: status, reason, DigiKey part "
        "number, packaging, order quantity, prices and the estimated total, plus the blocking lines "
        "with their candidates. Reads the saved session; makes no DigiKey calls and needs no credentials.",
        examples(
            ("report proj", "print the markdown report"),
            ("report proj --format csv --out bom.csv", "write a CSV file for a spreadsheet"),
            ("report proj --format json", "the full machine-readable result"),
        ),
    )
    p.add_argument("session", metavar="SESSION", help="session id printed by resolve (see 'sessions')")
    p.add_argument("--format", choices=sorted(SUFFIXES), default="md", help="output format")
    p.add_argument("--out", metavar="PATH", default=None,
                   help="write to this file (.md, .csv or .json to match the format; the directory must "
                        "exist) instead of stdout")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace, ctx: Context) -> int:
    from ...resolve import report, session

    bom = session.load_bom(args.session, cache_dir=ctx.cache_dir)
    if args.format == "md":
        text = report.to_markdown(bom)
    elif args.format == "csv":
        text = report.to_csv(bom)
    else:
        text = json.dumps(bom.to_dict(), indent=2) + "\n"
    if args.out is None:
        out(text.rstrip("\n"))
        return 0
    try:
        written = report.write_report(text, args.out, SUFFIXES[args.format])
    except ValueError as exc:
        raise CliError(str(exc).replace("write_to", "--out")) from None
    ctx.info(f"Wrote {written}")
    return 0
