"""The start banner the server prints on stderr (stdout is the MCP protocol on stdio).

Replaces FastMCP's own banner. Reads credentials only through `check_credentials`
and never shows a value. Importing this module pulls in neither fastmcp nor the CLI.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import textwrap
from collections.abc import Mapping
from pathlib import Path

from .client import (
    PRODUCTION_BASE_URL,
    SANDBOX_BASE_URL,
    check_credentials,
    sandbox_enabled,
)

SERVER_NAME = "DigiKey MCP Server"
PROG = "digikey-mcp"
LABEL = 12
WIDTH = 100
PLACEHOLDERS = {
    "CLIENT_ID_FILE": "/home/you/.config/digikey/client_id",
    "CLIENT_SECRET_FILE": "/home/you/.config/digikey/client_secret",
}
#: URL path FastMCP serves for each network transport
HTTP_PATHS = {"http": "/mcp", "streamable-http": "/mcp", "sse": "/sse"}
STDIO_NOTE = (
    "stdio. A stdio server has no address: each MCP client starts its own copy; "
    "this process serves only the client that launched it."
)
WAITING = (
    "Waiting for an MCP client on stdin; started by hand this serves nobody. Ctrl+C to stop. "
    f"Try: {PROG} doctor, {PROG} resolve <bom.csv>"
)


def quiet(env: Mapping[str, str] | None = None) -> bool:
    """True when DIGIKEY_MCP_QUIET is set to anything non-empty."""
    return bool((os.environ if env is None else env).get("DIGIKEY_MCP_QUIET"))


def launch_command(env: Mapping[str, str] | None = None, argv0: str | None = None) -> list[str]:
    """How a client starts this server: `direnv exec REPO digikey-mcp` from a dev shell, else the executable path."""
    env = os.environ if env is None else env
    argv0 = sys.argv[0] if argv0 is None else argv0
    direnv_dir = (env.get("DIRENV_DIR") or "").lstrip("-")
    if direnv_dir and Path(__file__).resolve().is_relative_to(Path(direnv_dir).resolve()):
        return ["direnv", "exec", direnv_dir, PROG, "serve"]
    if not argv0 or argv0.endswith(".py"):
        return [sys.executable, "-m", "digikey_mcp", "serve"]
    exe = argv0 if os.sep in argv0 else shutil.which(argv0) or argv0
    return [os.path.abspath(exe), "serve"]


def registration(env: Mapping[str, str] | None = None, argv0: str | None = None) -> tuple[str, bool]:
    """The `claude mcp add` line and whether it still holds placeholder paths."""
    env = os.environ if env is None else env
    words = ["claude", "mcp", "add", "digikey"]
    placeholder = False
    for name, example in PLACEHOLDERS.items():
        path = env.get(name)
        placeholder = placeholder or not path
        words += ["-e", f"{name}={path or example}"]
    quoted = " ".join(shlex.quote(w) for w in words)
    return f"{quoted} -- {shlex.join(launch_command(env, argv0))}", placeholder


def _rows(rows: list[tuple[str, str]]) -> list[str]:
    lines: list[str] = []
    for label, text in rows:
        # commands stay on one line so they can be copied
        wrapped = [text] if label == "register" else textwrap.wrap(text, WIDTH - LABEL - 2) or [""]
        lines.append(f"  {label:<{LABEL}}{wrapped[0]}")
        lines += [f"  {'':<{LABEL}}{w}" for w in wrapped[1:]]
    return lines


def render(
    version: str,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    env: Mapping[str, str] | None = None,
    argv0: str | None = None,
    interactive: bool = False,
) -> str:
    """The banner text: terse aligned rows, plus the waiting line when a human started it on a terminal."""
    env = os.environ if env is None else env
    problems = [c.describe() for c in check_credentials(env) if not c.ok]
    sandbox = sandbox_enabled(env)
    rows: list[tuple[str, str]] = [("server", f"{SERVER_NAME} {version}")]
    if transport in HTTP_PATHS:
        url = f"http://{host}:{port}{HTTP_PATHS[transport]}"
        rows += [
            ("transport", f"{transport}, listening on {host}:{port}"),
            ("url", url),
            ("register", f"claude mcp add --transport {'sse' if transport == 'sse' else 'http'} digikey {url}"),
        ]
    else:
        command, placeholder = registration(env, argv0)
        rows += [
            ("transport", STDIO_NOTE),
            ("register", command),
        ]
        if placeholder:
            rows.append(("", "replace the /home/you paths with your own credential files"))
    rows += [
        ("credentials", "ok" if not problems else f"missing: {'; '.join(problems)}; run '{PROG} doctor'"),
        ("mode", f"{'sandbox' if sandbox else 'production'} ({SANDBOX_BASE_URL if sandbox else PRODUCTION_BASE_URL})"),
    ]
    lines = _rows(rows)
    if interactive:
        lines += textwrap.wrap(WAITING, WIDTH)
    return "\n".join(lines) + "\n"


def print_banner(version: str, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    """Write the banner to stderr; nothing goes to stdout."""
    stdin = sys.stdin
    human = sys.stderr.isatty() and stdin is not None and stdin.isatty()
    text = render(version, transport, host, port, interactive=human and transport == "stdio")
    print(text, end="", file=sys.stderr, flush=True)
