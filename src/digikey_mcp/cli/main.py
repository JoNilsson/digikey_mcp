"""Entry point of the digikey-mcp command line."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from . import welcome
from .commands import cart, decide, doctor, info, ingest, part, quota, report, resolve, search, serve, sessions
from .common import (
    EXIT_OK,
    PROG,
    CliError,
    Context,
    UsageError,
    color_enabled,
    err,
    explain,
    package_version,
)
from .parsing import Parser, global_flags

#: command name -> function that adds it to the sub-parsers; the names must match welcome.COMMANDS
REGISTRY: dict[str, Callable[[Any], argparse.ArgumentParser]] = {
    "ingest": ingest.register,
    "resolve": resolve.register,
    "decide": decide.register,
    "report": report.register,
    "cart": cart.register,
    "search": search.register,
    "part": part.register,
    "quota": quota.register,
    "sessions": sessions.register,
    "serve": serve.register,
    "doctor": doctor.register,
    "version": info.register_version,
    "welcome": info.register_welcome,
    "help": info.register_help,
}


def build_parser() -> tuple[Parser, dict[str, argparse.ArgumentParser]]:
    """The full parser and a name -> sub-command parser map."""
    parser = Parser(
        prog=PROG,
        add_help=False,
        description="BOM in, DigiKey part numbers and cart links out. Run with no arguments for the overview.",
        parents=[global_flags(top=True)],
    )
    parser.add_argument("-h", "--help", action="store_true", dest="help_flag", help="show the overview")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=Parser)
    parsers = {name: REGISTRY[name](sub) for name, _summary in welcome.COMMANDS}
    return parser, parsers


def _context(args: argparse.Namespace) -> Context:
    cache = Path(args.cache_dir).expanduser() if args.cache_dir else None
    return Context(cache_dir=cache, quiet=args.quiet, color=color_enabled(args.no_color))


def _stdin_is_tty() -> bool:
    stdin = sys.stdin
    return bool(stdin is not None and getattr(stdin, "isatty", lambda: False)())


def dispatch(args: argparse.Namespace) -> int:
    ctx = _context(args)
    if args.version:
        print(f"{PROG} {package_version()}")
        return EXIT_OK
    if args.command is None:
        if args.help_flag or _stdin_is_tty():
            print(info.welcome_text(ctx).rstrip("\n"))
            return EXIT_OK
        # piped stdin: an MCP client launched us, keep the original behaviour
        return serve.run(argparse.Namespace(sandbox=False), ctx) or EXIT_OK
    if args.help_flag:
        args.parsers[args.command].print_help()
        return EXIT_OK
    return int(args.func(args, ctx) or EXIT_OK)


def _show_error(exc: BaseException) -> None:
    message, hint = explain(exc)
    err(f"{PROG}: error: {message}")
    if hint:
        err(f"hint: {hint}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit code (0 ok, 1 error, 2 usage, 3 blocked or resumable)."""
    parser, parsers = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
        args.parsers = parsers
        return dispatch(args)
    except SystemExit as exc:  # argparse printed help
        return exc.code if isinstance(exc.code, int) else EXIT_OK
    except KeyboardInterrupt:
        err(f"{PROG}: interrupted")
        return 130
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK
    except UsageError as exc:
        _show_error(exc)
        return exc.code
    except CliError as exc:
        _show_error(exc)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - the CLI never shows a traceback unless asked
        if os.environ.get("DIGIKEY_MCP_DEBUG"):
            raise
        _show_error(exc)
        if not isinstance(exc, (OSError, ValueError)) and type(exc).__module__.split(".")[0] != "digikey_mcp":
            err("hint: set DIGIKEY_MCP_DEBUG=1 to see the traceback")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
