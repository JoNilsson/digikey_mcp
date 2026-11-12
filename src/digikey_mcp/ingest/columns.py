"""Header synonym table, column mapping and header-row detection."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from .models import MappingReport

#: Fields a header can be mapped to. "item" and "ignore" are recognised but unused.
FIELDS = (
    "refdes", "quantity", "value", "footprint", "description", "mpn",
    "manufacturer", "digikey_pn", "supplier_pn", "dnp", "populate", "item", "ignore",
)

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "refdes": (
        "refdes", "reference", "references", "referencedesignator", "referencedesignators",
        "designator", "designators", "ref", "refs", "refdesignator", "refdess",
        "location", "locations", "partreference", "parts", "partref",
    ),
    "quantity": ("quantity", "qty", "qnty", "qtyperboard", "count", "quan", "pcs", "amount", "qtyboard"),
    "value": ("value", "val", "comment", "componentvalue", "partvalue", "values"),
    "footprint": (
        "footprint", "package", "pkg", "packagecase", "casepackage", "case", "pattern",
        "footprintname", "pcbfootprint", "packagetype", "housing",
    ),
    "description": ("description", "desc", "partdescription", "componentdescription", "descr", "libdescription"),
    "mpn": (
        "mpn", "mfrpart", "mfrpartnumber", "mfrpartno", "mfrpartnum", "mfrpn",
        "manufacturerpartnumber", "manufacturerpart", "manufacturerpn", "manufacturerpartno",
        "mfgpartnumber", "mfgpn", "mfgpart", "mfgpartno", "mfgrpartnumber", "mfgrpn",
        "manufacturerpartnum", "manufacturersparnumber", "partnumber", "partno", "partnum",
        "pn", "part", "orderingcode", "orderingpartnumber", "mpnumber",
    ),
    "manufacturer": (
        "manufacturer", "manufacturername", "mfr", "mfg", "mfgr", "make", "brand",
        "manufacture", "mfgname", "mfrname", "maker",
    ),
    "digikey_pn": (
        "digikeypn", "digikeypartnumber", "digikeypartno", "digikeypart", "digikeyno",
        "digikeynumber", "digikey", "dkpn", "dkpart", "dkpartnumber", "dkpartno",
        "dknumber", "dk", "dkno", "digikeyorderingnumber", "digikeysku",
    ),
    "supplier_pn": (
        "supplierpartnumber", "supplierpn", "supplierpart", "supplierpartno", "supplierno",
        "suppliernumber", "orderno", "ordernumber", "vendorpartnumber",
        "vendorpn", "distributorpartnumber", "distributorpn", "sku", "stockcode",
    ),
    "dnp": (
        "dnp", "dnf", "dni", "dnl", "donotpopulate", "donotfit", "donotplace", "donotinstall",
        "notfitted", "notpopulated", "nofit", "nopop", "dnpdnf", "dnpdni", "excludefrombom",
        "excludedfrombom", "bomexclude", "dnpstatus",
    ),
    "populate": ("populate", "populated", "fitted", "fit", "placed", "install", "installed", "mount", "stuff"),
    "item": ("item", "itemno", "itemnumber", "no", "line", "lineno", "lineitem", "index", "id", "number", "seq", "sn"),
}

_RANK: dict[str, tuple[str, int]] = {}
for _field, _names in _SYNONYMS.items():
    for _i, _n in enumerate(_names):
        _RANK.setdefault(_n, (_field, _i))

#: (key must contain any of, key must also contain any of or None, field)
_FUZZY = (
    (("digikey",), ("pn", "part", "no", "number", "sku"), "digikey_pn"),
    (("digikey",), None, "digikey_pn"),
    (("mfr", "mfg", "mfgr", "manufacturer", "mpn"), ("part", "pn", "number", "mpn"), "mpn"),
    (("mfr", "mfg", "mfgr", "manufacturer"), None, "manufacturer"),
    (("supplier", "vendor", "distributor"), ("part", "pn", "number", "sku"), "supplier_pn"),
    (("designator", "refdes"), None, "refdes"),
    (("quantity", "qty"), None, "quantity"),
    (("description",), None, "description"),
    (("footprint",), None, "footprint"),
    (("value",), None, "value"),
    (("donotpopulate", "dnp", "dnf"), None, "dnp"),
)
_FUZZY_RANK = 1000

#: Headers of unmapped columns whose cells may carry DNP remarks.
NOTE_HEADER = re.compile(r"note|remark|comment|variant|status|populat|assembly|option", re.I)

_KEY_FIELDS = ("refdes", "quantity", "value", "mpn")


def norm_header(header: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def _candidates(header: str) -> list[str]:
    cands = [norm_header(header)]
    no_paren = norm_header(re.sub(r"\(.*?\)", "", header))
    cands.append(no_paren)
    for inner in re.findall(r"\((.*?)\)", header):
        cands.append(norm_header(inner))
    cands.extend(c.rstrip("0123456789") for c in list(cands) if c.rstrip("0123456789") != c)
    out = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def classify_header(header: str | None) -> tuple[str, int] | None:
    """(field, rank) for a header, or None when it is not recognised."""
    if not header or not header.strip():
        return None
    cands = _candidates(header)
    for c in cands:
        if c in _RANK:
            return _RANK[c]
    for c in cands:
        for needles, extra, fld in _FUZZY:
            if any(n in c for n in needles) and (extra is None or any(e in c for e in extra)):
                return fld, _FUZZY_RANK
    return None


def validate_override(mapping: Mapping | None) -> dict:
    out: dict = {}
    for key, fld in (mapping or {}).items():
        target = "ignore" if fld is None else fld
        if target not in FIELDS:
            raise ValueError(
                f"unknown field {fld!r} in mapping override; valid fields: {', '.join(FIELDS)}"
            )
        out[key if isinstance(key, int) else norm_header(str(key))] = target
    return out


def header_is_recognised(cells: Iterable[str], override_keys: Iterable = ()) -> tuple[int, int, int]:
    """(distinct fields, recognised cells, non-empty cells) for a candidate header row."""
    keys = {k for k in override_keys if isinstance(k, str)}
    fields: set[str] = set()
    hits = nonempty = 0
    for cell in cells:
        if not cell or not cell.strip():
            continue
        nonempty += 1
        c = classify_header(cell)
        if c:
            hits += 1
            if c[0] not in ("item", "ignore"):
                fields.add(c[0])
        elif norm_header(cell) in keys:
            hits += 1
            fields.add("override:" + norm_header(cell))
    return len(fields), hits, nonempty


def find_header_row(rows: list[list[str]], override_keys: Iterable = (), limit: int = 40) -> int | None:
    """Index of the most header-like row among the first ``limit`` rows."""
    best: tuple[int, int] | None = None
    best_idx = None
    for idx, cells in enumerate(rows[:limit]):
        score, hits, nonempty = header_is_recognised(cells, override_keys)
        if score < 2 or not (score >= 3 or hits / max(nonempty, 1) >= 0.6):
            continue
        cand = (score, hits)
        if best is None or cand > best:
            best, best_idx = cand, idx
    return best_idx


def map_headers(
    headers: list[str],
    override: dict | None = None,
    *,
    header_row: int | None = None,
    sheet: str | None = None,
) -> tuple[dict[int, str], MappingReport, list[str]]:
    """Map column indexes to field names.

    Returns (index -> field, report, warnings). ``override`` is the validated
    output of :func:`validate_override`.
    """
    override = override or {}
    warnings: list[str] = []
    chosen: dict[str, tuple[int, int]] = {}  # field -> (column index, rank)
    forced: dict[int, str] = {}
    for idx, header in enumerate(headers):
        if idx in override:
            forced[idx] = override[idx]
        elif norm_header(header) in override and norm_header(header):
            forced[idx] = override[norm_header(header)]
    forced_fields: dict[str, int] = {}
    for idx, fld in list(forced.items()):
        if fld == "ignore":
            continue
        if fld in forced_fields:
            warnings.append(
                f"override maps both {headers[forced_fields[fld]]!r} and {headers[idx]!r} to {fld}; "
                f"using the first"
            )
            forced[idx] = "ignore"
            continue
        forced_fields[fld] = idx
    for idx, header in enumerate(headers):
        if idx in forced:
            continue
        c = classify_header(header)
        if not c:
            continue
        fld, rank = c
        if fld in forced_fields:
            continue
        if fld not in chosen or rank < chosen[fld][1]:
            chosen[fld] = (idx, rank)
    index_field = {idx: fld for fld, (idx, _r) in chosen.items()}
    index_field.update({idx: fld for idx, fld in forced.items()})

    report = MappingReport(header_row=header_row, sheet=sheet)
    for idx, header in enumerate(headers):
        label = header.strip()
        if idx in index_field:
            report.mapped[label or f"column {idx + 1}"] = index_field[idx]
            if idx in forced:
                report.overridden.append(label or f"column {idx + 1}")
        elif label:
            report.unmapped.append(label)
    present = set(index_field.values())
    if "digikey_pn" in present:
        present.add("mpn")
    report.missing_fields = [f for f in _KEY_FIELDS if f not in present]
    return index_field, report, warnings
