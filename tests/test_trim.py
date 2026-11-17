"""Trim tests use fixtures shaped after the ProductSearch v4 schema definitions
(Product, ProductVariation, PriceBreak, PricingOptionsForQuantityResponse, ...)."""

import copy

from digikey_mcp import trim

PRODUCT = {
    "Description": {
        "ProductDescription": "RES 10K OHM 1% 1/10W 0603",
        "DetailedDescription": "10 kOhms 1% 0.1W, 1/10W Chip Resistor 0603 Thick Film",
    },
    "Manufacturer": {"Id": 603, "Name": "YAGEO"},
    "ManufacturerProductNumber": "RC0603FR-0710KL",
    "UnitPrice": 0.1,
    "ProductUrl": "https://www.digikey.com/en/products/detail/yageo/RC0603FR-0710KL/726880",
    "DatasheetUrl": "https://www.yageo.com/upload/media/product/RC_Group_51_RoHS_L_12.pdf",
    "PhotoUrl": "https://mm.digikey.com/Volume0/opasdata/d220001/medias/images/RC0603.jpg",
    "QuantityAvailable": 4_500_000,
    "ProductStatus": {"Id": 0, "Status": "Active"},
    "BackOrderNotAllowed": False,
    "NormallyStocking": True,
    "Discontinued": False,
    "EndOfLife": False,
    "Ncnr": False,
    "PrimaryVideoUrl": "",
    "Category": {"CategoryId": 52, "ParentId": 0, "Name": "Resistors", "ProductCount": 1000, "ChildCategories": []},
    "Series": {"Id": 1, "Name": "RC"},
    "ManufacturerLeadWeeks": "12 weeks",
    "Classifications": {
        "ReachStatus": "REACH Unaffected",
        "RohsStatus": "ROHS3 Compliant",
        "MoistureSensitivityLevel": "1 (Unlimited)",
        "ExportControlClassNumber": "EAR99",
        "HtsusCode": "8533.21.0030",
    },
    "OtherNames": ["311-10.0KHRCT-ND"],
    "Parameters": [
        {"ParameterId": 2085, "ParameterText": "Resistance", "ParameterType": "String", "ValueId": "1", "ValueText": "10 kOhms"},
        {"ParameterId": 3, "ParameterText": "Tolerance", "ParameterType": "String", "ValueId": "2", "ValueText": "±1%"},
        {"ParameterId": 2049, "ParameterText": "Package / Case", "ParameterType": "String", "ValueId": "3", "ValueText": "0603 (1608 Metric)"},
    ],
    "ProductVariations": [
        {
            "DigiKeyProductNumber": "311-10.0KHRCT-ND",
            "PackageType": {"Id": 2, "Name": "Cut Tape (CT)"},
            "StandardPricing": [
                {"BreakQuantity": 1, "UnitPrice": 0.1, "TotalPrice": 0.1},
                {"BreakQuantity": 10, "UnitPrice": 0.012, "TotalPrice": 0.12},
            ],
            "MyPricing": [],
            "MarketPlace": False,
            "TariffActive": False,
            "Supplier": {"Id": 1, "Name": "DigiKey"},
            "QuantityAvailableforPackageType": 4_500_000,
            "MaxQuantityForDistribution": 0,
            "MinimumOrderQuantity": 1,
            "StandardPackage": 1,
            "DigiReelFee": 0,
        },
        {
            "DigiKeyProductNumber": "311-10.0KHRTR-ND",
            "PackageType": {"Id": 1, "Name": "Tape & Reel (TR)"},
            "StandardPricing": [{"BreakQuantity": 5000, "UnitPrice": 0.0016, "TotalPrice": 8.0}],
            "MarketPlace": False,
            "TariffActive": False,
            "QuantityAvailableforPackageType": 4_500_000,
            "MinimumOrderQuantity": 5000,
            "StandardPackage": 5000,
        },
    ],
}


def test_trim_product_fields():
    out = trim.trim_product(PRODUCT)
    assert out["manufacturer_part_number"] == "RC0603FR-0710KL"
    assert out["manufacturer"] == "YAGEO"
    assert out["manufacturer_id"] == 603
    assert out["description"] == "RES 10K OHM 1% 1/10W 0603"
    assert out["quantity_available"] == 4_500_000
    assert out["status"] == "Active"
    assert out["discontinued"] is False and out["end_of_life"] is False
    assert out["datasheet_url"].endswith("RC_Group_51_RoHS_L_12.pdf")
    assert out["category"] == {"id": 52, "name": "Resistors"}
    assert out["rohs_status"] == "ROHS3 Compliant"
    assert out["digikey_part_numbers"] == ["311-10.0KHRCT-ND", "311-10.0KHRTR-ND"]
    assert out["parameters"]["Tolerance"] == "±1%"
    # empty values and unused bulk fields are dropped
    assert "photo_url" in out
    assert "PrimaryVideoUrl" not in out and "OtherNames" not in out


def test_trim_variations():
    cut, reel = trim.trim_product(PRODUCT)["variations"]
    assert cut == {
        "digikey_part_number": "311-10.0KHRCT-ND",
        "packaging": "Cut Tape (CT)",
        "packaging_id": 2,
        "minimum_order_quantity": 1,
        "standard_package": 1,
        "quantity_available": 4_500_000,
        "max_quantity_for_distribution": 0,
        "marketplace": False,
        "tariff_active": False,
        "supplier": "DigiKey",
        "digireel_fee": 0,
        "price_breaks": [
            {"quantity": 1, "unit_price": 0.1, "total_price": 0.1},
            {"quantity": 10, "unit_price": 0.012, "total_price": 0.12},
        ],
    }
    assert reel["minimum_order_quantity"] == 5000
    assert reel["packaging"] == "Tape & Reel (TR)"
    assert reel["price_breaks"][0]["quantity"] == 5000


def test_parameter_selection():
    assert trim.trim_product(PRODUCT, parameters=False).get("parameters") is None
    selected = trim.trim_product(PRODUCT, parameters=["tolerance"])["parameters"]
    assert selected == {"Tolerance": "±1%"}


def test_trim_does_not_mutate_input():
    before = copy.deepcopy(PRODUCT)
    trim.trim_product(PRODUCT)
    assert PRODUCT == before


def test_trim_product_details_and_empty_input():
    assert trim.trim_product_details({"Product": PRODUCT})["manufacturer"] == "YAGEO"
    assert trim.trim_product_details({}) == {}
    assert trim.trim_product({}) == {}


def test_trim_keyword_response():
    resp = {"ProductsCount": 2, "Products": [PRODUCT, PRODUCT], "ExactMatches": [PRODUCT], "FilterOptions": {"huge": 1}}
    out = trim.trim_keyword_response(resp, parameters=False)
    assert out["products_count"] == 2
    assert len(out["products"]) == 2 and len(out["exact_matches"]) == 1
    assert "FilterOptions" not in out
    assert "parameters" not in out["products"][0]
    assert trim.trim_keyword_response({"ProductsCount": 0, "Products": []}) == {"products_count": 0}


def test_trim_pricing_by_quantity():
    resp = {
        "RequestedProduct": "311-10.0KHRCT-ND",
        "RequestedQuantity": 6000,
        "ManufacturerPartNumber": "RC0603FR-0710KL",
        "Manufacturer": {"Id": 603, "Name": "YAGEO"},
        "Description": {"ProductDescription": "RES 10K OHM 1% 1/10W 0603"},
        "StandardPricingOptions": [
            {
                "PricingOption": "Cheapest",
                "TotalQuantityPriced": 6000,
                "TotalPrice": 12.5,
                "QuantityAvailable": 100,
                "Products": [
                    {
                        "DigiKeyProductNumber": "311-10.0KHRTR-ND",
                        "QuantityPriced": 5000,
                        "MinimumOrderQuantity": 5000,
                        "ExtendedPrice": 8.0,
                        "UnitPrice": 0.0016,
                        "PackageType": {"Id": 1, "Name": "Tape & Reel (TR)"},
                        "TariffInformation": {"TariffActive": False},
                        "Marketplace": False,
                    }
                ],
            }
        ],
        "MyPricingOptions": [],
    }
    out = trim.trim_pricing_by_quantity(resp)
    assert out["requested_quantity"] == 6000
    assert out["manufacturer"] == "YAGEO"
    (option,) = out["options"]
    assert option["option"] == "Cheapest" and option["total_price"] == 12.5
    assert option["products"][0] == {
        "digikey_part_number": "311-10.0KHRTR-ND",
        "packaging": "Tape & Reel (TR)",
        "quantity_priced": 5000,
        "minimum_order_quantity": 5000,
        "unit_price": 0.0016,
        "extended_price": 8.0,
        "marketplace": False,
        "tariff_active": False,
    }
    assert "my_options" not in out


SUMMARY = {
    "ProductUrl": "https://www.digikey.com/x",
    "Description": "RES 10K OHM 1% 1/10W 0603",
    "Manufacturer": {"Id": 603, "Name": "YAGEO"},
    "ManufacturerProductNumber": "RC0603FR-0710KL",
    "UnitPrice": "0.1",
    "QuantityAvailable": 10,
    "DigiKeyProductNumber": "311-10.0KHRTR-ND",
}


def test_trim_alternate_packaging_object_and_list_shapes():
    expected = {"alternate_packaging": [trim.trim_summary(SUMMARY)]}
    as_object = {"AlternatePackagings": {"AlternatePackaging": [SUMMARY]}}
    as_list = {"AlternatePackagings": [{"AlternatePackaging": [SUMMARY]}]}
    assert trim.trim_alternate_packaging(as_object) == expected
    assert trim.trim_alternate_packaging(as_list) == expected
    assert trim.trim_alternate_packaging({}) == {"alternate_packaging": []}
    assert expected["alternate_packaging"][0]["digikey_part_number"] == "311-10.0KHRTR-ND"


def test_trim_substitutions():
    resp = {
        "ProductSubstitutesCount": 1,
        "ProductSubstitutes": [{**SUMMARY, "SubstituteType": "Manufacturer Direct"}],
    }
    out = trim.trim_substitutions(resp)
    assert out["count"] == 1
    assert out["substitutes"][0]["type"] == "Manufacturer Direct"
    assert out["substitutes"][0]["manufacturer"] == "YAGEO"


def test_trim_package_type_by_quantity():
    resp = {
        "Products": [
            {
                "RecommendedQuantity": 5000,
                "DigiKeyProductNumber": "311-10.0KHRTR-ND",
                "ManufacturerProductNumber": "RC0603FR-0710KL",
                "ManufacturerName": "YAGEO",
                "PackageType": {"Id": 1, "Name": "Tape & Reel (TR)"},
                "MinimumOrderQuantity": 5000,
                "StandardPackage": 5000,
                "ProductStatus": "Active",
                "PrimaryDatasheetUrl": "https://x/ds.pdf",
                "StandardPricing": [{"BreakQuantity": 5000, "UnitPrice": 0.0016, "TotalPrice": 8.0}],
                "Supplier": "DigiKey",
            }
        ]
    }
    (p,) = trim.trim_package_type_by_quantity(resp)["products"]
    assert p["packaging"] == "Tape & Reel (TR)"
    assert p["recommended_quantity"] == 5000
    assert p["price_breaks"][0]["unit_price"] == 0.0016
    assert "Supplier" not in p


def test_trim_pricing_response():
    resp = {
        "ProductsCount": 1,
        "ProductPricings": [
            {
                "ManufacturerProductNumber": "RC0603FR-0710KL",
                "Manufacturer": {"Id": 603, "Name": "YAGEO"},
                "QuantityAvailable": 5,
                "IsDiscontinued": False,
                "IsEndOfLife": False,
                "ProductVariations": [
                    {
                        "DigiKeyProductNumber": "311-10.0KHRCT-ND",
                        "PackageType": {"Id": 2, "Name": "Cut Tape (CT)"},
                        "IsTariffActive": False,
                        "DigiReelingFee": 0,
                        "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.1, "TotalPrice": 0.1}],
                    }
                ],
            }
        ],
    }
    out = trim.trim_pricing(resp)
    (var,) = out["products"][0]["variations"]
    assert var["tariff_active"] is False and var["digireel_fee"] == 0
    assert var["price_breaks"][0]["quantity"] == 1


def test_trim_keeps_the_child_category_path():
    product = {"Category": {"CategoryId": 7, "Name": "Circuit Protection",
                            "ChildCategories": [{"CategoryId": 8, "Name": "Fuses", "ChildCategories": []}]}}
    assert trim.trim_product(product)["category"] == {"id": 7, "name": "Circuit Protection", "path": ["Fuses"]}


def test_trim_parametric_filters_keeps_the_selected_parameters():
    response = {
        "FilterOptions": {
            "Manufacturers": [{"Id": 1, "Value": "Yageo"}],
            "ParametricFilters": [
                {"ParameterId": 2085, "ParameterName": "Resistance", "ParameterType": "UnitOfMeasure",
                 "FilterValues": [{"ValueId": "10 kOhms", "ValueName": "10 kOhms", "ProductCount": 7}]},
                {"ParameterId": 16, "ParameterName": "Package / Case",
                 "FilterValues": [{"ValueId": "39329", "ValueName": "0805 (2012 Metric)"}, {"ValueName": "no id"}]},
                {"ParameterId": 46, "ParameterName": "Size / Dimension", "FilterValues": [{"ValueId": "1", "ValueName": "x"}]},
            ],
        }
    }
    assert trim.trim_parametric_filters(response, ["resistance", "Package / Case"]) == [
        {"parameter_id": 2085, "name": "Resistance", "values": [{"id": "10 kOhms", "name": "10 kOhms"}]},
        {"parameter_id": 16, "name": "Package / Case", "values": [{"id": "39329", "name": "0805 (2012 Metric)"}]},
    ]
    assert trim.trim_parametric_filters({}, ["Resistance"]) == []
