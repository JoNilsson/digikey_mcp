"""Build DigiKey FastAdd links, a POST form page and quick-order text.

Everything here is pure string building: no network calls, nothing is
submitted. Part numbers and customer references are untrusted, so every line
is normalised by `normalize_lines` before it reaches a URL, a form or text.
"""

from __future__ import annotations

import html
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

FASTADD_URL = "https://www.digikey.com/classic/ordering/fastadd.aspx"
DEFAULT_MAX_URL_LEN = 1700
MAX_PARTS_PER_FORM = 400
MAX_CREF_LEN = 50
MAX_PART_NUMBER_LEN = 100
MAX_QUANTITY = 9_999_999
MAX_UTM_SOURCE_LEN = 64


class CartInputError(ValueError):
    """A BOM line or option cannot be turned into a cart link."""


@dataclass(frozen=True)
class CartLine:
    """One validated line. `number` is the 1-based position in the input."""

    number: int
    part_number: str
    quantity: int
    customer_reference: str


@dataclass(frozen=True)
class UrlChunk:
    """One FastAdd URL and the input lines (1-based, inclusive) it covers."""

    url: str
    first_line: int
    last_line: int
    length: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    """Drop control and format characters, collapse whitespace runs, strip."""
    text = "" if value is None else str(value)
    text = "".join(" " if ch.isspace() else ch for ch in text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())


def _parse_quantity(value: Any, number: int) -> int:
    if isinstance(value, bool):
        raise CartInputError(f"line {number}: quantity must be a positive integer, got {value!r}")
    if isinstance(value, int):
        qty = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        qty = int(value.strip())
    else:
        raise CartInputError(f"line {number}: quantity must be a positive integer, got {value!r}")
    if qty < 1:
        raise CartInputError(f"line {number}: quantity must be a positive integer, got {value!r}")
    if qty > MAX_QUANTITY:
        raise CartInputError(f"line {number}: quantity {qty} exceeds the maximum of {MAX_QUANTITY}")
    return qty


def normalize_lines(lines: Iterable[Any]) -> list[CartLine]:
    """Validate raw lines and return `CartLine`s numbered from 1.

    Each raw line is a sequence `(part_number, quantity[, customer_reference])`
    or a mapping with keys `digikey_part_number` (or `part_number`),
    `quantity` and optionally `customer_reference`. Part numbers and
    references lose control characters and surrounding whitespace; references
    are truncated to MAX_CREF_LEN. An empty part number, a non-positive or
    non-integer quantity, or an over-long part number raises CartInputError
    naming the line.
    """
    out: list[CartLine] = []
    for number, raw in enumerate(lines, start=1):
        if isinstance(raw, Mapping):
            pn = raw.get("digikey_part_number", raw.get("part_number"))
            qty = raw.get("quantity")
            cref = raw.get("customer_reference")
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and 2 <= len(raw) <= 3:
            pn, qty = raw[0], raw[1]
            cref = raw[2] if len(raw) == 3 else None
        else:
            raise CartInputError(
                f"line {number}: expected (part number, quantity[, customer reference]), got {raw!r}"
            )
        pn_clean = _clean(pn)
        if not pn_clean:
            raise CartInputError(f"line {number}: empty part number")
        if len(pn_clean) > MAX_PART_NUMBER_LEN:
            raise CartInputError(
                f"line {number}: part number longer than {MAX_PART_NUMBER_LEN} characters"
            )
        out.append(
            CartLine(number, pn_clean, _parse_quantity(qty, number), _clean(cref)[:MAX_CREF_LEN].strip())
        )
    return out


def _enc(value: str) -> str:
    return quote(value, safe="")


def _utm(utm_source: str | None) -> str:
    if utm_source is None:
        return ""
    cleaned = _clean(utm_source)[:MAX_UTM_SOURCE_LEN]
    return cleaned


def _line_params(line: CartLine, index: int) -> str:
    params = f"part{index}={_enc(line.part_number)}&qty{index}={line.quantity}"
    if line.customer_reference:
        params += f"&cref{index}={_enc(line.customer_reference)}"
    return params


def build_fastadd_urls(
    lines: Iterable[Any],
    *,
    newcart: bool = True,
    utm_source: str | None = None,
    max_url_len: int = DEFAULT_MAX_URL_LEN,
) -> list[UrlChunk]:
    """Build FastAdd GET URLs, one chunk per URL, none longer than `max_url_len`.

    `lines` holds `(digikey_part_number, quantity, customer_reference)` items
    (see `normalize_lines`). Lines are never split across URLs and keep their
    order; inside each URL they are numbered from 1. `newcart=true` is added
    to the first URL only, so later URLs append to the cart the first one
    emptied. `utm_source`, if given, is added to every URL. A single line that
    cannot fit alone raises CartInputError naming it; an empty BOM returns [].
    """
    if max_url_len < len(FASTADD_URL) + 1:
        raise CartInputError(f"max_url_len {max_url_len} is too small for a FastAdd URL")
    normalized = normalize_lines(lines)
    utm = _utm(utm_source)
    tail = f"&utm_source={_enc(utm)}" if utm else ""

    def build(group: Sequence[CartLine], first: bool) -> str:
        parts = [_line_params(line, i) for i, line in enumerate(group, start=1)]
        head = "newcart=true&" if (newcart and first) else ""
        return f"{FASTADD_URL}?{head}{'&'.join(parts)}{tail}"

    chunks: list[UrlChunk] = []
    group: list[CartLine] = []
    for line in normalized:
        candidate = [*group, line]
        if len(build(candidate, not chunks)) <= max_url_len:
            group = candidate
            continue
        if not group:
            raise CartInputError(
                f"line {line.number}: a single line needs {len(build(candidate, not chunks))} "
                f"characters, over the limit of {max_url_len}"
            )
        url = build(group, not chunks)
        chunks.append(UrlChunk(url, group[0].number, group[-1].number, len(url)))
        group = [line]
        if len(build(group, False)) > max_url_len:
            raise CartInputError(
                f"line {line.number}: a single line needs {len(build(group, False))} "
                f"characters, over the limit of {max_url_len}"
            )
    if group:
        url = build(group, not chunks)
        chunks.append(UrlChunk(url, group[0].number, group[-1].number, len(url)))
    return chunks


def build_fastadd_form_html(
    lines: Iterable[Any],
    *,
    newcart: bool = True,
    utm_source: str | None = None,
    max_parts_per_form: int = MAX_PARTS_PER_FORM,
) -> str:
    """Return a self-contained HTML page with FastAdd POST forms.

    For BOMs too large for GET URLs. Each form carries at most
    `max_parts_per_form` (default 400, the size DigiKey advises) parts and has
    its own visible submit button. `newcart=true` is a hidden field of the
    first form only. The page contains no script and a CSP that forbids
    scripts, so nothing is ever submitted unless the user clicks a button.
    """
    if not 1 <= max_parts_per_form <= MAX_PARTS_PER_FORM:
        raise CartInputError(f"max_parts_per_form must be between 1 and {MAX_PARTS_PER_FORM}")
    normalized = normalize_lines(lines)
    utm = _utm(utm_source)
    esc = html.escape

    def hidden(name: str, value: str) -> str:
        return f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">'

    forms: list[str] = []
    groups = [normalized[i : i + max_parts_per_form] for i in range(0, len(normalized), max_parts_per_form)]
    for form_no, group in enumerate(groups, start=1):
        fields: list[str] = []
        if newcart and form_no == 1:
            fields.append(hidden("newcart", "true"))
        rows: list[str] = []
        for idx, line in enumerate(group, start=1):
            fields.append(hidden(f"part{idx}", line.part_number))
            fields.append(hidden(f"qty{idx}", str(line.quantity)))
            if line.customer_reference:
                fields.append(hidden(f"cref{idx}", line.customer_reference))
            rows.append(
                f"<tr><td>{line.number}</td><td>{esc(line.part_number)}</td>"
                f"<td>{line.quantity}</td><td>{esc(line.customer_reference)}</td></tr>"
            )
        if utm:
            fields.append(hidden("utm_source", utm))
        label = f"Add lines {group[0].number}-{group[-1].number} ({len(group)} parts) to DigiKey cart"
        note = " Empties the DigiKey cart first." if newcart and form_no == 1 else ""
        forms.append(
            f"<section>\n<h2>Form {form_no} of {len(groups)}</h2>\n"
            f'<form method="post" action="{FASTADD_URL}" target="_blank">\n'
            + "\n".join(fields)
            + f'\n<button type="submit">{esc(label)}</button>{esc(note)}\n</form>\n'
            "<table>\n<tr><th>Line</th><th>Part number</th><th>Qty</th><th>Reference</th></tr>\n"
            + "\n".join(rows)
            + "\n</table>\n</section>"
        )
    body = "\n".join(forms) if forms else "<p>No lines.</p>"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        "<title>DigiKey cart handoff</title>\n"
        "<style>body{font-family:sans-serif;margin:2em}table{border-collapse:collapse;margin-top:1em}"
        "td,th{border:1px solid #888;padding:2px 8px;text-align:left}button{font-size:1.1em;padding:.4em 1em}"
        "</style>\n</head>\n<body>\n<h1>DigiKey cart handoff</h1>\n"
        "<p>Nothing is sent until you click a button. Each button opens DigiKey in a new tab "
        "with that form's parts in your cart; sign in and review the cart there.</p>\n"
        f"{body}\n</body>\n</html>\n"
    )


def build_quickorder_text(lines: Iterable[Any]) -> str:
    """Return `qty, part number, customer reference` lines for DigiKey Quick Order.

    One line per part, in input order; the reference is left out when empty.
    Commas inside a part number or reference become spaces so each line keeps
    three comma-separated fields. The importer's exact format (column order,
    delimiter, size limit) is unconfirmed: check DigiKey's Quick Order page
    before relying on this.
    """
    out = []
    for line in normalize_lines(lines):
        fields = [str(line.quantity), line.part_number.replace(",", " ")]
        if line.customer_reference:
            fields.append(line.customer_reference.replace(",", " "))
        out.append(", ".join(fields))
    return "\n".join(out) + ("\n" if out else "")
