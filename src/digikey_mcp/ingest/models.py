"""Data records returned by the BOM ingest layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: Categories that describe board features rather than purchasable parts.
NON_PART_CATEGORIES = frozenset({"fiducial", "mounting_hole", "test_point"})


class IngestError(Exception):
    """The input could not be read as a BOM at all."""


class MissingDependencyError(IngestError, ImportError):
    """An optional dependency needed for the requested format is not installed."""


@dataclass
class BomLine:
    """One normalised BOM line (one distinct part, possibly many designators)."""

    line_id: int
    refdes: list[str] = field(default_factory=list)
    quantity: int = 1
    #: Canonical value text, e.g. "4.7kohm", "100nF". Falls back to the cleaned original.
    value: str | None = None
    #: Value text exactly as it appeared in the source.
    value_raw: str | None = None
    #: Numeric value in base units (ohm, F, H) when the value parsed.
    value_si: float | None = None
    #: "ohm", "F" or "H" when the value parsed.
    value_unit: str | None = None
    #: Tolerance found in the value text, e.g. "1%".
    tolerance: str | None = None
    footprint: str | None = None
    #: Short package name derived from the footprint, e.g. "0603".
    package: str | None = None
    description: str | None = None
    mpn: str | None = None
    manufacturer: str | None = None
    #: DigiKey part number, e.g. "296-8875-1-ND".
    supplier_pn: str | None = None
    dnp: bool = False
    #: resistor, capacitor, inductor, connector, ic, diode, led, transistor,
    #: crystal, switch, fuse, fiducial, mounting_hole, test_point or other.
    category_hint: str = "other"
    #: True when neither an MPN nor a supplier part number is present.
    generic: bool = True
    #: Original source row (first row when several rows were merged).
    raw: dict[str, Any] = field(default_factory=dict)
    #: 1-based row (or line / component) number of the first source row.
    source_row: int | None = None
    #: Every source row merged into this line.
    source_rows: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def orderable(self) -> bool:
        """False for DNP lines and for generic fiducials, holes and test points."""
        if self.dnp:
            return False
        return not (self.category_hint in NON_PART_CATEGORIES and self.generic)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["orderable"] = self.orderable
        return data


@dataclass
class MappingReport:
    """Which source header was mapped to which BomLine field."""

    #: header text -> field name
    mapped: dict[str, str] = field(default_factory=dict)
    #: non-empty headers that matched no field (their cells stay in BomLine.raw)
    unmapped: list[str] = field(default_factory=list)
    #: headers whose mapping came from the caller's override
    overridden: list[str] = field(default_factory=list)
    #: key fields (designator, quantity, value, part number) with no column
    missing_fields: list[str] = field(default_factory=list)
    #: 1-based source row of the header, None when the table had no header
    header_row: int | None = None
    #: worksheet name for xlsx input
    sheet: str | None = None

    @property
    def fields(self) -> dict[str, str]:
        """field name -> header text (inverse of ``mapped``)."""
        return {f: h for h, f in self.mapped.items()}


@dataclass
class UnparsedRow:
    """A source row that was not turned into a BomLine."""

    source_row: int | None
    text: str
    reason: str


@dataclass
class IngestResult:
    lines: list[BomLine] = field(default_factory=list)
    mapping_report: MappingReport | None = None
    unparsed_rows: list[UnparsedRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: non-blank rows above the header (tool name, project, date ...), kept for reference
    preamble: list[str] = field(default_factory=list)
    #: detected input format: csv, xml, xlsx, pdf or text
    fmt: str = "text"

    @property
    def dnp_lines(self) -> list[BomLine]:
        return [ln for ln in self.lines if ln.dnp]

    @property
    def non_part_lines(self) -> list[BomLine]:
        return [ln for ln in self.lines if ln.category_hint in NON_PART_CATEGORIES]

    @property
    def orderable_lines(self) -> list[BomLine]:
        return [ln for ln in self.lines if ln.orderable]
