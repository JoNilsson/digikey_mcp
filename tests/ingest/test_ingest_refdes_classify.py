import pytest

from digikey_mcp.ingest import expand_refdes, extract_package, infer_category
from digikey_mcp.ingest.classify import strip_dnp


@pytest.mark.parametrize(
    "text, expected",
    [
        ("R1-R4", ["R1", "R2", "R3", "R4"]),
        ("R1 - R4", ["R1", "R2", "R3", "R4"]),
        ("R1-4", ["R1", "R2", "R3", "R4"]),
        ("R1~R3", ["R1", "R2", "R3"]),
        ("R1 to R3", ["R1", "R2", "R3"]),
        ("C1, C2,C7 C9", ["C1", "C2", "C7", "C9"]),
        ("R1-R3, C5, U2A", ["R1", "R2", "R3", "C5", "U2A"]),
        ("R1, R1, R2", ["R1", "R2"]),
        ("R08-R10", ["R08", "R09", "R10"]),
        ("", []),
    ],
)
def test_expand(text, expected):
    refs, notes = expand_refdes(text)
    assert refs == expected
    assert notes == []


def test_unsafe_ranges_are_kept_literally_with_a_note():
    refs, notes = expand_refdes("R10-R5")
    assert refs == ["R10-R5"] and notes
    refs, notes = expand_refdes("R1-C4")
    assert refs == ["R1-C4"] and notes
    refs, notes = expand_refdes("R1-R99999")
    assert refs == ["R1-R99999"] and notes


def test_hyphenated_names_are_not_ranges():
    assert expand_refdes("SW-1, TP_VCC")[0] == ["SW-1", "TP_VCC"]


@pytest.mark.parametrize(
    "refs, value, footprint, description, category",
    [
        (["R1"], "10k", None, None, "resistor"),
        (["C3"], "100nF", None, None, "capacitor"),
        (["L1"], "10uH", None, None, "inductor"),
        (["J1"], "USB-C", None, None, "connector"),
        (["U1"], "LM358", None, None, "ic"),
        (["D1"], "1N4148", None, None, "diode"),
        (["Q1"], "2N7002", None, None, "transistor"),
        (["FID1"], "Fiducial", None, None, "fiducial"),
        (["H2"], "MountingHole", "MountingHole:MountingHole_3.2mm_M3", None, "mounting_hole"),
        (["TP4"], "TestPoint", None, None, "test_point"),
        ([], "4k7", None, None, "resistor"),
        ([], "100nF", None, None, "capacitor"),
        ([], None, "Capacitor_SMD:C_0805_2012Metric", None, "capacitor"),
        ([], None, None, "Header 2x5 pin connector", "connector"),
        ([], "mystery", None, None, "other"),
    ],
)
def test_category(refs, value, footprint, description, category):
    assert infer_category(refs, value, footprint, description) == category


def test_package_extraction():
    assert extract_package("Resistor_SMD:R_0603_1608Metric") == "0603"
    assert extract_package("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm") == "SOIC-8_3.9x4.9mm_P1.27mm"
    assert extract_package("") is None


@pytest.mark.parametrize(
    "text, cleaned, found",
    [
        ("10k DNP", "10k", True),
        ("100nF (DNF)", "100nF", True),
        ("Do Not Populate", "", True),
        ("not fitted", "", True),
        ("NP0 100pF", "NP0 100pF", False),
        ("10k", "10k", False),
    ],
)
def test_strip_dnp(text, cleaned, found):
    assert strip_dnp(text) == (cleaned, found)


@pytest.mark.parametrize(
    "ref, prefix",
    [
        ("R12", "R"), ("D1", "D"), ("FU1", "FU"), ("TP_3", "TP"), ("U2A", "U"),
        ("D1608E", ""), ("OPTA", ""), ("K8AK1", ""), ("GA-IS", ""), ("P-1", ""), ("LAMP_GRN", ""),
    ],
)
def test_prefix_only_for_real_designators(ref, prefix):
    from digikey_mcp.ingest.refdes import refdes_prefix

    assert refdes_prefix(ref) == prefix


@pytest.mark.parametrize("ref", ["D1608E", "A0602", "OPTA", "K8AK1", "CS1", "RCT1", "DAT1", "GA-IS", "P-1", "LAMP_GRN"])
def test_module_names_and_labels_get_no_category(ref):
    assert infer_category([ref], None, None, None) == "other"


def test_part_number_stops_the_prefix_from_setting_a_spare_category():
    assert infer_category(["D5"], None, None, None, has_part_number=True) == "other"
    assert infer_category(["D5"], None, "Diode_SMD:D_SOD-123", None, has_part_number=True) == "diode"
    assert infer_category(["R1"], "10k", None, None, has_part_number=True) == "resistor"
    assert infer_category(["U1"], None, None, None, has_part_number=True) == "ic"
