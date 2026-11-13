"""sessions: saved resolution sessions with status counts."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .. import render
from ..common import PROG, Context, out
from ..parsing import command, examples


def register(sub: Any) -> argparse.ArgumentParser:
    p = command(
        sub, "sessions",
        "List the saved resolution sessions, newest first, with their line status counts and whether "
        "they are ready for the cart. Reads the session files; makes no DigiKey calls.",
        examples(
            ("sessions", "list sessions in the default cache"),
            ("sessions --cache-dir ./cache --json", "list sessions of another cache, as JSON"),
        ),
    )
    p.add_argument("--json", action="store_true", help="print the list as JSON on stdout")
    p.set_defaults(func=run)
    return p


def collect(ctx: Context) -> list[dict[str, Any]]:
    from ...resolve.session import Session
    from ...resolve.store import open_store

    rows: list[dict[str, Any]] = []
    with open_store(ctx.cache_dir) as store:
        files = sorted(store.sessions_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files:
            row: dict[str, Any] = {"session_id": f.stem, "modified": f.stat().st_mtime}
            try:
                bom = Session.load(store, f.stem).bom()
            except Exception:  # noqa: BLE001 - a damaged file must not hide the others
                row["error"] = "unreadable session file"
                rows.append(row)
                continue
            row.update(boards=bom.boards, line_count=len(bom.lines), counts=bom.counts, complete=bom.complete,
                       ready_for_cart=bom.complete and not bom.blocking, estimated_total=bom.estimated_total)
            rows.append(row)
    return rows


def run(args: argparse.Namespace, ctx: Context) -> int:
    rows = collect(ctx)
    if args.json:
        out(json.dumps(rows, indent=2))
        return 0
    if not rows:
        out("No saved sessions.")
        ctx.info(f"Start one with: {PROG} resolve bom.csv")
        return 0
    table = []
    for r in rows:
        if "error" in r:
            table.append((r["session_id"], render.when(r["modified"]), "", "", r["error"], ""))
            continue
        state = "ready for cart" if r["ready_for_cart"] else ("incomplete" if not r["complete"] else "needs decisions")
        counts = ", ".join(f"{k} {v}" for k, v in r["counts"].items())
        table.append((r["session_id"], render.when(r["modified"]), r["boards"], r["line_count"], counts, state))
    out("\n".join(render.table(["session", "modified", "boards", "lines", "status counts", "state"], table,
                               [34, 16, 6, 5, 60, 16], right=(2, 3))))
    return 0
