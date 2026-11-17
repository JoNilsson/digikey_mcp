"""Each rung of the resolution ladder, manufacturer aliasing, verification and substitutes."""

from __future__ import annotations

import pytest
from resolve_fakes import make_line, product, resistor_10k, substitute, variation

from digikey_mcp.resolve.gateway import Gateway
from digikey_mcp.resolve.ladder import LineResolver
from digikey_mcp.resolve.names import canonical_manufacturer, identity_key, manufacturers_match
from digikey_mcp.resolve.prefs import Preferences
from digikey_mcp.resolve.store import Store


@pytest.fixture
def resolver(fake, clock, cache):
    store = Store(cache)
    prefs = Preferences()
    gw = Gateway(fake, store, prefs, clock=clock, sleep=clock.sleep)
    yield LineResolver(gw, prefs)
    store.close()


def lm358(maker="Texas Instruments", **kw):
    return product("LM358DR", maker, [variation("296-1014-1-ND", "Cut Tape (CT)", breaks=((1, 0.5), (100, 0.3)))], **kw)


def test_rung_a_supplier_pn_uses_productdetails(resolver, fake):
    fake.add(resistor_10k())
    line = make_line(refdes=["R1", "R2", "R3"], supplier_pn="311-10.0KHRCT-ND", category_hint="resistor")
    rl = resolver.resolve(line, boards=1)
    assert rl.status == "resolved" and rl.source == "supplier_pn"
    assert rl.dk_pn == "311-10.0KHRCT-ND"  # the user's packaging is kept
    assert fake.names() == ["product_details"]
    assert rl.cref == "R1,R2,R3"


def test_rung_a_unknown_pn_is_unavailable(resolver, fake):
    rl = resolver.resolve(make_line(supplier_pn="NOPE-ND"), boards=1)
    assert rl.status == "unavailable" and "not found" in rl.reason


def test_supplier_pn_flags_cheaper_packaging(resolver, fake):
    fake.add(resistor_10k())
    line = make_line(quantity=3000, supplier_pn="311-10.0KHRCT-ND", category_hint="other")
    rl = resolver.resolve(line, boards=1)
    assert rl.dk_pn == "311-10.0KHRCT-ND" and rl.status == "resolved"
    assert any(f.startswith("CHEAPER_PACKAGING:311-10.0KHRTR-ND") for f in rl.flags)


def test_rung_b_mpn_and_manufacturer(resolver, fake):
    fake.add(lm358(), lm358("Some Other Corp"))
    rl = resolver.resolve(make_line(mpn="LM358DR", manufacturer="TI", quantity=3), boards=1)
    assert rl.status == "resolved" and rl.source == "mpn+manufacturer"
    assert rl.manufacturer == "Texas Instruments"
    assert rl.dk_pn == "296-1014-1-ND"
    assert fake.names() == ["keyword_search"]


def test_rung_b_manufacturer_mismatch_is_ambiguous(resolver, fake):
    fake.add(lm358())
    rl = resolver.resolve(make_line(mpn="LM358DR", manufacturer="Microchip"), boards=1)
    assert rl.status == "ambiguous"
    assert rl.candidates and rl.candidates[0].manufacturer == "Texas Instruments"
    assert "not Microchip" in rl.reason


def test_rung_c_mpn_only_single_manufacturer_resolves(resolver, fake):
    fake.add(lm358())
    rl = resolver.resolve(make_line(mpn="lm358dr"), boards=1)
    assert rl.status == "resolved" and rl.source == "mpn"


def test_rung_c_mpn_only_several_manufacturers_is_ambiguous(resolver, fake):
    fake.add(lm358(), lm358("ON Semiconductor"))
    rl = resolver.resolve(make_line(mpn="LM358DR"), boards=1)
    assert rl.status == "ambiguous"
    assert {c.manufacturer for c in rl.candidates} == {"Texas Instruments", "ON Semiconductor"}
    assert rl.dk_pn is None


def test_no_exact_mpn_lists_near_matches(resolver, fake):
    fake.add(lm358())
    rl = resolver.resolve(make_line(mpn="LM358"), boards=1)
    assert rl.status == "ambiguous" and "near match" in rl.reason


def test_mpn_not_found(resolver, fake):
    rl = resolver.resolve(make_line(mpn="XYZ123"), boards=1)
    assert rl.status == "unavailable" and "not found" in rl.reason


def test_generic_line_never_auto_picks(resolver, fake):
    # one perfect, cheap, in-stock match still needs the user's decision
    fake.searches["10kohm 0603 resistor"] = [resistor_10k()]
    line = make_line(refdes=["R1", "R2"], value="10kohm", package="0603", footprint="R_0603", category_hint="resistor")
    assert line.generic
    rl = resolver.resolve(line, boards=1)
    assert rl.status == "needs_decision"
    assert rl.dk_pn is None and rl.order_qty is None
    assert 1 <= len(rl.candidates) <= 5
    best = rl.candidates[0]
    assert best.mpn == "RC0603FR-0710KL" and best.stock > 0 and best.unit_price and best.why
    assert rl.needed_qty == 4  # 2 + max(ceil(0.2), 2)


def test_generic_candidates_ranked_and_capped(resolver, fake):
    items = []
    for i in range(8):
        items.append(
            product(
                f"RES-{i}", "Yageo" if i == 3 else "Generic",
                [variation(f"RES-{i}-ND", "Cut Tape (CT)", stock=0 if i == 0 else 5000, breaks=((1, 0.1 + i * 0.01),))],
                desc="10k 0603 resistor", params={"Package / Case": "0603"},
            )
        )
    fake.searches["10kohm 0603 resistor"] = items
    prefs = Preferences.from_dict({"generic": {"preferred_manufacturers": ["Yageo"]}})
    resolver.prefs = prefs
    resolver.gw.prefs = prefs
    rl = resolver.resolve(make_line(value="10kohm", package="0603", category_hint="resistor"), boards=1)
    assert len(rl.candidates) == 5
    assert rl.candidates[0].manufacturer == "Yageo" and "preferred manufacturer" in rl.candidates[0].why
    assert all(c.mpn != "RES-0" for c in rl.candidates)  # out of stock ranks last


def test_manufacturer_aliasing():
    assert manufacturers_match("Texas Instruments", "TI")
    assert manufacturers_match("STMicroelectronics", "ST")
    assert manufacturers_match("Texas Instruments Inc.", "texas instruments")
    assert manufacturers_match("NXP USA Inc.", "NXP Semiconductors")
    assert manufacturers_match("onsemi", "ON Semiconductor")
    assert not manufacturers_match("Texas Instruments", "Microchip")
    assert not manufacturers_match("", "TI")
    assert canonical_manufacturer("Analog Devices Inc") == "analog devices"


def test_dnp_and_non_part_lines_are_skipped(resolver, fake):
    rl = resolver.resolve(make_line(mpn="LM358DR", dnp=True), boards=1)
    assert rl.status == "dnp_skipped" and fake.calls == []
    fid = make_line(category_hint="fiducial")
    assert resolver.resolve(fid, boards=1).status == "dnp_skipped"


def test_skip_decision(resolver, fake):
    rl = resolver.resolve(make_line(mpn="LM358DR"), boards=1, decision="skip")
    assert rl.status == "skipped" and rl.decided and fake.calls == []


def test_out_of_stock_attaches_ranked_substitutes_without_applying(resolver, fake):
    p = product("LM358DR", "Texas Instruments", [variation("296-1014-1-ND", "Cut Tape (CT)", stock=2)])
    fake.add(p)
    fake.subs["296-1014-1-ND"] = [
        substitute("SUB-LOW-ND", "AAA1", "Foo", "Parametric Equivalent", stock=50000, price=0.1),
        substitute("SUB-DIRECT-ND", "LM358ADR", "Texas Instruments", "Manufacturer Direct", stock=9000, price=0.4),
        substitute("SUB-EMPTY-ND", "BBB2", "Bar", "Manufacturer Direct", stock=0, price=0.1),
    ]
    rl = resolver.resolve(make_line(mpn="LM358DR", quantity=10), boards=1)
    assert rl.status == "unavailable" and "stock 2" in rl.reason
    assert rl.order_qty is None  # nothing was swapped in
    assert [s.dk_pn for s in rl.substitutes] == ["SUB-DIRECT-ND", "SUB-LOW-ND", "SUB-EMPTY-ND"]
    assert [s.confidence for s in rl.substitutes] == ["high", "medium", "high"]
    assert "stock 0 below" in rl.substitutes[-1].why


@pytest.mark.parametrize(
    ("status", "flag", "expected"),
    [
        ("Not For New Designs", "NRND", "resolved"),
        ("Last Time Buy", "LAST_TIME_BUY", "resolved"),
        ("Obsolete", "OBSOLETE", "unavailable"),
        ("Discontinued at Digi-Key", "DISCONTINUED", "unavailable"),
    ],
)
def test_product_status_flags(resolver, fake, status, flag, expected):
    fake.add(lm358(status=status))
    rl = resolver.resolve(make_line(mpn="LM358DR"), boards=1)
    assert flag in rl.flags and rl.status == expected
    if expected == "resolved":
        assert flag in rl.reason


def test_obsolete_allowed_by_preference(resolver, fake):
    fake.add(lm358(status="Obsolete"))
    resolver.prefs = Preferences.from_dict({"packaging": {"allow_obsolete": True}})
    rl = resolver.resolve(make_line(mpn="LM358DR"), boards=1)
    assert rl.status == "resolved" and "OBSOLETE" in rl.flags


def test_marketplace_product_is_not_orderable_by_default(resolver, fake):
    p = product("MKT1", "Seller", [variation("MKT1-ND", "Bulk", marketplace=True)])
    fake.add(p)
    rl = resolver.resolve(make_line(mpn="MKT1"), boards=1)
    assert rl.status == "unavailable" and "marketplace" in rl.reason and "MARKETPLACE" in rl.flags
    resolver.prefs = Preferences.from_dict({"packaging": {"allow_marketplace": True}})
    assert resolver.resolve(make_line(mpn="MKT1"), boards=1).status == "resolved"


def test_api_error_becomes_error_status(resolver, fake):
    from digikey_mcp.client import DigiKeyError

    fake.fail_with = DigiKeyError("boom", status=500)
    rl = resolver.resolve(make_line(mpn="LM358DR"), boards=1)
    assert rl.status == "error" and "boom" in rl.reason


def test_identity_key():
    assert identity_key(make_line(mpn=" LM358 DR ")) == identity_key(make_line(line_id=9, mpn="lm358dr"))
    a = make_line(value="100nF", footprint="C_0603", category_hint="capacitor")
    b = make_line(line_id=4, refdes=("C9",), value="100 nF", footprint="c_0603", category_hint="capacitor")
    assert identity_key(a) == identity_key(b)
    assert identity_key(a) != identity_key(make_line(value="1uF", footprint="C_0603", category_hint="capacitor"))


def _fuse(mpn="0287002.PXCN", category="Circuit Protection", children=("Fuses", "Automotive Fuses")):
    return product(
        mpn, "Littelfuse",
        [variation("F2398-ND", "Bulk", breaks=((1, 0.5), (100, 0.3)))],
        desc="FUSE BLADE 32VDC 2A ATO", category=category, children=children,
    )


def _led():
    return product(
        "LTST-C190KGKT", "Lite-On",
        [variation("160-1435-1-ND", "Cut Tape (CT)", breaks=((1, 0.16),))],
        desc="LED GREEN CLEAR 0603 SMD", category="Optoelectronics", children=("LED Indication - Discrete",),
    )


@pytest.mark.parametrize("hint, refs", [("fuse", ["FU1", "FU2"]), ("switch", ["S1"])])
def test_valueless_generic_line_does_not_search(resolver, fake, hint, refs):
    # a bare category word returned unrelated parts before (fuse -> LEDs and tactile switches)
    fake.searches["fuse"] = fake.searches["switch"] = [_led()]
    rl = resolver.resolve(make_line(refdes=refs, category_hint=hint), boards=1)
    assert rl.status == "needs_decision" and rl.candidates == []
    assert "no value, rating, package or description" in rl.reason and "MPN" in rl.reason
    assert fake.names() == []


def test_generic_fuse_with_rating_finds_fuses_only(resolver, fake):
    fake.searches["2A 32V fuse"] = [_led(), _fuse()]
    rl = resolver.resolve(make_line(refdes=["F1"], value="2A 32V", category_hint="fuse"), boards=1)
    assert rl.status == "needs_decision"
    assert [c.mpn for c in rl.candidates] == ["0287002.PXCN"]


def test_candidates_outside_the_hinted_category_are_dropped(resolver, fake):
    fake.searches["2A 32V fuse"] = [_led(), _fuse(category="Switches", children=())]
    rl = resolver.resolve(make_line(refdes=["F1"], value="2A 32V", category_hint="fuse"), boards=1)
    assert rl.candidates == []


def test_unknown_product_category_is_dropped_when_the_hint_has_a_rule(resolver, fake):
    fake.searches["2A 32V fuse"] = [_fuse(category="", children=())]
    rl = resolver.resolve(make_line(refdes=["F1"], value="2A 32V", category_hint="fuse"), boards=1)
    assert rl.candidates == []


def test_generic_resistor_still_returns_candidates(resolver, fake):
    fake.searches["10kohm 0603 resistor"] = [resistor_10k()]
    rl = resolver.resolve(make_line(value="10kohm", package="0603", category_hint="resistor"), boards=1)
    assert [c.mpn for c in rl.candidates] == ["RC0603FR-0710KL"]
