# DigiKey MCP server

An MCP server (FastMCP, stdio) and a command line that turn a bill of
materials into exact DigiKey part numbers and order quantities and build links
that fill a DigiKey cart. Nothing is ever submitted as an order: the user opens
the links in a signed-in browser, reviews the cart, picks the payment method
and clicks buy.

The server also exposes the DigiKey Product Information v4 endpoints as
individual search and pricing tools. The command line (`digikey-mcp`) runs the
whole BOM-to-cart workflow from a terminal and calls the same functions as the
MCP tools.

For a step-by-step setup without Nix see [GETTING_STARTED.md](GETTING_STARTED.md).

## Workflow

1. Ingest: `resolve_bom` reads the BOM (KiCad or Altium CSV, KiCad XML, other
   CSV/TSV/XML, `.xlsx`, PDF, or pasted text) and normalises it to lines with
   designators, quantity, value, footprint, MPN, manufacturer and DigiKey part
   number. DNP lines, fiducials, mounting holes and test points are kept and
   not ordered.
2. Resolve: each line is resolved through the ladder DigiKey part number, then
   MPN with manufacturer, then MPN alone. Stock, status, marketplace sellers
   and packaging are checked, the cheapest valid packaging is chosen and the
   quantity is boards times per-board quantity plus overage, rounded to the
   minimum order quantity and reel multiples.
3. Generic lines (no MPN and no DigiKey number, for example `10k 0603 1%`) are
   never picked automatically. They always come back as `needs_decision` with
   up to five ranked candidates. Ambiguous, unavailable and failed lines are
   reported as blocking.
4. Decisions: `apply_decisions` maps a line id to a DigiKey part number
   (a candidate, a substitute or any part) or `skip`. The chosen part is
   verified like any other line. Decisions persist in the session and survive
   BOM revisions.
5. Report: `bom_report` renders the resolved BOM as markdown or CSV with
   status, reason, packaging, quantity, prices and the estimated total.
6. Cart: `cart_items_for_session` returns the orderable lines and the blocking
   ones; `cart_links` turns the items into FastAdd URLs, chunked to stay under
   1700 characters. The first URL empties the cart, the rest add to it. Large
   BOMs can use `output="html"`, a page of POST forms of up to 400 parts each.
7. Payment: the user opens the links in a signed-in browser and pays. Nothing
   in this server calls the DigiKey ordering API.

Detail on the ingest and resolver stages is in
[src/digikey_mcp/ingest/README.md](src/digikey_mcp/ingest/README.md) and
[src/digikey_mcp/resolve/README.md](src/digikey_mcp/resolve/README.md).

## Command line

`digikey-mcp` with no arguments prints a welcome screen when it runs in a
terminal and starts the MCP server when stdin is piped, so MCP client
registrations that pass no arguments keep working. `digikey-mcp --help`,
`digikey-mcp help` and `python -m digikey_mcp` show the same overview;
`digikey-mcp COMMAND --help` documents one command with worked examples.

| Command | Purpose |
| --- | --- |
| `ingest` | Parse a BOM file; show column mapping, unparsed rows, warnings. |
| `resolve` | Resolve a BOM (file, `-` or `--text`) to DigiKey part numbers. |
| `decide` | Answer lines: `LINE=PN`, `LINE=skip` or `--interactive`. |
| `report` | Write a session as markdown, CSV or JSON. |
| `cart` | Build FastAdd URLs, HTML forms or Quick Order text. |
| `search` | Keyword search, trimmed (`--limit`, `--in-stock`). |
| `part` | One product by DigiKey part number or MPN (`--raw`). |
| `quota` | DigiKey calls used and left this minute and UTC day. |
| `sessions` | Saved sessions with status counts. |
| `serve` | Start the MCP server on stdio (`--sandbox`). |
| `version`, `welcome`, `help` | Version, overview, help for one command. |

Global flags, accepted before or after the command: `--version`,
`--cache-dir DIR`, `-q/--quiet`, `--no-color`. Colour is used only when stdout
is a terminal and `NO_COLOR` is unset. Commands that print results take
`--json` (`report` takes `--format json`) and then write only JSON to stdout;
progress, hints and the cart notice go to stderr.

A typical run:

```bash
digikey-mcp ingest bom.csv                      # check how the BOM parses
digikey-mcp resolve bom.csv --boards 2          # prints the session id
digikey-mcp decide SESSION --interactive        # answer generic lines
digikey-mcp report SESSION --out bom.md
digikey-mcp cart SESSION                        # links for your own signed-in cart
```

Exit codes: 0 success; 1 runtime error; 2 usage error; 3 `resolve` or `decide`
ended with lines that still block the order, or the DigiKey quota ran out. In
the second case the session id and the resume command
(`digikey-mcp resolve --session SESSION`) are printed. Errors are one line plus
a hint on stderr.

## Setup

1. Create an account and organisation at <https://developer.digikey.com>.
2. Create a Production app with the Product Information v4 API enabled. Note
   its client ID and client secret. Two-legged OAuth (client credentials) is
   used; no OAuth callback is needed.
3. Put the client ID and the client secret in two separate files, each holding
   only the value, and keep them out of the repository.
4. Point the server at them with environment variables:

- `CLIENT_ID_FILE`, `CLIENT_SECRET_FILE`: paths to the files holding the
  client ID and the client secret.
- `CLIENT_ID`, `CLIENT_SECRET`: the values themselves, used only when the
  matching `_FILE` variable is unset.
- `DIGIKEY_SANDBOX`: `true` selects `sandbox-api.digikey.com`; unset or
  `false` selects production.
- `DIGIKEY_LOCALE_SITE`, `DIGIKEY_LOCALE_LANGUAGE`,
  `DIGIKEY_LOCALE_CURRENCY`: optional locale headers.
- `DIGIKEY_CUSTOMER_ID`: optional customer ID for account pricing.

Credentials are read on the first DigiKey call, so the server starts and
`cart_links` works without them.

## Running

### Nix

```bash
nix develop                  # dev shell: Python with all dependencies, ruff, pyright
pytest -q tests              # offline test suite
nix build .#default          # package with the ingest group; binary at result/bin/digikey-mcp
nix run .                    # welcome screen
nix run . -- serve           # run the server on stdio
```

Home Manager module (`homeManagerModules.default`), which installs the
package and registers the server for Claude Code:

```nix
services.digikey-mcp = {
  enable = true;
  clientIdFile = "/run/secrets/digikey/client_id";
  clientSecretFile = "/run/secrets/digikey/client_secret";
  # sandbox = true;      # sets DIGIKEY_SANDBOX=true
  # serverName = "digikey";
};
```

The module writes `~/.config/digikey-mcp/mcp.json` for
`claude --mcp-config`, and also sets `programs.claude-code.mcpServers` when
that Home Manager option exists. Only the file paths enter the Nix store, never
the secrets.

### uv or pip

Python 3.11 or newer is required.

```bash
uv sync --extra ingest       # or: pip install -e ".[ingest]"
CLIENT_ID_FILE=~/.config/digikey/client_id \
CLIENT_SECRET_FILE=~/.config/digikey/client_secret \
uv run digikey-mcp serve     # or: python -m digikey_mcp.server
```

The `ingest` extra adds `openpyxl` (`.xlsx`) and `pdfplumber` (PDF). Without
it CSV, XML and pasted text still work.

### Claude Code

A project `.mcp.json` entry for a non-Nix install:

```json
{
  "mcpServers": {
    "digikey": {
      "type": "stdio",
      "command": "digikey-mcp",
      "args": ["serve"],
      "env": {
        "CLIENT_ID_FILE": "/home/you/.config/digikey/client_id",
        "CLIENT_SECRET_FILE": "/home/you/.config/digikey/client_secret"
      }
    }
  }
}
```

## Tools

BOM and cart:

- `resolve_bom`: ingest a BOM and resolve every line to a DigiKey part
  number, quantity and packaging.
- `apply_decisions`: decide lines of a session with a part number or `skip`.
- `bom_report`: render a session as markdown or CSV, optionally to a file.
- `cart_items_for_session`: items ready for `cart_links`, plus the blocking
  lines.
- `cart_links`: FastAdd URLs, an HTML page of POST forms, or Quick Order
  text; builds text only.
- `quota_status`: DigiKey calls used and left this minute and UTC day.

DigiKey Product Information v4:

- `keyword_search`: search by keyword, MPN or DigiKey part number, with
  filters and sorting.
- `product_details`: one product with all packaging variations, price breaks
  and parameters.
- `pricing_by_quantity`: price a quantity across the packaging options.
- `alternate_packaging`: the other packaging variants of a part.
- `substitutions`: substitutes DigiKey suggests.
- `package_type_by_quantity`: recommended packaging for a quantity.
- `product_pricing`: pricing for every product matching a part number.
- `digireel_pricing`: DigiReel fee and prices for a quantity.
- `recommended_products`: products recommended alongside a part.
- `associations`: kits, mating and for-use-with products.
- `media`: images, datasheets and videos.
- `search_manufacturers`: manufacturers with ids.
- `search_categories`: categories with ids.
- `get_category_by_id`: one category with its children.

The search tools return trimmed data; pass `raw=True` where offered for the
full DigiKey response.

## Rate limits and cache

DigiKey allows 120 calls per minute and 1000 per day for Product Information
v4, with no batch endpoint, so a BOM costs at least one call per line. The
resolver keeps a reserve of 5, sleeps through a full minute window when the
wait is short, and otherwise stops with `complete: false` and a resume message;
`resolve_bom` with the same `session_id` continues where it stopped. A 403 or
429 from DigiKey holds all calls off for 10 minutes.

Trimmed responses are cached for 6 hours for price and stock endpoints and
1 day for static data. Cache and sessions live under
`$XDG_CACHE_HOME/digikey-mcp` (default `~/.cache/digikey-mcp`): `cache.sqlite3`
and `sessions/<id>.json`. The command line's `--cache-dir` points them
elsewhere.

## Preferences

`resolve_bom` takes `preferences_path`, a TOML file for overage per category,
allowed packaging, marketplace and obsolete handling, preferred manufacturers
and keywords for generic lines, quota limits and cache lifetimes. Unknown
sections or keys are rejected. The full key list is in
[src/digikey_mcp/resolve/README.md](src/digikey_mcp/resolve/README.md).

```toml
[overage.resistor]
percent = 10
min_extra = 2

[packaging]
allowed = ["cut tape", "tape & reel"]

[generic]
preferred_manufacturers = ["Yageo", "Murata"]
```

## Limitations

Unverified against the real world:

- DigiKey response shapes. The field names, packaging names, status text and
  substitute types the resolver expects are assumptions in
  `src/digikey_mcp/resolve/adapter.py` and have not been checked against live
  responses.
- FastAdd. The cart URL and form behaviour follow DigiKey's October 2025
  forum guide; no live test has confirmed it in 2026, nor the line cap per
  cart. Quick Order text format is unconfirmed.
- KiCad and Altium exports. Ingest is tested on small fixtures, not on real
  project exports.
- PDF extraction. Table and text extraction from PDF BOMs is tested only on
  generated files.

Tests are offline (stub client, MockTransport, local fixtures) and need no
credentials or network.
