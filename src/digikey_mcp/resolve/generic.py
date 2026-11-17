"""Candidates for generic BOM lines (no MPN). The user always decides these."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from . import packaging
from .adapter import Facet, ProductInfo
from .gateway import Gateway, NotFound
from .models import Candidate, ResolvedLine
from .names import manufacturers_match
from .prefs import Preferences
from .spec import Spec, spec_for

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


#: hint -> (DigiKey category names that qualify, words one of which must also appear in the category
#: path or the description when the category is broader than the hint)
_CATEGORY_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "resistor": (("resistors",), ()),
    "capacitor": (("capacitors",), ()),
    "inductor": (("inductors",), ()),
    "connector": (("connectors",), ()),
    "ic": (("integrated circuits",), ()),
    "diode": (("discrete semiconductor",), ("diode", "rectifier")),
    "led": (("optoelectronics",), ("led", "light emitting")),
    "transistor": (("discrete semiconductor",), ("transistor", "mosfet", "bjt")),
    "crystal": (("crystals",), ()),
    "switch": (("switches",), ()),
    "fuse": (("circuit protection",), ("fuse",)),
}


def in_hinted_category(hint: str, p: ProductInfo) -> bool:
    """True when the product sits in a DigiKey category that fits the hint.

    A hint with a rule never passes a product whose category is unknown. Hints without one
    (other, fiducial, ...) are not filtered.
    """
    rule = _CATEGORY_RULES.get(hint)
    if rule is None:
        return True
    names, words = rule
    chain = [p.category, *p.category_path]
    if not any(n in c.casefold() for c in chain for n in names):
        return False
    if not words:
        return True
    text = " ".join([*chain, p.description]).casefold()
    return any(w in text for w in words)


def has_search_basis(rl: ResolvedLine) -> bool:
    """A generic line needs a value, a package or footprint, or a description to search on."""
    line = rl.line
    return bool(line.value or line.package or line.footprint or line.description)


def build_keywords(rl: ResolvedLine, prefs: Preferences) -> str:
    """Search words from value, package, category and preferences (unverified request shape)."""
    line = rl.line
    if not has_search_basis(rl):
        return ""
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
    if not line.value and line.description:
        words.append(line.description)
    seen: set[str] = set()
    return " ".join(w for w in words if not (w.casefold() in seen or seen.add(w.casefold())))


def _score(
    rl: ResolvedLine, prefs: Preferences, p: ProductInfo, cand: Candidate, cheapest: float | None, verified: list[str]
) -> tuple[float, list[str]]:
    line = rl.line
    score = 0.0
    why: list[str] = ["verified " + ", ".join(verified)] if verified else []
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
    if package and package in text and not verified:
        score += 20
        why.append(f"package {line.package}")
    if line.tolerance and line.tolerance.casefold() in text and not verified:
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


@dataclass
class Found:
    """Candidates for a generic line, and why there are none when the list is empty."""

    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""


def _facet(facets: list[Facet], names: tuple[str, ...]) -> Facet | None:
    by_name = {f.name: f for f in facets}
    return next((by_name[n] for n in names if n in by_name), None)


def _filters(spec: Spec, facets: list[Facet]) -> tuple[dict[int, list[str]], str]:
    """DigiKey parameter filters for the spec ({parameter id: value ids}), or why none can match."""
    filters: dict[int, list[str]] = {}
    for want in spec.wants:
        facet = _facet(facets, want.params) if want.facet else None
        if facet is None:
            continue
        ids = [vid for vid, name in facet.values if want.test(name)]
        if not ids:
            return {}, f"DigiKey lists no in-stock {spec.kind} with {want.label} {want.text}"
        filters[facet.parameter_id] = ids
    return filters, ""


def _candidate(p: ProductInfo, rl: ResolvedLine, prefs: Preferences) -> Candidate | None:
    options, _ = packaging.compare_with_spares(p.packagings, rl.base_qty, rl.overage, prefs, p.stock)
    top = options[0] if options else None
    if top is None and p.unit_price is None:
        return None
    return Candidate(
        dk_pn=top.dk_pn if top else p.dk_pn, mpn=p.mpn, manufacturer=p.manufacturer,
        description=p.description, stock=p.stock,
        unit_price=top.unit_price if top else p.unit_price,
        order_qty=top.order_qty if top else None,
        total_cost=top.total_cost if top else None,
        packaging=top.packaging if top else "", status=p.status,
    )


def _rank(kept: list[tuple[ProductInfo, list[str]]], rl: ResolvedLine, prefs: Preferences) -> list[Candidate]:
    pairs: list[tuple[ProductInfo, list[str], Candidate]] = []
    for p, verified in kept:
        if p.marketplace and not prefs.allow_marketplace:
            continue
        cand = _candidate(p, rl, prefs)
        if cand is not None:
            pairs.append((p, verified, cand))
    costs = [c.total_cost for _, _, c in pairs if c.total_cost and c.stock > 0]
    cheapest = min(costs) if costs else None
    for p, verified, cand in pairs:
        cand.score, why = _score(rl, prefs, p, cand, cheapest, verified)
        cand.score = round(cand.score, 1)
        cand.why = "; ".join(why)
    return sorted((c for _, _, c in pairs), key=lambda c: -c.score)[: prefs.max_candidates]


def _verified(spec: Spec, rl: ResolvedLine, found: list[ProductInfo]) -> tuple[list[tuple[ProductInfo, list[str]]], str]:
    """Products that match the spec with the parameters checked, and the commonest reason for the rest."""
    kept: list[tuple[ProductInfo, list[str]]] = []
    failures: Counter[str] = Counter()
    for p in found:
        if not in_hinted_category(rl.line.category_hint, p):
            failures["not in the expected DigiKey category"] += 1
            continue
        verified, failed = spec.verify(p.parameters, p.description)
        if failed:
            failures[f"{failed.label} {failed.text}"] += 1
        else:
            kept.append((p, verified))
    if kept:
        return kept, ""
    if not found:
        return [], f"DigiKey returned no {spec.kind} for {', '.join(w.label + ' ' + w.text for w in spec.wants)}"
    label, count = failures.most_common(1)[0]
    return [], f"none of {len(found)} {spec.kind} results matched: {count} failed on {label}"


def _search_verified(gw: Gateway, prefs: Preferences, rl: ResolvedLine, spec: Spec) -> Found:
    filters, why_not = _filters(spec, gw.facets(spec.category_id, spec.kind, spec.facet_names()))
    if why_not:
        return Found([], why_not)
    args = {"category_id": spec.category_id, "filters": filters, "exclude_marketplace": not prefs.allow_marketplace}
    found = gw.search(spec.keywords, limit=prefs.search_limit, in_stock=True, min_quantity=rl.needed_qty, **args)
    if not found:
        found = gw.search(spec.keywords, limit=prefs.search_limit, **args)
    kept, reason = _verified(spec, rl, found)
    ranked = _rank(kept, rl, prefs)
    if kept and not ranked:
        reason = f"{len(kept)} matching part(s), all marketplace sellers or without a price"
    return Found(ranked, reason)


def _search_keywords(gw: Gateway, prefs: Preferences, rl: ResolvedLine) -> Found:
    """Keyword search for a part type without a parametric verifier: checked for category only."""
    keywords = build_keywords(rl, prefs)
    if not keywords:
        return Found()
    found = gw.search(keywords, limit=prefs.search_limit, in_stock=True, min_quantity=rl.needed_qty)
    if not found:
        found = gw.search(keywords, limit=prefs.search_limit)
    kept = [(p, []) for p in found if in_hinted_category(rl.line.category_hint, p)]
    ranked = _rank(kept, rl, prefs)
    for cand in ranked:
        cand.why = "parameters not verified (no parametric check for this part type); " + cand.why
    return Found(ranked, "" if ranked else f"no {rl.line.category_hint} results for '{keywords}'")


def find(gw: Gateway, prefs: Preferences, rl: ResolvedLine) -> Found:
    """Up to `max_candidates` ranked candidates for a generic line, each verified against the line.

    Resistors, capacitors, LEDs, blade and PTC fuses and pin headers are searched with DigiKey's
    parameter filter and every result is checked against its own parameters. Other part types
    use a keyword search limited to the hinted category.
    """
    try:
        spec = spec_for(rl.line)
        return _search_verified(gw, prefs, rl, spec) if spec else _search_keywords(gw, prefs, rl)
    except NotFound:
        return Found([], "DigiKey found nothing")
