# BOM resolver

Turns the `BomLine` records from `digikey_mcp.ingest` into exact DigiKey part
numbers and order quantities, ready for the cart tools. Nothing is submitted;
the network is reached only through `DigiKeyClient`. All assumptions about
DigiKey response shapes (trimmed field names, packaging names, status text,
substitute types) live in `adapter.py` and are not yet checked against live
responses.

```python
from digikey_mcp.resolve import resolve_bom, apply_decisions, cart_items, to_markdown

bom = resolve_bom("bom.csv", boards=2, client=client, session_id="proj")
bom = apply_decisions("proj", {3: "1276-1000-1-ND", 4: "skip"}, client=client)
items = cart_items("proj")        # [(digikey_pn, quantity, cref), ...]
```

`register(mcp)` adds the MCP tools `resolve_bom`, `apply_decisions`,
`bom_report`, `cart_items_for_session` and `quota_status`. The tool client
comes from `digikey_mcp.server.get_client`; `register(mcp, client_factory=...)`
overrides it.

## Resolution ladder

| Line has | Calls | Result |
| --- | --- | --- |
| DNP, fiducial, hole, test point | none | `dnp_skipped` |
| DigiKey part number | `productdetails` | that exact packaging, verified |
| MPN and manufacturer | keyword search, exact MPN filter, fuzzy manufacturer match | `resolved`; `ambiguous` if the manufacturer does not match the exact-MPN hits |
| MPN only | keyword search, exact MPN filter | `resolved`; `ambiguous` if several manufacturers list it |
| nothing (generic) | keyword search from value, package, tolerance, category noun and preference keywords | always `needs_decision` with up to 5 ranked candidates |

Manufacturer matching normalises case, punctuation and corporate suffixes and
applies an alias table (Texas Instruments = TI, STMicroelectronics = ST, NXP =
Freescale, onsemi = Fairchild, and others) before a prefix and similarity test.
An MPN with no exact hit lists near matches as `ambiguous`; with no hits at all
it is `unavailable`. A DigiKey part number or MPN that DigiKey does not know is
`unavailable`; other API failures are `error`.

Candidates carry DigiKey part number, MPN, manufacturer, stock, unit price at
the needed quantity and a `why` string. Ranking adds points for stock covering
the quantity, active status, package, tolerance and keyword matches in the
description and parameters, preferred manufacturers and a low total cost.
Out-of-stock parts and marketplace sellers rank last or are dropped.

## Statuses

`resolved`, `ambiguous`, `needs_decision`, `unavailable`, `dnp_skipped`,
`error`, plus `skipped` (decided skip) and `pending` (not reached because the
quota or time budget ran out). Every line has a one-line `reason`. Lines in
`ambiguous`, `needs_decision`, `unavailable`, `error` and `pending` block the
order and are listed under `blocking`.

## Quantity

`needed = boards x per-board quantity + overage`. Overage is
`max(ceil(base x percent / 100), min_extra)`, capped by `max_extra`, per
category. Defaults: resistor and capacitor 10 % (at least 2), LED 10 % (at
least 1), inductor, diode and transistor 5 % (at least 1), everything else 0.
The order quantity is then raised to the packaging's minimum and rounded up to
its multiple: tape and reel orders are multiples of the reel size, other
packagings use their minimum order quantity only.

## Packaging

Each product's packaging variations (cut tape, tape and reel, DigiReel, tray,
tube, bulk) are costed at the needed quantity from their price breaks:
`unit price at the rounded quantity x quantity + fee`, with spare units and
their cost reported. A variation is invalid when it has no price, its stock is
below the order quantity, it is a marketplace seller, or the packaging is not in
`packaging.allowed`. The cheapest valid variation is chosen; all alternatives
with total cost stay on the line. When a reel and a cut-tape or DigiReel
variation both exist the line carries the break-even quantity where the reel
becomes cheaper.

A line that names a DigiKey part number keeps that packaging; a cheaper valid
packaging is flagged `CHEAPER_PACKAGING` (set `repackage_supplier_pn` to switch).
`packagetypebyquantity` supplies price breaks when `productdetails` has none.
With `[api] thorough = true` every line also calls `pricingbyquantity`
(DigiKey's own quote, listed as information), `packagetypebyquantity` and
`alternatepackaging`.

## Verification

Stock must cover the order quantity in the chosen packaging. Status flags:
`NRND` and `LAST_TIME_BUY` stay `resolved` with the flag; Obsolete and
Discontinued become `unavailable` unless `allow_obsolete` is set. `MARKETPLACE`
marks third-party sellers, which are not orderable unless `allow_marketplace`
is set. An unavailable line calls `substitutions` and attaches up to 5
substitutes ranked by stock coverage, confidence (`high` for direct, `medium`
for parametric or equivalent, `low` otherwise) and price. A substitute is never
applied; choosing one is a decision.

## Sessions and decisions

A session is `sessions/<id>.json` under `$XDG_CACHE_HOME/digikey-mcp` (default
`~/.cache/digikey-mcp`). It stores the lines, results, decisions and
preferences and is saved after every line.

* `apply_decisions(session_id, {line_id: digikey_pn or "skip"})` looks up and
  verifies the chosen part like any other line, or marks the line `skipped`.
  A decision can name a candidate, a substitute or any part number.
* Decisions are keyed by line identity: the MPN, else the DigiKey part number,
  else category, value and footprint. Calling `resolve_bom` with an existing
  session id and a changed source or board count re-resolves the revised BOM
  and applies the stored decisions to every line whose identity is unchanged.
* `resolve_bom(session_id=...)` without a source, or with the unchanged source,
  resumes `pending` and `error` lines.

## Cache and quota

`cache.sqlite3` in the same directory caches trimmed API responses keyed by
endpoint and arguments: 6 hours for price and stock bearing endpoints, 1 day
for `alternatepackaging`. Unknown parts (404) are cached as well. Expired
entries are deleted at the start of every run.

Quota defaults follow DigiKey's 120 calls per minute and 1000 per day minus a
reserve of 5, counted for calls made through the resolver (cache hits are free;
the daily counter uses UTC dates). When the per-minute window is full the
resolver sleeps if the wait is at most `max_sleep_seconds` (65) and the run is
within `time_budget_seconds` (240); otherwise, and for the daily limit, it stops
and returns `complete: false` with a `stopped` message such as
`quota exhausted (day): 41 of 60 lines done, resume with session_id 'proj'`.
A 403 or 429 from DigiKey stops the run and holds all calls off for 10 minutes.
`quota_status` reports the counters.

## Preferences

TOML file passed as `preferences_path`; unknown sections or keys are rejected.

```toml
[overage.resistor]
percent = 10
min_extra = 2
max_extra = 20        # optional

[packaging]
allow_marketplace = false
allow_obsolete = false
allowed = ["cut tape", "tape & reel"]   # empty allows all
repackage_supplier_pn = false

[generic]
keywords = { resistor = ["1%"], capacitor = ["X7R", "50V"] }
preferred_manufacturers = ["Yageo", "Murata"]
max_candidates = 5
search_limit = 25

[quota]
per_minute = 120
per_day = 1000
reserve = 5
max_sleep_seconds = 65
time_budget_seconds = 240

[cache]
price_ttl_hours = 6
static_ttl_days = 1

[api]
thorough = false
```

Reading the file needs Python 3.11 (`tomllib`) or the `tomli` package.

## Output

`ResolvedBom` holds the lines, `counts` by status, `estimated_total` (resolved
lines only), `blocking` lines and `complete`. `to_markdown` and `to_csv` render
the report; the `bom_report` tool writes only to a `.md` or `.csv` path the
caller gives. `cart_items(session_id)` returns `(digikey_pn, order quantity,
ref-des list)` for resolved or decided, non-skipped lines; the
`cart_items_for_session` tool returns the same as dictionaries for `cart_links`
together with the blocking lines.

## Tests

`tests/resolve` runs offline against a stub client in `resolve_fakes.py`.
