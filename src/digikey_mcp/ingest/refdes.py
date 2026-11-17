"""Reference designator list parsing and range expansion."""

from __future__ import annotations

import re

MAX_RANGE = 2000

_REF = re.compile(r"^(?P<prefix>[A-Za-z_]+?)(?P<num>\d+)(?P<suffix>[A-Za-z]?)$")
#: a real designator: letters, then digits; a unit letter (U2A) only after up to three digits,
#: so a module name such as D1608E is not one
_DESIGNATOR = re.compile(r"^(?P<prefix>[A-Za-z]+)_?(?:\d+|\d{1,3}[A-Za-z])$")
_RANGE = re.compile(
    r"^(?P<p1>[A-Za-z_]+)(?P<n1>\d+)[-–—~](?P<p2>[A-Za-z_]*)(?P<n2>\d+)$"
)
_SPACED_DASH = re.compile(r"(?<=\d)\s+([-–—~])\s+(?=[A-Za-z_]*\d)")
_SPACED_TO = re.compile(r"(?<=\d)\s+to\s+(?=[A-Za-z_]+\d)", re.I)
_SPLIT = re.compile(r"[,;\s]+")


def refdes_prefix(ref: str) -> str:
    """Upper-case letter prefix of a designator ("R12" -> "R", "TP3" -> "TP"); "" when `ref` is no designator."""
    m = _DESIGNATOR.match(ref)
    return m.group("prefix").upper() if m else ""


def natural_key(ref: str):
    m = _REF.match(ref)
    if m:
        return (m.group("prefix").upper(), int(m.group("num")), m.group("suffix"))
    return (ref.upper(), 0, "")


def expand_refdes(text: str | None) -> tuple[list[str], list[str]]:
    """Expand "R1-R4, C7 C9" into ["R1", "R2", "R3", "R4", "C7", "C9"].

    Returns (designators in first-seen order without duplicates, notes).
    Ranges that cannot be expanded safely are kept as the literal token and
    reported in the notes.
    """
    notes: list[str] = []
    if not text:
        return [], notes
    s = _SPACED_DASH.sub(r"\1", text)
    s = _SPACED_TO.sub("-", s)
    out: list[str] = []
    seen: set[str] = set()

    def add(ref: str) -> None:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)

    for raw in _SPLIT.split(s):
        tok = raw.strip("()[]{}\"'")
        if not tok:
            continue
        m = _RANGE.match(tok)
        if not m:
            add(tok)
            continue
        p1, p2 = m.group("p1"), m.group("p2")
        n1, n2 = m.group("n1"), m.group("n2")
        if p2 and p2 != p1:
            notes.append(f"range {tok!r} spans different prefixes; kept literally")
            add(tok)
            continue
        a, b = int(n1), int(n2)
        if b < a or b - a >= MAX_RANGE:
            notes.append(f"range {tok!r} is reversed or too large; kept literally")
            add(tok)
            continue
        width = len(n1) if n1.startswith("0") and len(n1) == len(n2) else 0
        for n in range(a, b + 1):
            add(f"{p1}{str(n).zfill(width) if width else n}")
    return out, notes
