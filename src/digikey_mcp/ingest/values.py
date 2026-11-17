"""Component value parsing and canonicalisation (4k7, 0.1uF, 10K 1% ...)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

_NUM = r"(?:\d+(?:[.,]\d+)?|[.,]\d+)"
_PRE = r"(?:meg|[pnumkg])"
_UNIT = r"(?:ohms?|farads?|henr(?:y|ies)|[fhr])"

_MAIN = re.compile(rf"^(?P<num>{_NUM})(?P<pre>{_PRE})?(?P<unit>{_UNIT})?$", re.I)
_INFIX = re.compile(
    rf"^(?P<int>\d*)(?P<pre>meg|[pnumkgr])(?P<frac>\d{{1,3}})(?P<unit>{_UNIT})?$", re.I
)
_TOLERANCE = re.compile(r"^(?:±|\+/-|\+-)?(\d+(?:[.,]\d+)?)%$")
_VOLTAGE = re.compile(r"^\d+(?:\.\d+)?V(?:DC|AC)?$", re.I)
_POWER = re.compile(r"^(?:\d+/\d+|\d*\.?\d+m?)W$", re.I)
_DIELECTRIC = frozenset(
    {"x7r", "x5r", "x7s", "x6s", "x8r", "c0g", "np0", "y5v", "z5u", "x7t"}
)
_SIZE_CODES = frozenset(
    {"0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010", "2512"}
)

_EXP = {"p": -12, "n": -9, "u": -6, "m": -3, "k": 3, "g": 9}

_ALLOWED_PREFIX_EXP = {
    "ohm": {-3, 0, 3, 6, 9},
    "F": {-12, -9, -6, -3, 0},
    "H": {-9, -6, -3, 0},
}
_FORMAT_PREFIXES = {
    "ohm": [(-3, "m"), (0, ""), (3, "k"), (6, "M"), (9, "G")],
    "F": [(-12, "p"), (-9, "n"), (-6, "u"), (-3, "m"), (0, "")],
    "H": [(-9, "n"), (-6, "u"), (-3, "m"), (0, "")],
}
_HINT_UNIT = {"resistor": "ohm", "capacitor": "F", "inductor": "H"}


@dataclass
class ValueInfo:
    original: str
    canonical: str
    number: float | None = None
    unit: str | None = None
    tolerance: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def parsed(self) -> bool:
        return self.number is not None


def _prepare(text: str) -> str:
    s = text.replace("µ", "u").replace("μ", "u")
    s = s.replace("Ω", "ohm").replace("Ω", "ohm")
    s = re.sub(r"\s+", " ", s).strip()
    # "100 nF" / "4.7 k" -> "100nF" / "4.7k"
    s = re.sub(
        r"(?<=\d) (?=[a-z])(?=(?:meg|[pnumkg])?(?:ohms?|[fhr])?(?:[\s,;]|$))", "", s, flags=re.I
    )
    return s


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s;]+|,(?=\s|$)", s) if t]


def _prefix_exp(ch: str) -> int:
    low = ch.lower()
    if low == "meg":
        return 6
    if ch == "M":
        return 6
    if ch == "G":
        return 9
    return _EXP[low]


def _unit_name(raw: str) -> str:
    low = raw.lower()
    if low.startswith("ohm") or low == "r":
        return "ohm"
    if low.startswith("f"):
        return "F"
    return "H"


def _parse_token(tok: str, hint: str | None, allow_bare: bool):
    """Return (Decimal base-unit value, unit) or None."""
    m = _INFIX.match(tok)
    if m:
        pre = m.group("pre")
        mant_s = f"{m.group('int') or '0'}.{m.group('frac')}"
        if pre.lower() == "r":
            exp, unit = 0, _unit_name(m.group("unit") or "R")
        else:
            exp = _prefix_exp(pre)
            unit = _unit_name(m.group("unit")) if m.group("unit") else None
        return _finish(mant_s, exp, unit, hint, explicit=bool(m.group("unit")) or pre.lower() == "r", pre=pre)
    m = _MAIN.match(tok)
    if not m:
        return None
    pre, unit_raw = m.group("pre"), m.group("unit")
    if not pre and not unit_raw and not (allow_bare and hint in _HINT_UNIT):
        return None
    exp = _prefix_exp(pre) if pre else 0
    unit = _unit_name(unit_raw) if unit_raw else None
    return _finish(m.group("num").replace(",", "."), exp, unit, hint, bool(unit_raw), pre=pre)


def _finish(mant_s, exp, unit, hint, explicit, pre):
    try:
        mant = Decimal(mant_s)
    except InvalidOperation:
        return None
    if unit is None:
        unit = _HINT_UNIT.get(hint or "")
        if unit is None:
            if pre and pre.lower() in ("p", "n", "u"):
                unit = "F"
            elif pre and (pre in ("k", "K", "M", "G", "g") or pre.lower() == "meg"):
                unit = "ohm"
            else:
                return None
        if exp not in _ALLOWED_PREFIX_EXP[unit]:
            return None
    return mant.scaleb(exp), unit


def _format(value: Decimal, unit: str) -> str:
    if value == 0:
        return f"0{unit}"
    table = _FORMAT_PREFIXES[unit]
    if unit == "ohm" and value >= Decimal("0.1"):
        table = table[1:]  # 0.22ohm, not 220mohm
    exp, sym = table[0]
    for e, sy in table:
        if value >= Decimal(1).scaleb(e):
            exp, sym = e, sy
    mant = value.scaleb(-exp).normalize()
    return f"{format(mant, 'f')}{sym}{unit}"


def normalise_value(text: str | None, category_hint: str | None = None) -> ValueInfo:
    """Canonicalise a value string, keeping the original.

    "4k7", "4.7k" and "4,7k" all become "4.7kohm"; "0.1uF" becomes "100nF";
    "10K 1%" becomes "10kohm" with tolerance "1%". Text that is not a number
    (part names such as "LM358") is returned whitespace-collapsed.
    """
    original = text or ""
    s = _prepare(original)
    info = ValueInfo(original=original, canonical=s)
    if not s:
        return info
    # A part that is classified as something other than a passive is not a value.
    if category_hint not in (None, "other") and category_hint not in _HINT_UNIT:
        return info
    primary = None
    for idx, tok in enumerate(_tokens(s)):
        tol = _TOLERANCE.match(tok)
        if tol:
            info.tolerance = tol.group(1).replace(",", ".") + "%"
            continue
        if _VOLTAGE.match(tok):
            info.attributes["voltage"] = tok.upper().replace("VDC", "V").replace("VAC", "V")
            continue
        if tok.lower() in _DIELECTRIC:
            info.attributes["dielectric"] = tok.upper()
            continue
        if _POWER.match(tok):
            info.attributes["power"] = tok.upper()
            continue
        if tok in _SIZE_CODES:
            info.attributes.setdefault("size", tok)
            continue
        if primary is None:
            parsed = _parse_token(tok, category_hint, allow_bare=idx == 0)
            if parsed:
                primary = parsed
    if primary:
        value, unit = primary
        info.number = float(value)
        info.unit = unit
        info.canonical = _format(value, unit)
    return info


_MEASURE = re.compile(rf"^(?P<num>{_NUM})\s*(?P<pre>[mkMuµ]?)(?P<unit>[AV])(?:DC|AC)?$")
_MEASURE_EXP = {"": 0, "m": -3, "u": -6, "µ": -6, "k": 3, "M": 6}


def parse_measure(text: str | None) -> tuple[float, str] | None:
    """Current or voltage text as (value in A or V, unit): "2 A", "200 mA", "32V", "0.5A".

    Parameter values are DigiKey's own spelling; text that is not a plain measure gives None.
    """
    m = _MEASURE.match((text or "").strip())
    if not m:
        return None
    exp = _MEASURE_EXP[m.group("pre")]
    return float(Decimal(m.group("num").replace(",", ".")).scaleb(exp)), m.group("unit")
