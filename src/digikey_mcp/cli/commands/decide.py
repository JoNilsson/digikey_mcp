"""decide: answer lines that need a part number or a skip."""

from __future__ import annotations

import argparse
from typing import Any

from .. import common, render
from ..common import PROG, Context, UsageError, out
from ..parsing import command, examples
from .resolve import conclude

NEEDS_ANSWER = ("needs_decision", "ambiguous")
PROMPT_HELP = "number = pick a candidate, or type a DigiKey part number; s = skip the line; Enter = leave it; q = quit"


def parse_pairs(pairs: list[str]) -> dict[int, str]:
    decisions: dict[int, str] = {}
    for item in pairs:
        line, sep, value = item.partition("=")
        line, value = line.strip(), value.strip()
        if not sep or not line.isdigit() or not value:
            raise UsageError(f"decision {item!r} is not LINE=PART_NUMBER or LINE=skip",
                             hint="example: 3=311-10.0KHRCT-ND 7=skip")
        decisions[int(line)] = value
    return decisions


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "decide",
        "Answer lines of a session: LINE=PART_NUMBER picks a DigiKey part (a candidate, a substitute or "
        "any part you name; it is looked up and checked like any other line) and LINE=skip leaves the "
        "line out of the cart. With --interactive each line that is needs_decision or ambiguous is "
        "shown with its ranked candidates and you are asked. This is how generic lines get answered. "
        "Decisions are saved with the session and survive BOM revisions. Needs DigiKey credentials.",
        examples(
            ("decide bom-20260101-120000-ab12 --interactive", "walk through every open line"),
            ("decide proj 3=311-10.0KHRCT-ND 7=skip", "answer lines 3 and 7 without prompts"),
            ("decide proj 4=1276-1000-1-ND --interactive", "answer line 4, then prompt for the rest"),
        ) + "\n\nExit status: 0 nothing blocks the order any more; 3 lines still need attention; 1 error; 2 usage.",
    )
    p.add_argument("session", metavar="SESSION", help="session id printed by resolve (see 'sessions')")
    p.add_argument("decisions", nargs="*", metavar="LINE=PN|skip",
                   help="line id and DigiKey part number, or skip; the line ids are in the resolve output")
    p.add_argument("--interactive", action="store_true", help="prompt for every needs_decision or ambiguous line")
    p.add_argument("--json", action="store_true", help="print the resulting BOM as JSON on stdout")
    p.set_defaults(func=run)
    return p


def _show(ln: Any, options: list[tuple[str, str]], index: int, total: int, style: Any) -> None:
    out("")
    out(style.bold(f"Line {ln.line.line_id} ({index} of {total}), {ln.cref or 'no designator'}: ")
        + render.status_text(ln.status, style))
    spec = " ".join(x for x in (ln.line.value, ln.line.footprint, ln.line.mpn, ln.line.description) if x)
    if spec:
        out(f"  {render.clip(spec, 90)}")
    out(f"  need {ln.needed_qty} ({render.clip(ln.reason, 100)})")
    if not options:
        out("  no candidates found; type a DigiKey part number, or s to skip")
    for n, (text, _pn) in enumerate(options, 1):
        out(f"  {n:>2}) {text}")


def ask(options: list[tuple[str, str]]) -> str | None:
    """Prompt until the answer is valid. Returns a part number, 'skip', '' (leave) or None (quit)."""
    while True:
        try:
            answer = input(f"choose [1-{len(options)}], PN, s, Enter, q (? for help): " if options
                           else "part number, s, Enter, q (? for help): ").strip()
        except EOFError:
            out("")
            return None
        low = answer.casefold()
        if low in ("q", "quit"):
            return None
        if low in ("", "l", "leave"):
            return ""
        if low in ("s", "skip"):
            return "skip"
        if low in ("?", "h", "help"):
            out(f"  {PROMPT_HELP}")
            continue
        if answer.isdigit():
            n = int(answer)
            if 1 <= n <= len(options):
                return options[n - 1][1]
            out(f"  no option {n}; choose 1-{len(options)}")
            continue
        if " " in answer:
            out("  a part number has no spaces; ? for help")
            continue
        return answer


def interactive(session_mod: Any, sid: str, ctx: Context, client: Any) -> Any:
    """Prompt for each open line, applying every answer at once. Returns the last BOM."""
    bom = session_mod.load_bom(sid, cache_dir=ctx.cache_dir)
    open_lines = [ln for ln in bom.lines if ln.status in NEEDS_ANSWER]
    if not open_lines:
        ctx.info("No line needs a decision.")
        return bom
    out(f"{len(open_lines)} line(s) need a decision. {PROMPT_HELP}.")
    for i, ln in enumerate(open_lines, 1):
        options = render.option_lines(ln)
        _show(ln, options, i, len(open_lines), ctx.style)
        answer = ask(options)
        if answer is None:
            break
        if answer == "":
            continue
        bom = session_mod.apply_decisions(sid, {ln.line.line_id: answer}, client=client, cache_dir=ctx.cache_dir)
        new = next(x for x in bom.lines if x.line.line_id == ln.line.line_id)
        what = f"{new.dk_pn} x {new.order_qty}" if new.dk_pn else render.clip(new.reason, 80)
        out(f"  -> line {new.line.line_id}: {render.status_text(new.status, ctx.style)} {what}")
        if not bom.complete:
            break
    return session_mod.load_bom(sid, cache_dir=ctx.cache_dir)


def run(args: argparse.Namespace, ctx: Context) -> int:
    from ...resolve import session

    decisions = parse_pairs(args.decisions)
    if not decisions and not args.interactive:
        raise UsageError("nothing to decide: give LINE=PART_NUMBER pairs or --interactive",
                         hint=f"run '{PROG} decide --help' for examples")
    if args.interactive and args.json:
        raise UsageError("--interactive cannot be combined with --json")
    client = common.get_client()
    bom = None
    if decisions:
        bom = session.apply_decisions(args.session, decisions, client=client, cache_dir=ctx.cache_dir)
    if args.interactive:
        if bom is not None and not bom.complete:
            pass  # a quota stop: report it below instead of prompting
        else:
            bom = interactive(session, args.session, ctx, client)
    assert bom is not None
    if args.interactive:
        out("")
    return conclude(bom, ctx, args.json)
