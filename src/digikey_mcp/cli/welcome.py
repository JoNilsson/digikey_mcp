"""The welcome screen and the one-line command summaries (single source of text).

The parser takes its sub-command help from COMMANDS, and the tests check that
every registered sub-command appears here, so a command without welcome text
fails the test suite.
"""

from __future__ import annotations

import textwrap

from .common import PROG, Style, package_version

#: (name, one-line summary), in workflow order
COMMANDS: tuple[tuple[str, str], ...] = (
    ("ingest", "Parse a BOM file and show how its columns were read."),
    ("resolve", "Resolve a BOM to DigiKey part numbers and quantities."),
    ("decide", "Answer lines that need a decision (generic, ambiguous)."),
    ("report", "Write the resolved BOM as markdown, CSV or JSON."),
    ("cart", "Build DigiKey cart links; nothing is submitted."),
    ("search", "Keyword search DigiKey, trimmed results."),
    ("part", "Show one part by DigiKey part number or MPN."),
    ("quota", "Show DigiKey API calls used and left."),
    ("sessions", "List saved resolution sessions with status counts."),
    ("serve", "Start the MCP server on stdio."),
    ("doctor", "Check the DigiKey credentials without printing them."),
    ("version", "Print the version."),
    ("welcome", "Show this overview."),
    ("help", "Show this overview, or help for one command."),
)

PURPOSE = (
    "Takes a bill of materials in and gives exact DigiKey part numbers, order "
    "quantities and cart links out. It never submits an order: you open the "
    "links in your own signed-in DigiKey session, pick the payment method and "
    "click buy."
)

STEPS: tuple[tuple[str, str], ...] = (
    ("Check how your BOM parses", f"{PROG} ingest bom.csv"),
    ("Resolve it against DigiKey", f"{PROG} resolve bom.csv --boards 2"),
    ("Answer the generic lines", f"{PROG} decide SESSION --interactive"),
    ("Review the result", f"{PROG} report SESSION --out bom.md"),
    ("Build the cart links", f"{PROG} cart SESSION"),
    ("Open the links in your DigiKey account, review the cart and pay yourself.", ""),
)

GLOBAL_FLAGS: tuple[tuple[str, str], ...] = (
    ("--version", "Print the version and exit."),
    ("--cache-dir DIR", "Keep cache and sessions in DIR."),
    ("-q, --quiet", "Hide progress and hints; results and errors still print."),
    ("--no-color", "No colour (also off when NO_COLOR is set or not a terminal)."),
    ("-h, --help", "Show this overview; 'COMMAND --help' shows one command."),
)

EXIT_CODES = (
    "0 done, 1 runtime error, 2 usage error, 3 lines still need decisions or "
    "the DigiKey quota ran out (resumable)."
)

STATE_DIR = "$XDG_CACHE_HOME/digikey-mcp (default ~/.cache/digikey-mcp)"


def summary(name: str) -> str:
    """One-line summary of a command; KeyError when the command has no welcome text."""
    return dict(COMMANDS)[name]


def _para(text: str, width: int, indent: int = 2) -> list[str]:
    pad = " " * indent
    return textwrap.wrap(text, width, initial_indent=pad, subsequent_indent=pad)


def _rows(rows: tuple[tuple[str, str], ...], width: int, first: int) -> list[str]:
    """Two-column rows: a label column of `first` characters, wrapped description."""
    lines: list[str] = []
    for label, text in rows:
        wrapped = textwrap.wrap(text, width - 2 - first) or [""]
        lines.append(f"  {label:<{first}}{wrapped[0]}".rstrip())
        lines += [f"  {'':<{first}}{w}" for w in wrapped[1:]]
    return lines


def render(width: int = 80, style: Style | None = None, cache_dir: str | None = None) -> str:
    """The welcome screen as plain ASCII text wrapped at `width`."""
    style = style or Style(False)
    width = min(width, 80)
    head = style.bold

    out = [head(f"DigiKey MCP {package_version()}"), "BOM in, DigiKey part numbers and cart links out.", ""]
    out += textwrap.wrap(PURPOSE, width)

    out += ["", head("TYPICAL WORKFLOW")]
    for number, (what, command) in enumerate(STEPS, 1):
        if command:
            out.append(f"  {number}. {what}:")
            out.append(f"       {command}")
        else:
            out += textwrap.wrap(
                what, width, initial_indent=f"  {number}. ", subsequent_indent="     "
            )

    out += ["", head("COMMANDS")]
    out += _rows(COMMANDS, width, 10)
    out += ["", f"  Details and examples: {PROG} COMMAND --help"]

    out += ["", head("GLOBAL FLAGS (before or after the command)")]
    out += _rows(GLOBAL_FLAGS, width, 17)

    out += ["", head("CREDENTIALS")]
    out += _para(
        "Set CLIENT_ID_FILE and CLIENT_SECRET_FILE to files holding your DigiKey "
        "client ID and secret (or CLIENT_ID and CLIENT_SECRET directly). They come "
        "from an app at developer.digikey.com, a different sign-up from the DigiKey "
        "shop account. Run 'digikey-mcp doctor' to check them.",
        width,
    )
    out += _para("Set DIGIKEY_SANDBOX=true to use the DigiKey sandbox instead of production.", width)

    out += ["", head("STATE")]
    out += _para(f"Cache, quota counters and sessions live in {cache_dir or STATE_DIR}.", width)

    out += ["", head("EXIT CODES")]
    out += _para(EXIT_CODES, width)
    return "\n".join(out) + "\n"
