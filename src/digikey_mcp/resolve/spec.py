"""What a generic BOM line asks for, written as checks on DigiKey product parameters.

`spec_for` turns a line into a `Spec`: a DigiKey category plus `Want`s such as resistance equal to
47 kOhm, voltage rating at least 50 V or package 0805. Each want is used twice: to pick the value
ids for DigiKey's parameter filter, and to verify every returned product against its own
parameters. Values are parsed into SI units with the ingest parser, so "1.47 kOhms" never passes
for 47k.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..ingest import BomLine, normalise_value
from ..ingest.values import parse_measure

#: relative tolerance when comparing a parsed value with the BOM value: unit conversion noise only,
#: because E96 neighbours sit 0.15 % apart (680 and 681 ohm)
VALUE_TOLERANCE = 1e-4

#: DigiKey leaf category ids
CAT_CHIP_RESISTOR = 52
CAT_THT_RESISTOR = 53
CAT_ALUMINUM_CAP = 58
CAT_TANTALUM_CAP = 59
CAT_CERAMIC_CAP = 60
CAT_POLYMER_CAP = 70
CAT_LED = 105
CAT_FUSE = 139
CAT_PTC_FUSE = 150
CAT_PIN_HEADER = 314

_PKG = ("Package / Case", "Supplier Device Package")
_FACET_PARAMS: dict[int, tuple[str, ...]] = {
    CAT_CHIP_RESISTOR: ("Resistance", "Tolerance", *_PKG),
    CAT_THT_RESISTOR: ("Resistance", "Tolerance", *_PKG),
    CAT_CERAMIC_CAP: ("Capacitance", "Voltage - Rated", "Tolerance", "Temperature Coefficient", "Mounting Type", *_PKG),
    CAT_ALUMINUM_CAP: ("Capacitance", "Voltage - Rated", "Tolerance", "Mounting Type", *_PKG),
    CAT_TANTALUM_CAP: ("Capacitance", "Voltage - Rated", "Tolerance", "Mounting Type", *_PKG),
    CAT_POLYMER_CAP: ("Capacitance", "Voltage - Rated", "Tolerance", "Mounting Type", *_PKG),
    CAT_LED: ("Color", *_PKG),
    CAT_FUSE: ("Current Rating (Amps)", "Voltage Rating - DC", "Voltage Rating - AC", *_PKG),
    CAT_PTC_FUSE: ("Current - Hold (Ih) (Max)", "Voltage - Max"),
    CAT_PIN_HEADER: ("Connector Type", "Number of Positions", "Number of Rows", "Pitch - Mating", "Mounting Type"),
}
_SIZE_CODES = ("0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010", "2512")
_COLORS = ("yellow-green", "red", "green", "yellow", "blue", "amber", "orange", "white", "pink", "purple", "cyan")


@dataclass(frozen=True)
class Want:
    """One requirement on a parameter: the DigiKey names it may appear under and the test on its text."""

    label: str
    params: tuple[str, ...]
    test: Callable[[str], bool]
    #: the requirement in words, for reasons
    text: str
    #: False for tests too wide to turn into a parameter filter
    facet: bool = True


@dataclass
class Spec:
    #: what the part is, also the keyword of the discovery call
    kind: str
    category_id: int
    #: relevance words for the filtered search: the part noun and the package code, never a value (a
    #: value in the keywords drops the parts DigiKey writes differently, such as 0.1UF for 100nF)
    keywords: str
    wants: list[Want] = field(default_factory=list)

    def facet_names(self) -> tuple[str, ...]:
        """The parameters to list in the discovery call; fixed per category so the answer is cached across lines."""
        return _FACET_PARAMS[self.category_id]

    def verify(self, parameters: Mapping[str, str], description: str = "") -> tuple[list[str], Want | None]:
        """(verified parameters as text, the first want the product fails); the want is None on a match."""
        values = {**parameters, "Description": description}
        done: list[str] = []
        for w in self.wants:
            hit = next(((n, values[n]) for n in w.params if n in values and w.test(values[n])), None)
            if hit is None:
                return done, w
            if w.label != "Description":
                done.append(f"{hit[0]} {hit[1]}")
        return done, None


# ---- value tests ---------------------------------------------------------------


def _close(got: float, want: float) -> bool:
    return got == 0 if want == 0 else abs(got - want) <= abs(want) * VALUE_TOLERANCE


def _value_is(hint: str, unit: str, want: float) -> Callable[[str], bool]:
    def test(text: str) -> bool:
        info = normalise_value(text, hint)
        return info.parsed and info.unit == unit and _close(info.number or 0.0, want)

    return test


def _measure_is(unit: str, want: float) -> Callable[[str], bool]:
    def test(text: str) -> bool:
        m = parse_measure(text)
        return bool(m) and m[1] == unit and _close(m[0], want)

    return test


def _measure_at_least(unit: str, want: float) -> Callable[[str], bool]:
    def test(text: str) -> bool:
        m = parse_measure(text)
        return bool(m) and m[1] == unit and m[0] >= want * (1 - 1e-9)

    return test


def _tolerance_within(percent: float) -> Callable[[str], bool]:
    def test(text: str) -> bool:
        found = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
        return bool(found) and max(found) <= percent + 1e-9

    return test


def _package_is(code: str) -> Callable[[str], bool]:
    rx = re.compile(rf"^{code}(?!\d)|\({code}\)")
    return lambda text: bool(rx.search(text.strip()))


def _mm_is(want: float, tol: float = 0.02) -> Callable[[str], bool]:
    def test(text: str) -> bool:
        m = re.search(r"\((\d+(?:\.\d+)?)mm\)", text)
        return bool(m) and abs(float(m.group(1)) - want) <= tol

    return test


def _diameter_at_most(mm: float) -> Callable[[str], bool]:
    def test(text: str) -> bool:
        m = re.search(r"Dia\s*\((\d+(?:\.\d+)?)mm\)", text)
        return bool(m) and float(m.group(1)) <= mm + 0.05

    return test


def _dielectric_is(name: str) -> Callable[[str], bool]:
    same = {"C0G": "C0G", "NP0": "C0G"}
    want = same.get(name, name)
    return lambda text: same.get(text.strip().upper(), text.strip().upper()) == want


# ---- reading the line ----------------------------------------------------------


def _text(line: BomLine) -> str:
    return " ".join(x for x in (line.value, line.description, line.footprint) if x)


def _first_float(rx: str, *texts: str | None) -> float | None:
    for t in texts:
        m = re.search(rx, t or "", re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def _size_code(line: BomLine) -> str | None:
    for t in (line.package, line.footprint, line.description):
        m = re.search(r"(?<!\d)(" + "|".join(_SIZE_CODES) + r")(?!\d)", t or "")
        if m:
            return m.group(1)
    return None


def _si(line: BomLine, hint: str, unit: str) -> float | None:
    if line.value_si is not None and line.value_unit == unit:
        return line.value_si
    info = normalise_value(line.value, hint)
    return info.number if info.unit == unit else None


def _voltage(line: BomLine) -> float | None:
    return _first_float(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*V(?:DC|AC)?\b", line.value, line.description)


def _tolerance(line: BomLine) -> float | None:
    if line.tolerance:
        return _first_float(r"(\d+(?:[.,]\d+)?)", line.tolerance)
    return _first_float(r"(\d+(?:[.,]\d+)?)\s*%", line.value, line.description)


def _current(line: BomLine) -> float | None:
    for t in (line.value, line.description):
        m = re.search(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(m?)A\b", t or "")
        if m:
            return float(m.group(1).replace(",", ".")) * (1e-3 if m.group(2) else 1.0)
    return None


def _short(value: float, unit: str) -> str:
    if unit == "ohm":
        for scale, sym in ((1e6, "M"), (1e3, "k"), (1.0, "")):
            if value >= scale:
                return f"{value / scale:.6g}{sym}"
        return f"{value:.6g}"
    for scale, sym in ((1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p")):
        if value >= scale * 0.999999:
            return f"{value / scale:.6g}{sym}F"
    return f"{value:.6g}F"


def _words(noun: str, code: str | None) -> str:
    return f"{noun} {code}" if code else noun


def _pkg_want(code: str) -> Want:
    return Want("Package / Case", ("Package / Case", "Supplier Device Package"), _package_is(code), code)


# ---- per part type -------------------------------------------------------------


def _resistor(line: BomLine) -> Spec | None:
    ohms = _si(line, "resistor", "ohm")
    if ohms is None:
        return None
    code = _size_code(line)
    tht = not code and re.search(r"THT|axial|DIN0", line.footprint or "", re.IGNORECASE)
    wants = [Want("Resistance", ("Resistance",), _value_is("resistor", "ohm", ohms), _short(ohms, "ohm") + "ohm")]
    tol = _tolerance(line)
    if tol is not None and ohms > 0:  # a zero-ohm jumper lists "Jumper" as its tolerance
        wants.append(Want("Tolerance", ("Tolerance",), _tolerance_within(tol), f"{tol:g}% or tighter"))
    if code:
        wants.append(_pkg_want(code))
    return Spec("resistor", CAT_THT_RESISTOR if tht else CAT_CHIP_RESISTOR, _words("resistor", code), wants)


def _capacitor(line: BomLine) -> Spec | None:
    farads = _si(line, "capacitor", "F")
    if farads is None:
        return None
    text = _text(line)
    code = _size_code(line)
    if re.search(r"polymer", text, re.IGNORECASE):
        cat, kind = CAT_POLYMER_CAP, "polymer capacitor"
    elif re.search(r"tantalum|\btant\b", text, re.IGNORECASE):
        cat, kind = CAT_TANTALUM_CAP, "tantalum capacitor"
    elif re.search(r"electrolytic|radial|\bCP_|alum", text, re.IGNORECASE):
        cat, kind = CAT_ALUMINUM_CAP, "aluminum electrolytic capacitor"
    else:
        cat, kind = CAT_CERAMIC_CAP, "ceramic capacitor"
    wants = [Want("Capacitance", ("Capacitance",), _value_is("capacitor", "F", farads), _short(farads, "F"))]
    volts = _voltage(line)
    if volts is not None:
        wants.append(Want("Voltage - Rated", ("Voltage - Rated", "Voltage Rating"), _measure_at_least("V", volts),
                          f"{volts:g}V or more"))
    tol = _tolerance(line)
    if tol is not None:
        wants.append(Want("Tolerance", ("Tolerance",), _tolerance_within(tol), f"{tol:g}% or tighter"))
    die = re.search(r"\b(X7R|X5R|X7S|X6S|X8R|C0G|NP0|X7T|Y5V|Z5U)\b", text, re.IGNORECASE)
    if die and cat == CAT_CERAMIC_CAP:
        name = die.group(1).upper()
        wants.append(Want("Temperature Coefficient", ("Temperature Coefficient",), _dielectric_is(name), name))
    thd = bool(re.search(r"THT|radial|axial", line.footprint or "", re.IGNORECASE))
    if cat == CAT_ALUMINUM_CAP:
        if re.search(r"radial", text, re.IGNORECASE):
            wants.append(Want("Package / Case", ("Package / Case",),
                              lambda t: t.startswith("Radial") and "SMD" not in t, "radial"))
        pitch = _first_float(r"_P(\d+(?:\.\d+)?)mm", line.footprint)
        if pitch:
            wants.append(Want("Lead Spacing", ("Lead Spacing",), _mm_is(pitch), f"{pitch:g}mm pitch"))
        dia = _first_float(r"_D(\d+(?:\.\d+)?)mm", line.footprint)
        if dia:
            wants.append(Want("Size / Dimension", ("Size / Dimension",), _diameter_at_most(dia),
                              f"diameter up to {dia:g}mm", facet=False))
    elif code:
        wants.append(_pkg_want(code))
    if thd:
        wants.append(Want("Mounting Type", ("Mounting Type",), lambda t: t == "Through Hole", "through hole"))
    return Spec(kind, cat, _words("capacitor", code), wants)


def _led(line: BomLine) -> Spec | None:
    text = _text(line).casefold()
    color = next((c for c in _COLORS if re.search(rf"(?<![a-z]){c}(?![a-z])", text)), None)
    code = _size_code(line)
    if color:
        wants = [Want("Color", ("Color",), lambda t, c=color: t.strip().casefold() == c, color)]
    else:
        wants = [Want("Color", ("Color",), lambda t: not re.search(r"infrared|uv|violet|,", t, re.IGNORECASE), "a visible single colour")]
    if code:
        wants.append(_pkg_want(code))
    return Spec("LED", CAT_LED, _words("LED", code), wants)


def _fuse(line: BomLine) -> Spec | None:
    text = _text(line)
    amps = _current(line)
    if amps is None:
        return None
    volts = _voltage(line)
    ptc = re.search(r"\bPTC\b|resettable", text, re.IGNORECASE)
    if ptc:
        wants = [Want("Current - Hold (Ih) (Max)", ("Current - Hold (Ih) (Max)",), _measure_is("A", amps), f"{amps:g}A hold")]
        if volts is not None:
            wants.append(Want("Voltage - Max", ("Voltage - Max",), _measure_at_least("V", volts), f"{volts:g}V or more"))
        return Spec("PTC resettable fuse", CAT_PTC_FUSE, "PTC fuse", wants)
    if re.search(r"\bAT[OC]\b|blade", text, re.IGNORECASE):
        wants = [Want("Current Rating (Amps)", ("Current Rating (Amps)",), _measure_is("A", amps), f"{amps:g}A")]
        if volts is not None:
            wants.append(Want("Voltage Rating", ("Voltage Rating - DC", "Voltage Rating - AC"),
                              _measure_at_least("V", volts), f"{volts:g}V or more"))
        wants.append(Want("Package / Case", ("Package / Case",), lambda t: "ATO" in t.upper(), "ATO blade"))
        return Spec("ATO blade fuse", CAT_FUSE, "ATO fuse", wants)
    return None


def _header(line: BomLine) -> Spec | None:
    text = _text(line)
    if not re.search(r"header", text, re.IGNORECASE):
        return None
    fp = re.search(r"(\d+)x(\d+)", line.footprint or "")
    rows = int(fp.group(1)) if fp else None
    pins = rows * int(fp.group(2)) if fp and rows else None
    if pins is None:
        m = re.search(r"(\d+)[-\s]?pin", text, re.IGNORECASE)
        pins = int(m.group(1)) if m else None
    pitch = _first_float(r"P(\d+\.\d+)mm", line.footprint) or _first_float(r"(\d+\.\d+)\s*mm", line.value, line.description)
    if pins is None or pitch is None:
        return None
    wants = [
        Want("Connector Type", ("Connector Type",), lambda t: t.startswith("Header"), "header"),
        Want("Number of Positions", ("Number of Positions",), lambda t: t.strip() == str(pins), f"{pins} positions"),
    ]
    if rows:
        wants.append(Want("Number of Rows", ("Number of Rows",), lambda t: t.strip() == str(rows), f"{rows} row(s)"))
    wants.append(Want("Pitch - Mating", ("Pitch - Mating",), _mm_is(pitch), f"{pitch:g}mm pitch"))
    if re.search(r"SMD|SMT", line.footprint or "", re.IGNORECASE):
        wants.append(Want("Mounting Type", ("Mounting Type",), lambda t: t.startswith("Surface Mount"), "surface mount"))
    else:
        wants.append(Want("Mounting Type", ("Mounting Type",), lambda t: t == "Through Hole", "through hole"))
    if re.search(r"vertical", line.footprint or "", re.IGNORECASE):
        wants.append(Want("Description", ("Description",), lambda t: bool(re.search(r"\bVERT", t, re.IGNORECASE)), "vertical",
                          facet=False))
    return Spec("pin header", CAT_PIN_HEADER, "pin header", wants)


_BUILDERS = {"resistor": _resistor, "capacitor": _capacitor, "led": _led, "fuse": _fuse, "connector": _header}


def spec_for(line: BomLine) -> Spec | None:
    """The parametric spec for a generic line, or None when the part type has no verifier."""
    builder = _BUILDERS.get(line.category_hint)
    return builder(line) if builder else None
