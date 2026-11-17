"""doctor: check the DigiKey credentials and mode without printing any secret."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .. import common
from ..common import Context, out
from ..parsing import command, examples


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "doctor",
        "Report which credential variables are set, whether the files they name exist and are "
        "non-empty, and whether sandbox or production is selected. Values are never printed. "
        "Makes no network call unless --online is given; --online then requests one access token. "
        "Exit code 0 when the credentials are usable (and the token request, if made, succeeded), "
        "1 otherwise.",
        examples(
            ("doctor", "check variables and files offline"),
            ("doctor --online", "also request a token from DigiKey"),
            ("doctor --json", "machine-readable"),
        ),
    )
    p.add_argument("--online", action="store_true", help="also request an access token from DigiKey")
    p.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    p.set_defaults(func=run)
    return p


def _credential_entry(check: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": check.name, "ok": check.ok, "source": check.source}
    if check.path:
        entry["path"] = check.path
    if check.problem:
        entry["problem"] = check.problem
    return entry


def _credential_text(entry: dict[str, Any]) -> str:
    name = entry["name"]
    if entry["source"] == "file":
        path = entry["path"]
        return f"{entry['problem']}: {path}" if "problem" in entry else f"ok, from file {path}"
    if entry["source"] == "variable":
        return f"ok, from variable {name}"
    return f"missing: neither {name}_FILE nor {name} is set"


def collect(online: bool) -> dict[str, Any]:
    """The doctor report as a dict (no secret values)."""
    from ...client import (
        PRODUCTION_BASE_URL,
        SANDBOX_BASE_URL,
        DigiKeyError,
        check_credentials,
        sandbox_enabled,
    )

    checks = check_credentials()
    sandbox = sandbox_enabled()
    ready = all(c.ok for c in checks)
    report: dict[str, Any] = {
        "credentials": [_credential_entry(c) for c in checks],
        "mode": "sandbox" if sandbox else "production",
        "base_url": SANDBOX_BASE_URL if sandbox else PRODUCTION_BASE_URL,
        "token_request": "not run",
    }
    if online:
        if not ready:
            report["token_request"] = "skipped: credentials incomplete"
        else:
            try:
                client = common.get_client()
                try:
                    client.authenticate()
                finally:
                    client.close()
                report["token_request"] = "ok"
            except DigiKeyError as exc:
                report["token_request"] = f"failed: {exc}"
    report["ok"] = ready and report["token_request"] in ("ok", "not run")
    return report


def run(args: argparse.Namespace, ctx: Context) -> int:
    report = collect(args.online)
    if args.json:
        out(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    style = ctx.style
    lines = ["DigiKey credentials"]
    for entry in report["credentials"]:
        text = _credential_text(entry)
        lines.append(f"  {entry['name']:<16}{style.green(text) if entry['ok'] else style.red(text)}")
    lines.append(f"  {'mode':<16}{report['mode']} ({report['base_url']})")
    token = report["token_request"]
    if token == "not run":
        token = "not run (add --online to request a token)"
    lines.append(f"  {'token request':<16}{token}")
    if not report["ok"]:
        lines += [
            "",
            "Credentials come from an app at developer.digikey.com; see README.md section 'Setup'.",
        ]
    out("\n".join(lines))
    return 0 if report["ok"] else 1
