"""Stub DigiKeyClient with canned v4-shaped responses, and response builders."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"
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


#: DigiKey parameter ids of the parameters the tests use; value ids are the text itself
PARAM_IDS = {
    "Resistance": 2085, "Tolerance": 3, "Package / Case": 16, "Capacitance": 2049, "Voltage - Rated": 14,
    "Temperature Coefficient": 17, "Color": 37, "Current Rating (Amps)": 2088, "Voltage Rating - DC": 2144,
    "Current - Hold (Ih) (Max)": 682, "Voltage - Max": 686, "Mounting Type": 69, "Lead Spacing": 508,
    "Size / Dimension": 46, "Connector Type": 28, "Number of Positions": 88, "Number of Rows": 90,
    "Pitch - Mating": 1790,
}


def product(mpn, maker, variations, *, status="Active", stock=None, desc="", params=None, unit_price=None,
            category="Resistors", children=(), category_id=None):
    if stock is None:
        stock = max((v["QuantityAvailableforPackageType"] for v in variations), default=0)
    return {
        "ManufacturerProductNumber": mpn,
        "Manufacturer": {"Id": 1, "Name": maker},
        "Description": {"ProductDescription": desc or f"{mpn} {maker}"},
        "QuantityAvailable": stock,
        "ProductStatus": {"Id": 0, "Status": status},
        "UnitPrice": unit_price,
        "Category": {"CategoryId": 1, "Name": category,
                     "ChildCategories": [{"CategoryId": category_id or 2, "Name": c} for c in children]},
        "ProductVariations": variations,
        "Parameters": [
            {"ParameterId": PARAM_IDS.get(k, 9999), "ParameterText": k, "ValueId": v, "ValueText": v}
            for k, v in (params or {}).items()
        ],
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
        #: DigiKey category id -> products; searched with parameter filters like the real API
        self.catalog: dict[int, list[dict]] = {}
        #: category id -> FilterOptions.ParametricFilters to answer discovery calls with (default: built from the catalog)
        self.facets: dict[int, list[dict]] = {}
        #: answer filtered searches with the whole category (a DigiKey that ignores the filter)
        self.ignore_filters = False
        self.subs: dict[str, list[dict]] = {}
        self.calls: list[tuple] = []
        self.fail_with: DigiKeyError | None = None

    def add(self, *products: dict) -> None:
        self.products.extend(products)

    def load(self, case: str) -> dict:
        """Install a captured DigiKey case (fixtures/<case>.json: real facets and products of one search).

        The filtered products go under the case's category, the unfiltered ones (what a keyword search
        returned before) under their own leaf category. Returns the fixture.
        """
        fx = json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))
        self.facets[fx["category_id"]] = fx["facets"]
        self.catalog.setdefault(fx["category_id"], []).extend(fx["products"])
        for p in fx["unfiltered"]:
            leaf = p["Category"]
            while leaf.get("ChildCategories"):
                leaf = leaf["ChildCategories"][-1]
            self.catalog.setdefault(leaf["CategoryId"], []).append(p)
        return fx

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

    def _facets_of(self, category_id):
        if category_id in self.facets:
            return self.facets[category_id]
        found: dict[tuple, dict] = {}
        for p in self.catalog.get(category_id, []):
            for x in p["Parameters"]:
                f = found.setdefault((x["ParameterId"], x["ParameterText"]), {
                    "ParameterId": x["ParameterId"], "ParameterName": x["ParameterText"], "FilterValues": {}})
                f["FilterValues"].setdefault(x["ValueId"], {"ValueId": x["ValueId"], "ValueName": x["ValueText"]})
        return [{**f, "FilterValues": list(f["FilterValues"].values())} for f in found.values()]

    def _filtered(self, category_id, filters, kw):
        out = []
        for p in self.catalog.get(category_id, []):
            if "InStock" in (kw.get("search_options") or []) and p["QuantityAvailable"] <= 0:
                continue
            if (kw.get("minimum_quantity_available") or 0) > p["QuantityAvailable"]:
                continue
            by_id = {x["ParameterId"]: x["ValueId"] for x in p["Parameters"]}
            if self.ignore_filters or all(by_id.get(int(pid)) in values for pid, values in (filters or {}).items()):
                out.append(p)
        return out

    def keyword_search(self, keywords, **kw):
        self._check("keyword_search", keywords)
        category_id = kw.get("parameter_category_id")
        if category_id is not None:
            if not kw.get("parameter_filters"):
                return {"ProductsCount": 0, "FilterOptions": {"ParametricFilters": self._facets_of(int(category_id))}}
            products = self._filtered(int(category_id), kw["parameter_filters"], kw)
            return {"ProductsCount": len(products), "Products": products}
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
    params = {"Resistance": "10 kOhms", "Tolerance": "±1%", "Package / Case": "0603 (1608 Metric)"}
    return product("RC0603FR-0710KL", "YAGEO", [ct, tr, dr], desc="RES 10K OHM 1% 1/10W 0603", params=params,
                   children=("Chip Resistor - Surface Mount",), category_id=52, **overrides)


def make_line(line_id=1, refdes=("R1",), quantity=None, **kw) -> BomLine:
    refdes = list(refdes)
    kw.setdefault("category_hint", "ic")
    if "mpn" not in kw and "supplier_pn" not in kw:
        kw["generic"] = True
    else:
        kw["generic"] = False
    return BomLine(line_id=line_id, refdes=refdes, quantity=quantity or len(refdes), **kw)


