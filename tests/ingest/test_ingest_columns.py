import pytest

from digikey_mcp.ingest import ingest
from digikey_mcp.ingest.columns import classify_header, map_headers, validate_override


@pytest.mark.parametrize(
    "header, field",
    [
        ("Mfr Part #", "mpn"),
        ("Manufacturer_Part_Number", "mpn"),
        ("MPN", "mpn"),
        ("Part Number", "mpn"),
        ("Manufacturer Part Number 1", "mpn"),
        ("Manufacturer Part Number (MPN)", "mpn"),
        ("Manufacturer", "manufacturer"),
        ("Mfr", "manufacturer"),
        ("Manufacturer 1", "manufacturer"),
        ("DigiKey PN", "digikey_pn"),
        ("DK Part", "digikey_pn"),
        ("Digi-Key Part Number", "digikey_pn"),
        ("Supplier Part Number 1", "supplier_pn"),
        ("Designator", "refdes"),
        ("Reference", "refdes"),
        ("Ref Des", "refdes"),
        ("Qty", "quantity"),
        ("QUANTITY", "quantity"),
        ("Comment", "value"),
        ("Value", "value"),
        ("Footprint", "footprint"),
        ("Package / Case", "footprint"),
        ("Description", "description"),
        ("DNP", "dnp"),
        ("Do Not Populate", "dnp"),
        ("Fitted", "populate"),
    ],
)
def test_synonyms(header, field):
    assert classify_header(header)[0] == field


@pytest.mark.parametrize("header", ["Datasheet", "Supplier 1", "LibRef", "Price", "", "  "])
def test_unrecognised_headers(header):
    assert classify_header(header) is None


def test_value_column_outranks_comment():
    idx, report, _ = map_headers(["Reference", "Comment", "Value"])
    assert idx[2] == "value"
    assert report.unmapped == ["Comment"]


def test_report_lists_mapped_unmapped_and_missing():
    _, report, _ = map_headers(["Reference", "Value", "Datasheet", "Qty"])
    assert report.mapped == {"Reference": "refdes", "Value": "value", "Qty": "quantity"}
    assert report.unmapped == ["Datasheet"]
    assert report.missing_fields == ["mpn"]
    assert report.fields["refdes"] == "Reference"


def test_override_wins_and_is_reported():
    ov = validate_override({"Teile Nr": "mpn", "Comment": "description"})
    idx, report, warnings = map_headers(["Designator", "Teile Nr", "Comment", "Value"], ov)
    assert idx == {0: "refdes", 1: "mpn", 2: "description", 3: "value"}
    assert sorted(report.overridden) == ["Comment", "Teile Nr"]
    assert warnings == []


def test_override_by_column_index():
    ov = validate_override({0: "mpn"})
    idx, _, _ = map_headers(["Reference", "Value"], ov)
    assert idx[0] == "mpn"


def test_override_rejects_unknown_field():
    with pytest.raises(ValueError):
        validate_override({"X": "colour"})


def test_override_makes_odd_headers_usable_end_to_end():
    text = "Teile Nr,Anzahl,Wert\nRC0603FR-0710KL,5,10k\n"
    res = ingest(text, mapping={"Teile Nr": "mpn", "Anzahl": "quantity", "Wert": "value"})
    assert len(res.lines) == 1
    line = res.lines[0]
    assert (line.mpn, line.quantity, line.value) == ("RC0603FR-0710KL", 5, "10kohm")
    assert sorted(res.mapping_report.overridden) == ["Anzahl", "Teile Nr", "Wert"]


def test_unused_override_is_warned():
    res = ingest("Reference,Value\nR1,10k\n", mapping={"Nope": "mpn"})
    assert any("Nope" in w or "nope" in w for w in res.warnings)
