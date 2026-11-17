"""Reduce DigiKey v4 responses to the fields a BOM workflow needs.

Field paths follow the ProductSearch v4 schema (Product, ProductVariation,
PricingOptionsForQuantity, ProductSummary, ...). Every function tolerates
missing keys and never mutates its input. Empty values are dropped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

Parameters = bool | Iterable[str]


def _compact(d: Mapping[str, Any]) -> dict[str, Any]:
    """Drop None, empty strings and empty containers; keep 0 and False."""
    return {k: v for k, v in d.items() if v is not None and v != "" and v != [] and v != {}}


def _name(obj: Any) -> str | None:
    return obj.get("Name") if isinstance(obj, Mapping) else None


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def trim_price_breaks(breaks: Any) -> list[dict[str, Any]]:
    return [
        _compact(
            {
                "quantity": b.get("BreakQuantity"),
                "unit_price": b.get("UnitPrice"),
                "total_price": b.get("TotalPrice"),
            }
        )
        for b in _list(breaks)
        if isinstance(b, Mapping)
    ]


def trim_variation(v: Mapping[str, Any]) -> dict[str, Any]:
    """One packaging variation (ProductVariation or ProductPricingVariation)."""
    package = v.get("PackageType") or {}
    supplier = v.get("Supplier")
    return _compact(
        {
            "digikey_part_number": v.get("DigiKeyProductNumber"),
            "packaging": _name(package),
            "packaging_id": package.get("Id") if isinstance(package, Mapping) else None,
            "minimum_order_quantity": v.get("MinimumOrderQuantity"),
            "standard_package": v.get("StandardPackage"),
            "quantity_available": v.get("QuantityAvailableforPackageType"),
            "max_quantity_for_distribution": v.get("MaxQuantityForDistribution"),
            "marketplace": v.get("MarketPlace"),
            "tariff_active": v.get("TariffActive", v.get("IsTariffActive")),
            "supplier": _name(supplier),
            "digireel_fee": v.get("DigiReelFee", v.get("DigiReelingFee")),
            "price_breaks": trim_price_breaks(v.get("StandardPricing")),
            "my_price_breaks": trim_price_breaks(v.get("MyPricing")),
        }
    )


def trim_parameters(params: Any, select: Parameters = True) -> dict[str, str]:
    """Map ParameterText to ValueText.

    `select` is True for all parameters, False for none, or an iterable of
    parameter names (case-insensitive) to keep.
    """
    if select is False:
        return {}
    wanted = None if select is True else {s.lower() for s in select}
    out: dict[str, str] = {}
    for p in _list(params):
        if not isinstance(p, Mapping):
            continue
        name = p.get("ParameterText")
        if not name or (wanted is not None and name.lower() not in wanted):
            continue
        out.setdefault(name, p.get("ValueText"))
    return out


def _category_path(category: Any) -> list[str]:
    """Names of the child categories below a v4 Category, parent before child."""
    names: list[str] = []
    for child in _list(category.get("ChildCategories")) if isinstance(category, Mapping) else []:
        if isinstance(child, Mapping):
            names += [str(child["Name"])] if child.get("Name") else []
            names += _category_path(child)
    return names


def trim_product(product: Mapping[str, Any], parameters: Parameters = True) -> dict[str, Any]:
    """Trim a v4 Product (from keyword search or productdetails)."""
    description = product.get("Description") or {}
    manufacturer = product.get("Manufacturer") or {}
    status = product.get("ProductStatus") or {}
    category = product.get("Category") or {}
    classifications = product.get("Classifications") or {}
    variations = [trim_variation(v) for v in _list(product.get("ProductVariations")) if isinstance(v, Mapping)]
    return _compact(
        {
            "manufacturer_part_number": product.get("ManufacturerProductNumber"),
            "manufacturer": manufacturer.get("Name"),
            "manufacturer_id": manufacturer.get("Id"),
            "description": description.get("ProductDescription"),
            "detailed_description": description.get("DetailedDescription"),
            "quantity_available": product.get("QuantityAvailable"),
            "status": status.get("Status"),
            "discontinued": product.get("Discontinued"),
            "end_of_life": product.get("EndOfLife"),
            "normally_stocking": product.get("NormallyStocking"),
            "back_order_not_allowed": product.get("BackOrderNotAllowed"),
            "non_cancellable_non_returnable": product.get("Ncnr"),
            "date_last_buy_chance": product.get("DateLastBuyChance"),
            "manufacturer_lead_weeks": product.get("ManufacturerLeadWeeks"),
            "unit_price": product.get("UnitPrice"),
            "datasheet_url": product.get("DatasheetUrl"),
            "product_url": product.get("ProductUrl"),
            "photo_url": product.get("PhotoUrl"),
            "category": _compact({"id": category.get("CategoryId"), "name": category.get("Name"),
                                   "path": _category_path(category)}),
            "series": _name(product.get("Series")),
            "rohs_status": classifications.get("RohsStatus"),
            "reach_status": classifications.get("ReachStatus"),
            "moisture_sensitivity_level": classifications.get("MoistureSensitivityLevel"),
            "digikey_part_numbers": [v["digikey_part_number"] for v in variations if "digikey_part_number" in v],
            "variations": variations,
            "parameters": trim_parameters(product.get("Parameters"), parameters),
        }
    )


def trim_product_details(response: Mapping[str, Any], parameters: Parameters = True) -> dict[str, Any]:
    """Trim a productdetails response ({"Product": {...}})."""
    return trim_product(response.get("Product") or {}, parameters)


def trim_parametric_filters(response: Mapping[str, Any], select: Iterable[str]) -> list[dict[str, Any]]:
    """The selectable parameter values of a category-filtered keyword search.

    Keeps the parameters named in `select` (case-insensitive) as
    {"parameter_id", "name", "values": [{"id", "name"}]}.
    """
    wanted = {s.lower() for s in select}
    out = []
    for f in _list((response.get("FilterOptions") or {}).get("ParametricFilters")):
        if not isinstance(f, Mapping) or str(f.get("ParameterName", "")).lower() not in wanted:
            continue
        values = [
            {"id": str(v.get("ValueId")), "name": str(v.get("ValueName"))}
            for v in _list(f.get("FilterValues"))
            if isinstance(v, Mapping) and v.get("ValueId") is not None
        ]
        out.append({"parameter_id": f.get("ParameterId"), "name": f.get("ParameterName"), "values": values})
    return out


def trim_keyword_response(response: Mapping[str, Any], parameters: Parameters = True) -> dict[str, Any]:
    """Trim a keyword search response."""
    return _compact(
        {
            "products_count": response.get("ProductsCount"),
            "exact_matches": [
                trim_product(p, parameters) for p in _list(response.get("ExactMatches")) if isinstance(p, Mapping)
            ],
            "products": [
                trim_product(p, parameters) for p in _list(response.get("Products")) if isinstance(p, Mapping)
            ],
        }
    )


def trim_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    """Trim a ProductSummary or ProductSubstitute entry."""
    return _compact(
        {
            "type": item.get("SubstituteType"),
            "digikey_part_number": item.get("DigiKeyProductNumber"),
            "manufacturer_part_number": item.get("ManufacturerProductNumber"),
            "manufacturer": _name(item.get("Manufacturer")),
            "description": item.get("Description"),
            "unit_price": item.get("UnitPrice"),
            "quantity_available": item.get("QuantityAvailable"),
            "product_url": item.get("ProductUrl"),
        }
    )


def trim_substitutions(response: Mapping[str, Any]) -> dict[str, Any]:
    return _compact(
        {
            "count": response.get("ProductSubstitutesCount"),
            "substitutes": [
                trim_summary(s) for s in _list(response.get("ProductSubstitutes")) if isinstance(s, Mapping)
            ],
        }
    )


def trim_alternate_packaging(response: Mapping[str, Any]) -> dict[str, Any]:
    """Trim an alternatepackaging response.

    The schema nests the list as AlternatePackagings.AlternatePackaging; the
    wrapper may arrive as an object or a one-element list, so both are flattened.
    """
    items: list[Mapping[str, Any]] = []
    for wrapper in _list(response.get("AlternatePackagings")):
        if isinstance(wrapper, Mapping):
            items.extend(i for i in _list(wrapper.get("AlternatePackaging")) if isinstance(i, Mapping))
    return {"alternate_packaging": [trim_summary(i) for i in items]}


def _trim_option(option: Mapping[str, Any]) -> dict[str, Any]:
    products = []
    for p in _list(option.get("Products")):
        if not isinstance(p, Mapping):
            continue
        products.append(
            _compact(
                {
                    "digikey_part_number": p.get("DigiKeyProductNumber"),
                    "packaging": _name(p.get("PackageType")),
                    "quantity_priced": p.get("QuantityPriced"),
                    "minimum_order_quantity": p.get("MinimumOrderQuantity"),
                    "unit_price": p.get("UnitPrice"),
                    "extended_price": p.get("ExtendedPrice"),
                    "marketplace": p.get("Marketplace"),
                    "tariff_active": (p.get("TariffInformation") or {}).get("TariffActive"),
                }
            )
        )
    return _compact(
        {
            "option": option.get("PricingOption"),
            "total_quantity_priced": option.get("TotalQuantityPriced"),
            "total_price": option.get("TotalPrice"),
            "quantity_available": option.get("QuantityAvailable"),
            "products": products,
        }
    )


def trim_pricing_by_quantity(response: Mapping[str, Any]) -> dict[str, Any]:
    return _compact(
        {
            "requested_product": response.get("RequestedProduct"),
            "requested_quantity": response.get("RequestedQuantity"),
            "manufacturer_part_number": response.get("ManufacturerPartNumber"),
            "manufacturer": _name(response.get("Manufacturer")),
            "description": (response.get("Description") or {}).get("ProductDescription"),
            "product_url": response.get("ProductUrl"),
            "options": [_trim_option(o) for o in _list(response.get("StandardPricingOptions")) if isinstance(o, Mapping)],
            "my_options": [_trim_option(o) for o in _list(response.get("MyPricingOptions")) if isinstance(o, Mapping)],
        }
    )


def trim_package_type_by_quantity(response: Mapping[str, Any]) -> dict[str, Any]:
    products = []
    for p in _list(response.get("Products")):
        if not isinstance(p, Mapping):
            continue
        products.append(
            _compact(
                {
                    "digikey_part_number": p.get("DigiKeyProductNumber"),
                    "manufacturer_part_number": p.get("ManufacturerProductNumber"),
                    "manufacturer": p.get("ManufacturerName"),
                    "description": p.get("ProductDescription"),
                    "packaging": _name(p.get("PackageType")),
                    "recommended_quantity": p.get("RecommendedQuantity"),
                    "minimum_order_quantity": p.get("MinimumOrderQuantity"),
                    "standard_package": p.get("StandardPackage"),
                    "quantity_available": p.get("QuantityAvailable"),
                    "status": p.get("ProductStatus"),
                    "datasheet_url": p.get("PrimaryDatasheetUrl"),
                    "product_url": p.get("ProductUrl"),
                    "marketplace": p.get("MarketPlace"),
                    "price_breaks": trim_price_breaks(p.get("StandardPricing")),
                }
            )
        )
    return {"products": products}


def trim_pricing(response: Mapping[str, Any]) -> dict[str, Any]:
    """Trim a /pricing response (ProductPricingResponse)."""
    products = []
    for p in _list(response.get("ProductPricings")):
        if not isinstance(p, Mapping):
            continue
        variations = [trim_variation(v) for v in _list(p.get("ProductVariations")) if isinstance(v, Mapping)]
        products.append(
            _compact(
                {
                    "manufacturer_part_number": p.get("ManufacturerProductNumber"),
                    "manufacturer": _name(p.get("Manufacturer")),
                    "description": (p.get("Description") or {}).get("ProductDescription"),
                    "quantity_available": p.get("QuantityAvailable"),
                    "discontinued": p.get("IsDiscontinued"),
                    "end_of_life": p.get("IsEndOfLife"),
                    "standard_package": p.get("StandardPackage"),
                    "product_url": p.get("ProductUrl"),
                    "variations": variations,
                }
            )
        )
    return _compact({"products_count": response.get("ProductsCount"), "products": products})
