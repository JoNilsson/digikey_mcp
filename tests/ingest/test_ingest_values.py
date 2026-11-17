import pytest

from digikey_mcp.ingest import normalise_value


@pytest.mark.parametrize(
    "text, hint, canonical, unit",
    [
        ("4k7", None, "4.7kohm", "ohm"),
        ("4.7k", None, "4.7kohm", "ohm"),
        ("4,7k", None, "4.7kohm", "ohm"),
        ("4K7", None, "4.7kohm", "ohm"),
        ("10K", None, "10kohm", "ohm"),
        ("1M5", None, "1.5Mohm", "ohm"),
        ("1Meg", None, "1Mohm", "ohm"),
        ("0R22", None, "0.22ohm", "ohm"),
        ("4R7", None, "4.7ohm", "ohm"),
        ("47R", None, "47ohm", "ohm"),
        ("0R", None, "0ohm", "ohm"),
        ("100 ohm", None, "100ohm", "ohm"),
        ("100", "resistor", "100ohm", "ohm"),
        ("100nF", None, "100nF", "F"),
        ("0.1uF", None, "100nF", "F"),
        ("0.1 uF", None, "100nF", "F"),
        ("1000nF", None, "1uF", "F"),
        ("2u2", None, "2.2uF", "F"),
        ("1n5", None, "1.5nF", "F"),
        ("22p", "capacitor", "22pF", "F"),
        ("4.7µF", None, "4.7uF", "F"),
        ("10uH", "inductor", "10uH", "H"),
        ("2.2μH", None, "2.2uH", "H"),
    ],
)
def test_canonical_forms(text, hint, canonical, unit):
    info = normalise_value(text, hint)
    assert info.canonical == canonical
    assert info.unit == unit
    assert info.original == text
    assert info.parsed


def test_equivalent_spellings_share_one_canonical_form():
    forms = {normalise_value(t).canonical for t in ("4k7", "4.7k", "4.7 kohm", "4700")} - {"4700"}
    assert forms == {"4.7kohm"}
    assert normalise_value("0.1uF").canonical == normalise_value("100nF").canonical


def test_numeric_value_is_in_base_units():
    assert normalise_value("4k7").number == pytest.approx(4700)
    assert normalise_value("100nF").number == pytest.approx(1e-7)


def test_tolerance_and_attributes_are_split_out():
    info = normalise_value("10K 1%")
    assert (info.canonical, info.tolerance) == ("10kohm", "1%")
    info = normalise_value("100nF 50V X7R")
    assert info.canonical == "100nF"
    assert info.attributes == {"voltage": "50V", "dielectric": "X7R"}
    info = normalise_value("1/4W 5% 10k")
    assert (info.canonical, info.tolerance, info.attributes["power"]) == ("10kohm", "5%", "1/4W")


@pytest.mark.parametrize("text", ["LM358", "STM32F103C8T6", "1N4148", "3V3", "USB-C", ""])
def test_non_numeric_values_are_left_alone(text):
    info = normalise_value(text)
    assert not info.parsed
    assert info.canonical == text


def test_package_size_is_not_a_value():
    assert not normalise_value("0603", "resistor").parsed


def test_part_name_with_non_passive_hint_is_not_parsed():
    assert not normalise_value("1N914", "diode").parsed


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2 A", (2.0, "A")),
        ("200 mA", (0.2, "A")),
        ("0.5A", (0.5, "A")),
        ("32V", (32.0, "V")),
        ("6.3V", (6.3, "V")),
        ("50 V", (50.0, "V")),
        ("1.5 kV", (1500.0, "V")),
        ("32VDC", (32.0, "V")),
        ("-", None),
        ("0805 (2012 Metric)", None),
        ("±1%", None),
        ("", None),
    ],
)
def test_parse_measure(text, expected):
    from digikey_mcp.ingest.values import parse_measure

    assert parse_measure(text) == expected


@pytest.mark.parametrize(
    "text, hint, number, unit",
    [
        ("10 kOhms", "resistor", 10_000.0, "ohm"),
        ("0 Ohms", "resistor", 0.0, "ohm"),
        ("1.47 kOhms", "resistor", 1_470.0, "ohm"),
        ("4.7 mOhms", "resistor", 0.0047, "ohm"),
        ("0.1 µF", "capacitor", 1e-7, "F"),
        ("470 µF", "capacitor", 4.7e-4, "F"),
        ("100 pF", "capacitor", 1e-10, "F"),
    ],
)
def test_digikey_parameter_values_parse_to_si(text, hint, number, unit):
    info = normalise_value(text, hint)
    assert info.unit == unit and info.number == pytest.approx(number)
