"""Category inference, DNP detection and footprint shortening."""

from __future__ import annotations

import re

from .refdes import refdes_prefix
from .values import normalise_value

PREFIX_CATEGORY = {
    "R": "resistor", "RN": "resistor", "RES": "resistor", "RP": "resistor",
    "C": "capacitor", "CP": "capacitor", "CAP": "capacitor",
    "L": "inductor", "IND": "inductor",
    "J": "connector", "P": "connector", "CN": "connector", "CON": "connector",
    "JP": "connector", "XS": "connector", "XP": "connector",
    "U": "ic", "IC": "ic",
    "D": "diode", "CR": "diode", "ZD": "diode",
    "LED": "led", "LD": "led", "DS": "led",
    "Q": "transistor", "T": "other",
    "Y": "crystal", "XTAL": "crystal",
    "SW": "switch", "S": "switch", "BTN": "switch",
    "F": "fuse", "FU": "fuse",
    "FID": "fiducial", "FD": "fiducial",
    "TP": "test_point", "TPT": "test_point",
    "H": "mounting_hole", "MH": "mounting_hole", "MP": "mounting_hole",
    "FB": "other", "K": "other", "RV": "other", "RT": "other", "BT": "other",
    "B": "other", "X": "other", "SP": "other", "TH": "other",
}

#: Prefixes the free-text parser accepts as designators.
KNOWN_PREFIXES = frozenset(PREFIX_CATEGORY)

#: categories that carry spare-part overage; a designator prefix alone does not set them on a line
#: with an explicit part number
_OVERAGE_CATEGORIES = frozenset({"resistor", "capacitor", "inductor", "led", "diode", "transistor"})

_STRONG_TEXT = [
    (re.compile(r"fiducial", re.I), "fiducial"),
    (re.compile(r"mounting[\s_-]*hole|mtg[\s_-]*hole", re.I), "mounting_hole"),
    (re.compile(r"test[\s_-]*(?:point|pad)", re.I), "test_point"),
]
_FOOTPRINT_LIB = [
    (re.compile(r"^resistor", re.I), "resistor"),
    (re.compile(r"^capacitor", re.I), "capacitor"),
    (re.compile(r"^inductor", re.I), "inductor"),
    (re.compile(r"^connector", re.I), "connector"),
    (re.compile(r"^package_", re.I), "ic"),
    (re.compile(r"^led_", re.I), "led"),
    (re.compile(r"^diode", re.I), "diode"),
    (re.compile(r"^crystal", re.I), "crystal"),
    (re.compile(r"^fuse", re.I), "fuse"),
    (re.compile(r"^button_switch", re.I), "switch"),
]
_DESCRIPTION_KEYWORDS = [
    (re.compile(r"resistor|\bres\b", re.I), "resistor"),
    (re.compile(r"capacitor|\bcap\b", re.I), "capacitor"),
    (re.compile(r"inductor|choke", re.I), "inductor"),
    (re.compile(r"connector|header|receptacle|terminal block|\bjack\b|socket", re.I), "connector"),
    (re.compile(r"\bled\b", re.I), "led"),
    (re.compile(r"diode|rectifier", re.I), "diode"),
    (re.compile(r"transistor|mosfet|\bbjt\b", re.I), "transistor"),
    (re.compile(r"crystal|oscillator", re.I), "crystal"),
    (re.compile(r"\bfuse\b", re.I), "fuse"),
    (re.compile(r"microcontroller|\bmcu\b|regulator|\bldo\b|amplifier|op[\s-]?amp|transceiver|converter", re.I), "ic"),
]
_UNIT_CATEGORY = {"ohm": "resistor", "F": "capacitor", "H": "inductor"}

DNP_MARKER = re.compile(
    r"(?<![A-Za-z0-9])(?:dnp|dnf|dni|dnl|"
    r"do[\s_-]*not[\s_-]*(?:populate|place|fit|install|mount|stuff|load)|"
    r"not[\s_-]*(?:fitted|populated|installed|mounted|placed|stuffed|loaded)|"
    r"no[\s_-]*(?:pop|stuff|fit)|unpopulated|nopop)(?![A-Za-z0-9])",
    re.I,
)
TRUE_WORDS = frozenset({"1", "y", "yes", "true", "x", "t", "dnp", "dnf", "dni", "dnl", "nf", "np", "exclude", "excluded"})
FALSE_WORDS = frozenset({"", "0", "n", "no", "false", "f", "-", "--", "none", "n/a", "na"})

_PACKAGE_SIZE = re.compile(r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?!\d)")
PACKAGE_TOKEN = re.compile(
    r"^(?:0201|0402|0603|0805|1206|1210|1812|2010|2512|"
    r"(?:SOIC|SOT|SOP|SSOP|TSSOP|MSOP|QFN|DFN|TQFP|LQFP|QFP|DIP|BGA|SMA|SMB|SMC|SOD|TO|DPAK|D2PAK)[-_]?[\dA-Z]*[-\w]*)$",
    re.I,
)


def strip_dnp(text: str | None) -> tuple[str, bool]:
    """Remove DNP markers from text. Returns (cleaned text, marker found)."""
    if not text:
        return text or "", False
    if not DNP_MARKER.search(text):
        return text, False
    cleaned = DNP_MARKER.sub(" ", text)
    cleaned = re.sub(r"\(\s*\)|\[\s*\]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:/-")
    return cleaned, True


def has_dnp_marker(text: str | None) -> bool:
    return bool(text and DNP_MARKER.search(text))


def extract_package(footprint: str | None) -> str | None:
    """Short package name from a footprint string ("R_0603_1608Metric" -> "0603")."""
    if not footprint:
        return None
    name = footprint.split(":", 1)[-1].strip()
    m = _PACKAGE_SIZE.search(name)
    return m.group(1) if m else (name or None)


def _is_led(footprint: str | None, description: str | None, value: str | None) -> bool:
    lib = (footprint or "").split(":", 1)[0] if ":" in (footprint or "") else ""
    return bool(
        re.match(r"led_", lib, re.IGNORECASE)
        or any(re.search(r"\bled\b", t or "", re.IGNORECASE) for t in (description, value))
    )


def infer_category(
    refs: list[str],
    value: str | None,
    footprint: str | None,
    description: str | None,
    has_part_number: bool = False,
) -> str:
    """resistor / capacitor / inductor / connector / ic / ... / other.

    The designator prefix only counts for a real designator (`refdes_prefix`). With
    `has_part_number`, a prefix does not by itself give a resistor, capacitor, inductor,
    LED, diode or transistor; footprint, description or a passive value must.
    """
    for text in (footprint, description, value):
        for rx, cat in _STRONG_TEXT:
            if text and rx.search(text):
                return cat
    if refs:
        cat = PREFIX_CATEGORY.get(refdes_prefix(refs[0]))
        if cat == "diode" and _is_led(footprint, description, value):
            cat = "led"  # LEDs are numbered D1, D2 ... like any diode
        if cat and cat != "other" and not (has_part_number and cat in _OVERAGE_CATEGORIES):
            return cat
    if footprint:
        lib = footprint.split(":", 1)[0] if ":" in footprint else ""
        for rx, cat in _FOOTPRINT_LIB:
            if lib and rx.search(lib):
                return cat
    if value:
        unit = normalise_value(value).unit
        if unit:
            return _UNIT_CATEGORY[unit]
    if description:
        for rx, cat in _DESCRIPTION_KEYWORDS:
            if rx.search(description):
                return cat
    return "other"
