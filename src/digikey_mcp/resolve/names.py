"""Manufacturer matching and line identity."""

from __future__ import annotations

import difflib
import re

from ..ingest import BomLine

_SUFFIX_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "gmbh",
    "ag", "sa", "plc", "usa", "us", "na", "kg", "bv", "nv", "se",
}

#: canonical name -> aliases (all compared after `_normalise`)
ALIASES: dict[str, tuple[str, ...]] = {
    "texas instruments": ("ti", "texas instruments incorporated", "burr brown", "national semiconductor"),
    "stmicroelectronics": ("st", "stmicro", "st microelectronics", "st micro"),
    "nxp": ("nxp semiconductors", "nxp usa", "freescale", "freescale semiconductor"),
    "microchip": ("microchip technology", "atmel", "microsemi", "sst"),
    "analog devices": ("adi", "analog devices inc", "maxim integrated", "maxim", "linear technology", "linear"),
    "onsemi": ("on semiconductor", "on semi", "fairchild", "fairchild semiconductor"),
    "infineon": ("infineon technologies", "cypress", "cypress semiconductor", "international rectifier", "ir"),
    "renesas": ("renesas electronics", "intersil", "idt", "integrated device technology"),
    "murata": ("murata electronics", "murata manufacturing"),
    "samsung electro mechanics": ("samsung", "semco", "samsung electro-mechanics"),
    "tdk": ("tdk corporation", "epcos", "tdk epcos"),
    "vishay": ("vishay dale", "vishay siliconix", "vishay semiconductor", "vishay general semiconductor", "vishay beyschlag"),
    "te connectivity": ("te", "tyco electronics", "amp", "te connectivity amp", "te connectivity amp connectors"),
    "wurth elektronik": ("we", "wuerth elektronik", "würth elektronik", "wurth"),
    "diodes": ("diodes incorporated", "diodes inc", "zetex"),
    "yageo": ("yageo america", "yageo corporation"),
    "panasonic": ("panasonic electronic components", "panasonic industrial"),
    "kemet": ("kemet corporation", "yageo kemet"),
    "littelfuse": ("littelfuse inc",),
    "bourns": ("bourns inc",),
    "rohm": ("rohm semiconductor",),
    "nexperia": ("nexperia usa", "nexperia b v"),
    "molex": ("molex llc",),
    "amphenol": ("amphenol icc", "amphenol fci", "fci", "amphenol cs"),
    "espressif": ("espressif systems",),
    "lite-on": ("liteon", "lite on", "lite-on inc"),
}


def _normalise(name: str) -> str:
    n = name.casefold().replace("&", " and ")
    n = re.sub(r"[^\w\s]", " ", n)
    words = [w for w in n.split() if w not in _SUFFIX_WORDS]
    return " ".join(words)


_CANON: dict[str, str] = {}
for _canon, _aliases in ALIASES.items():
    _CANON[_normalise(_canon)] = _canon
    for _a in _aliases:
        _CANON[_normalise(_a)] = _canon


def canonical_manufacturer(name: str | None) -> str:
    """Canonical manufacturer key: alias table first, else the normalised name."""
    if not name:
        return ""
    n = _normalise(name)
    return _CANON.get(n, n)


def manufacturers_match(a: str | None, b: str | None) -> bool:
    """True when two manufacturer names plausibly denote the same company."""
    ca, cb = canonical_manufacturer(a), canonical_manufacturer(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long_ = sorted((ca, cb), key=len)
    if len(short) >= 4 and long_.startswith(short + " "):
        return True
    return difflib.SequenceMatcher(None, ca, cb).ratio() >= 0.88


def norm_mpn(mpn: str | None) -> str:
    return re.sub(r"\s+", "", mpn or "").casefold()


def identity_key(line: BomLine) -> str:
    """Stable identity for a BOM line across revisions: MPN, else DigiKey PN, else value+footprint."""
    if line.mpn:
        return "mpn:" + norm_mpn(line.mpn)
    if line.supplier_pn:
        return "dk:" + norm_mpn(line.supplier_pn)
    value = re.sub(r"\s+", "", line.value or "").casefold()
    footprint = re.sub(r"\s+", "", line.footprint or line.package or "").casefold()
    if value or footprint:
        return f"vf:{line.category_hint}|{value}|{footprint}"
    return "ref:" + ",".join(line.refdes) if line.refdes else f"line:{line.line_id}"
