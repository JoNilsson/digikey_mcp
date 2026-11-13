"""Offline fixtures for the command line tests: a fake DigiKey client and a cache in tmp_path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "src", ROOT / "tests" / "resolve"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from resolve_fakes import FakeDigiKey, product, resistor_10k, variation  # noqa: E402

from digikey_mcp import cli  # noqa: E402
from digikey_mcp.cli import common  # noqa: E402

HEADER = "Reference,Qty,Value,Footprint,MPN,Manufacturer\n"
BOM = (
    HEADER
    + "R1 R2 R3,3,10k,R_0603_1608Metric,RC0603FR-0710KL,Yageo\n"
    + "U1,1,LM358,SOIC-8,LM358DR,TI\n"
    + "C1 C2,2,100nF,C_0603_1608Metric,,\n"
    + "R9,1,1k,R_0603_1608Metric,,\n"
)
CAP_PN = "1276-1000-1-ND"


def lm358():
    return product("LM358DR", "Texas Instruments",
                   [variation("296-1014-1-ND", "Cut Tape (CT)", breaks=((1, 0.5), (100, 0.3)))])


def capacitors():
    return [
        product("CL10B104KB8NNNC", "Samsung Electro-Mechanics",
                [variation(CAP_PN, "Cut Tape (CT)", breaks=((1, 0.1), (100, 0.02)))],
                desc="CAP CER 0.1UF 50V X7R 0603", params={"Package / Case": "0603"}),
        product("GRM188R71H104KA93D", "Murata Electronics",
                [variation("490-1532-1-ND", "Cut Tape (CT)", breaks=((1, 0.1), (100, 0.03)))],
                desc="CAP CER 0.1UF 50V X7R 0603", params={"Package / Case": "0603"}),
    ]


@pytest.fixture
def fake(monkeypatch):
    """A stocked fake client, installed as the CLI's client."""
    client = FakeDigiKey()
    caps = capacitors()
    client.add(resistor_10k(), lm358(), *caps)
    client.searches["100nF 0603 capacitor"] = caps
    client.searches["1kohm 0603 resistor"] = []
    monkeypatch.setattr(common, "get_client", lambda: client)
    return client


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


@pytest.fixture
def run(cache, capsys):
    """run(*argv) -> (exit code, stdout, stderr) against the temporary cache."""

    def go(*argv, cache_dir=True):
        args = list(argv)
        if cache_dir:
            args = ["--cache-dir", str(cache), *args]
        code = cli.main(args)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return go


@pytest.fixture
def bom_file(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text(BOM, encoding="utf-8")
    return path
