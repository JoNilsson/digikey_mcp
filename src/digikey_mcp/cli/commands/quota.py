"""quota: DigiKey calls used and left."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .. import render
from ..common import Context, out
from ..parsing import command, examples


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "quota",
        "Show the DigiKey calls used and left this minute and this UTC day. DigiKey allows 120 calls per "
        "minute and 1000 per day for Product Information v4; the resolver keeps a reserve of 5. Only "
        "calls made by the resolver are counted and cache hits are free. Reads local counters; makes no "
        "DigiKey calls and needs no credentials.",
        examples(
            ("quota", "show the usage with default limits"),
            ("quota proj", "use the limits stored in a session's preferences"),
            ("quota --json", "machine-readable"),
        ),
    )
    p.add_argument("session", nargs="?", metavar="SESSION", default=None,
                   help="take the limits from this session's preferences")
    p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace, ctx: Context) -> int:
    from ...resolve import session

    data = session.quota_status(args.session, cache_dir=ctx.cache_dir)
    out(json.dumps(data, indent=2) if args.json else render.quota_text(data, ctx.style).rstrip("\n"))
    return 0
