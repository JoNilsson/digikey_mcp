"""Start banner: content, stderr only, quiet mode, no secret values."""

from __future__ import annotations

import io

import pytest

from digikey_mcp import banner, server

VARS = ("CLIENT_ID", "CLIENT_SECRET", "CLIENT_ID_FILE", "CLIENT_SECRET_FILE", "DIGIKEY_SANDBOX",
        "DIGIKEY_MCP_QUIET", "DIRENV_DIR")
EXE = "/opt/bin/digikey-mcp"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)


def test_stdio_without_credentials():
    text = banner.render("0.2.0", env={}, argv0=EXE)
    assert "server" in text and "DigiKey MCP Server 0.2.0" in text
    flat = " ".join(text.split())
    assert "stdio. A stdio server has no address" in flat
    assert "this process serves only the client that launched it" in flat
    assert "-e CLIENT_ID_FILE=/home/you/.config/digikey/client_id" in text
    assert f"-e CLIENT_SECRET_FILE=/home/you/.config/digikey/client_secret -- {EXE} serve" in text
    assert "replace the /home/you paths" in text
    assert "missing: CLIENT_ID not set; CLIENT_SECRET not set" in text
    assert "production (https://api.digikey.com)" in text
    assert "Waiting for an MCP client" not in text


def test_stdio_with_credentials_shows_configured_paths_not_values(tmp_path):
    cid, csec = tmp_path / "client_id", tmp_path / "client_secret"
    cid.write_text("id-value-XYZZY\n", encoding="utf-8")
    csec.write_text("secret-value-XYZZY\n", encoding="utf-8")
    env = {"CLIENT_ID_FILE": str(cid), "CLIENT_SECRET_FILE": str(csec), "DIGIKEY_SANDBOX": "true"}
    text = banner.render("0.2.0", env=env, argv0=EXE)
    assert f"-e CLIENT_ID_FILE={cid}" in text and f"-e CLIENT_SECRET_FILE={csec}" in text
    assert "replace" not in text and "/home/you" not in text
    assert "credentials ok" in text
    assert "sandbox (https://sandbox-api.digikey.com)" in text
    assert "XYZZY" not in text


def test_waiting_line_only_when_started_by_hand():
    text = banner.render("0.2.0", env={}, argv0=EXE, interactive=True)
    assert "Waiting for an MCP client on stdin; started by hand this serves nobody. Ctrl+C to stop." in " ".join(text.split())
    assert "digikey-mcp doctor, digikey-mcp resolve <bom.csv>" in " ".join(text.split())


def test_dev_shell_uses_direnv_exec(tmp_path):
    repo = banner.Path(banner.__file__).resolve().parents[2]
    command, _ = banner.registration({"DIRENV_DIR": f"-{repo}"}, argv0=EXE)
    assert command.endswith(f"-- direnv exec {repo} digikey-mcp serve")


def test_direnv_of_another_project_is_ignored(tmp_path):
    command, _ = banner.registration({"DIRENV_DIR": f"-{tmp_path}"}, argv0=EXE)
    assert command.endswith(f"-- {EXE} serve")


def test_module_run_falls_back_to_python_dash_m():
    command, _ = banner.registration({}, argv0="/x/digikey_mcp/__main__.py")
    assert " -m digikey_mcp serve" in command


def test_http_transport_prints_url_and_matching_command():
    text = banner.render("0.2.0", "http", "127.0.0.1", 8000, env={}, argv0=EXE)
    assert "http://127.0.0.1:8000/mcp" in text
    assert "claude mcp add --transport http digikey http://127.0.0.1:8000/mcp" in text
    assert "no address" not in text
    sse = banner.render("0.2.0", "sse", "0.0.0.0", 9000, env={}, argv0=EXE)
    assert "claude mcp add --transport sse digikey http://0.0.0.0:9000/sse" in sse


def test_main_prints_banner_on_stderr_only(monkeypatch, capsys):
    monkeypatch.setattr(server.mcp, "run", lambda **kw: None)
    server.main()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "DigiKey MCP Server" in captured.err and "stdio" in captured.err


def test_main_is_quiet_with_the_variable_but_still_warns(monkeypatch, capsys):
    monkeypatch.setenv("DIGIKEY_MCP_QUIET", "1")
    monkeypatch.setattr(server.mcp, "run", lambda **kw: None)
    server.main()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stdio" not in captured.err
    assert captured.err.count("\n") == 1 and "credentials missing" in captured.err


def test_main_is_silent_when_quiet_and_credentials_ok(monkeypatch, capsys):
    monkeypatch.setenv("DIGIKEY_MCP_QUIET", "1")
    monkeypatch.setenv("CLIENT_ID", "i")
    monkeypatch.setenv("CLIENT_SECRET", "s")
    monkeypatch.setattr(server.mcp, "run", lambda **kw: None)
    server.main()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_waiting_line_needs_both_streams_on_a_terminal(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    both = Tty()
    monkeypatch.setattr(banner.sys, "stdin", Tty())
    monkeypatch.setattr(banner.sys, "stderr", both)
    banner.print_banner("0.2.0")
    assert "Waiting for an MCP client" in both.getvalue()
    piped = io.StringIO()
    monkeypatch.setattr(banner.sys, "stderr", piped)
    banner.print_banner("0.2.0")
    assert "Waiting for an MCP client" not in piped.getvalue()
