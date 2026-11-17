"""Overage, MOQ and order multiples, packaging choice and reel break-evens."""

from __future__ import annotations

import pytest
from resolve_fakes import make_line, product, resistor_10k, variation

from digikey_mcp.resolve import adapter, packaging
from digikey_mcp.resolve.gateway import Gateway
from digikey_mcp.resolve.ladder import LineResolver
from digikey_mcp.resolve.prefs import (
    Overage,
    Preferences,
    load_preferences,
    needed_quantity,
)
from digikey_mcp.resolve.store import Store


@pytest.mark.parametrize(
    ("category", "per_board", "boards", "expected"),
    [
        ("resistor", 10, 1, 12),  # 10% = 1, minimum +2
        ("resistor", 100, 1, 110),
        ("resistor", 25, 1, 28),  # ceil(2.5) = 3
        ("resistor", 30, 1, 33),  # 3.0000000000000004 must not round to 4
        ("capacitor", 4, 5, 22),  # 20 base + 10% = 2
        ("resistor", 1, 1, 3),  # a single resistor: the minimum of +2
        ("resistor", 92, 1, 102),  # 10 % of 92 rounded up
        ("led", 17, 1, 19),
        ("led", 1, 1, 2),
        ("diode", 1, 1, 1),  # no spares outside resistors, capacitors and LEDs
        ("diode", 47, 1, 47),
        ("transistor", 3, 1, 3),
        ("inductor", 3, 1, 3),
        ("ic", 4, 3, 12),
        ("connector", 2, 10, 20),
        ("other", 7, 1, 7),
    ],
)
def test_default_overage(category, per_board, boards, expected):
    line = make_line(quantity=per_board, category_hint=category)
    assert needed_quantity(line, boards, Preferences())[2] == expected


def test_overage_overridden_by_toml(tmp_path):
    path = tmp_path / "prefs.toml"
    path.write_text(
        "[overage.ic]\npercent = 0\nmin_extra = 1\n\n[overage.resistor]\npercent = 20\nmin_extra = 5\nmax_extra = 8\n"
        "[packaging]\nallow_marketplace = true\n"
    )
    prefs = load_preferences(path)
    assert needed_quantity(make_line(quantity=4, category_hint="ic"), 1, prefs) == (4, 1, 5)
    assert needed_quantity(make_line(quantity=100, category_hint="resistor"), 1, prefs) == (100, 8, 108)
    assert needed_quantity(make_line(quantity=10, category_hint="resistor"), 1, prefs) == (10, 5, 15)
    assert prefs.allow_marketplace
    assert needed_quantity(make_line(quantity=100, category_hint="diode"), 1, prefs) == (100, 0, 100)
    # untouched categories keep their defaults
    assert needed_quantity(make_line(quantity=100, category_hint="capacitor"), 1, prefs)[1] == 10


def test_bad_preferences_are_rejected(tmp_path):
    path = tmp_path / "p.toml"
    path.write_text("[packagng]\nx = 1\n")
    with pytest.raises(ValueError, match="unknown preference section"):
        load_preferences(path)
    path.write_text("[quota]\nper_hour = 1\n")
    with pytest.raises(ValueError, match="unknown key"):
        load_preferences(path)


def pk(kind_name, pn, **kw):
    return adapter.parse_packaging(
        {
            "digikey_part_number": pn,
            "packaging": kind_name,
            "price_breaks": [{"quantity": q, "unit_price": p} for q, p in kw.pop("breaks", [(1, 0.1)])],
            **kw,
        }
    )


def test_moq_and_multiples():
    tray = pk("Tray", "T-ND", minimum_order_quantity=25, breaks=[(25, 1.0)])
    reel = pk("Tape & Reel (TR)", "R-ND", minimum_order_quantity=2500, standard_package=2500, breaks=[(2500, 0.1)])
    cut = pk("Cut Tape (CT)", "C-ND")
    assert packaging.order_quantity(tray, 10) == 25
    assert packaging.order_quantity(tray, 26) == 26  # trays: any quantity above the MOQ
    assert packaging.order_quantity(reel, 1) == 2500
    assert packaging.order_quantity(reel, 2501) == 5000
    assert packaging.order_quantity(reel, 5000) == 5000
    assert packaging.order_quantity(cut, 7) == 7
    opt = packaging.option_for(reel, 2501, Preferences(), 100000)
    assert (opt.order_qty, opt.leftover, opt.moq, opt.multiple) == (5000, 2499, 2500, 2500)
    assert opt.total_cost == pytest.approx(500.0) and opt.leftover_cost == pytest.approx(249.9)


def test_price_break_selection():
    breaks = [(1, 0.10), (10, 0.05), (100, 0.01)]
    assert packaging.price_at(breaks, 1) == 0.10
    assert packaging.price_at(breaks, 9) == 0.10
    assert packaging.price_at(breaks, 10) == 0.05
    assert packaging.price_at(breaks, 5000) == 0.01
    assert packaging.price_at(breaks, 0) == 0.10
    assert packaging.price_at([], 5) is None


@pytest.fixture
def resolver(fake, clock, cache):
    store = Store(cache)
    prefs = Preferences()
    yield LineResolver(Gateway(fake, store, prefs, clock=clock, sleep=clock.sleep), prefs)
    store.close()


def resolve_resistor(resolver, fake, qty):
    fake.add(resistor_10k())
    return resolver.resolve(make_line(quantity=qty, mpn="RC0603FR-0710KL", category_hint="other"), boards=1)


def test_small_quantity_picks_cut_tape(resolver, fake):
    rl = resolve_resistor(resolver, fake, 100)
    assert rl.dk_pn == "311-10.0KHRCT-ND" and rl.order_qty == 100
    assert rl.total_cost == pytest.approx(1.65)
    # DigiReel costs the same plus its fee and is shown, as is the reel
    kinds = {o.kind: o for o in rl.alternatives}
    assert kinds["digireel"].total_cost == pytest.approx(8.65)
    assert kinds["tape_reel"].order_qty == 5000 and kinds["tape_reel"].total_cost == pytest.approx(21.0)
    assert kinds["tape_reel"].leftover == 4900
    assert rl.alternatives[0].dk_pn == rl.dk_pn


def test_large_quantity_picks_reel_with_leftover(resolver, fake):
    rl = resolve_resistor(resolver, fake, 3000)
    assert rl.dk_pn == "311-10.0KHRTR-ND"
    assert rl.order_qty == 5000 and rl.unit_price == 0.0042 and rl.total_cost == pytest.approx(21.0)
    cut = next(o for o in rl.alternatives if o.kind == "cut_tape")
    assert cut.total_cost == pytest.approx(23.4) and cut.order_qty == 3000
    assert "2000 extra for MOQ/multiple" in rl.reason


def test_reel_break_even(resolver, fake):
    rl = resolve_resistor(resolver, fake, 100)
    text = next(t for t in rl.break_even if "Cut Tape" in t)
    # 0.0078 each as cut tape against $21.00 for a reel: equal from 2693 pcs
    assert "from 2693 pcs" in text and "$21.00" in text and "2307 spare" in text
    assert packaging.break_even(pk("Cut Tape", "C", breaks=[(1, 0.001)]), pk("Tape & Reel (TR)", "R", minimum_order_quantity=100, standard_package=100, breaks=[(100, 0.01)])).endswith("reel $1.00 vs $0.10 as Cut Tape")


def test_stock_decides_between_packagings(resolver, fake):
    ct = variation("X-CT-ND", "Cut Tape (CT)", stock=50, breaks=((1, 0.1),))
    tr = variation("X-TR-ND", "Tape & Reel (TR)", moq=1000, std=1000, stock=9000, breaks=((1000, 0.09),))
    fake.add(product("X1", "Maker", [ct, tr]))
    rl = resolver.resolve(make_line(quantity=60, mpn="X1"), boards=1)
    assert rl.status == "resolved" and rl.dk_pn == "X-TR-ND" and rl.order_qty == 1000
    ct_opt = next(o for o in rl.alternatives if o.dk_pn == "X-CT-ND")
    assert not ct_opt.valid and "stock 50" in ct_opt.invalid_reason


def test_allowed_packagings_preference(resolver, fake):
    resolver.prefs = Preferences.from_dict({"packaging": {"allowed": ["cut tape"]}})
    rl = resolve_resistor(resolver, fake, 3000)
    assert rl.dk_pn == "311-10.0KHRCT-ND"


def test_missing_prices_fall_back_to_packagetype_endpoint(resolver, fake):
    p = product("NOPRICE", "Maker", [variation("NP-CT-ND", "Cut Tape (CT)", breaks=())])
    fake.add(p)
    fake.package_type_by_quantity = lambda pn, qty, **_: (
        fake.calls.append(("package_type_by_quantity", pn, qty))
        or {"Products": [{"DigiKeyProductNumber": "NP-CT-ND", "PackageType": {"Name": "Cut Tape (CT)"},
                          "QuantityAvailable": 100, "MinimumOrderQuantity": 1,
                          "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.25}]}]}
    )
    rl = resolver.resolve(make_line(quantity=4, mpn="NOPRICE"), boards=1)
    assert rl.status == "resolved" and rl.total_cost == pytest.approx(1.0)
    assert "PRICES_FROM_PACKAGETYPE" in rl.flags


def test_thorough_mode_uses_all_pricing_endpoints(resolver, fake):
    resolver.prefs = Preferences.from_dict({"api": {"thorough": True}})
    resolve_resistor(resolver, fake, 100)
    assert {"pricing_by_quantity", "package_type_by_quantity"} <= set(fake.names())


BOM_HEADER = "Reference,Qty,Value,MPN\n"


def _spares(rows: str) -> dict[str, tuple[int, str]]:
    """Ingest CSV rows and return {first reference: (overage, category)} under the default preferences."""
    from digikey_mcp.ingest import ingest

    result = ingest(BOM_HEADER + rows, fmt="csv")
    return {
        ln.refdes[0]: (needed_quantity(ln, 1, Preferences())[1], ln.category_hint) for ln in result.lines
    }


@pytest.mark.parametrize(
    "reference, mpn",
    [
        ("D1608E", "AFX00005"),  # Arduino Opta expansion module
        ("A0602", "ABX00123"),
        ("OPTA", "AFX00004"),
        ("K8AK1", "8AK1000"),
        ("CS1 RCT1", "CS1234"),
        ("DAT1", "DAT-1234"),
    ],
)
def test_module_names_get_no_spares(reference, mpn):
    qty = len(reference.split())
    spares = _spares(f"{reference},{qty},,{mpn}\n")
    assert spares[reference.split()[0]][0] == 0


def test_module_name_with_a_diode_like_prefix_is_not_a_diode():
    assert _spares("D1608E,1,,AFX00005\n")["D1608E"] == (0, "other")


def test_real_designators_get_the_spares_of_their_category():
    spares = _spares("D1 D2,2,1N4148,\nR1,1,10k,\nC3,1,100nF,\n")
    assert spares["D1"] == (0, "diode")
    assert spares["R1"] == (2, "resistor")
    assert spares["C3"] == (2, "capacitor")


def test_a_named_part_gets_no_spares():
    assert _spares("R1,1,10k,RC0603FR-0710KL\n")["R1"] == (0, "resistor")


def test_spares_for_named_parts_when_the_preferences_say_so():
    prefs = Preferences.from_dict({"overage": {"named_parts": True}})
    line = make_line(quantity=1, mpn="RC0603FR-0710KL", category_hint="resistor")
    assert needed_quantity(line, 1, prefs) == (1, 2, 3)


def test_spares_never_more_than_double_above_ten():
    assert Overage(200, 0).extra(50) == 50
    assert Overage(100, 2).extra(11) == 11
    assert Overage(200, 0).extra(10) == 20  # up to ten parts only the percentage and minimum apply
    prefs = Preferences.from_dict({"overage": {"resistor": {"percent": 300}}})
    assert needed_quantity(make_line(quantity=40, category_hint="resistor"), 1, prefs) == (40, 40, 80)


def test_spare_preferences_are_validated():
    with pytest.raises(ValueError, match="takes percent"):
        Preferences.from_dict({"overage": {"resistor": 5}})
    prefs = Preferences.from_dict({"overage": {"price_limit": 0, "named_parts": True}})
    assert prefs.spare_price_limit == 0 and prefs.spares_for_named_parts


def test_prefix_alone_is_not_enough_when_a_part_number_is_given():
    assert _spares("D5,1,,BAT54\n")["D5"] == (0, "other")


def test_led_numbered_like_a_diode_is_an_led():
    spares = _spares("D3 D5,2,LED green,\n")
    assert spares["D3"] == (1, "led")


# ---- spares and price breaks in the reason ------------------------------------


def costly_cap(price):
    return product("CAP-1", "Maker", [variation("CAP-1-ND", "Cut Tape (CT)", breaks=((1, price),))], category="Capacitors")


def test_reason_shows_the_arithmetic(resolver, fake):
    fake.add(costly_cap(0.5))
    line = make_line(quantity=45, category_hint="capacitor")
    rl = resolver.resolve(line, boards=1, decision="CAP-1-ND")  # a decision on a generic line keeps its spares
    assert (rl.base_qty, rl.overage, rl.needed_qty, rl.order_qty) == (45, 5, 50, 50)
    assert "need 45 + 5 spare = 50" in rl.reason


def test_no_spares_above_the_unit_price_limit(resolver, fake):
    fake.add(costly_cap(2.0))
    rl = resolver.resolve(make_line(quantity=45, category_hint="capacitor"), boards=1, decision="CAP-1-ND")
    assert (rl.overage, rl.needed_qty, rl.order_qty) == (0, 45, 45)
    assert "need 45)" in rl.reason and "no spares, unit price $2.00 is above the $1.00 limit" in rl.reason


def test_spare_price_limit_can_be_changed(resolver, fake):
    resolver.prefs = Preferences.from_dict({"overage": {"price_limit": 5}})
    fake.add(costly_cap(2.0))
    rl = resolver.resolve(make_line(quantity=45, category_hint="capacitor"), boards=1, decision="CAP-1-ND")
    assert rl.overage == 5 and rl.order_qty == 50


def test_round_up_to_a_price_break_only_when_cheaper():
    cut = pk("Cut Tape (CT)", "C-ND", breaks=[(1, 0.10), (10, 0.043), (100, 0.0165), (500, 0.0105)])
    # 45 x 0.043 = 1.935; 100 x 0.0165 = 1.65 is cheaper, so the order is raised to 100
    opt = packaging.option_for(cut, 45, Preferences(), 100000)
    assert (opt.order_qty, opt.rounded_from) == (100, 45) and opt.total_cost == pytest.approx(1.65)
    assert opt.saving == pytest.approx(1.935 - 1.65)
    # 300 x 0.0165 = 4.95 against 500 x 0.0105 = 5.25: stay at 300
    opt = packaging.option_for(cut, 300, Preferences(), 100000)
    assert (opt.order_qty, opt.rounded_from, opt.saving) == (300, 0, 0.0)
    # the stock must cover the larger quantity
    opt = packaging.option_for(pk("Cut Tape (CT)", "C-ND", breaks=[(1, 0.10), (10, 0.043)], quantity_available=8), 6, Preferences(), 8)
    assert opt.order_qty == 6


def test_break_rounding_is_named_in_the_reason(resolver, fake):
    fake.add(resistor_10k())
    rl = resolver.resolve(make_line(quantity=45, mpn="RC0603FR-0710KL", category_hint="other"), boards=1)
    assert rl.order_qty == 100 and "need 45)" in rl.reason
    assert "raised to 100 for the price break (saves $0.28)" in rl.reason
