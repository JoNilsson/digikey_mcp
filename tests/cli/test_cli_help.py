"""Help text: every sub-command documents its flags and has welcome text."""

from __future__ import annotations

import re

import pytest

from digikey_mcp import cli
from digikey_mcp.cli import common, welcome
from digikey_mcp.cli.main import REGISTRY, build_parser

PARSER, PARSERS = build_parser()
NAMES = sorted(PARSERS)
EXPECTED = {"serve", "ingest", "resolve", "decide", "report", "cart", "search", "part", "quota",
            "sessions", "version", "welcome", "help"}


def long_flags(parser):
    return sorted({o for a in parser._actions for o in a.option_strings if o.startswith("--")})


def test_expected_commands_exist():
    assert EXPECTED <= set(PARSERS)


@pytest.mark.parametrize("name", NAMES)
def test_subcommand_help_exits_zero_and_mentions_every_flag(name, capsys):
    assert cli.main([name, "--help"]) == 0
    text = capsys.readouterr().out
    for flag in long_flags(PARSERS[name]):
        assert flag in text, f"{name} --help does not mention {flag}"
    assert "Examples:" in text
    assert len(re.findall(r"^  digikey-mcp \S", text, re.M)) >= 2, f"{name} needs worked examples"
    assert "default:" in text or not [a for a in PARSERS[name]._actions if a.option_strings and a.dest != "help"]


@pytest.mark.parametrize("name", NAMES)
def test_short_help_flag_works(name, capsys):
    assert cli.main([name, "-h"]) == 0
    assert capsys.readouterr().out.startswith("usage: digikey-mcp " + name)


def test_help_text_wraps_inside_80_columns(capsys):
    for name in NAMES:
        cli.main([name, "--help"])
        for line in capsys.readouterr().out.splitlines():
            assert len(line) <= 80, f"{name}: {line!r}"


def test_every_registered_command_has_welcome_text():
    text = welcome.render(80)
    listed = set(re.findall(r"^  (\w+) ", text.split("COMMANDS", 1)[1], re.M))
    assert set(PARSERS) <= listed
    assert set(REGISTRY) == {n for n, _ in welcome.COMMANDS} == set(PARSERS)
    for name, parser in PARSERS.items():
        summary = welcome.summary(name)
        assert summary and summary in text


def test_command_without_welcome_text_is_rejected():
    with pytest.raises(KeyError):
        welcome.summary("brand-new-command")


def test_welcome_content():
    text = welcome.render(80)
    for needle in ("TYPICAL WORKFLOW", "COMMANDS", "GLOBAL FLAGS", "CREDENTIALS", "STATE", "CLIENT_ID_FILE",
                   "CLIENT_SECRET_FILE", "DIGIKEY_SANDBOX", ".cache/digikey-mcp", "never submits an order",
                   "--cache-dir", "--quiet", "--no-color", "--version"):
        assert needle in text, needle
    for command in ("ingest", "resolve", "decide", "report", "cart"):
        assert re.search(rf"^\s+digikey-mcp {command}\b", text, re.M)
    assert max(len(line) for line in text.splitlines()) <= 80
    assert text.isascii() and "\x1b" not in text


def test_welcome_colour_rules(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    assert common.color_enabled(False, Tty())
    assert not common.color_enabled(True, Tty())
    assert not common.color_enabled(False, object())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not common.color_enabled(False, Tty())
    coloured = welcome.render(80, common.Style(True))
    assert "\x1b[1m" in coloured
    assert max(len(re.sub(r"\x1b\[[0-9;]*m", "", ln)) for ln in coloured.splitlines()) <= 80


def test_welcome_command_is_plain_when_piped(capsys, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    cli.main(["welcome"])
    assert "\x1b" not in capsys.readouterr().out


def test_global_flags_work_before_and_after_the_command(cache, capsys):
    assert cli.main(["--cache-dir", str(cache), "sessions", "--json"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["sessions", "--json", "--cache-dir", str(cache)]) == 0
    assert capsys.readouterr().out == first
    assert cache.is_dir()
