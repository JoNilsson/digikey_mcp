"""The resolution ladder: one BOM line to one verified DigiKey part (or a decision request)."""

from __future__ import annotations

from collections import defaultdict

from ..ingest import BomLine
from . import adapter, generic, packaging
from .adapter import ProductInfo
from .gateway import ApiError, Gateway, NotFound
from .models import (
    AMBIGUOUS,
    DNP_SKIPPED,
    ERROR,
    NEEDS_DECISION,
    RESOLVED,
    SKIPPED,
    UNAVAILABLE,
    Candidate,
    PackagingOption,
    ResolvedLine,
    Substitute,
)
from .names import canonical_manufacturer, identity_key, manufacturers_match, norm_mpn
from .prefs import Preferences, needed_quantity

SKIP = "skip"
_BAD_STATUS = {"obsolete", "discontinued"}
_FLAG_FOR_STATUS = {"nrnd": "NRND", "last_time_buy": "LAST_TIME_BUY", "obsolete": "OBSOLETE", "discontinued": "DISCONTINUED"}
_MAX_SUBSTITUTES = 5


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


class LineResolver:
    def __init__(self, gateway: Gateway, prefs: Preferences):
        self.gw = gateway
        self.prefs = prefs

    # ---- entry point ------------------------------------------------------

    def resolve(self, line: BomLine, boards: int, decision: str | None = None) -> ResolvedLine:
        """Resolve one line. `decision` is a DigiKey part number or 'skip'.

        QuotaExhausted propagates; every other failure becomes the line's status.
        """
        base, extra, needed = needed_quantity(line, boards, self.prefs)
        rl = ResolvedLine(line=line, key=identity_key(line), base_qty=base, overage=extra, needed_qty=needed)
        if not line.orderable:
            rl.status = DNP_SKIPPED
            rl.reason = "DNP, not populated" if line.dnp else f"{line.category_hint.replace('_', ' ')}, not a purchased part"
            return rl
        if decision == SKIP:
            rl.status, rl.decided, rl.source = SKIPPED, True, "decision"
            rl.reason = "skipped by decision"
            return rl
        if needed <= 0:
            rl.status, rl.reason = ERROR, "quantity is zero"
            return rl
        try:
            if decision:
                return self._by_pn(rl, decision, "decision", decided=True)
            if line.supplier_pn:
                return self._by_pn(rl, line.supplier_pn, "supplier_pn")
            if line.mpn:
                return self._by_mpn(rl)
            return self._generic(rl)
        except NotFound:
            rl.status = UNAVAILABLE
            what = decision or line.supplier_pn or line.mpn or "part"
            rl.reason = f"{what} not found on DigiKey"
            return rl
        except ApiError as exc:
            rl.status, rl.reason = ERROR, f"DigiKey error: {exc}"[:200]
            return rl

    # ---- rungs ------------------------------------------------------------

    def _by_pn(self, rl: ResolvedLine, pn: str, source: str, decided: bool = False) -> ResolvedLine:
        product = self.gw.product_details(pn)
        if product is None:
            raise NotFound(pn)
        rl.source, rl.decided = source, decided
        return self._finalize(rl, product, pinned_pn=pn)

    def _by_mpn(self, rl: ResolvedLine) -> ResolvedLine:
        line = rl.line
        target = norm_mpn(line.mpn)
        found = self.gw.search(line.mpn or "", limit=10)
        if not found:
            raise NotFound(line.mpn or "")
        exact = [p for p in found if norm_mpn(p.mpn) == target]
        if not exact:
            near = [p for p in found if target in norm_mpn(p.mpn) or norm_mpn(p.mpn) in target][: self.prefs.max_candidates]
            rl.status = AMBIGUOUS if near else UNAVAILABLE
            rl.candidates = [self._candidate(rl, p, "near match, not an exact MPN") for p in near]
            rl.reason = (
                f"no exact MPN match for {line.mpn}; {len(near)} near match(es)" if near else f"{line.mpn} not found on DigiKey"
            )
            return rl
        if line.manufacturer:
            rl.source = "mpn+manufacturer"
            matching = [p for p in exact if manufacturers_match(p.manufacturer, line.manufacturer)]
            if not matching:
                makers = sorted({p.manufacturer for p in exact})
                rl.status = AMBIGUOUS
                rl.candidates = [self._candidate(rl, p, f"BOM manufacturer {line.manufacturer} does not match") for p in exact[:5]]
                rl.reason = f"{line.mpn} is listed for {', '.join(makers)}, not {line.manufacturer}"
                return rl
            product = self._pick_record(rl, matching)
            return self._finalize(rl, product)
        rl.source = "mpn"
        by_maker: dict[str, list[ProductInfo]] = defaultdict(list)
        for p in exact:
            by_maker[canonical_manufacturer(p.manufacturer)].append(p)
        if len(by_maker) > 1:
            rl.status = AMBIGUOUS
            picks = [max(group, key=lambda p: p.stock) for group in by_maker.values()][:5]
            rl.candidates = [self._candidate(rl, p, "same MPN, different manufacturer") for p in picks]
            rl.reason = f"{line.mpn} exists from {len(by_maker)} manufacturers: {', '.join(p.manufacturer for p in picks)}"
            return rl
        return self._finalize(rl, self._pick_record(rl, exact))

    def _generic(self, rl: ResolvedLine) -> ResolvedLine:
        rl.source = "search"
        rl.status = NEEDS_DECISION
        line = rl.line
        what = " ".join(x for x in (line.value, line.package or line.footprint, line.category_hint) if x)
        if not generic.has_search_basis(rl):
            rl.candidates = []
            rl.reason = (
                f"generic line ({what}): no value, rating, package or description to search on; "
                "supply them or an MPN or DigiKey part number"
            )
            return rl
        found = generic.find(self.gw, self.prefs, rl)
        rl.candidates = found.candidates
        if rl.candidates:
            rl.reason = f"generic line ({what}): pick one of {len(rl.candidates)} candidate(s) or skip"
        else:
            rl.reason = f"generic line ({what}): no candidates"
            if found.reason:
                rl.reason += f": {found.reason}"
            rl.reason += "; supply an MPN or DigiKey part number"
        return rl

    @staticmethod
    def _pick_record(rl: ResolvedLine, products: list[ProductInfo]) -> ProductInfo:
        if len(products) > 1:
            rl.flags.append("MULTIPLE_RECORDS")
        return max(products, key=lambda p: (p.stock, len(p.packagings)))

    # ---- verification -----------------------------------------------------

    def _candidate(self, rl: ResolvedLine, p: ProductInfo, why: str) -> Candidate:
        options = packaging.compare(self._packagings(p), rl.needed_qty, self.prefs, p.stock)
        top = options[0] if options else None
        return Candidate(
            dk_pn=top.dk_pn if top else p.dk_pn,
            mpn=p.mpn,
            manufacturer=p.manufacturer,
            description=p.description,
            stock=p.stock,
            unit_price=top.unit_price if top else p.unit_price,
            order_qty=top.order_qty if top else None,
            total_cost=top.total_cost if top else None,
            packaging=top.packaging if top else "",
            status=p.status,
            why=why,
        )

    @staticmethod
    def _packagings(p: ProductInfo) -> list[adapter.Packaging]:
        """The product's packagings; a product with only a unit price gets one synthetic packaging."""
        if p.packagings or p.unit_price is None:
            return p.packagings
        return [adapter.Packaging(dk_pn=p.dk_pn or p.mpn, name="", breaks=[(1, p.unit_price)], stock=p.stock)]

    def _ensure_packagings(self, rl: ResolvedLine, product: ProductInfo, pn: str) -> list[adapter.Packaging]:
        packagings = [p for p in product.packagings if p.breaks]
        if packagings:
            return product.packagings
        # productdetails carried no price breaks: ask packagetypebyquantity
        extra = self.gw.package_types(pn, rl.needed_qty)
        if any(p.breaks for p in extra):
            rl.flags.append("PRICES_FROM_PACKAGETYPE")
            return extra
        return product.packagings

    def _finalize(self, rl: ResolvedLine, product: ProductInfo, pinned_pn: str | None = None) -> ResolvedLine:
        prefs = self.prefs
        rl.mpn, rl.manufacturer, rl.product_status = product.mpn, product.manufacturer, product.status
        pn = pinned_pn or product.dk_pn
        flag = _FLAG_FOR_STATUS.get(product.status_class)
        if flag:
            rl.flags.append(flag)
        if product.marketplace:
            rl.flags.append("MARKETPLACE")
        packagings = self._ensure_packagings(rl, product, pn)
        options = packaging.compare(packagings, rl.needed_qty, prefs, product.stock)
        rl.break_even = packaging.break_evens(packagings)
        if prefs.thorough:
            options = self._thorough(rl, product, pn, options)
        rl.alternatives = options

        best_option = packaging.best(options)
        chosen: PackagingOption | None = best_option
        if pinned_pn:
            chosen = next((o for o in options if o.dk_pn.casefold() == pinned_pn.casefold()), None)
            if chosen is None:
                rl.flags.append("PN_NOT_IN_VARIATIONS")
                chosen = best_option
            elif prefs.repackage_supplier_pn and best_option:
                chosen = best_option
            if chosen and chosen.valid and best_option and best_option is not chosen:
                saving = (chosen.total_cost or 0) - (best_option.total_cost or 0)
                if saving > 0:
                    rl.flags.append(f"CHEAPER_PACKAGING:{best_option.dk_pn} saves {_money(saving)}")

        if product.status_class in _BAD_STATUS and not prefs.allow_obsolete:
            return self._unavailable(rl, product, pn, f"{product.status or product.status_class} at DigiKey")
        if chosen is None or not chosen.valid:
            problem = (chosen or (options[0] if options else None))
            problem = problem.invalid_reason if problem else "no packaging data"
            if best_option:
                problem += f"; {best_option.packaging or best_option.dk_pn} ({best_option.dk_pn}) is orderable"
            return self._unavailable(rl, product, pn, problem)

        rl.status = RESOLVED
        rl.dk_pn, rl.packaging, rl.order_qty = chosen.dk_pn, chosen.packaging or chosen.kind, chosen.order_qty
        rl.unit_price, rl.total_cost = chosen.unit_price, chosen.total_cost
        spare = chosen.order_qty - rl.needed_qty
        text = (
            f"{rl.mpn} by {rl.manufacturer}: {chosen.order_qty} x {rl.packaging} {chosen.dk_pn} "
            f"at {_money(chosen.unit_price)} = {_money(chosen.total_cost)}"
        )
        if rl.overage:
            text += f" (need {rl.base_qty} + {rl.overage} spare)"
        if spare:
            text += f", {spare} extra for MOQ/multiple"
        others = [o for o in options if o is not chosen and o.valid]
        if others:
            text += f"; {len(others)} other packaging(s) costed"
        if rl.flags:
            text += f" [{', '.join(f.split(':')[0] for f in rl.flags)}]"
        rl.reason = text
        return rl

    def _thorough(self, rl: ResolvedLine, product: ProductInfo, pn: str, options: list[PackagingOption]) -> list[PackagingOption]:
        """Add DigiKey's own quote and recommended package type as information."""
        for q in self.gw.quote(pn, rl.needed_qty):
            if q.total_price is None or not q.products:
                continue
            first = q.products[0]
            options.append(
                PackagingOption(
                    dk_pn=" + ".join(p[0] for p in q.products), packaging=q.label or first[1], kind="dk_quote",
                    order_qty=q.quantity or rl.needed_qty, unit_price=first[3], total_cost=q.total_price,
                    stock=q.stock, valid=False, invalid_reason="DigiKey quote, informational", source="dk_quote",
                )
            )
        types = self.gw.package_types(pn, rl.needed_qty)
        if types:
            rl.flags.append(f"DK_RECOMMENDS:{types[0].name or types[0].dk_pn}")
        if len(product.packagings) < 2:
            siblings = self.gw.alternate_packaging(pn)
            if siblings:
                rl.flags.append("ALT_PACKAGING:" + ",".join(siblings[:4]))
        return options

    def _unavailable(self, rl: ResolvedLine, product: ProductInfo, pn: str, problem: str) -> ResolvedLine:
        rl.status = UNAVAILABLE
        rl.dk_pn = pn
        rl.substitutes = self._substitutes(rl, pn)
        text = f"{rl.mpn} not orderable: {problem} (need {rl.needed_qty})"
        text += f"; {len(rl.substitutes)} substitute(s) listed, none applied" if rl.substitutes else "; no substitutes found"
        rl.reason = text
        return rl

    def _substitutes(self, rl: ResolvedLine, pn: str) -> list[Substitute]:
        try:
            subs = self.gw.substitutions(pn)
        except NotFound:
            return []
        rank = {"high": 0, "medium": 1, "low": 2}
        subs.sort(key=lambda s: (s.stock < rl.needed_qty, rank[s.confidence], s.unit_price if s.unit_price is not None else 1e9))
        out = []
        for s in subs[:_MAX_SUBSTITUTES]:
            fits = "stock covers the need" if s.stock >= rl.needed_qty else f"stock {s.stock} below {rl.needed_qty}"
            out.append(
                Substitute(
                    dk_pn=s.dk_pn, mpn=s.mpn, manufacturer=s.manufacturer, stock=s.stock,
                    unit_price=s.unit_price, kind=s.kind, confidence=s.confidence,
                    why=f"{s.kind or 'substitute'} ({s.confidence} confidence); {fits}",
                )
            )
        return out
