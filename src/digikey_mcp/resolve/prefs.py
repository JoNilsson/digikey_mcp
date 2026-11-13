"""Resolution preferences (TOML) and order-quantity overage."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ingest import BomLine


@dataclass
class Overage:
    """Spare parts: `percent` of the base quantity rounded up, at least `min_extra`, at most `max_extra`."""

    percent: float = 0.0
    min_extra: int = 0
    max_extra: int | None = None

    def extra(self, base: int) -> int:
        extra = max(math.ceil(base * self.percent / 100.0 - 1e-9), self.min_extra)
        if self.max_extra is not None:
            extra = min(extra, self.max_extra)
        return max(extra, 0)


DEFAULT_OVERAGE: dict[str, Overage] = {
    "resistor": Overage(10, 2),
    "capacitor": Overage(10, 2),
    "inductor": Overage(5, 1),
    "led": Overage(10, 1),
    "diode": Overage(5, 1),
    "transistor": Overage(5, 1),
    "ic": Overage(0, 0),
    "connector": Overage(0, 0),
    "crystal": Overage(0, 0),
    "switch": Overage(0, 0),
    "fuse": Overage(0, 0),
    "default": Overage(0, 0),
}

_SECTIONS = {
    "overage": None,
    "packaging": {"allow_marketplace", "allow_obsolete", "allowed", "repackage_supplier_pn"},
    "generic": {"keywords", "preferred_manufacturers", "max_candidates", "search_limit"},
    "quota": {"per_minute", "per_day", "reserve", "max_sleep_seconds", "time_budget_seconds"},
    "cache": {"price_ttl_hours", "static_ttl_days"},
    "api": {"thorough"},
}


@dataclass
class Preferences:
    overage: dict[str, Overage] = field(default_factory=lambda: dict(DEFAULT_OVERAGE))
    allow_marketplace: bool = False
    #: Obsolete / discontinued parts count as unavailable unless this is true.
    allow_obsolete: bool = False
    #: packaging name fragments to allow (case-insensitive); empty allows all
    allowed_packagings: list[str] = field(default_factory=list)
    #: switch a line that names a DigiKey part number to a cheaper packaging
    repackage_supplier_pn: bool = False
    #: extra search words per category for generic lines, e.g. {"resistor": ["1%"]}
    generic_keywords: dict[str, list[str]] = field(default_factory=dict)
    preferred_manufacturers: list[str] = field(default_factory=list)
    max_candidates: int = 5
    search_limit: int = 25
    per_minute: int = 120
    per_day: int = 1000
    #: calls kept free of each limit
    reserve: int = 5
    #: longest wait for the per-minute window before returning a partial result
    max_sleep_seconds: float = 65.0
    #: stop a resolution run after this many seconds and report it as resumable
    time_budget_seconds: float = 240.0
    price_ttl_hours: float = 6.0
    static_ttl_days: float = 1.0
    #: also call pricingbyquantity, packagetypebyquantity and alternatepackaging for every line
    thorough: bool = False
    #: the TOML mapping this was built from (stored in sessions)
    raw: dict[str, Any] = field(default_factory=dict)

    def overage_for(self, category: str) -> Overage:
        return self.overage.get(category) or self.overage["default"]

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Preferences:
        data = data or {}
        unknown = set(data) - set(_SECTIONS)
        if unknown:
            raise ValueError(f"unknown preference section(s): {', '.join(sorted(unknown))}")
        prefs = cls(raw=data)
        for name, spec in (data.get("overage") or {}).items():
            if not isinstance(spec, dict) or set(spec) - {"percent", "min_extra", "max_extra"}:
                raise ValueError(f"[overage.{name}] takes percent, min_extra, max_extra")
            base = prefs.overage.get(name, Overage())
            prefs.overage[name] = Overage(
                float(spec.get("percent", base.percent)),
                int(spec.get("min_extra", base.min_extra)),
                spec.get("max_extra", base.max_extra),
            )
        for section, allowed in _SECTIONS.items():
            if allowed is None:
                continue
            values = data.get(section) or {}
            bad = set(values) - allowed
            if bad:
                raise ValueError(f"[{section}] unknown key(s): {', '.join(sorted(bad))}")
        pk = data.get("packaging") or {}
        prefs.allow_marketplace = bool(pk.get("allow_marketplace", prefs.allow_marketplace))
        prefs.allow_obsolete = bool(pk.get("allow_obsolete", prefs.allow_obsolete))
        prefs.allowed_packagings = [str(x) for x in pk.get("allowed", [])]
        prefs.repackage_supplier_pn = bool(pk.get("repackage_supplier_pn", False))
        gen = data.get("generic") or {}
        prefs.generic_keywords = {k: [str(x) for x in v] for k, v in (gen.get("keywords") or {}).items()}
        prefs.preferred_manufacturers = [str(x) for x in gen.get("preferred_manufacturers", [])]
        prefs.max_candidates = max(1, min(int(gen.get("max_candidates", 5)), 5))
        prefs.search_limit = max(1, min(int(gen.get("search_limit", 25)), 50))
        q = data.get("quota") or {}
        for key in ("per_minute", "per_day", "reserve"):
            setattr(prefs, key, int(q.get(key, getattr(prefs, key))))
        for key in ("max_sleep_seconds", "time_budget_seconds"):
            setattr(prefs, key, float(q.get(key, getattr(prefs, key))))
        c = data.get("cache") or {}
        prefs.price_ttl_hours = float(c.get("price_ttl_hours", prefs.price_ttl_hours))
        prefs.static_ttl_days = float(c.get("static_ttl_days", prefs.static_ttl_days))
        prefs.thorough = bool((data.get("api") or {}).get("thorough", False))
        return prefs


def load_preferences(path: str | Path | None) -> Preferences:
    """Read a TOML preferences file; None gives the defaults."""
    if path is None:
        return Preferences()
    try:
        import tomllib
    except ImportError:  # Python 3.10
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            raise ValueError("reading a preferences file needs Python 3.11 or the tomli package") from None
    p = Path(path).expanduser()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read preferences file {p}: {exc.strerror}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"preferences file {p} is not valid TOML: {exc}") from None
    return Preferences.from_dict(data)


def needed_quantity(line: BomLine, boards: int, prefs: Preferences) -> tuple[int, int, int]:
    """(base, overage, needed): boards x per-board quantity plus category overage."""
    base = max(int(line.quantity), 0) * max(int(boards), 1)
    extra = prefs.overage_for(line.category_hint).extra(base) if base else 0
    return base, extra, base + extra
