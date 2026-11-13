"""Candidates for generic BOM lines (no MPN). The user always decides these."""

from __future__ import annotations

from . import packaging
from .adapter import ProductInfo
from .gateway import Gateway, NotFound
from .models import Candidate, ResolvedLine
from .names import manufacturers_match
from .prefs import Preferences

_CATEGORY_WORDS = {
    "resistor": "resistor",
    "capacitor": "capacitor",
    "inductor": "inductor",
    "connector": "connector",
    "ic": "",
    "diode": "diode",
    "led": "LED",
    "transistor": "transistor",
    "crystal": "crystal",
    "switch": "switch",
    "fuse": "fuse",
}


def build_keywords(rl: ResolvedLine, prefs: Preferences) -> str:
    """Search words from value, package, category and preferences (unverified request shape)."""
    line = rl.line
    words: list[str] = []
    if line.value:
        words.append(line.value)
    package = line.package or line.footprint
    if package:
        words.append(package)
    if line.tolerance:
        words.append(line.tolerance)
    noun = _CATEGORY_WORDS.get(line.category_hint, "")
    if noun:
        words.append(noun)
    words.extend(prefs.generic_keywords.get(line.category_hint, []))
    if not words and line.description:
        words.append(line.description)
    seen: set[str] = set()
    return " ".join(w for w in words if not (w.casefold() in seen or seen.add(w.casefold())))


def _score(rl: ResolvedLine, prefs: Preferences, p: ProductInfo, cand: Candidate, cheapest: float | None) -> tuple[float, list[str]]:
    line = rl.line
    score = 0.0
    why: list[str] = []
    if cand.stock >= (cand.order_qty or rl.needed_qty):
        score += 30
        why.append(f"stock {cand.stock} covers {cand.order_qty or rl.needed_qty}")
    elif cand.stock > 0:
        score += 5
        why.append(f"stock {cand.stock} is short")
    else:
        score -= 50
        why.append("out of stock")
    if p.status_class == "active":
        score += 10
    elif p.status_class != "unknown":
        score -= 20
        why.append(p.status or p.status_class)
    text = " ".join([p.description, *p.parameters.values()]).casefold()
    package = (line.package or "").casefold()
    if package and package in text:
        score += 20
        why.append(f"package {line.package}")
    if line.tolerance and line.tolerance.casefold() in text:
        score += 10
        why.append(f"tolerance {line.tolerance}")
    for kw in prefs.generic_keywords.get(line.category_hint, []):
        if kw.casefold() in text:
            score += 5
            why.append(f"matches '{kw}'")
    if any(manufacturers_match(p.manufacturer, m) for m in prefs.preferred_manufacturers):
        score += 15
        why.append("preferred manufacturer")
    if cand.total_cost and cheapest:
        score += 20 * cheapest / cand.total_cost
        why.append(f"${cand.total_cost:.2f} for {cand.order_qty}")
    return score, why


def candidates(gw: Gateway, prefs: Preferences, rl: ResolvedLine) -> list[Candidate]:
    """Up to `max_candidates` ranked in-stock candidates for a generic line."""
    keywords = build_keywords(rl, prefs)
    if not keywords:
        return []
    try:
        found = gw.search(keywords, limit=prefs.search_limit, in_stock=True, min_quantity=rl.needed_qty)
        if not found:
            found = gw.search(keywords, limit=prefs.search_limit)
    except NotFound:
        return []
    pairs: list[tuple[ProductInfo, Candidate]] = []
    for p in found:
        if p.marketplace and not prefs.allow_marketplace:
            continue
        options = packaging.compare(p.packagings, rl.needed_qty, prefs, p.stock)
        top = options[0] if options else None
        if top is None and p.unit_price is None:
            continue
        pairs.append(
            (
                p,
                Candidate(
                    dk_pn=top.dk_pn if top else p.dk_pn, mpn=p.mpn, manufacturer=p.manufacturer,
                    description=p.description, stock=p.stock,
                    unit_price=top.unit_price if top else p.unit_price,
                    order_qty=top.order_qty if top else None,
                    total_cost=top.total_cost if top else None,
                    packaging=top.packaging if top else "", status=p.status,
                ),
            )
        )
    costs = [c.total_cost for _, c in pairs if c.total_cost and c.stock > 0]
    cheapest = min(costs) if costs else None
    for p, cand in pairs:
        cand.score, why = _score(rl, prefs, p, cand, cheapest)
        cand.score = round(cand.score, 1)
        cand.why = "; ".join(why)
    ranked = sorted((c for _, c in pairs), key=lambda c: -c.score)
    return ranked[: prefs.max_candidates]
