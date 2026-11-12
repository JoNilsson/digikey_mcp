"""Every assumption about DigiKey response shapes lives here.

The rest of the package works with the dataclasses below and never reads a
trimmed or raw DigiKey dictionary. Two kinds of assumption are isolated:

* which `trim` function turns each client response into a dictionary, and the
  keys of that dictionary (`fetch`, `parse_*`);
* how DigiKey's free-text fields are classified (packaging names, product
  status text, substitute types).

All of it is unverified against live responses; when a live run disagrees,
fix this module only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import trim

#: Parameters kept from product responses (everything else is dropped before caching).
PARAMETER_KEEP = (
    "Package / Case",
    "Supplier Device Package",
    "Tolerance",
    "Resistance",
    "Capacitance",
    "Inductance",
    "Voltage - Rated",
    "Voltage Rating",
    "Power (Watts)",
    "Temperature Coefficient",
    "Mounting Type",
)

KIND_CUT_TAPE = "cut_tape"
KIND_TAPE_REEL = "tape_reel"
KIND_DIGIREEL = "digireel"
KIND_TRAY = "tray"
KIND_TUBE = "tube"
KIND_BULK = "bulk"
KIND_OTHER = "other"


@dataclass
class Packaging:
    """One orderable packaging variation of a product (one DigiKey part number)."""

    dk_pn: str
    name: str = ""
    kind: str = KIND_OTHER
    moq: int = 1
    standard_package: int = 0
    stock: int | None = None
    marketplace: bool = False
    #: (break quantity, unit price) ascending by quantity
    breaks: list[tuple[int, float]] = field(default_factory=list)
    #: one-off fee added to the order (DigiReel)
    fee: float = 0.0


@dataclass
class ProductInfo:
    mpn: str = ""
    manufacturer: str = ""
    manufacturer_id: int | None = None
    description: str = ""
    status: str = ""
    #: normalised status, see `status_class`
    status_class: str = "unknown"
    stock: int = 0
    unit_price: float | None = None
    category: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    packagings: list[Packaging] = field(default_factory=list)

    @property
    def marketplace(self) -> bool:
        return bool(self.packagings) and all(p.marketplace for p in self.packagings)

    @property
    def dk_pn(self) -> str:
        return self.packagings[0].dk_pn if self.packagings else ""


@dataclass
class SubstituteInfo:
    dk_pn: str
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    stock: int = 0
    unit_price: float | None = None
    kind: str = ""
    #: high, medium or low
    confidence: str = "low"


@dataclass
class QuoteOption:
    """One option from pricingbyquantity: DigiKey's own price for an exact quantity."""

    label: str
    total_price: float | None
    quantity: int
    stock: int | None
    #: (dk_pn, packaging, quantity priced, unit price)
    products: list[tuple[str, str, int, float | None]] = field(default_factory=list)


# ---- endpoint fetching -------------------------------------------------------

#: endpoint -> "price" (price/stock bearing) or "static" cache class
ENDPOINT_CLASS = {
    "product_details": "price",
    "keyword_search": "price",
    "pricing_by_quantity": "price",
    "package_type_by_quantity": "price",
    "substitutions": "price",
    "alternate_packaging": "static",
}


def fetch(client: Any, endpoint: str, args: Mapping[str, Any]) -> dict:
    """Call one client endpoint and return the trimmed dictionary."""
    if endpoint == "product_details":
        return trim.trim_product_details(client.product_details(args["pn"]), PARAMETER_KEEP)
    if endpoint == "keyword_search":
        response = client.keyword_search(
            args["keywords"],
            limit=args.get("limit", 10),
            search_options=args.get("search_options") or None,
            minimum_quantity_available=args.get("min_quantity"),
        )
        return trim.trim_keyword_response(response, PARAMETER_KEEP)
    if endpoint == "substitutions":
        return trim.trim_substitutions(client.substitutions(args["pn"]))
    if endpoint == "alternate_packaging":
        return trim.trim_alternate_packaging(client.alternate_packaging(args["pn"]))
    if endpoint == "pricing_by_quantity":
        return trim.trim_pricing_by_quantity(client.pricing_by_quantity(args["pn"], args["quantity"]))
    if endpoint == "package_type_by_quantity":
        return trim.trim_package_type_by_quantity(client.package_type_by_quantity(args["pn"], args["quantity"]))
    raise ValueError(f"unknown endpoint {endpoint!r}")


# ---- classification ----------------------------------------------------------


def packaging_kind(name: str | None) -> str:
    n = (name or "").casefold()
    if "digi-reel" in n or "digireel" in n or "digi reel" in n:
        return KIND_DIGIREEL
    if "cut tape" in n:
        return KIND_CUT_TAPE
    if "tape & reel" in n or "tape and reel" in n or "(tr)" in n:
        return KIND_TAPE_REEL
    if "tray" in n:
        return KIND_TRAY
    if "tube" in n:
        return KIND_TUBE
    if "bulk" in n:
        return KIND_BULK
    return KIND_OTHER


def status_class(status: str | None, discontinued: Any = None, end_of_life: Any = None) -> str:
    """active, nrnd, last_time_buy, obsolete, discontinued, preliminary or unknown."""
    s = (status or "").casefold()
    if "last time" in s:
        return "last_time_buy"
    if "not for new" in s or "nrnd" in s:
        return "nrnd"
    if "discontinued" in s:
        return "discontinued"
    if "obsolete" in s:
        return "obsolete"
    if "preliminary" in s:
        return "preliminary"
    if s == "active":
        return "active"
    if discontinued is True:
        return "discontinued"
    if end_of_life is True:
        return "obsolete"
    return "unknown"


def substitute_confidence(kind: str | None) -> str:
    k = (kind or "").casefold()
    if "direct" in k or "identical" in k or "same" in k:
        return "high"
    if "parametric" in k or "similar" in k or "equivalent" in k or "cross" in k:
        return "medium"
    return "low"


# ---- parsing -----------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_packaging(v: Mapping[str, Any]) -> Packaging | None:
    pn = v.get("digikey_part_number")
    if not pn:
        return None
    breaks = sorted(
        (_int(b.get("quantity")), price)
        for b in v.get("price_breaks") or []
        if isinstance(b, Mapping) and (price := _float(b.get("unit_price"))) is not None
    )
    stock = v.get("quantity_available")
    name = v.get("packaging") or ""
    return Packaging(
        dk_pn=str(pn),
        name=name,
        kind=packaging_kind(name),
        moq=max(_int(v.get("minimum_order_quantity"), 1), 1),
        standard_package=max(_int(v.get("standard_package")), 0),
        stock=None if stock is None else _int(stock),
        marketplace=bool(v.get("marketplace")),
        breaks=breaks,
        fee=_float(v.get("digireel_fee")) or 0.0,
    )


def parse_product(p: Mapping[str, Any]) -> ProductInfo:
    packagings = [pk for v in p.get("variations") or [] if (pk := parse_packaging(v))]
    unit_price = _float(p.get("unit_price"))
    category = p.get("category") or {}
    info = ProductInfo(
        mpn=str(p.get("manufacturer_part_number") or ""),
        manufacturer=str(p.get("manufacturer") or ""),
        manufacturer_id=p.get("manufacturer_id"),
        description=str(p.get("description") or ""),
        status=str(p.get("status") or ""),
        status_class=status_class(p.get("status"), p.get("discontinued"), p.get("end_of_life")),
        stock=_int(p.get("quantity_available")),
        unit_price=unit_price,
        category=str(category.get("name") or "") if isinstance(category, Mapping) else "",
        parameters=dict(p.get("parameters") or {}),
        packagings=packagings,
    )
    if not info.stock:
        info.stock = max((pk.stock or 0 for pk in packagings), default=0)
    return info


def parse_details(trimmed: Mapping[str, Any]) -> ProductInfo | None:
    """A productdetails response; None when it holds no product."""
    info = parse_product(trimmed)
    return info if (info.mpn or info.packagings) else None


def parse_keyword(trimmed: Mapping[str, Any]) -> list[ProductInfo]:
    """Exact matches first, then the other results, no duplicates."""
    seen: set[tuple[str, str, str]] = set()
    out: list[ProductInfo] = []
    for group in ("exact_matches", "products"):
        for p in trimmed.get(group) or []:
            info = parse_product(p)
            key = (info.mpn.casefold(), info.manufacturer.casefold(), info.dk_pn)
            if (info.mpn or info.packagings) and key not in seen:
                seen.add(key)
                out.append(info)
    return out


def parse_substitutes(trimmed: Mapping[str, Any]) -> list[SubstituteInfo]:
    out = []
    for s in trimmed.get("substitutes") or []:
        pn = s.get("digikey_part_number")
        if not pn:
            continue
        out.append(
            SubstituteInfo(
                dk_pn=str(pn),
                mpn=str(s.get("manufacturer_part_number") or ""),
                manufacturer=str(s.get("manufacturer") or ""),
                description=str(s.get("description") or ""),
                stock=_int(s.get("quantity_available")),
                unit_price=_float(s.get("unit_price")),
                kind=str(s.get("type") or ""),
                confidence=substitute_confidence(s.get("type")),
            )
        )
    return out


def parse_alternate_packaging(trimmed: Mapping[str, Any]) -> list[str]:
    """DigiKey part numbers of the other packagings of the same part."""
    return [str(i["digikey_part_number"]) for i in trimmed.get("alternate_packaging") or [] if i.get("digikey_part_number")]


def parse_quote(trimmed: Mapping[str, Any]) -> list[QuoteOption]:
    out = []
    for o in trimmed.get("options") or []:
        products = [
            (str(p.get("digikey_part_number") or ""), str(p.get("packaging") or ""), _int(p.get("quantity_priced")), _float(p.get("unit_price")))
            for p in o.get("products") or []
        ]
        stock = o.get("quantity_available")
        out.append(
            QuoteOption(
                label=str(o.get("option") or ""),
                total_price=_float(o.get("total_price")),
                quantity=_int(o.get("total_quantity_priced")),
                stock=None if stock is None else _int(stock),
                products=products,
            )
        )
    return out


def parse_package_types(trimmed: Mapping[str, Any]) -> list[Packaging]:
    """packagetypebyquantity products as packagings (they carry price breaks)."""
    out = []
    for p in trimmed.get("products") or []:
        pk = parse_packaging({**p, "quantity_available": p.get("quantity_available")})
        if pk:
            out.append(pk)
    return out
