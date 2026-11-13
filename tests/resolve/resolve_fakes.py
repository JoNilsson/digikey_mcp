"""Stub DigiKeyClient with canned v4-shaped responses, and response builders."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from digikey_mcp.client import DigiKeyError  # noqa: E402
from digikey_mcp.ingest import BomLine  # noqa: E402


def variation(pn, packaging, *, moq=1, std=0, stock=100000, breaks=((1, 0.1),), marketplace=False, fee=None):
    v: dict[str, Any] = {
        "DigiKeyProductNumber": pn,
        "PackageType": {"Id": 1, "Name": packaging},
        "MinimumOrderQuantity": moq,
        "StandardPackage": std,
        "QuantityAvailableforPackageType": stock,
        "MarketPlace": marketplace,
        "StandardPricing": [
            {"BreakQuantity": q, "UnitPrice": p, "TotalPrice": round(q * p, 4)} for q, p in breaks
        ],
    }
    if fee is not None:
        v["DigiReelFee"] = fee
    return v


def product(mpn, maker, variations, *, status="Active", stock=None, desc="", params=None, unit_price=None):
    if stock is None:
        stock = max((v["QuantityAvailableforPackageType"] for v in variations), default=0)
    return {
        "ManufacturerProductNumber": mpn,
        "Manufacturer": {"Id": 1, "Name": maker},
        "Description": {"ProductDescription": desc or f"{mpn} {maker}"},
        "QuantityAvailable": stock,
        "ProductStatus": {"Id": 0, "Status": status},
        "UnitPrice": unit_price,
        "Category": {"CategoryId": 1, "Name": "Resistors"},
        "ProductVariations": variations,
        "Parameters": [{"ParameterText": k, "ValueText": v} for k, v in (params or {}).items()],
    }


def substitute(pn, mpn, maker, kind, stock=1000, price=0.2):
    return {
        "SubstituteType": kind,
        "DigiKeyProductNumber": pn,
        "ManufacturerProductNumber": mpn,
        "Manufacturer": {"Name": maker},
        "Description": f"{mpn} substitute",
        "UnitPrice": price,
        "QuantityAvailable": stock,
    }


class FakeDigiKey:
    """Stands in for DigiKeyClient: returns raw v4-shaped dictionaries, counts calls."""

    def __init__(self):
        self.products: list[dict] = []
        self.searches: dict[str, list[dict]] = {}
        self.subs: dict[str, list[dict]] = {}
        self.calls: list[tuple] = []
        self.fail_with: DigiKeyError | None = None

    def add(self, *products: dict) -> None:
        self.products.extend(products)

    def _check(self, name, *args):
        self.calls.append((name, *args))
        if self.fail_with:
            raise self.fail_with

    def _find(self, pn):
        for p in self.products:
            if any(v["DigiKeyProductNumber"].casefold() == pn.casefold() for v in p["ProductVariations"]):
                return p
            if p["ManufacturerProductNumber"].casefold() == pn.casefold():
                return p
        return None

    def product_details(self, product_number, **_):
        self._check("product_details", product_number)
        p = self._find(product_number)
        if p is None:
            raise DigiKeyError("not found", status=404)
        return {"Product": p}

    def keyword_search(self, keywords, **kw):
        self._check("keyword_search", keywords)
        if keywords in self.searches:
            return {"ProductsCount": len(self.searches[keywords]), "Products": self.searches[keywords]}
        k = keywords.casefold()
        exact = [p for p in self.products if p["ManufacturerProductNumber"].casefold() == k]
        near = [p for p in self.products if k in p["ManufacturerProductNumber"].casefold() and p not in exact]
        return {"ProductsCount": len(exact) + len(near), "ExactMatches": exact, "Products": near}

    def substitutions(self, product_number, **_):
        self._check("substitutions", product_number)
        subs = self.subs.get(product_number, [])
        return {"ProductSubstitutesCount": len(subs), "ProductSubstitutes": subs}

    def alternate_packaging(self, product_number):
        self._check("alternate_packaging", product_number)
        return {"AlternatePackagings": []}

    def pricing_by_quantity(self, product_number, requested_quantity, **_):
        self._check("pricing_by_quantity", product_number, requested_quantity)
        return {"StandardPricingOptions": []}

    def package_type_by_quantity(self, product_number, requested_quantity, **_):
        self._check("package_type_by_quantity", product_number, requested_quantity)
        return {"Products": []}

    def names(self):
        return [c[0] for c in self.calls]


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def resistor_10k(**overrides):
    """Yageo 10k 0603: cut tape, tape and reel, DigiReel."""
    ct = variation(
        "311-10.0KHRCT-ND", "Cut Tape (CT)",
        breaks=((1, 0.10), (10, 0.043), (100, 0.0165), (500, 0.0105), (1000, 0.0078)),
    )
    tr = variation("311-10.0KHRTR-ND", "Tape & Reel (TR)", moq=5000, std=5000, breaks=((5000, 0.0042),))
    dr = variation(
        "311-10.0KHRDKR-ND", "Digi-Reel®", fee=7.0,
        breaks=((1, 0.10), (10, 0.043), (100, 0.0165), (500, 0.0105), (1000, 0.0078)),
    )
    return product("RC0603FR-0710KL", "YAGEO", [ct, tr, dr], desc="RES 10K OHM 1% 1/10W 0603", **overrides)


def make_line(line_id=1, refdes=("R1",), quantity=None, **kw) -> BomLine:
    refdes = list(refdes)
    kw.setdefault("category_hint", "ic")
    if "mpn" not in kw and "supplier_pn" not in kw:
        kw["generic"] = True
    else:
        kw["generic"] = False
    return BomLine(line_id=line_id, refdes=refdes, quantity=quantity or len(refdes), **kw)


