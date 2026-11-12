# Getting started

This guide sets up the DigiKey server for Claude Code without Nix. It takes
about 15 minutes. The server reads your bill of materials (BOM), finds the
exact DigiKey part numbers and quantities, and gives you links that fill your
DigiKey cart. It never places an order: you review the cart and pay yourself.

## 1. Install Python 3.11 or newer

Check what you have:

```bash
python3 --version
```

On Windows use `py --version`. If the version is older than 3.11, install a
current one from <https://www.python.org/downloads/>. On Windows tick "Add
python.exe to PATH" in the installer.

## 2. Install the project

Open a terminal in the project folder and create a virtual environment so the
install stays separate from the rest of your system.

Linux and macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install ".[ingest]"
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install ".[ingest]"
```

The `ingest` part adds support for Excel and PDF bills of materials. The
program is now at `.venv/bin/digikey-mcp` (Linux and macOS) or
`.venv\Scripts\digikey-mcp.exe` (Windows).

## 3. Get DigiKey credentials

1. Create an account at <https://developer.digikey.com> and an organisation.
2. Create an app of type Production and enable the Product Information v4 API.
3. Copy the client ID and the client secret from the app page.

To try things without real data, create a sandbox app instead and set
`DIGIKEY_SANDBOX` to `true` in step 5.

## 4. Put the credentials in two files

Make one file for each value. Each file contains only the value, nothing else.

Linux and macOS:

```bash
mkdir -p ~/.config/digikey
printf '%s' 'YOUR_CLIENT_ID' > ~/.config/digikey/client_id
printf '%s' 'YOUR_CLIENT_SECRET' > ~/.config/digikey/client_secret
chmod 600 ~/.config/digikey/client_id ~/.config/digikey/client_secret
```

Windows (PowerShell):

```powershell
mkdir $env:USERPROFILE\digikey
Set-Content -NoNewline $env:USERPROFILE\digikey\client_id 'YOUR_CLIENT_ID'
Set-Content -NoNewline $env:USERPROFILE\digikey\client_secret 'YOUR_CLIENT_SECRET'
```

Do not put these files in the project folder or in any git repository.

## 5. Add the server to Claude Code

Create a file named `.mcp.json` in the folder where you start Claude Code, or
add the entry to your existing one. Use the full path to the program from
step 2.

Linux and macOS:

```json
{
  "mcpServers": {
    "digikey": {
      "type": "stdio",
      "command": "/home/you/digikey_mcp/.venv/bin/digikey-mcp",
      "env": {
        "CLIENT_ID_FILE": "/home/you/.config/digikey/client_id",
        "CLIENT_SECRET_FILE": "/home/you/.config/digikey/client_secret"
      }
    }
  }
}
```

Windows (write backslashes doubled in JSON, or use forward slashes):

```json
{
  "mcpServers": {
    "digikey": {
      "type": "stdio",
      "command": "C:\\Users\\you\\digikey_mcp\\.venv\\Scripts\\digikey-mcp.exe",
      "env": {
        "CLIENT_ID_FILE": "C:\\Users\\you\\digikey\\client_id",
        "CLIENT_SECRET_FILE": "C:\\Users\\you\\digikey\\client_secret"
      }
    }
  }
}
```

Add `"DIGIKEY_SANDBOX": "true"` inside `env` to use the sandbox. Restart Claude
Code and approve the new server when it asks. The command `/mcp` should list
`digikey` as connected.

## 6. Run your first BOM

Export a BOM from your design tool as CSV (KiCad or Altium), `.xlsx` or PDF.
In Claude Code, ask for example:

> Resolve the BOM in C:\Users\you\boards\amp\bom.csv for 2 boards, show me the
> report, and ask me about anything that needs a decision.

What happens next:

1. The server reads the file and looks up every line on DigiKey.
2. Lines with a manufacturer part number or DigiKey number are matched
   automatically.
3. Lines with only a value, such as `10k 0603 1%`, are never picked for you.
   You get a short list of candidates and choose one, or skip the line.
4. You get a report with the chosen part, quantity, packaging and price for
   each line. Check it.
5. Ask for the cart links. Open them in a browser where you are signed in to
   DigiKey. The first link empties your cart and the rest add the parts.
6. Review the cart on DigiKey, choose the payment method and place the order
   yourself.

DigiKey limits lookups to 120 per minute and 1000 per day. A long BOM may
stop part way with a message; ask Claude to continue the same session later.
Results and sessions are kept in `.cache\digikey-mcp` under your home folder.

## If something goes wrong

- `/mcp` shows digikey as failed: run the program from step 2 in a terminal.
  A Python error there names the problem. Check the path in `.mcp.json`.
- "CLIENT_ID not set" or "points to an unreadable file": check the two file
  paths and that each file holds only the value.
- Authentication errors: the app must be a Production app with Product
  Information v4 enabled, and sandbox and production credentials must not be
  mixed.
- Excel or PDF import fails with a missing package: repeat step 2 with
  `".[ingest]"`.

Further reading is in [README.md](README.md).
