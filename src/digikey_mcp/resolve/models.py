"""Result records of the resolver. All are JSON-serialisable through `to_dict`."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from ..ingest import BomLine

RESOLVED = "resolved"
AMBIGUOUS = "ambiguous"
NEEDS_DECISION = "needs_decision"
UNAVAILABLE = "unavailable"
DNP_SKIPPED = "dnp_skipped"
ERROR = "error"
#: decided "skip" by the user
SKIPPED = "skipped"
#: not yet processed (quota ran out or the time budget ended)
PENDING = "pending"

STATUSES = (RESOLVED, AMBIGUOUS, NEEDS_DECISION, UNAVAILABLE, DNP_SKIPPED, ERROR, SKIPPED, PENDING)
#: statuses that put a line in the cart
CARTABLE = (RESOLVED,)
#: statuses that block the order
BLOCKING = (AMBIGUOUS, NEEDS_DECISION, UNAVAILABLE, ERROR, PENDING)


class ResolveError(Exception):
    """A resolver-level failure the caller can act on (unknown session, bad decision ...)."""


@dataclass
class PackagingOption:
    dk_pn: str
    packaging: str
    kind: str
    order_qty: int
    unit_price: float | None
    total_cost: float | None
    #: units ordered beyond the quantity needed, and what they cost
    leftover: int = 0
    leftover_cost: float = 0.0
    moq: int = 1
    multiple: int = 1
    stock: int | None = None
    valid: bool = True
    #: why the option cannot be ordered, when `valid` is false
    invalid_reason: str = ""
    #: "variation" (computed from price breaks) or "dk_quote" (pricingbyquantity)
    source: str = "variation"

    def label(self) -> str:
        cost = "n/a" if self.total_cost is None else f"${self.total_cost:.2f}"
        text = f"{self.packaging or self.kind} {self.dk_pn}: {self.order_qty} pcs, {cost}"
        if self.leftover:
            text += f" ({self.leftover} spare, ${self.leftover_cost:.2f})"
        if not self.valid:
            text += f" [{self.invalid_reason}]"
        return text


@dataclass
class Candidate:
    dk_pn: str
    mpn: str
    manufacturer: str
    description: str = ""
    stock: int = 0
    #: unit price at the needed quantity in the cheapest valid packaging
    unit_price: float | None = None
    order_qty: int | None = None
    total_cost: float | None = None
    packaging: str = ""
    status: str = ""
    score: float = 0.0
    why: str = ""


@dataclass
class Substitute:
    dk_pn: str
    mpn: str
    manufacturer: str
    stock: int
    unit_price: float | None
    kind: str
    confidence: str
    why: str = ""


@dataclass
class ResolvedLine:
    line: BomLine
    #: the line's identity across BOM revisions
    key: str
    status: str = PENDING
    reason: str = ""
    base_qty: int = 0
    overage: int = 0
    needed_qty: int = 0
    dk_pn: str | None = None
    mpn: str | None = None
    manufacturer: str | None = None
    packaging: str | None = None
    #: quantity to put in the cart (needed quantity rounded to MOQ and multiples)
    order_qty: int | None = None
    unit_price: float | None = None
    total_cost: float | None = None
    product_status: str = ""
    #: how the part was found: supplier_pn, mpn+manufacturer, mpn, search or decision
    source: str = ""
    decided: bool = False
    flags: list[str] = field(default_factory=list)
    alternatives: list[PackagingOption] = field(default_factory=list)
    break_even: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    substitutes: list[Substitute] = field(default_factory=list)

    @property
    def cref(self) -> str:
        return ",".join(self.line.refdes)

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "line"}
        for name in ("alternatives", "candidates", "substitutes"):
            data[name] = [asdict(x) for x in data[name]]
        data["line"] = self.line.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolvedLine:
        data = dict(data)
        line_data = dict(data.pop("line"))
        line_data.pop("orderable", None)
        line = BomLine(**line_data)
        for name, kind in (("alternatives", PackagingOption), ("candidates", Candidate), ("substitutes", Substitute)):
            data[name] = [kind(**x) for x in data.get(name, [])]
        return cls(line=line, **data)

    def summary(self) -> dict[str, Any]:
        """Compact view for tool output."""
        out: dict[str, Any] = {
            "line_id": self.line.line_id,
            "refdes": self.line.refdes,
            "status": self.status,
            "reason": self.reason,
            "needed_qty": self.needed_qty,
        }
        if self.dk_pn:
            out.update(
                digikey_part_number=self.dk_pn, mpn=self.mpn, manufacturer=self.manufacturer,
                packaging=self.packaging, order_qty=self.order_qty, unit_price=self.unit_price,
                total_cost=self.total_cost,
            )
        if self.flags:
            out["flags"] = self.flags
        if self.alternatives:
            out["packaging_options"] = [o.label() for o in self.alternatives]
        if self.break_even:
            out["break_even"] = self.break_even
        for name in ("candidates", "substitutes"):
            items = getattr(self, name)
            if items:
                out[name] = [asdict(x) for x in items]
        return out


@dataclass
class ResolvedBom:
    session_id: str
    boards: int
    lines: list[ResolvedLine]
    #: False when the run stopped early (quota, time budget); resume with the session id
    complete: bool = True
    #: set when the run stopped early
    stopped: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {s: 0 for s in STATUSES}
        for ln in self.lines:
            counts[ln.status] += 1
        return {k: v for k, v in counts.items() if v}

    @property
    def estimated_total(self) -> float:
        return round(sum(ln.total_cost or 0.0 for ln in self.lines if ln.status in CARTABLE), 2)

    @property
    def blocking(self) -> list[ResolvedLine]:
        return [ln for ln in self.lines if ln.status in BLOCKING]

    def to_dict(self, detail: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "session_id": self.session_id,
            "boards": self.boards,
            "complete": self.complete,
            "line_count": len(self.lines),
            "counts": self.counts,
            "estimated_total": self.estimated_total,
            "ready_for_cart": not self.blocking and self.complete,
            "blocking": [
                {"line_id": ln.line.line_id, "refdes": ln.line.refdes, "status": ln.status, "reason": ln.reason}
                for ln in self.blocking
            ],
        }
        if self.stopped:
            data["stopped"] = self.stopped
        if self.warnings:
            data["warnings"] = self.warnings
        if detail:
            data["lines"] = [ln.summary() for ln in self.lines]
        return data
