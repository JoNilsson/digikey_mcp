"""Generic lines: DigiKey parameter filters, and every candidate verified against its own parameters.

The fixtures under fixtures/ are real DigiKey v4 responses (parameter facets of a category and the
products of a filtered search) captured for the lines of a 62-line board BOM, plus the products a
plain keyword search returned for the same line before verification (1.47k for 47k, a tantalum for
a ceramic capacitor, an infrared emitter for a red LED, PTC fuses for a blade fuse).
"""

from __future__ import annotations

import pytest
from resolve_fakes import FakeDigiKey, product, variation

from digikey_mcp.ingest import ingest
from digikey_mcp.resolve import spec as specs
from digikey_mcp.resolve.gateway import Gateway
from digikey_mcp.resolve.ladder import LineResolver
from digikey_mcp.resolve.prefs import Preferences
from digikey_mcp.resolve.store import Store

HEADER = "Reference,Qty,Value,Footprint,Description\n"

R0805 = "Resistor_SMD:R_0805_2012Metric"
LED0805 = "LED_SMD:LED_0805_2012Metric"

#: case -> (BOM row, expected verified text in every candidate, text that must never appear in a candidate)
CASES = {
    "r10k_0805": (f"R1 R2,2,10k,{R0805},10k 1% 0805", ["Resistance 10 kOhms", "0805 (2012 Metric)"], "1.47"),
    "r47k_0805": (f"R1 R73,2,47k,{R0805},47k 1% 0805", ["Resistance 47 kOhms", "0805 (2012 Metric)"], "1.47K"),
    "r0_0805": (f"R75,1,0R,{R0805},0R 1% 0805", ["Resistance 0 Ohms", "0805 (2012 Metric)"], "FLIP CHIP"),
    "c1u_1206": ("C2,1,1uF,Capacitor_SMD:C_1206_3216Metric,1 uF 100 V X7R 1206",
                 ["Capacitance 1 µF", "Voltage - Rated", "X7R", "1206 (3216 Metric)"], "0.1UF"),
    "c100n_0805": ("C3,1,100nF,Capacitor_SMD:C_0805_2012Metric,100 nF 50 V X7R 0805",
                   ["Capacitance 0.1 µF", "X7R", "0805 (2012 Metric)"], "TANT"),
    "c470u_radial": ("C1,1,470uF,Capacitor_THT:CP_Radial_D12.5mm_P5.00mm,470 uF 50 V 105 C low-ESR",
                     ["Capacitance 470 µF", "Radial", "Lead Spacing 0.197\" (5.00mm)", "Through Hole"], "TANT"),
    "led_red_0805": (f"D4 D6,2,LED red,{LED0805},LED 0805 red", ["Color Red", "0805 (2012 Metric)"], "INFRARED"),
    "led_green_0805": (f"D3 D5,2,LED green,{LED0805},LED 0805 green", ["Color Green", "0805 (2012 Metric)"], "INFRARED"),
    "fuse_ato_2a": ("F1 F5,2,2A ATO,,ATO blade fuse 2 A 32 V",
                    ["Current Rating (Amps) 2 A", "Voltage Rating - DC", "Blade, ATO/ATC"], "PTC"),
    "ptc_0p2a": ("F21,1,0.2A,,PTC-MFM: PTC; 0.2 A hold; 60 V", ["Current - Hold (Ih) (Max) 200 mA", "Voltage - Max"], "TVS"),
    "header_2pin": ("JP2 JP3,2,2-pin header,Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical,2-pin header + shunt",
                    ["Number of Positions 2", "Number of Rows 1", '0.100" (2.54mm)', "Through Hole"], "2.50"),
}


def bom_line(row: str):
    return ingest(HEADER + row + "\n", fmt="csv").lines[0]


@pytest.fixture
def resolver(clock, cache):
    store = Store(cache)
    prefs = Preferences()
    fake = FakeDigiKey()
    gw = Gateway(fake, store, prefs, clock=clock, sleep=clock.sleep)
    r = LineResolver(gw, prefs)
    r.fake = fake
    yield r
    store.close()


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("ignore_filters", [False, True])
def test_candidates_are_verified_against_the_line(resolver, case, ignore_filters):
    row, expected, forbidden = CASES[case]
    resolver.fake.load(case)
    resolver.fake.ignore_filters = ignore_filters  # a DigiKey that returns the whole category
    rl = resolver.resolve(bom_line(row), boards=1)
    assert rl.status == "needs_decision" and rl.decided is False
    assert 1 <= len(rl.candidates) <= 5, rl.reason
    for c in rl.candidates:
        assert all(text in c.why for text in expected), c.why
        assert forbidden.casefold() not in (c.description + " " + c.why).casefold()
        assert c.stock > 0 and c.unit_price


def test_47k_never_offers_1_47k(resolver):
    fx = resolver.fake.load("r47k_0805")
    assert any("1.47KOHM" in p["Description"]["ProductDescription"] for p in fx["unfiltered"])  # the old result
    rl = resolver.resolve(bom_line(CASES["r47k_0805"][0]), boards=1)
    assert rl.candidates and all("47K OHM" in c.description for c in rl.candidates)


def test_zero_ohm_is_a_jumper_not_a_flip_chip(resolver):
    resolver.fake.load("r0_0805")
    rl = resolver.resolve(bom_line(CASES["r0_0805"][0]), boards=1)
    assert all("JUMPER" in c.description or "0 OHM" in c.description for c in rl.candidates)


def test_ceramic_capacitor_is_not_a_tantalum(resolver):
    fx = resolver.fake.load("c100n_0805")
    assert any("TANT" in p["Description"]["ProductDescription"] for p in fx["unfiltered"])
    resolver.fake.ignore_filters = True
    rl = resolver.resolve(bom_line(CASES["c100n_0805"][0]), boards=1)
    assert rl.candidates and not any("TANT" in c.description for c in rl.candidates)


def test_one_microfarad_is_not_a_tenth(resolver):
    resolver.fake.load("c1u_1206")
    resolver.fake.ignore_filters = True
    rl = resolver.resolve(bom_line(CASES["c1u_1206"][0]), boards=1)
    assert rl.candidates and all("1UF" in c.description and "0.1UF" not in c.description for c in rl.candidates)


def test_candidates_state_the_voltage_rating_at_least_the_bom_voltage(resolver):
    resolver.fake.load("c1u_1206")
    rl = resolver.resolve(bom_line(CASES["c1u_1206"][0]), boards=1)
    volts = [float(c.why.split("Voltage - Rated ")[1].split("V")[0]) for c in rl.candidates]
    assert volts and min(volts) >= 100


def test_infrared_emitter_is_not_a_visible_led(resolver):
    resolver.fake.load("led_red_0805")
    ir = product(
        "IR26-21C", "Everlight", [variation("1080-IR26-ND", "Cut Tape (CT)", breaks=((1, 0.2),))],
        desc="INFRARED EMITTING DIODE 940NM 0805", category="Optoelectronics", children=("LED Indication - Discrete",),
        params={"Color": "Infrared", "Package / Case": "0805 (2012 Metric)"}, category_id=specs.CAT_LED,
    )
    resolver.fake.catalog[specs.CAT_LED].append(ir)
    resolver.fake.ignore_filters = True
    rl = resolver.resolve(bom_line(CASES["led_red_0805"][0]), boards=1)
    assert rl.candidates and all("Color Red" in c.why for c in rl.candidates)
    assert not any("INFRARED" in c.description for c in rl.candidates)


def test_ptc_is_not_a_blade_fuse(resolver):
    resolver.fake.load("fuse_ato_2a")
    resolver.fake.ignore_filters = True
    rl = resolver.resolve(bom_line(CASES["fuse_ato_2a"][0]), boards=1)
    assert rl.candidates and all("ATO" in c.why for c in rl.candidates)
    assert not any("PTC" in c.description for c in rl.candidates)


def test_no_verified_candidate_says_which_parameter_could_not_be_matched(resolver):
    resolver.fake.load("fuse_ato_1p5a")
    rl = resolver.resolve(bom_line("F8,1,1.5A ATO,,FUSE-LIM: 1.5 A ATO 32 V; provisional"), boards=1)
    assert rl.status == "needs_decision" and rl.candidates == []
    assert "Current Rating (Amps) 1.5A" in rl.reason and "MPN" in rl.reason


def test_results_that_all_fail_the_check_give_the_failing_parameter(resolver):
    fake = resolver.fake
    # DigiKey offers the 0805 value but returns a 0603 part for it
    fake.facets[specs.CAT_CHIP_RESISTOR] = [
        {"ParameterId": 2085, "ParameterName": "Resistance", "FilterValues": [{"ValueId": "10 kOhms", "ValueName": "10 kOhms"}]},
        {"ParameterId": 16, "ParameterName": "Package / Case",
         "FilterValues": [{"ValueId": "39329", "ValueName": "0805 (2012 Metric)"}]},
    ]
    fake.catalog[specs.CAT_CHIP_RESISTOR] = [
        product("R-0603", "Yageo", [variation("R-0603-ND", "Cut Tape (CT)", breaks=((1, 0.1),))],
                params={"Resistance": "10 kOhms", "Tolerance": "±1%", "Package / Case": "0603 (1608 Metric)"})
    ]
    fake.ignore_filters = True
    rl = resolver.resolve(bom_line(CASES["r10k_0805"][0]), boards=1)
    assert rl.candidates == []
    assert "none of 1 resistor results matched" in rl.reason and "Package / Case 0805" in rl.reason


def test_no_facet_value_for_the_line_gives_no_search(resolver):
    resolver.fake.load("c100n_0805")
    rl = resolver.resolve(bom_line("C9,1,22nF,Capacitor_SMD:C_0805_2012Metric,22 nF X7R 0805"), boards=1)
    assert rl.candidates == [] and "no in-stock ceramic capacitor with Capacitance 22nF" in rl.reason
    assert resolver.fake.names() == ["keyword_search"]  # the facet call only


def test_parameter_filter_ids_come_from_the_facets(resolver):
    fake = resolver.fake
    fake.load("r10k_0805")
    resolver.resolve(bom_line(CASES["r10k_0805"][0]), boards=1)
    assert fake.names().count("keyword_search") == 2  # facet discovery, then the filtered search
    resolver.resolve(bom_line(f"R9,1,10k,{R0805},10k 1% 0805"), boards=1)
    assert fake.names().count("keyword_search") == 3  # the facets are cached, the search is not


# ---- the specs ----------------------------------------------------------------


def labels(row):
    s = specs.spec_for(bom_line(row))
    return s.kind, s.category_id, {w.label: w.text for w in s.wants}


def test_spec_resistor():
    kind, cat, wants = labels(f"R1,1,47k,{R0805},47k 1% 0805")
    assert (kind, cat) == ("resistor", specs.CAT_CHIP_RESISTOR)
    assert wants == {"Resistance": "47kohm", "Tolerance": "1% or tighter", "Package / Case": "0805"}


def test_spec_zero_ohm_jumper_has_no_tolerance():
    assert "Tolerance" not in labels(f"R75,1,0R,{R0805},0R 1% 0805")[2]


def test_spec_capacitors_by_type():
    assert labels("C1,1,1uF,Capacitor_SMD:C_0805_2012Metric,1 uF 50 V X7R 0805")[:2] == ("ceramic capacitor", specs.CAT_CERAMIC_CAP)
    assert labels("C1,1,22uF,Capacitor_SMD:C_1206_3216Metric,22 uF 16 V 1206")[1] == specs.CAT_CERAMIC_CAP  # no tantalum unasked
    assert labels("C1,1,10uF,Capacitor_SMD:C_1206_3216Metric,10 uF 16 V tantalum")[1] == specs.CAT_TANTALUM_CAP
    _, cat, wants = labels("C1,1,470uF,Capacitor_THT:CP_Radial_D12.5mm_P5.00mm,470 uF 50 V")
    assert (cat, wants["Lead Spacing"]) == (specs.CAT_ALUMINUM_CAP, "5mm pitch")
    assert wants["Voltage - Rated"] == "50V or more"


def test_spec_fuses():
    assert labels("F1,1,10A ATO,,ATO blade fuse 10 A 32 V")[:2] == ("ATO blade fuse", specs.CAT_FUSE)
    assert labels("F9,1,0.5A,,PTC; 0.5 A hold; 60 V")[:2] == ("PTC resettable fuse", specs.CAT_PTC_FUSE)
    assert specs.spec_for(bom_line("F9,1,1A,,fast blow fuse")) is None


def test_spec_unsupported_part_types_have_no_verifier():
    assert specs.spec_for(bom_line("U2,1,5V,Package_TO_SOT_SMD:SOT-223-3_TabPin2,LDO linear 5 V")) is None
    assert specs.spec_for(bom_line("D7,1,33V,Diode_SMD:D_SMB,TVS 33 V")) is None


def test_value_match_is_exact_not_close():
    s = specs.spec_for(bom_line(f"R1,1,680,{R0805},680 R 1% 0805"))
    _, bad = s.verify({"Resistance": "680 Ohms", "Tolerance": "±1%", "Package / Case": "0805 (2012 Metric)"})
    assert bad is None
    _, bad = s.verify({"Resistance": "681 Ohms", "Tolerance": "±1%", "Package / Case": "0805 (2012 Metric)"})
    assert bad is not None and bad.label == "Resistance"
    _, bad = s.verify({"Resistance": "6.8 kOhms", "Tolerance": "±1%", "Package / Case": "0805 (2012 Metric)"})
    assert bad is not None
    # 0603 is not 0201 (0603 Metric)
    s = specs.spec_for(bom_line("R1,1,10k,Resistor_SMD:R_0603_1608Metric,10k 1% 0603"))
    _, bad = s.verify({"Resistance": "10 kOhms", "Tolerance": "±1%", "Package / Case": "0201 (0603 Metric)"})
    assert bad is not None and bad.label == "Package / Case"
