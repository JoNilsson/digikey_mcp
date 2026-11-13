"""Shared pieces of the command line: exit codes, errors, colour and output helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
#: a resolve or decide run ended with blocking lines or stopped on the quota
EXIT_BLOCKED = 3

PROG = "digikey-mcp"

CREDENTIAL_HINT = (
    "set CLIENT_ID_FILE and CLIENT_SECRET_FILE to files holding your DigiKey client ID "
    f"and secret (or CLIENT_ID and CLIENT_SECRET); see '{PROG} welcome'"
)


class CliError(Exception):
    """A failure to show as one error line plus an optional hint."""

    code = EXIT_ERROR

    def __init__(self, message: str, hint: str | None = None, code: int | None = None):
        super().__init__(message)
        self.hint = hint
        if code is not None:
            self.code = code


class UsageError(CliError):
    """The command line itself is wrong."""

    code = EXIT_USAGE


class Style:
    """ANSI colour when enabled, plain text otherwise."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)


def color_enabled(no_color_flag: bool, stream: Any = None) -> bool:
    """Colour only on a terminal, with NO_COLOR unset and no --no-color."""
    stream = sys.stdout if stream is None else stream
    if no_color_flag or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


@dataclass
class Context:
    """Global flags, resolved once in main()."""

    cache_dir: Path | None = None
    quiet: bool = False
    color: bool = False

    @property
    def style(self) -> Style:
        return Style(self.color)

    def info(self, text: str = "") -> None:
        """Progress and hints: stderr, hidden by --quiet."""
        if not self.quiet:
            print(text, file=sys.stderr)


def out(text: str = "") -> None:
    print(text)


def err(text: str = "") -> None:
    print(text, file=sys.stderr)


def get_client() -> Any:
    """The DigiKey client, built from the environment (credentials are read here)."""
    from ..client import DigiKeyClient

    return DigiKeyClient.from_env()


def package_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("digikey-mcp")
        except PackageNotFoundError:
            pass
    except ImportError:  # pragma: no cover
        pass
    try:
        import tomllib

        data = tomllib.loads((Path(__file__).resolve().parents[3] / "pyproject.toml").read_text("utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, ValueError):
        return "unknown"


def explain(exc: BaseException) -> tuple[str, str | None]:
    """One-line message and hint for an exception raised by the underlying code."""
    text = str(exc) or type(exc).__name__
    if isinstance(exc, CliError):
        return text, exc.hint

    from ..client import DigiKeyAuthError, DigiKeyConfigError, DigiKeyError
    from ..ingest import IngestError, MissingDependencyError
    from ..resolve.models import ResolveError

    if isinstance(exc, DigiKeyConfigError):
        return text, CREDENTIAL_HINT
    if isinstance(exc, DigiKeyAuthError):
        return text, (
            "check the client ID and secret, and that DIGIKEY_SANDBOX matches the app "
            "(sandbox credentials only work with DIGIKEY_SANDBOX=true)"
        )
    if isinstance(exc, DigiKeyError):
        if exc.status == 429 or exc.status == 403:
            return text, f"DigiKey is limiting calls; run '{PROG} quota' and retry later"
        return text, None
    if isinstance(exc, ResolveError):
        if "unknown session" in text:
            return text, f"run '{PROG} sessions' to list saved sessions"
        return text, None
    if isinstance(exc, MissingDependencyError):
        return text, "install the optional extras: pip install 'digikey-mcp[ingest]'"
    if isinstance(exc, IngestError):
        return text, None
    if isinstance(exc, FileNotFoundError):
        return f"file not found: {exc.filename}" if exc.filename else text, None
    if isinstance(exc, OSError) and exc.strerror:
        where = f" ({exc.filename})" if exc.filename else ""
        return f"{exc.strerror}{where}", None
    return text, None
