"""Overage, MOQ and order multiples, packaging choice and reel break-evens."""

from __future__ import annotations

import pytest
from resolve_fakes import make_line, product, resistor_10k, variation

from digikey_mcp.resolve import adapter, packaging
from digikey_mcp.resolve.gateway import Gateway
from digikey_mcp.resolve.ladder import LineResolver
from digikey_mcp.resolve.prefs import Preferences, load_preferences, needed_quantity
from digikey_mcp.resolve.store import Store


@pytest.mark.parametrize(
    ("category", "per_board", "boards", "expected"),
    [
        ("resistor", 10, 1, 12),  # 10% = 1, minimum +2
        ("resistor", 100, 1, 110),
        ("resistor", 25, 1, 28),  # ceil(2.5) = 3
        ("resistor", 30, 1, 33),  # 3.0000000000000004 must not round to 4
        ("capacitor", 4, 5, 22),  # 20 base + 10% = 2
        ("ic", 4, 3, 12),  # no overage
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
