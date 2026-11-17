"""Packaging comparison: order quantity, total cost, leftover cost and reel break-evens."""

from __future__ import annotations

import bisect
import math

from . import adapter
from .adapter import Packaging
from .models import PackagingOption
from .prefs import Preferences


def price_at(breaks: list[tuple[int, float]], qty: int) -> float | None:
    """Unit price at `qty`: the largest break not above it, else the first break."""
    if not breaks:
        return None
    idx = bisect.bisect_right([b[0] for b in breaks], qty) - 1
    return breaks[max(idx, 0)][1]


def order_rules(p: Packaging) -> tuple[int, int]:
    """(minimum order quantity, order multiple) for one packaging."""
    if p.kind == adapter.KIND_TAPE_REEL:
        size = p.standard_package if p.standard_package > 1 else p.moq
        return max(p.moq, size), max(size, 1)
    return p.moq, 1


def order_quantity(p: Packaging, needed: int) -> int:
    """`needed` raised to the packaging's MOQ and rounded up to its order multiple."""
    moq, multiple = order_rules(p)
    qty = max(needed, moq, 1)
    return math.ceil(qty / multiple) * multiple


def total_for(p: Packaging, qty: int) -> tuple[float | None, float | None]:
    """(unit price, total cost) for ordering `qty` of this packaging."""
    unit = price_at(p.breaks, qty)
    if unit is None:
        return None, None
    return unit, round(unit * qty + p.fee, 4)


def _break_quantity(p: Packaging, qty: int, stock: int) -> tuple[int, float | None, float | None]:
    """(quantity, unit price, total) for `qty`, or for a larger price break when that costs less in total.

    Only breaks the stock covers are considered; a tie keeps the smaller quantity.
    """
    unit, total = total_for(p, qty)
    if total is None:
        return qty, unit, total
    best = (qty, unit, total)
    for break_qty, _ in p.breaks:
        if break_qty <= qty:
            continue
        rounded = order_quantity(p, break_qty)
        if rounded > stock:
            continue
        b_unit, b_total = total_for(p, rounded)
        if b_total is not None and b_total < best[2]:
            best = (rounded, b_unit, b_total)
    return best


def option_for(p: Packaging, needed: int, prefs: Preferences, product_stock: int = 0) -> PackagingOption:
    qty = order_quantity(p, needed)
    moq, multiple = order_rules(p)
    stock = p.stock if p.stock is not None else product_stock
    rounded_from, saving = 0, 0.0
    base_total = total_for(p, qty)[1]
    qty, unit, total = _break_quantity(p, qty, stock)
    if base_total is not None and total is not None and total < base_total:
        rounded_from = order_quantity(p, needed)
        saving = round(base_total - total, 4)
    leftover = qty - needed
    reason = ""
    if p.marketplace and not prefs.allow_marketplace:
        reason = "marketplace seller"
    elif prefs.allowed_packagings and not any(a.casefold() in p.name.casefold() for a in prefs.allowed_packagings):
        reason = "packaging not allowed by preferences"
    elif unit is None:
        reason = "no price"
    elif stock < qty:
        reason = f"stock {stock} below {qty}"
    return PackagingOption(
        dk_pn=p.dk_pn,
        packaging=p.name,
        kind=p.kind,
        order_qty=qty,
        unit_price=unit,
        total_cost=total,
        leftover=leftover,
        leftover_cost=round(leftover * unit, 4) if unit is not None and leftover > 0 else 0.0,
        moq=moq,
        multiple=multiple,
        stock=stock,
        valid=not reason,
        invalid_reason=reason,
        rounded_from=rounded_from,
        saving=saving,
    )


def compare(packagings: list[Packaging], needed: int, prefs: Preferences, product_stock: int = 0) -> list[PackagingOption]:
    """All packagings as options: valid ones first, cheapest total first."""
    options = [option_for(p, needed, prefs, product_stock) for p in packagings]
    options.sort(key=lambda o: (not o.valid, o.total_cost if o.total_cost is not None else float("inf"), o.order_qty))
    return options


def best(options: list[PackagingOption]) -> PackagingOption | None:
    return options[0] if options and options[0].valid else None


def _money(value: float) -> str:
    return f"${value:,.2f}"


def break_even(cut: Packaging, reel: Packaging) -> str | None:
    """Where a full reel becomes cheaper than buying the same quantity as cut tape or DigiReel."""
    moq, size = order_rules(reel)
    reel_unit, reel_total = total_for(reel, size)
    if reel_total is None or size <= 1:
        return None
    first = max(cut.moq, 1)
    crossing = None
    for qty in range(first, size + 1):
        _, cost = total_for(cut, qty)
        if cost is not None and cost >= reel_total:
            crossing = qty
            break
    cut_name = cut.name or "cut tape"
    reel_name = reel.name or "reel"
    if crossing is None:
        _, at_full = total_for(cut, size)
        return (
            f"{reel_name} ({reel.dk_pn}) is not cheaper than {cut_name} ({cut.dk_pn}) up to one reel of {size}: "
            f"reel {_money(reel_total)} vs {_money(at_full or 0)} as {cut_name}"
        )
    spare = size - crossing
    spare_cost = _money(spare * reel_unit) if reel_unit is not None else "n/a"
    return (
        f"{reel_name} ({reel.dk_pn}) is cheaper than {cut_name} ({cut.dk_pn}) from {crossing} pcs: "
        f"a reel of {size} costs {_money(reel_total)}, leaving {spare} spare ({spare_cost})"
    )


def break_evens(packagings: list[Packaging]) -> list[str]:
    reels = [p for p in packagings if p.kind == adapter.KIND_TAPE_REEL]
    cuts = [p for p in packagings if p.kind in (adapter.KIND_CUT_TAPE, adapter.KIND_DIGIREEL)]
    out = []
    for reel in reels:
        for cut in cuts:
            text = break_even(cut, reel)
            if text:
                out.append(text)
    return out


def compare_with_spares(
    packagings: list[Packaging], base: int, spares: int, prefs: Preferences, product_stock: int = 0
) -> tuple[list[PackagingOption], int]:
    """`compare` at base + spares; the spares are dropped when the unit price is above the limit.

    Returns the options and the number of spares actually planned.
    """
    options = compare(packagings, base + spares, prefs, product_stock)
    top = best(options)
    limit = prefs.spare_price_limit
    if spares and limit > 0 and top is not None and (top.unit_price or 0.0) > limit:
        return compare(packagings, base, prefs, product_stock), 0
    return options, spares
