"""Entry behaviour: no arguments, help, version, welcome and the registry."""

from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

import pytest

from digikey_mcp import cli
from digikey_mcp.cli import welcome
from digikey_mcp.cli.commands import serve

SRC = Path(__file__).resolve().parents[2] / "src"


class Stdin(io.StringIO):
    def __init__(self, tty: bool, text: str = ""):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(serve, "start_server", lambda: calls.append("started"))
    return calls


def test_no_args_on_a_terminal_shows_welcome(monkeypatch, served, capsys):
    monkeypatch.setattr(sys, "stdin", Stdin(True))
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "TYPICAL WORKFLOW" in out and "digikey-mcp resolve" in out
    assert served == []


def test_no_args_with_piped_stdin_starts_the_mcp_server(monkeypatch, served, capsys):
    monkeypatch.setattr(sys, "stdin", Stdin(False))
    assert cli.main([]) == 0
    assert served == ["started"]
    assert capsys.readouterr().out == ""


def test_serve_command_starts_server_and_sandbox_sets_env(monkeypatch, served):
    monkeypatch.delenv("DIGIKEY_SANDBOX", raising=False)
    assert cli.main(["serve", "--sandbox"]) == 0
    assert served == ["started"]
    import os

    assert os.environ["DIGIKEY_SANDBOX"] == "true"
    monkeypatch.delenv("DIGIKEY_SANDBOX")


def test_serve_rejects_cache_dir(served, capsys):
    assert cli.main(["--cache-dir", "x", "serve"]) == 2
    assert served == []
    assert "--cache-dir" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--help"], ["-h"], ["help"], ["welcome"]])
def test_overview_forms_print_the_same_text(argv, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Stdin(False))
    assert cli.main(argv) == 0
    assert capsys.readouterr().out.rstrip("\n") == welcome.render(80).rstrip("\n")


def test_help_command_describes_one_command(capsys):
    assert cli.main(["help", "resolve"]) == 0
    assert "--boards" in capsys.readouterr().out
    assert cli.main(["help", "nonesuch"]) == 2


def test_version_forms(capsys):
    assert cli.main(["--version"]) == 0
    a = capsys.readouterr().out
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out == a
    assert re.fullmatch(r"digikey-mcp \d+\.\d+\.\d+\S*\n", a)


def test_python_dash_m_runs_the_cli():
    import subprocess

    env = {**os.environ, "PYTHONPATH": str(SRC)}
    done = subprocess.run([sys.executable, "-m", "digikey_mcp", "version"], capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL, timeout=60)
    assert done.returncode == 0
    assert done.stdout.startswith("digikey-mcp ")
    welcome_run = subprocess.run([sys.executable, "-m", "digikey_mcp", "welcome"], capture_output=True, text=True,
                                 env=env, stdin=subprocess.DEVNULL, timeout=60)
    assert "TYPICAL WORKFLOW" in welcome_run.stdout


def test_console_script_points_at_the_cli():
    import tomllib

    data = tomllib.loads((SRC.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["digikey-mcp"] == "digikey_mcp.cli:main"


def test_start_server_runs_the_fastmcp_server(monkeypatch):
    from digikey_mcp import server

    calls = []
    monkeypatch.setattr(server, "main", lambda: calls.append("main"))
    serve.start_server()
    assert calls == ["main"]
