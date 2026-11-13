"""argparse setup shared by every command: parser class, help formatter, global flags."""

from __future__ import annotations

import argparse
import shutil
import sys
import textwrap
from typing import Any

from . import welcome
from .common import PROG, UsageError


def help_width() -> int:
    return max(min(shutil.get_terminal_size().columns - 2, 78), 40)


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Keeps description and epilog layout and states every option's default."""

    def __init__(self, prog: str, **kwargs: Any):
        kwargs.setdefault("width", help_width())
        kwargs.setdefault("max_help_position", 28)
        super().__init__(prog, **kwargs)

    def _get_help_string(self, action: argparse.Action) -> str | None:
        text = action.help or ""
        if (
            not action.option_strings
            or action.default is argparse.SUPPRESS
            or "(default" in text
            or action.dest in ("help", "version")
        ):
            return text
        default = action.default
        if default is None:
            shown = "none"
        elif default is False:
            shown = "off"
        elif default is True:
            shown = "on"
        else:
            shown = str(default)
        return f"{text} (default: {shown})"


class Parser(argparse.ArgumentParser):
    """Reports usage errors as UsageError so main() prints one clean line and exits 2."""

    def __init__(self, *args: Any, **kwargs: Any):
        if sys.version_info >= (3, 14):
            kwargs.setdefault("color", False)
        kwargs.setdefault("formatter_class", HelpFormatter)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> Any:
        raise UsageError(message, hint=f"run '{self.prog} --help' for usage")


def global_flags(top: bool) -> argparse.ArgumentParser:
    """The flags accepted before and after the command.

    On the top-level parser they carry real defaults; on sub-commands the default is
    suppressed so a value given before the command is not overwritten.
    """
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_argument_group("global flags (also accepted before the command)")
    sup = argparse.SUPPRESS

    def default(value: Any) -> Any:
        return value if top else sup

    note = "" if top else " (default: none)"
    group.add_argument("--cache-dir", metavar="DIR", default=default(None),
                        help="keep the response cache, quota counters and sessions in DIR instead of "
                             "$XDG_CACHE_HOME/digikey-mcp" + note)
    group.add_argument("-q", "--quiet", action="store_true", default=default(False),
                        help="hide progress and hints; results, errors and the resume line still print"
                             + ("" if top else " (default: off)"))
    group.add_argument("--no-color", action="store_true", default=default(False),
                        help="never colour output (colour is also off when NO_COLOR is set or stdout "
                             "is not a terminal)" + ("" if top else " (default: off)"))
    return parent


def command(subparsers: Any, name: str, description: str, epilog: str, **kwargs: Any) -> Parser:
    """Add one sub-command; its one-line help comes from the welcome text."""
    width = help_width()
    blocks = [b if b.startswith("Examples:") else textwrap.fill(b, width) for b in epilog.split("\n\n")]
    return subparsers.add_parser(
        name,
        help=welcome.summary(name),
        description=textwrap.fill(description, width),
        epilog="\n\n".join(blocks),
        parents=[global_flags(top=False)],
        **kwargs,
    )


def examples(*pairs: tuple[str, str]) -> str:
    """Epilog text: `Examples:` followed by command lines with a comment each."""
    lines = ["Examples:"]
    for text, comment in pairs:
        lines.append(f"  {PROG} {text}")
        lines.append(f"      {comment}")
    return "\n".join(lines)
