"""welcome, help and version."""

from __future__ import annotations

import argparse
import shutil
from typing import Any

from .. import welcome
from ..common import PROG, Context, UsageError, out, package_version
from ..parsing import command, examples


def welcome_text(ctx: Context) -> str:
    cache = str(ctx.cache_dir) if ctx.cache_dir else None
    return welcome.render(min(shutil.get_terminal_size().columns, 80), ctx.style, cache)


def register_welcome(sub: Any) -> argparse.ArgumentParser:
    p = command(sub, "welcome",
                "Print the overview: purpose, workflow, commands, flags and setup. Running digikey-mcp with "
                "no arguments in a terminal prints the same text.",
                examples(("welcome", "show the overview"), ("welcome --no-color", "plain text for a log or a pipe")))
    p.set_defaults(func=run_welcome)
    return p


def run_welcome(args: argparse.Namespace, ctx: Context) -> int:
    out(welcome_text(ctx).rstrip("\n"))
    return 0


def register_version(sub: Any) -> argparse.ArgumentParser:
    p = command(sub, "version", "Print the version of digikey-mcp.",
                examples(("version", "print the version"), ("--version", "the same, as a global flag")))
    p.set_defaults(func=run_version)
    return p


def run_version(args: argparse.Namespace, ctx: Context) -> int:
    out(f"{PROG} {package_version()}")
    return 0


def register_help(sub: Any) -> argparse.ArgumentParser:
    p = command(sub, "help", "Show the overview, or the help of one command.",
                examples(("help", "the overview"), ("help resolve", "same as: resolve --help")))
    p.add_argument("topic", nargs="?", metavar="COMMAND", help="command to describe; omit for the overview")
    p.set_defaults(func=run_help)
    return p


def run_help(args: argparse.Namespace, ctx: Context) -> int:
    if not args.topic:
        out(welcome_text(ctx).rstrip("\n"))
        return 0
    parser = args.parsers.get(args.topic)
    if parser is None:
        raise UsageError(f"unknown command {args.topic!r}", hint=f"run '{PROG} help' for the list")
    parser.print_help()
    return 0
