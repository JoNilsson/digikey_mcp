"""part: one product by DigiKey part number or MPN."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .. import render
from ..common import Context, out
from ..parsing import command, examples
from .search import call_tool


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "part",
        "Show one product with its packaging variations, price breaks and parameters, trimmed to the "
        "fields a BOM needs. Works best with a DigiKey part number; an MPN may match an unrelated part. "
        "Needs DigiKey credentials.",
        examples(
            ("part 311-10.0KHRCT-ND", "details of a DigiKey part number"),
            ("part RC0603FR-0710KL --json", "the same for an MPN, as JSON"),
            ("part 311-10.0KHRCT-ND --raw", "the full DigiKey response, unmodified"),
        ),
    )
    p.add_argument("product", metavar="PRODUCT", help="DigiKey part number or manufacturer part number")
    p.add_argument("--raw", action="store_true", help="print the full DigiKey response as JSON instead of trimmed data")
    p.add_argument("--json", action="store_true", help="print the trimmed result as JSON on stdout")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace, ctx: Context) -> int:
    data = call_tool("product_details", args.product, raw=args.raw)
    if args.raw or args.json:
        out(json.dumps(data, indent=2))
    else:
        out(render.part_text(data, ctx.style).rstrip("\n"))
    return 0
