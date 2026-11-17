# DigiKey MCP server

An MCP server (FastMCP, stdio) and a command line that turn a bill of
materials into exact DigiKey part numbers and order quantities and build links
that fill a DigiKey cart. Nothing is ever submitted as an order: the user opens
the links in a signed-in browser, reviews the cart, picks the payment method
and clicks buy.

You need:

- Python 3.11 or newer, or Nix.
- An account at <https://developer.digikey.com> (a different sign-up from the
  DigiKey shop account).
- A Production app there with Product Information V4 enabled.
- The app's client ID and client secret, each in its own file.

Without credentials the server starts and `ingest` and `cart_links` work; every
DigiKey lookup fails. Section [Setup](#setup) has the steps and
[Troubleshooting](#troubleshooting) the error messages.

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
   up to five ranked candidates. For resistors, capacitors, LEDs, ATO blade
   and PTC fuses and pin headers the search uses DigiKey's parameter filters
   and every candidate is checked against its own parameters (resistance,
   capacitance, voltage rating, tolerance, package, current, colour, pitch and
   positions); a candidate's `why` lists the verified values. A line with no
   verified candidate returns none and the reason names the parameter that
   could not be matched. Ambiguous, unavailable and failed lines are reported
   as blocking.
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
| `doctor` | Check credentials and mode, no secrets shown (`--online`: token). |
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

1. Create an account and organisation at <https://developer.digikey.com>. It
   is a different sign-up from the DigiKey shop account.
2. Create a Production app. Tick one API product, Product Information V4. Note
   its client ID and client secret.
   - The wizard asks for an "OAuth Callback URL". It is unused (two-legged
     client-credentials OAuth); `https://localhost` satisfies the form.
   - Sandbox and production credentials are different apps. A sandbox app needs
     `DIGIKEY_SANDBOX=true`.
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
- `DIGIKEY_MCP_DEBUG`: set to `1` to print the traceback when a command line
  run fails with a runtime error.

Credentials are read on the first DigiKey call, so the server starts and
`cart_links` works without them. The start banner shows the credential state
(see [Start banner](#start-banner)). `digikey-mcp doctor` reports which variables
are set, whether the files exist and are non-empty, and sandbox or production
mode, without printing a secret; `digikey-mcp doctor --online` also requests
one token.

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
CLIENT_ID_FILE=/home/you/.config/digikey/client_id \
CLIENT_SECRET_FILE=/home/you/.config/digikey/client_secret \
uv run digikey-mcp serve     # or: python -m digikey_mcp.server
```

The `ingest` extra adds `openpyxl` (`.xlsx`) and `pdfplumber` (PDF). Without
it CSV, XML and pasted text still work.

### Start banner

At start the server prints a block on stderr (stdout stays the MCP protocol):
name and version, transport, the `claude mcp add` line for this executable
with the configured credential paths (`/home/you/...` placeholders marked
"replace" when unset), credential state (never the values), and production or
sandbox with the API host. Run by hand in a terminal it adds a line saying it
waits for an MCP client on stdin and serves nobody. It replaces FastMCP's own
banner. `DIGIKEY_MCP_QUIET=1` or `-q` suppresses it; the one-line warning
about missing credentials stays. Transport is stdio; `FASTMCP_TRANSPORT=http`
or `sse` (FastMCP's own settings, with `FASTMCP_HOST`, `FASTMCP_PORT`) serves
over HTTP instead, and the banner then shows the URL and the matching
`claude mcp add --transport` line. The server has no flag of its own for it.

### Claude Code

Replace every path below with your own. Command line form:

```bash
claude mcp add digikey \
  -e CLIENT_ID_FILE=/home/you/.config/digikey/client_id \
  -e CLIENT_SECRET_FILE=/home/you/.config/digikey/client_secret \
  -- digikey-mcp serve
```

To run the server from another project's directory through direnv (the dev
shell of this checkout, after `direnv allow` there):

```bash
claude mcp add digikey \
  -e CLIENT_ID_FILE=/home/you/.config/digikey/client_id \
  -e CLIENT_SECRET_FILE=/home/you/.config/digikey/client_secret \
  -- direnv exec /path/to/digikey_mcp digikey-mcp
```

An MCP stdio server is started by each client. A copy running in another
terminal does not serve a second client; every client needs its own
registration.

The same registration as a project `.mcp.json` entry for a non-Nix install:

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

## Troubleshooting

- `CLIENT_ID and CLIENT_SECRET not set` (or one of the two): the variable is
  unset in the environment the client gave the server. Set `CLIENT_ID_FILE` and
  `CLIENT_SECRET_FILE` in the registration's `env`, or with `-e` on
  `claude mcp add`.
- `CLIENT_ID_FILE points to an unreadable file` (or `CLIENT_SECRET_FILE`): the
  path is wrong, the file is not readable, or a placeholder path from an
  example was left in. Point it at the real file; `digikey-mcp doctor` names
  the path and the problem.
- `points to an empty file`: the file holds no value. Write only the value
  into it.
- `/mcp` shows the server Connected but every lookup fails: credentials are
  read on the first DigiKey call, not at start. Run `digikey-mcp doctor`, then
  `digikey-mcp doctor --online`.
- `token request rejected (401)`: wrong ID or secret, the app lacks Product
  Information V4, or sandbox and production credentials are mixed. Check the
  app on developer.digikey.com; set `DIGIKEY_SANDBOX=true` only with a sandbox
  app.

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
