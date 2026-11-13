"""cart: DigiKey cart links for a resolved session. Builds links only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..common import PROG, CliError, Context, err, out
from ..parsing import command, examples

NOTICE = (
    "Nothing has been submitted or ordered. Open the links in a browser signed in to your own "
    "DigiKey account, review the cart there, then choose payment and place the order yourself."
)


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "cart",
        "Build the links that fill your DigiKey cart from the resolved lines of a session. It only builds "
        "links or text: no network call is made and nothing is submitted. You open the links in your own "
        "signed-in DigiKey session, review the cart and pay there. Lines that are not resolved are left out "
        "and listed. 'urls' gives FastAdd links under 1700 characters (the first empties the cart, the rest "
        "add to it); 'html' gives a page of POST forms for large BOMs; 'text' gives qty, part number, "
        "reference lines for the Quick Order box (importer format unconfirmed).",
        examples(
            ("cart proj", "print the FastAdd links, one per line"),
            ("cart proj --output html --out cart.html", "write a page with one button per 400 parts"),
            ("cart proj --no-newcart", "add to the existing DigiKey cart instead of emptying it first"),
        ),
    )
    p.add_argument("session", metavar="SESSION", help="session id printed by resolve (see 'sessions')")
    p.add_argument("--output", choices=("urls", "html", "text"), default="urls", help="kind of handoff to build")
    p.add_argument("--out", metavar="PATH", default=None,
                   help="write the result to this file instead of stdout (html needs .html or .htm; "
                        "the directory must exist)")
    p.add_argument("--no-newcart", action="store_true",
                   help="add to the cart that is already open instead of emptying it first")
    p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace, ctx: Context) -> int:
    from ...cart import cart_links
    from ...resolve import session

    summary = session.cart_items_summary(args.session, cache_dir=ctx.cache_dir)
    items = summary["items"]
    if not items:
        raise CliError("the session has no resolved lines to put in a cart",
                       hint=f"run '{PROG} resolve --session {args.session}' or '{PROG} decide {args.session} --interactive'")
    to_html = args.out if args.output == "html" else None
    result = cart_links(items, output=args.output, write_html_to=to_html, newcart=not args.no_newcart)

    if summary["blocking"]:
        err(f"WARNING: {len(summary['blocking'])} line(s) are not in the cart:")
        for b in summary["blocking"]:
            err(f"  line {b['line_id']} [{','.join(b['refdes'])}] {b['status']}: {b['reason']}")
        err(f"Answer them with: {PROG} decide {args.session} --interactive")
    err(NOTICE)

    if args.json:
        out(json.dumps(result, indent=2))
        return 0
    if args.output == "urls":
        body = "\n".join(c["url"] for c in result["urls"])
        ctx.info(f"{result['url_count']} link(s) for {result['line_count']} parts, open them in order."
                 + ("" if args.no_newcart else " The first one empties your DigiKey cart."))
    elif args.output == "text":
        body = result["text"].rstrip("\n")
    else:
        body = result.get("html", "").rstrip("\n")
    if args.output == "html" and args.out:
        ctx.info(f"Wrote {result['written_to']} ({result['form_count']} form(s)). Open it in your browser.")
        return 0
    if args.out:
        path = Path(args.out).expanduser()
        if path.is_dir() or not path.parent.is_dir():
            raise CliError(f"cannot write {args.out}: give a file path in an existing directory")
        path.write_text(body + "\n", encoding="utf-8")
        ctx.info(f"Wrote {path}")
        return 0
    out(body)
    return 0
