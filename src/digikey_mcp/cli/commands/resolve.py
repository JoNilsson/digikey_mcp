"""resolve: BOM to DigiKey part numbers, quantities and packaging."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .. import common, render
from ..common import EXIT_BLOCKED, PROG, Context, UsageError, err, out
from ..parsing import command, examples


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def cache_flag(ctx: Context) -> str:
    return f" --cache-dir {ctx.cache_dir}" if ctx.cache_dir else ""


def conclude(bom: Any, ctx: Context, as_json: bool) -> int:
    """Print a resolved BOM and return the exit code: 3 while lines block or the run stopped."""
    if as_json:
        out(json.dumps(bom.to_dict(), indent=2))
    else:
        out(render.bom_text(bom, ctx.style).rstrip("\n"))
    sid, flags = bom.session_id, cache_flag(ctx)
    if not bom.complete:
        err(f"Stopped early: {bom.stopped}")
        err(f"Session: {sid}")
        err(f"Resume with: {PROG} resolve --session {sid}{flags}")
        return EXIT_BLOCKED
    if bom.blocking:
        err(f"{len(bom.blocking)} line(s) still need a decision. Session: {sid}")
        err(f"Next: {PROG} decide {sid} --interactive{flags}")
        return EXIT_BLOCKED
    ctx.info(f"Every line is resolved. Session: {sid}")
    ctx.info(f"Next: {PROG} report {sid}{flags}   or   {PROG} cart {sid}{flags}")
    return 0


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "resolve",
        "Resolve a BOM to exact DigiKey part numbers, order quantities and packaging. A line with a "
        "DigiKey part number is looked up directly; MPN with manufacturer, and MPN alone, are searched. "
        "Generic lines (no MPN) are never picked for you: they come back as needs_decision with ranked "
        "candidates, answered with 'decide'. Quantity is boards x per-board quantity plus overage, "
        "rounded to the minimum order and reel multiples. Nothing is ordered. Progress is saved after "
        "every line, so a run that stops on the DigiKey quota or the time budget resumes with --session. "
        "Needs DigiKey credentials.",
        examples(
            ("resolve bom.csv --boards 5", "resolve a BOM for five boards; prints the session id"),
            ("resolve --session bom-20260101-120000-ab12", "resume a run that stopped early"),
            ("resolve --text 'R1,R2 10k 0603' --session test", "resolve pasted BOM text into a named session"),
            ("resolve - --json < bom.txt", "read text from stdin, print machine-readable JSON"),
        ) + "\n\nExit status: 0 all lines resolved; 3 lines still need decisions or the run stopped "
            "(quota, time budget); 1 error; 2 usage.",
    )
    p.add_argument("source", nargs="?", metavar="SOURCE",
                   help="BOM file (csv, xlsx, xml, pdf), or - for text on stdin; omit with --session to resume")
    p.add_argument("--text", action="store_true", help="treat SOURCE as the BOM text itself, not a file name")
    p.add_argument("--boards", type=positive_int, default=1, metavar="N", help="number of boards to build")
    p.add_argument("--prefs", metavar="PATH", default=None,
                   help="TOML file with overage, packaging, generic-search, quota and cache preferences")
    p.add_argument("--session", metavar="ID", default=None,
                   help="name a new session or resume/revise an existing one; generated when omitted")
    p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace, ctx: Context) -> int:
    from ...resolve import session

    source, is_text = args.source, args.text
    if source == "-":
        source, is_text = sys.stdin.read(), True
        if not source.strip():
            raise UsageError("no BOM text on stdin", hint=f"pipe a BOM in, e.g. cat bom.txt | {PROG} resolve -")
    elif source is None and not args.session:
        raise UsageError("give a BOM SOURCE, or --session ID to resume a run",
                         hint=f"run '{PROG} resolve --help' for examples")
    elif source is not None and is_text and not source.strip():
        raise UsageError("--text needs the BOM text as SOURCE")
    client = common.get_client()
    sid = args.session or session.new_session_id()
    ctx.info(f"Resolving into session {sid} (progress is saved after every line) ...")
    try:
        bom = session.resolve_bom(source, args.boards, args.prefs, sid, is_text,
                                  client=client, cache_dir=ctx.cache_dir)
    except KeyboardInterrupt:
        err(f"Interrupted. Resume with: {PROG} resolve --session {sid}{cache_flag(ctx)}")
        return 130
    return conclude(bom, ctx, args.json)
